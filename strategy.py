"""Estrategia: VWAP (diario UTC) + EMA200 del TF del gráfico + EMA200 del TF superior.

LONG : vela cerrada por encima de las 3 líneas y con mínimo que tocó la línea más alta (pullback).
SHORT: vela cerrada por debajo de las 3 líneas y con máximo que tocó la línea más baja.

v4: filtros extra (ADX, pendiente EMA, volumen relativo, coste/riesgo, tendencia de BTC).
Las mismas funciones las usan el bot en vivo y backtest.py, para que no haya diferencias.
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return _true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = 1 / n
    atr_ = _true_range(df).ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr_
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def day_key(df: pd.DataFrame, anchor_hour: int = 0) -> pd.Series:
    """Identificador de 'sesión' VWAP: se reinicia cada día a las anchor_hour UTC."""
    return (df["ts"].astype("int64") - int(anchor_hour) * 3600000) // 86400000


def daily_vwap(df: pd.DataFrame, anchor_hour: int = 0) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = day_key(df, anchor_hour)
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum()
    return pv / vv


@dataclass
class Analysis:
    symbol: str
    trend: str          # long | short | none
    signal: bool
    side: str           # long | short | ""
    entry: float
    sl_dist: float
    sl_pct: float
    score: float
    atr_pct: float
    rel_vol: float
    adx: float = 0.0
    why: str = ""       # motivo si hubo pullback pero se bloqueó


def compute_features(df: pd.DataFrame, dfh: pd.DataFrame, cfg) -> pd.DataFrame:
    """Indicadores por vela (vectorizado). dfh = velas del TF superior (cerradas)."""
    d = df.copy().reset_index(drop=True)
    d["ema"] = ema(d["close"], cfg.ema_len)
    d["vwap"] = daily_vwap(d, cfg.vwap_anchor_hour)
    d["vwap_age"] = d.groupby(day_key(d, cfg.vwap_anchor_hour)).cumcount()   # velas desde el reinicio del VWAP
    d["atr"] = atr(d, cfg.atr_len)
    d["adx"] = adx(d, cfg.adx_len)
    d["ema_slope"] = d["ema"] - d["ema"].shift(cfg.slope_bars)
    d["rel_vol"] = d["volume"] / d["volume"].shift(1).rolling(20).mean()

    tf_ms = int(d["ts"].diff().median())
    htf_ms = int(dfh["ts"].diff().median())
    hh = pd.DataFrame({
        "avail": dfh["ts"].values.astype("int64") + htf_ms,   # momento en que cierra la vela HTF
        "ema_h": ema(dfh["close"], cfg.ema_len).values,
    })
    left = pd.DataFrame({"t": d["ts"].values.astype("int64") + tf_ms})
    m = pd.merge_asof(left, hh, left_on="t", right_on="avail", direction="backward")
    d["ema_h"] = m["ema_h"].values
    return d


def _lines(r):
    vals = (r["vwap"], r["ema"], r["ema_h"])
    return max(vals), min(vals)


def trend_of(r) -> str:
    if any(isinstance(r[k], float) and math.isnan(r[k]) for k in ("vwap", "ema", "ema_h", "close")):
        return "none"
    upper, lower = _lines(r)
    return "long" if r["close"] > upper else "short" if r["close"] < lower else "none"


def btc_state_series(feat: pd.DataFrame) -> pd.Series:
    """bull / bear / neutral por vela, indexado por ts."""
    upper = feat[["vwap", "ema", "ema_h"]].max(axis=1)
    lower = feat[["vwap", "ema", "ema_h"]].min(axis=1)
    st = np.where(feat["close"] > upper, "bull", np.where(feat["close"] < lower, "bear", "neutral"))
    return pd.Series(st, index=feat["ts"].values)


def evaluate_row(symbol: str, r: dict, cfg, is_btc: bool = False, btc_state: str = "neutral"):
    need = ("vwap", "ema", "ema_h", "atr", "close")
    if any(r[k] is None or (isinstance(r[k], float) and math.isnan(r[k])) for k in need):
        return None
    if not (r["vwap"] > 0 and r["atr"] > 0):
        return None

    trend = trend_of(r)
    if trend == "none":
        return Analysis(symbol, "none", False, "", float(r["close"]), 0, 0, 0.0, 0.0, 0.0)

    upper, lower = _lines(r)
    touched = (r["low"] <= upper) if trend == "long" else (r["high"] >= lower)

    sl_dist = max(r["atr"] * cfg.atr_mult, r["close"] * cfg.min_sl_pct / 100)
    sl_pct = sl_dist / r["close"] * 100
    atr_pct = r["atr"] / r["close"] * 100
    rel_vol = 0.0 if (isinstance(r["rel_vol"], float) and math.isnan(r["rel_vol"])) else float(r["rel_vol"])
    adx_v = 0.0 if (isinstance(r["adx"], float) and math.isnan(r["adx"])) else float(r["adx"])
    slope = 0.0 if (isinstance(r["ema_slope"], float) and math.isnan(r["ema_slope"])) else float(r["ema_slope"])
    trend_str = abs(r["close"] - r["ema_h"]) / r["atr"]
    score = round(min(rel_vol, 3.0) + min(trend_str, 4.0) * 0.5 + min(adx_v, 40) / 40, 2)

    why = ""
    if not touched:
        why = "sin pullback"
    elif r["vwap_age"] < cfg.vwap_min_bars:
        why = "VWAP recién reiniciado"
    elif sl_pct > cfg.max_sl_pct:
        why = "stop demasiado ancho"
    elif cfg.adx_min > 0 and adx_v < cfg.adx_min:
        why = "ADX bajo (lateral)"
    elif cfg.slope_filter and ((trend == "long" and slope <= 0) or (trend == "short" and slope >= 0)):
        why = "EMA200 sin pendiente a favor"
    elif rel_vol < cfg.min_rel_vol:
        why = "volumen bajo"
    elif (cfg.fee_rt_pct + cfg.slip_pct) / sl_pct > cfg.max_cost_ratio:
        why = "comisiones/riesgo alto"
    elif cfg.btc_filter and not is_btc:
        if cfg.btc_mode == "strict":
            ok = (trend == "long" and btc_state == "bull") or (trend == "short" and btc_state == "bear")
        else:
            ok = not ((trend == "long" and btc_state == "bear") or (trend == "short" and btc_state == "bull"))
        if not ok:
            why = "BTC en contra"

    signal = (why == "")
    return Analysis(
        symbol=symbol, trend=trend, signal=signal, side=trend if signal else "",
        entry=float(r["close"]), sl_dist=float(sl_dist), sl_pct=float(sl_pct), score=score,
        atr_pct=float(atr_pct), rel_vol=rel_vol, adx=adx_v, why=why,
    )


def analyze(symbol: str, df: pd.DataFrame, dfh: pd.DataFrame, cfg, btc_state: str = "neutral", is_btc: bool = False):
    if len(df) < cfg.ema_len + 25 or len(dfh) < cfg.ema_len + 5:
        return None
    feat = compute_features(df, dfh, cfg)
    return evaluate_row(symbol, feat.iloc[-1].to_dict(), cfg, is_btc, btc_state)


def btc_state_now(df: pd.DataFrame, dfh: pd.DataFrame, cfg) -> str:
    if len(df) < cfg.ema_len + 25 or len(dfh) < cfg.ema_len + 5:
        return "neutral"
    feat = compute_features(df, dfh, cfg)
    return str(btc_state_series(feat).iloc[-1])

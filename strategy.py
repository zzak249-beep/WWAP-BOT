"""Estrategia: VWAP (diario UTC) + EMA200 del TF del gráfico + EMA200 del TF superior.

LONG : la vela cerrada está por encima de las 3 líneas y su mínimo tocó la línea más alta (pullback).
SHORT: la vela cerrada está por debajo de las 3 líneas y su máximo tocó la línea más baja.
"""
from dataclasses import dataclass

import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def daily_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.date
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


def analyze(symbol: str, df: pd.DataFrame, dfh: pd.DataFrame, cfg):
    need = cfg.ema_len + 25
    if len(df) < need or len(dfh) < cfg.ema_len + 5:
        return None

    d = df.copy()
    d["ema"] = ema(d["close"], cfg.ema_len)
    d["vwap"] = daily_vwap(d)
    d["atr"] = atr(d, cfg.atr_len)
    ema_h = float(ema(dfh["close"], cfg.ema_len).iloc[-1])

    c = d.iloc[-1]
    if not (c["vwap"] > 0 and c["atr"] > 0):
        return None

    upper = max(c["vwap"], c["ema"], ema_h)
    lower = min(c["vwap"], c["ema"], ema_h)

    trend = "long" if c["close"] > upper else "short" if c["close"] < lower else "none"
    if trend == "none":
        return Analysis(symbol, "none", False, "", float(c["close"]), 0, 0, 0.0, 0.0, 0.0)

    touched = (c["low"] <= upper) if trend == "long" else (c["high"] >= lower)

    sl_dist = max(c["atr"] * cfg.atr_mult, c["close"] * cfg.min_sl_pct / 100)
    sl_pct = sl_dist / c["close"] * 100
    atr_pct = c["atr"] / c["close"] * 100

    avg_vol = d["volume"].iloc[-21:-1].mean()
    rel_vol = float(c["volume"] / avg_vol) if avg_vol > 0 else 0.0
    trend_str = abs(c["close"] - ema_h) / c["atr"]

    # score: volumen relativo (máx 3) + fuerza de tendencia vs EMA superior (máx 4 * 0.5)
    score = round(min(rel_vol, 3.0) + min(trend_str, 4.0) * 0.5, 2)

    signal = bool(touched and sl_pct <= cfg.max_sl_pct)
    return Analysis(
        symbol=symbol,
        trend=trend,
        signal=signal,
        side=trend if signal else "",
        entry=float(c["close"]),
        sl_dist=float(sl_dist),
        sl_pct=float(sl_pct),
        score=score,
        atr_pct=float(atr_pct),
        rel_vol=rel_vol,
    )

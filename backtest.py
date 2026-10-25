"""Backtest con velas públicas de BingX (no necesita API keys).

Uso:   python backtest.py --days 45 --symbols 25
Compara la configuración actual contra variantes (sin ADX, sin filtro BTC, SL mínimo 0.3...)
incluyendo comisiones y deslizamiento, con partición en muestra / fuera de muestra (70/30)
y resultados por hora UTC. Usa EXACTAMENTE la misma lógica que el bot en vivo (strategy.py).

Aviso: un backtest nunca iguala la realidad (deslizamiento, fallos de ejecución, cambios de mercado).
Úsalo para descartar ideas malas, no para confirmar que una es buena.
"""
import argparse
import dataclasses
import os
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from config import cfg as BASE
from strategy import btc_state_series, compute_features, evaluate_row

WARMUP = BASE.ema_len * 3 + 30   # velas para que la EMA200 del TF superior esté estable


# ---------------------------------------------------------------- datos
def _retry(fn, *a, **k):
    for i in range(5):
        try:
            return fn(*a, **k)
        except Exception as e:
            if "100410" in str(e) or "429" in str(e):
                time.sleep(2 * (i + 1))
                continue
            raise
    return fn(*a, **k)


def fetch_history(ex, symbol, tf, days, cache_dir="data"):
    os.makedirs(cache_dir, exist_ok=True)
    fn = os.path.join(cache_dir, f"{symbol.replace('/', '_').replace(':', '_')}_{tf}_{days}d.csv")
    if os.path.exists(fn) and time.time() - os.path.getmtime(fn) < 12 * 3600:
        return pd.read_csv(fn)
    tf_ms = ex.parse_timeframe(tf) * 1000
    since = ex.milliseconds() - int(days * 86400000)
    rows = []
    while True:
        batch = _retry(ex.fetch_ohlcv, symbol, tf, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + tf_ms
        if nxt <= since or len(batch) < 2 or nxt >= ex.milliseconds():
            break
        since = nxt
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if len(df) and df["ts"].iloc[-1] + tf_ms > ex.milliseconds():
        df = df.iloc[:-1]
    df.to_csv(fn, index=False)
    return df


# ---------------------------------------------------------------- simulación
def simulate(rec, i, side, entry, R, cfg, max_hold=288):
    """Devuelve (índice_salida, precio_salida). SL/TP intravela; si ambos en la misma vela, gana el SL."""
    s = 1 if side == "long" else -1
    sl = entry - s * R
    tp = entry + s * R * cfg.rr
    be, ext = False, 0
    n = len(rec)
    last = min(i + max_hold, n - 1)
    for j in range(i + 1, last + 1):
        hi, lo, cl = rec[j]["high"], rec[j]["low"], rec[j]["close"]
        # stop
        if (s == 1 and lo <= sl) or (s == -1 and hi >= sl):
            return j, sl
        # ampliación de TP
        if cfg.ext_tp:
            while ext < cfg.max_ext and ((s == 1 and hi >= tp - 0.2 * R) or (s == -1 and lo <= tp + 0.2 * R)):
                tp += s * cfg.ext_r * R
                ext += 1
        if (s == 1 and hi >= tp) or (s == -1 and lo <= tp):
            return j, tp
        pr = s * (cl - entry) / R
        # break-even y trailing (al cierre de la vela)
        if cfg.trail:
            if not be and pr >= cfg.be_r:
                new_sl = entry * (1 + s * cfg.be_buf_pct / 100)
                if s * (new_sl - sl) > 0:
                    sl, be = new_sl, True
            elif be:
                tr = cl - s * cfg.trail_r * R
                if s * (tr - sl) >= cfg.trail_step_r * R:
                    sl = tr
        # time stop
        if cfg.time_stop_bars > 0 and not be and (j - i) >= cfg.time_stop_bars and pr < cfg.time_stop_min_r:
            return j, cl
    return last, rec[last]["close"]


def symbol_trades(symbol, feat, btc_arr, cfg):
    rec = feat.to_dict("records")
    is_btc = symbol.startswith("BTC/")
    out, busy_until, last_sig = [], -1, -10**9
    for i in range(WARMUP, len(rec) - 1):
        if i <= busy_until or i - last_sig <= cfg.cooldown_candles:
            continue
        a = evaluate_row(symbol, rec[i], cfg, is_btc, btc_arr[i])
        if not a or not a.signal:
            continue
        last_sig = i
        j, px = simulate(rec, i, a.side, a.entry, a.sl_dist, cfg)
        s = 1 if a.side == "long" else -1
        gross = s * (px - a.entry) / a.entry * 100
        net = gross - cfg.fee_rt_pct - cfg.slip_pct
        out.append({
            "symbol": symbol, "side": a.side, "t_in": rec[i]["ts"], "t_out": rec[j]["ts"],
            "R": net / a.sl_pct, "net_pct": net, "score": a.score,
        })
        busy_until = j
    return out


def portfolio(trades, cfg):
    """Aplica MAX_OPEN y MAX_TRADES_DAY en orden cronológico."""
    trades = sorted(trades, key=lambda t: (t["t_in"], -t["score"]))
    active, per_day, kept = [], defaultdict(int), []
    for t in trades:
        active = [x for x in active if x > t["t_in"]]
        day = pd.Timestamp(t["t_in"], unit="ms", tz="UTC").date()
        if len(active) >= cfg.max_open or per_day[day] >= cfg.max_trades_day:
            continue
        active.append(t["t_out"])
        per_day[day] += 1
        kept.append(t)
    return kept


def stats(trades, risk_pct):
    if not trades:
        return dict(n=0, win=0, avgW=0, avgL=0, exp=0, pf=0, totR=0, ret=0, dd=0)
    r = np.array([t["R"] for t in trades])
    w, l = r[r > 0], r[r <= 0]
    cum = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(cum) - cum))
    return dict(
        n=len(r), win=100 * len(w) / len(r), avgW=w.mean() if len(w) else 0, avgL=l.mean() if len(l) else 0,
        exp=r.mean(), pf=(w.sum() / -l.sum()) if l.sum() < 0 else float("inf"),
        totR=r.sum(), ret=r.sum() * risk_pct, dd=dd,
    )


# ---------------------------------------------------------------- variantes
VARIANTS = {
    "ACTUAL (todos los filtros)": {},
    "sin ADX": dict(adx_min=0),
    "sin pendiente EMA": dict(slope_filter=False),
    "sin filtro BTC": dict(btc_filter=False),
    "BTC estricto": dict(btc_mode="strict"),
    "sin filtro volumen": dict(min_rel_vol=0),
    "sin espera tras reinicio VWAP": dict(vwap_min_bars=0),
    "espera 12 velas (1h) VWAP": dict(vwap_min_bars=12),
    "espera 72 velas (6h) VWAP": dict(vwap_min_bars=72),
    "VWAP anclado 13 UTC (~NY)": dict(vwap_anchor_hour=13),
    "SL mínimo 0.30%": dict(min_sl_pct=0.30, max_cost_ratio=99),
    "sin trailing/ampliar TP": dict(trail=False, ext_tp=False),
    "time stop 12 velas": dict(time_stop_bars=12),
    "ORIGINAL (sin filtros nuevos)": dict(adx_min=0, slope_filter=False, btc_filter=False,
                                          min_rel_vol=0, min_sl_pct=0.30, max_cost_ratio=99, vwap_min_bars=0),
}


def run(data, btc_feat, cfg_base, variants=VARIANTS, verbose=True):
    feats = {}
    btc_series = btc_state_series(btc_feat) if btc_feat is not None else None
    for sym, (df, dfh) in data.items():
        f = compute_features(df, dfh, cfg_base)
        if btc_series is not None:
            arr = btc_series.reindex(f["ts"].values).fillna("neutral").values
        else:
            arr = np.array(["neutral"] * len(f))
        feats[sym] = (f, arr)

    results = {}
    for name, over in variants.items():
        cfg = dataclasses.replace(cfg_base, **over)
        allt = []
        for sym, (f, arr) in feats.items():
            allt += symbol_trades(sym, f, arr, cfg)
        kept = portfolio(allt, cfg)
        results[name] = (kept, cfg)
        if verbose:
            print(f"  variante lista: {name} ({len(kept)} operaciones)")
    return results


def report(results, risk_pct):
    print("\n" + "=" * 100)
    print(f"{'Variante':32} {'N':>5} {'Gan%':>6} {'GanR':>6} {'PerR':>6} {'Esp.R':>7} {'PF':>5} {'TotR':>7} {'Ret%':>7} {'DD(R)':>6}")
    print("-" * 100)
    for name, (kept, _) in results.items():
        s = stats(kept, risk_pct)
        print(f"{name:32} {s['n']:5d} {s['win']:6.1f} {s['avgW']:6.2f} {s['avgL']:6.2f} {s['exp']:7.3f} "
              f"{s['pf']:5.2f} {s['totR']:7.1f} {s['ret']:7.1f} {s['dd']:6.1f}")
    print("(R = múltiplos del riesgo por operación, ya con comisiones y deslizamiento. Esp.R = expectativa por operación.)")

    kept, _ = results["ACTUAL (todos los filtros)"]
    if len(kept) >= 20:
        kept = sorted(kept, key=lambda t: t["t_in"])
        cut = int(len(kept) * 0.7)
        a, b = stats(kept[:cut], risk_pct), stats(kept[cut:], risk_pct)
        print("\nMuestra (70%) vs fuera de muestra (30%) — configuración ACTUAL")
        print(f"  70% inicial : N={a['n']:4d}  Esp.R={a['exp']:+.3f}  PF={a['pf']:.2f}  Gan%={a['win']:.1f}")
        print(f"  30% final   : N={b['n']:4d}  Esp.R={b['exp']:+.3f}  PF={b['pf']:.2f}  Gan%={b['win']:.1f}")
        print("  Si el tramo final es mucho peor, desconfía del resultado.")

        by_h = defaultdict(list)
        for t in kept:
            by_h[pd.Timestamp(t["t_in"], unit="ms", tz="UTC").hour].append(t["R"])
        print("\nResultado por hora UTC (solo horas con >=5 operaciones; muestra pequeña = ruido)")
        for h in sorted(by_h):
            if len(by_h[h]) >= 5:
                print(f"  {h:02d}h  N={len(by_h[h]):3d}  Esp.R={np.mean(by_h[h]):+.3f}")
        longs = [t["R"] for t in kept if t["side"] == "long"]
        shorts = [t["R"] for t in kept if t["side"] == "short"]
        print(f"\nLargos: N={len(longs)} Esp.R={np.mean(longs) if longs else 0:+.3f} | "
              f"Cortos: N={len(shorts)} Esp.R={np.mean(shorts) if shorts else 0:+.3f}")
    n = len(results["ACTUAL (todos los filtros)"][0])
    if n < 100:
        print(f"\n⚠️ Solo {n} operaciones: por debajo de ~100-200 el resultado es poco fiable. Usa más días o más símbolos.")


# ---------------------------------------------------------------- main
def main():
    import ccxt
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--symbols", type=int, default=25)
    args = ap.parse_args()

    ex = ccxt.bingx({"enableRateLimit": True, "rateLimit": 250, "options": {"defaultType": "swap"}})
    ex.load_markets()
    from exchange import BingX
    bx = BingX.__new__(BingX)
    bx.cfg, bx.ex, bx.hedge = dataclasses.replace(BASE, top_n=args.symbols), ex, True
    symbols = bx.top_symbols()
    if "BTC/USDT:USDT" not in symbols:
        symbols = ["BTC/USDT:USDT"] + symbols
    print(f"Descargando {args.days} días de {len(symbols)} símbolos (puede tardar varios minutos)...")

    data = {}
    for s in symbols:
        try:
            df = fetch_history(ex, s, BASE.timeframe, args.days)
            dfh = fetch_history(ex, s, BASE.htf, args.days)
            if len(df) > WARMUP + 100 and len(dfh) > BASE.ema_len + 5:
                data[s] = (df, dfh)
                print(f"  {s}: {len(df)} velas {BASE.timeframe}")
            else:
                print(f"  {s}: pocas velas ({len(df)}), omitido")
        except Exception as e:
            print(f"  {s}: error {e}")
    if "BTC/USDT:USDT" not in data:
        raise SystemExit("No se pudo obtener BTC; necesario para el filtro BTC.")
    btc_feat = compute_features(*data["BTC/USDT:USDT"], BASE)
    results = run(data, btc_feat, BASE)
    report(results, BASE.risk_pct)


if __name__ == "__main__":
    main()

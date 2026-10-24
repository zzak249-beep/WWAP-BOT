import logging

import ccxt
import pandas as pd

log = logging.getLogger("exchange")

STABLES = {"USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDD", "USDE", "USDP"}


class BingX:
    def __init__(self, cfg):
        self.cfg = cfg
        params = {"enableRateLimit": True, "options": {"defaultType": "swap"}}
        if cfg.api_key and cfg.api_secret:
            params.update({"apiKey": cfg.api_key, "secret": cfg.api_secret})
        self.ex = ccxt.bingx(params)
        self.ex.load_markets()
        self.hedge = cfg.position_mode == "hedge"

    # ---------- datos de mercado ----------
    def top_symbols(self):
        tickers = self.ex.fetch_tickers()
        rows = []
        for sym, t in tickers.items():
            m = self.ex.markets.get(sym)
            if not m or not m.get("swap") or not m.get("linear") or m.get("quote") != "USDT":
                continue
            if m.get("active") is False:
                continue
            base = (m.get("base") or "").upper()
            if base in STABLES or base in self.cfg.exclude_bases:
                continue
            qv = t.get("quoteVolume") or 0
            if qv < self.cfg.min_quote_volume:
                continue
            rows.append((sym, qv))
        rows.sort(key=lambda x: -x[1])
        return [s for s, _ in rows[: self.cfg.top_n]]

    def ohlcv(self, symbol, tf, limit=300):
        raw = self.ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        tf_ms = self.ex.parse_timeframe(tf) * 1000
        if len(df) and df["ts"].iloc[-1] + tf_ms > self.ex.milliseconds():
            df = df.iloc[:-1]  # descartar vela en formación
        return df.reset_index(drop=True)

    def last_price(self, symbol):
        return float(self.ex.fetch_ticker(symbol)["last"])

    # ---------- cuenta ----------
    def balance(self):
        bal = self.ex.fetch_balance()
        u = bal.get("USDT", {}) or {}
        free = float(u.get("free") or 0)
        total = float(u.get("total") or free)
        return total, free

    def open_positions(self):
        out = {}
        for p in self.ex.fetch_positions():
            if float(p.get("contracts") or 0) > 0:
                out[p["symbol"]] = p
        return out

    # ---------- ejecución ----------
    def setup_symbol(self, symbol):
        try:
            self.ex.set_margin_mode(self.cfg.margin_mode, symbol)
        except Exception as e:
            log.info("margin mode %s: %s", symbol, e)
        for side in (["LONG", "SHORT"] if self.hedge else ["BOTH"]):
            try:
                self.ex.set_leverage(self.cfg.leverage, symbol, {"side": side})
            except Exception as e:
                log.info("leverage %s %s: %s", symbol, side, e)

    def position_size(self, symbol, equity, free, sl_dist, price):
        cfg = self.cfg
        qty = (equity * cfg.risk_pct / 100) / sl_dist
        max_notional = min(equity * cfg.leverage, free * cfg.leverage) * 0.9
        qty = min(qty, max_notional / price)
        qty = float(self.ex.amount_to_precision(symbol, qty))
        lim = self.ex.markets[symbol].get("limits", {})
        min_amt = ((lim.get("amount") or {}).get("min")) or 0
        min_cost = ((lim.get("cost") or {}).get("min")) or 0
        if qty <= 0 or qty < min_amt or qty * price < min_cost:
            return 0.0
        return qty

    def _pos_side(self, side):
        if not self.hedge:
            return "BOTH"
        return "LONG" if side == "buy" else "SHORT"

    def open_trade(self, symbol, side, qty, sl, tp):
        ps = self._pos_side(side)
        px = self.ex.price_to_precision
        params = {
            "positionSide": ps,
            "stopLoss": {"triggerPrice": float(px(symbol, sl))},
            "takeProfit": {"triggerPrice": float(px(symbol, tp))},
        }
        try:
            return self.ex.create_order(symbol, "market", side, qty, None, params)
        except Exception as e:
            log.warning("orden con SL/TP adjuntos falló (%s). Reintento por separado.", e)

        # fallback: entrada + SL/TP separados
        order = self.ex.create_order(symbol, "market", side, qty, None, {"positionSide": ps})
        opp = "sell" if side == "buy" else "buy"
        try:
            self.ex.create_order(
                symbol, "STOP_MARKET", opp, qty, None,
                {"positionSide": ps, "stopPrice": float(px(symbol, sl))},
            )
            self.ex.create_order(
                symbol, "TAKE_PROFIT_MARKET", opp, qty, None,
                {"positionSide": ps, "stopPrice": float(px(symbol, tp))},
            )
        except Exception as e2:
            log.error("no se pudo colocar SL/TP, cierro la posición por seguridad: %s", e2)
            self.close_position(symbol, side, qty)
            raise RuntimeError(f"SL/TP fallaron; posición cerrada por seguridad: {e2}")
        return order

    def close_position(self, symbol, side, qty):
        opp = "sell" if side == "buy" else "buy"
        params = {"positionSide": self._pos_side(side)}
        if not self.hedge:
            params["reduceOnly"] = True
        return self.ex.create_order(symbol, "market", opp, qty, None, params)

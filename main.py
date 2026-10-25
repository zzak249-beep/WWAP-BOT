import logging
import sys
from collections import Counter
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import cfg
from exchange import BingX
from strategy import analyze, btc_state_now
from telegram_notify import Telegram

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("bot")
ET = ZoneInfo("America/New_York")


def fp(x):
    return f"{x:.6g}"


class Bot:
    def __init__(self):
        self.live = cfg.trade_mode == "live"
        if self.live and not (cfg.api_key and cfg.api_secret):
            raise SystemExit("TRADE_MODE=live requiere BINGX_API_KEY y BINGX_API_SECRET")
        self.tg = Telegram(cfg.tg_token, cfg.tg_chat)
        self.ex = BingX(cfg)
        self.tf_sec = self.ex.ex.parse_timeframe(cfg.timeframe)
        self.htf_sec = self.ex.ex.parse_timeframe(cfg.htf)

        self.paused = False
        self.halted = False
        self.day = None
        self.trades_today = 0
        self.start_equity = None
        self.cooldown = {}
        self.tracked = {}
        self.htf_cache = {}
        self.last_analyses = []
        self.cache = {"equity": None, "positions": 0, "pos_detail": []}
        self.last_monitor = 0.0
        self.last_full_alert = 0.0
        self.last_manage = 0.0
        self.btc_state = "neutral"

    # ---------- utilidades ----------
    def in_session(self):
        if not cfg.use_session:
            return True
        now = datetime.now(ET).strftime("%H:%M")
        return cfg.session_start <= now < cfg.session_end

    def new_day_check(self):
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day = today
            self.trades_today = 0
            self.halted = False
            if self.live:
                eq, _ = self.ex.balance()
                self.start_equity = eq
                self.cache["equity"] = eq

    def refresh_positions(self):
        pos = self.ex.open_positions()
        self.cache["positions"] = len(pos)
        self.cache["pos_detail"] = [
            f"{s} {p.get('side')} qty={p.get('contracts')} entrada={p.get('entryPrice')} pnl={p.get('unrealizedPnl')}"
            for s, p in pos.items()
        ]
        return pos

    def htf_df(self, symbol):
        bucket = int(time.time() // self.htf_sec)
        hit = self.htf_cache.get(symbol)
        if hit and hit[0] == bucket:
            return hit[1]
        dfh = self.ex.ohlcv(symbol, cfg.htf, 300)
        self.htf_cache[symbol] = (bucket, dfh)
        return dfh

    # ---------- scanner ----------
    def scan(self):
        symbols = self.ex.top_symbols()
        btc_state = "neutral"
        if cfg.btc_filter:
            try:
                bs = "BTC/USDT:USDT"
                btc_state = btc_state_now(self.ex.ohlcv(bs, cfg.timeframe, 300), self.htf_df(bs), cfg)
            except Exception as e:
                log.warning("estado BTC no disponible: %s", e)
        self.btc_state = btc_state
        out = []
        for s in symbols:
            self.manage()
            try:
                df = self.ex.ohlcv(s, cfg.timeframe, 300)
                dfh = self.htf_df(s)
                a = analyze(s, df, dfh, cfg, btc_state, is_btc=s.startswith("BTC/"))
                if a:
                    out.append(a)
            except Exception as e:
                log.warning("%s: %s", s, e)
        out.sort(key=lambda a: -a.score)
        return out, len(symbols)

    def cycle(self):
        self.new_day_check()
        if self.paused or self.halted:
            log.info("en pausa/halt, no escaneo")
            return
        if not self.in_session():
            log.info("fuera de sesión")
            return

        if self.live:
            pos = self.refresh_positions()
            if len(pos) >= cfg.max_open:
                log.info("máx. posiciones (%d): %s", len(pos), self.cache["pos_detail"])
                if time.time() - self.last_full_alert > 1800:
                    self.last_full_alert = time.time()
                    self.tg.send(
                        f"⛔ No escaneo: el bot ve {len(pos)} posiciones abiertas (máx {cfg.max_open}).\n"
                        + "\n".join(self.cache["pos_detail"])
                        + "\nSi no coincide con tu BingX, escribe /pos y avísame."
                    )
                return

        analyses, n = self.scan()
        self.last_analyses = analyses
        bucket = int(time.time() // self.tf_sec)
        sigs = [
            a for a in analyses
            if a.signal and bucket - self.cooldown.get(a.symbol, -999) > cfg.cooldown_candles
        ]
        trending = sum(1 for a in analyses if a.trend != "none")
        blocked = Counter(a.why for a in analyses if a.trend != "none" and not a.signal and a.why not in ("", "sin pullback"))
        log.info("escaneadas=%d en tendencia=%d señales=%d BTC=%s bloqueadas=%s",
                 n, trending, len(sigs), self.btc_state, dict(blocked))
        for a in sigs[: cfg.max_signals_per_scan]:
            self.cooldown[a.symbol] = bucket
            if self.live:
                ok, why = self.execute(a)
                if not ok:
                    self.notify_signal(a, f"NO ejecutada: {why}")
            else:
                self.notify_signal(a, "solo señal (no se opera)")

    # ---------- señales / ejecución ----------
    def levels(self, a, price):
        if a.side == "long":
            return price - a.sl_dist, price + a.sl_dist * cfg.rr
        return price + a.sl_dist, price - a.sl_dist * cfg.rr

    def notify_signal(self, a, mode):
        sl, tp = self.levels(a, a.entry)
        icon = "🟢 LONG" if a.side == "long" else "🔴 SHORT"
        self.tg.send(
            f"{icon} <b>{a.symbol}</b> ({cfg.timeframe})\n"
            f"Entrada ≈ {fp(a.entry)}\n"
            f"SL: {fp(sl)} (-{a.sl_pct:.2f}%)\n"
            f"TP: {fp(tp)} (+{a.sl_pct * cfg.rr:.2f}%)\n"
            f"R:R 1:{cfg.rr:g} | Score {a.score} | VolRel {a.rel_vol:.1f}x | ADX {a.adx:.0f} | ATR {a.atr_pct:.2f}%\n"
            f"Estado: {mode}"
        )

    def execute(self, a):
        """Devuelve (ok, motivo)."""
        try:
            if self.trades_today >= cfg.max_trades_day:
                return False, "límite diario de operaciones"
            pos = self.refresh_positions()
            if len(pos) >= cfg.max_open:
                return False, f"máx. posiciones ({len(pos)})"
            if any(k.split("#")[0] == a.symbol for k in pos):
                return False, "ya hay posición en esta moneda"
            equity, free = self.ex.balance()
            self.cache["equity"] = equity
            if self.start_equity and equity <= self.start_equity * (1 - cfg.daily_loss_pct / 100):
                self.halted = True
                self.tg.send(f"🛑 Pérdida diaria ({cfg.daily_loss_pct}%) alcanzada. Bot detenido hasta mañana (UTC).")
                return False, "límite de pérdida diaria"
            price = self.ex.last_price(a.symbol)
            if abs(price - a.entry) > 0.5 * a.sl_dist:
                return False, "el precio se alejó de la entrada"
            sl, tp = self.levels(a, price)
            qty = self.ex.position_size(a.symbol, equity, free, a.sl_dist, price)
            if qty <= 0:
                return False, f"saldo/tamaño bajo el mínimo del contrato (equity {equity:.2f}, libre {free:.2f})"
            side = "buy" if a.side == "long" else "sell"
            self.ex.setup_symbol(a.symbol)
            self.ex.open_trade(a.symbol, side, qty, sl, tp)
            self.trades_today += 1
            self.tracked[a.symbol] = {
                "side": a.side, "entry": price, "sl": sl, "tp": tp, "qty": qty,
                "R": a.sl_dist, "be": False, "ext": 0, "ts": time.time(),
            }
            self.tg.send(
                f"✅ Orden enviada {a.side.upper()} <b>{a.symbol}</b>\n"
                f"Cantidad: {qty} | Entrada ≈ {fp(price)}\n"
                f"SL {fp(sl)} | TP {fp(tp)} | Riesgo {cfg.risk_pct}% | {cfg.leverage}x\n"
                f"Trailing: {'ON' if cfg.trail else 'OFF'}\n"
                f"Score {a.score} | VolRel {a.rel_vol:.1f}x"
            )
            return True, ""
        except Exception as e:
            log.exception("error ejecutando")
            self.tg.send(f"❌ Error ejecutando {a.symbol}: {e}")
            return False, f"error: {e}"

    # ---------- gestión de ganancias: break-even, trailing y TP ampliable ----------
    def manage(self):
        if not self.live or not self.tracked or time.time() - self.last_manage < cfg.manage_sec:
            return
        self.last_manage = time.time()
        for sym, t in list(self.tracked.items()):
            try:
                self._manage_one(sym, t)
            except Exception as e:
                log.warning("manage %s: %s", sym, e)

    def _manage_one(self, sym, t):
        s = 1 if t["side"] == "long" else -1
        side = "buy" if s == 1 else "sell"
        R = t["R"]
        price = self.ex.last_price(sym)
        profit_r = s * (price - t["entry"]) / R

        # time stop (opcional): si en N velas no hay avance mínimo, cerrar
        if cfg.time_stop_bars > 0 and not t["be"]:
            if time.time() - t["ts"] >= cfg.time_stop_bars * self.tf_sec and profit_r < cfg.time_stop_min_r:
                self.ex.close_position(sym, side, t["qty"])
                self.ex.cancel_open(sym)
                self.tracked.pop(sym, None)
                self.tg.send(f"⏱ {sym}: sin avance en {cfg.time_stop_bars} velas ({profit_r:+.1f}R) → cerrada a mercado")
                return

        if cfg.trail:
            if not t["be"] and profit_r >= cfg.be_r:
                new_sl = t["entry"] * (1 + s * cfg.be_buf_pct / 100)
                if s * (new_sl - t["sl"]) > 0:
                    self.ex.replace_protective(sym, side, t["qty"], "sl", new_sl)
                    t["sl"], t["be"] = new_sl, True
                    self.tg.send(f"🔒 {sym}: +{profit_r:.1f}R → stop movido a break-even ({fp(new_sl)})")
            elif t["be"]:
                trail_sl = price - s * cfg.trail_r * R
                if s * (trail_sl - t["sl"]) >= cfg.trail_step_r * R:
                    self.ex.replace_protective(sym, side, t["qty"], "sl", trail_sl)
                    t["sl"] = trail_sl
                    self.tg.send(f"📈 {sym}: +{profit_r:.1f}R → stop subido a {fp(trail_sl)} (beneficio asegurado)")

        if cfg.ext_tp and t["ext"] < cfg.max_ext:
            tp_r = s * (t["tp"] - t["entry"]) / R
            if profit_r >= tp_r - 0.2:
                new_tp = t["tp"] + s * cfg.ext_r * R
                try:
                    self.ex.replace_protective(sym, side, t["qty"], "tp", new_tp, require_old=True)
                    t["tp"] = new_tp
                    t["ext"] += 1
                    self.tg.send(f"🎯 {sym}: objetivo ampliado a {fp(new_tp)} (ampliación {t['ext']}/{cfg.max_ext})")
                except Exception as e:
                    t["ext"] = cfg.max_ext  # no insistir
                    log.warning("no se pudo ampliar TP de %s: %s", sym, e)

    # ---------- monitoreo ----------
    def monitor(self):
        if not self.live or time.time() - self.last_monitor < 30:
            return
        self.last_monitor = time.time()
        pos = self.refresh_positions()
        equity, _ = self.ex.balance()
        self.cache["equity"] = equity
        open_syms = {k.split("#")[0] for k in pos}
        for sym in list(self.tracked):
            if sym not in open_syms:
                t = self.tracked.pop(sym)
                self.ex.cancel_open(sym)   # limpia SL/TP sueltos para que no afecten a futuras posiciones
                self.tg.send(f"🏁 Posición cerrada {t['side'].upper()} {sym}\nEquity actual: {equity:.2f} USDT")

    # ---------- comandos de Telegram ----------
    def on_command(self, cmd):
        if cmd == "/pause":
            self.paused = True
            return "⏸ Bot en pausa (no abrirá nuevas operaciones)."
        if cmd == "/resume":
            self.paused = False
            self.halted = False
            return "▶️ Bot reanudado."
        if cmd == "/pos":
            d = self.cache["pos_detail"]
            return "Posiciones que ve el bot:\n" + ("\n".join(d) if d else "ninguna")
        if cmd == "/status":
            return (
                f"Modo: {'LIVE' if self.live else 'SEÑALES'}\n"
                f"Pausado: {self.paused} | Halt diario: {self.halted}\n"
                f"Operaciones hoy: {self.trades_today}/{cfg.max_trades_day}\n"
                f"Posiciones abiertas: {self.cache['positions']}\n"
                f"Equity: {self.cache['equity']}\n"
                f"TF {cfg.timeframe}/{cfg.htf} | Riesgo {cfg.risk_pct}% | {cfg.leverage}x"
            )
        if cmd == "/top":
            rows = [a for a in self.last_analyses if a.trend != "none"][:10]
            if not rows:
                return "Sin monedas en tendencia en el último escaneo."
            lines = [
                f"{'🟢' if a.trend == 'long' else '🔴'} {a.symbol} score {a.score} | VolRel {a.rel_vol:.1f}x"
                f"{' ⚡señal' if a.signal else ''}"
                for a in rows
            ]
            return "<b>Mejores monedas (último escaneo)</b>\n" + "\n".join(lines)
        if cmd in ("/start", "/help"):
            return "Comandos: /status /pos /top /pause /resume"
        return None

    # ---------- bucle principal ----------
    def run(self):
        self.tg.listen(self.on_command)
        self.tg.send(
            f"🤖 Bot iniciado | modo {'LIVE' if self.live else 'SEÑALES'} | {cfg.timeframe}/{cfg.htf} | "
            f"top {cfg.top_n} monedas BingX"
        )
        last_bucket = None
        last_err = 0.0
        while True:
            try:
                now = time.time()
                bucket = int(now // self.tf_sec)
                if bucket != last_bucket and (now % self.tf_sec) >= cfg.scan_delay_sec:
                    last_bucket = bucket
                    self.cycle()
                self.manage()
                self.monitor()
            except Exception as e:
                log.exception("error en bucle")
                if time.time() - last_err > 600:
                    last_err = time.time()
                    self.tg.send(f"⚠️ Error en el bot: {e}")
            time.sleep(5)


if __name__ == "__main__":
    Bot().run()

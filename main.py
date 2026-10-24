import logging
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import cfg
from .exchange import BingX
from .strategy import analyze
from .telegram_notify import Telegram

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

        self.paused = False
        self.halted = False
        self.day = None
        self.trades_today = 0
        self.start_equity = None
        self.cooldown = {}
        self.tracked = {}
        self.last_analyses = []
        self.cache = {"equity": None, "positions": 0}
        self.last_monitor = 0.0

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

    # ---------- scanner ----------
    def scan(self):
        symbols = self.ex.top_symbols()
        out = []
        for s in symbols:
            try:
                df = self.ex.ohlcv(s, cfg.timeframe, 300)
                dfh = self.ex.ohlcv(s, cfg.htf, 300)
                a = analyze(s, df, dfh, cfg)
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
        analyses, n = self.scan()
        self.last_analyses = analyses
        bucket = int(time.time() // self.tf_sec)
        sigs = [
            a for a in analyses
            if a.signal and bucket - self.cooldown.get(a.symbol, -999) > cfg.cooldown_candles
        ]
        trending = sum(1 for a in analyses if a.trend != "none")
        log.info("escaneadas=%d en tendencia=%d señales=%d", n, trending, len(sigs))
        for a in sigs[: cfg.max_signals_per_scan]:
            self.cooldown[a.symbol] = bucket
            self.notify_signal(a)
            if self.live:
                self.execute(a)

    # ---------- señales / ejecución ----------
    def levels(self, a, price):
        if a.side == "long":
            return price - a.sl_dist, price + a.sl_dist * cfg.rr
        return price + a.sl_dist, price - a.sl_dist * cfg.rr

    def notify_signal(self, a):
        sl, tp = self.levels(a, a.entry)
        icon = "🟢 LONG" if a.side == "long" else "🔴 SHORT"
        mode = "EJECUTANDO en BingX" if self.live else "solo señal (no se opera)"
        self.tg.send(
            f"{icon} <b>{a.symbol}</b> ({cfg.timeframe})\n"
            f"Entrada ≈ {fp(a.entry)}\n"
            f"SL: {fp(sl)} (-{a.sl_pct:.2f}%)\n"
            f"TP: {fp(tp)} (+{a.sl_pct * cfg.rr:.2f}%)\n"
            f"R:R 1:{cfg.rr:g} | Score {a.score} | VolRel {a.rel_vol:.1f}x | ATR {a.atr_pct:.2f}%\n"
            f"Modo: {mode}"
        )

    def execute(self, a):
        try:
            if self.trades_today >= cfg.max_trades_day:
                self.tg.send("⛔ Límite diario de operaciones alcanzado")
                return
            pos = self.ex.open_positions()
            if len(pos) >= cfg.max_open or a.symbol in pos:
                log.info("saltada %s (posiciones abiertas=%d)", a.symbol, len(pos))
                return
            equity, free = self.ex.balance()
            self.cache["equity"] = equity
            if self.start_equity and equity <= self.start_equity * (1 - cfg.daily_loss_pct / 100):
                self.halted = True
                self.tg.send(f"🛑 Límite de pérdida diaria ({cfg.daily_loss_pct}%) alcanzado. Bot detenido hasta mañana (UTC).")
                return
            price = self.ex.last_price(a.symbol)
            if abs(price - a.entry) > 0.5 * a.sl_dist:
                self.tg.send(f"⚠️ {a.symbol}: el precio se alejó de la entrada, no persigo la operación.")
                return
            sl, tp = self.levels(a, price)
            qty = self.ex.position_size(a.symbol, equity, free, a.sl_dist, price)
            if qty <= 0:
                self.tg.send(f"⚠️ {a.symbol}: saldo insuficiente o tamaño bajo el mínimo del contrato.")
                return
            side = "buy" if a.side == "long" else "sell"
            self.ex.setup_symbol(a.symbol)
            self.ex.open_trade(a.symbol, side, qty, sl, tp)
            self.trades_today += 1
            self.tracked[a.symbol] = {"side": a.side, "entry": price, "sl": sl, "tp": tp, "qty": qty}
            self.tg.send(
                f"✅ Orden enviada {a.side.upper()} <b>{a.symbol}</b>\n"
                f"Cantidad: {qty} | Entrada ≈ {fp(price)}\n"
                f"SL {fp(sl)} | TP {fp(tp)} | Riesgo {cfg.risk_pct}% | {cfg.leverage}x"
            )
        except Exception as e:
            log.exception("error ejecutando")
            self.tg.send(f"❌ Error ejecutando {a.symbol}: {e}")

    # ---------- monitoreo ----------
    def monitor(self):
        if not self.live or time.time() - self.last_monitor < 30:
            return
        self.last_monitor = time.time()
        pos = self.ex.open_positions()
        equity, _ = self.ex.balance()
        self.cache.update({"equity": equity, "positions": len(pos)})
        for sym in list(self.tracked):
            if sym not in pos:
                t = self.tracked.pop(sym)
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
            return "Comandos: /status /top /pause /resume"
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
                self.monitor()
            except Exception as e:
                log.exception("error en bucle")
                if time.time() - last_err > 600:
                    last_err = time.time()
                    self.tg.send(f"⚠️ Error en el bot: {e}")
            time.sleep(5)


if __name__ == "__main__":
    Bot().run()

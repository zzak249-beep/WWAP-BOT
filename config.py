import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _s(name, default=""):
    return os.getenv(name, default).strip()


def _f(name, default):
    return float(os.getenv(name, str(default)))


def _i(name, default):
    return int(float(os.getenv(name, str(default))))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sí")


@dataclass(frozen=True)
class Config:
    # credenciales
    api_key: str = field(default_factory=lambda: _s("BINGX_API_KEY"))
    api_secret: str = field(default_factory=lambda: _s("BINGX_API_SECRET"))
    tg_token: str = field(default_factory=lambda: _s("TELEGRAM_BOT_TOKEN"))
    tg_chat: str = field(default_factory=lambda: _s("TELEGRAM_CHAT_ID"))

    # modo
    trade_mode: str = field(default_factory=lambda: _s("TRADE_MODE", "signals").lower())

    # estrategia
    timeframe: str = field(default_factory=lambda: _s("TIMEFRAME", "5m"))
    htf: str = field(default_factory=lambda: _s("HTF", "15m"))
    ema_len: int = field(default_factory=lambda: _i("EMA_LEN", 200))
    atr_len: int = field(default_factory=lambda: _i("ATR_LEN", 14))
    atr_mult: float = field(default_factory=lambda: _f("ATR_MULT", 1.5))
    min_sl_pct: float = field(default_factory=lambda: _f("MIN_SL_PCT", 0.30))
    max_sl_pct: float = field(default_factory=lambda: _f("MAX_SL_PCT", 2.0))
    rr: float = field(default_factory=lambda: _f("RR", 2.0))

    # scanner
    top_n: int = field(default_factory=lambda: _i("TOP_N", 40))
    min_quote_volume: float = field(default_factory=lambda: _f("MIN_QUOTE_VOLUME", 5_000_000))
    exclude_bases: tuple = field(
        default_factory=lambda: tuple(x.strip().upper() for x in _s("EXCLUDE_BASES").split(",") if x.strip())
    )
    include_tradfi: bool = field(default_factory=lambda: _b("INCLUDE_TRADFI", False))
    max_signals_per_scan: int = field(default_factory=lambda: _i("MAX_SIGNALS_PER_SCAN", 3))
    cooldown_candles: int = field(default_factory=lambda: _i("COOLDOWN_CANDLES", 6))
    scan_delay_sec: int = field(default_factory=lambda: _i("SCAN_DELAY_SEC", 5))

    # riesgo
    risk_pct: float = field(default_factory=lambda: _f("RISK_PCT", 1.0))
    leverage: int = field(default_factory=lambda: _i("LEVERAGE", 5))
    margin_mode: str = field(default_factory=lambda: _s("MARGIN_MODE", "isolated").lower())
    position_mode: str = field(default_factory=lambda: _s("POSITION_MODE", "hedge").lower())
    max_open: int = field(default_factory=lambda: _i("MAX_OPEN", 3))
    max_trades_day: int = field(default_factory=lambda: _i("MAX_TRADES_DAY", 6))
    daily_loss_pct: float = field(default_factory=lambda: _f("DAILY_LOSS_PCT", 3.0))

    # sesión (hora ET)
    use_session: bool = field(default_factory=lambda: _b("USE_SESSION", True))
    session_start: str = field(default_factory=lambda: _s("SESSION_START", "10:00"))
    session_end: str = field(default_factory=lambda: _s("SESSION_END", "17:00"))

    # tamaño igual por posición
    equal_size: bool = field(default_factory=lambda: _b("EQUAL_SIZE", True))

    # gestión de ganancias (trailing)
    trail: bool = field(default_factory=lambda: _b("TRAIL", True))
    be_r: float = field(default_factory=lambda: _f("BE_R", 1.0))
    be_buf_pct: float = field(default_factory=lambda: _f("BE_BUF_PCT", 0.12))
    trail_r: float = field(default_factory=lambda: _f("TRAIL_R", 1.0))
    trail_step_r: float = field(default_factory=lambda: _f("TRAIL_STEP_R", 0.5))
    ext_tp: bool = field(default_factory=lambda: _b("EXTEND_TP", True))
    ext_r: float = field(default_factory=lambda: _f("EXTEND_R", 1.0))
    max_ext: int = field(default_factory=lambda: _i("MAX_EXTENSIONS", 3))
    manage_sec: int = field(default_factory=lambda: _i("MANAGE_SEC", 15))

    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO").upper())


cfg = Config()

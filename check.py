"""Prueba rápida: python -m bot.check"""
from .config import cfg
from .exchange import BingX
from .telegram_notify import Telegram

ex = BingX(cfg)
syms = ex.top_symbols()
print(f"Top {len(syms)} símbolos por volumen:", syms[:10])

if cfg.api_key and cfg.api_secret:
    eq, free = ex.balance()
    print(f"Balance USDT: total={eq} libre={free}")
    print("Posiciones abiertas:", list(ex.open_positions().keys()))
else:
    print("(sin API keys: solo datos públicos)")

Telegram(cfg.tg_token, cfg.tg_chat).send("✅ Prueba de conexión OK")

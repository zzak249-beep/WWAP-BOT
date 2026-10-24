# Bot BingX (estructura plana)

Todos los archivos van en la RAÍZ del repositorio de GitHub (sin carpetas):

main.py, config.py, exchange.py, strategy.py, telegram_notify.py, check.py,
requirements.txt, Procfile, railway.json, .python-version, .gitignore, .env.example

Railway: comando de inicio `python main.py` (ya definido en railway.json y Procfile).
Variables: ver .env.example. Empieza con TRADE_MODE=signals.
Prueba local: pip install -r requirements.txt && python check.py

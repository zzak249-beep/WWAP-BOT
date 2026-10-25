# Bot BingX v4 (estructura plana: todos los archivos en la raíz del repo)

main.py, config.py, exchange.py, strategy.py, telegram_notify.py, backtest.py, check.py,
requirements.txt, Procfile, railway.json, .python-version, .gitignore, .env.example

- Railway: comando de inicio `python main.py`. Variables: ver .env.example. Empieza con TRADE_MODE=signals.
- Backtest (en tu ordenador, no en Railway): `pip install -r requirements.txt && python backtest.py --days 45 --symbols 25`
  Compara la configuración actual contra variantes, con comisiones, 70/30 muestra/fuera de muestra y resultados por hora UTC.
- Prueba de conexión: `python check.py`

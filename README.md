# Bot BingX: Scanner + Señales Telegram + Ejecución automática

Estrategia: **VWAP + EMA200 (5m) + EMA200 (15m)**. El bot escanea las monedas de futuros perpetuos USDT de BingX con más volumen, cada vez que cierra una vela de 5 min, y:

1. Detecta las que están por encima o por debajo de las 3 líneas y las ordena por *score* (volumen relativo + fuerza de tendencia).
2. Si hay pullback a una línea → envía la señal por Telegram.
3. Si `TRADE_MODE=live` → abre la operación en BingX con SL y TP, tamaño según tu riesgo.

> ⚠️ **Empieza SIEMPRE en `TRADE_MODE=signals`** durante al menos 1-2 semanas y compara las señales. Ninguna estrategia garantiza ganancias. Puedes perder todo el capital, sobre todo con apalancamiento. Esto no es asesoramiento financiero.

## 1. Telegram
1. Habla con **@BotFather** → `/newbot` → copia el **token**.
2. Escribe cualquier mensaje a tu bot nuevo.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia tu `chat.id`.

## 2. API de BingX (solo para modo live)
- BingX → Gestión de API → crear clave con permisos **Lectura + Trading de futuros perpetuos**. **NO actives retiros.**
- Revisa en la app si tu modo de posición es **Hedge** (dos direcciones) o **One-way** y ajusta `POSITION_MODE` (`hedge` / `oneway`).
- No uses lista blanca de IP salvo que tengas IP fija en Railway (las IP salientes normales cambian).

## 3. Subir a GitHub
```bash
cd bingx-bot
git init
git add .
git commit -m "bot bingx"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/bingx-bot.git
git push -u origin main
```
El archivo `.env` está en `.gitignore`: **nunca subas tus claves**.

## 4. Desplegar en Railway
1. railway.app → **New Project → Deploy from GitHub repo** → elige el repo.
2. Pestaña **Variables**: copia las de `.env.example` (mínimo `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TRADE_MODE`; y las de BingX si usas live).
3. Es un servicio *worker* (sin puerto web). Arranca con `python -m bot.main` (ya definido en `railway.json`).
4. Si BingX bloquea la conexión por región, cambia la región del servicio en Settings (por ejemplo a Europa).
5. Mira los **Logs**: deberías recibir en Telegram "🤖 Bot iniciado".

Prueba local opcional: `pip install -r requirements.txt && cp .env.example .env && python -m bot.check`

## 5. Comandos de Telegram
`/status` · `/top` (mejores monedas del último escaneo) · `/pause` · `/resume`

## 6. Variables principales
| Variable | Defecto | Qué hace |
|---|---|---|
| TRADE_MODE | signals | `signals` solo avisa, `live` opera |
| TIMEFRAME / HTF | 5m / 15m | Gráfico y filtro de tendencia |
| TOP_N | 40 | Nº de monedas por volumen a escanear |
| MIN_QUOTE_VOLUME | 5000000 | Volumen 24h mínimo en USDT |
| ATR_MULT / MIN_SL_PCT / MAX_SL_PCT | 1.5 / 0.30 / 2.0 | Stop por ATR, con mínimo y máximo en % |
| RR | 2.0 | Take profit = RR × riesgo |
| RISK_PCT | 1.0 | % de la cuenta arriesgado por operación |
| LEVERAGE | 5 | Apalancamiento (isolated) |
| MAX_OPEN / MAX_TRADES_DAY | 3 / 6 | Límites de posiciones y operaciones diarias |
| DAILY_LOSS_PCT | 3.0 | Si la cuenta cae este % en el día, el bot se detiene hasta el día siguiente (UTC) |
| USE_SESSION, SESSION_START/END | true, 10:00-17:00 | Horario ET en que se permiten entradas |

## 7. Seguridad integrada
- Si no se puede colocar el SL/TP tras abrir, **cierra la posición** automáticamente.
- No persigue el precio si se alejó más de medio stop de la señal.
- Máximo de posiciones, operaciones por día y pérdida diaria.
- Tamaño calculado por riesgo, con tope de apalancamiento.

## 8. Limitaciones a tener en cuenta
- Entra con orden de mercado (comisión taker, 0.05% por lado en tarifa base; revisa la tuya en BingX).
- El contador diario y el equity inicial del día viven en memoria: si Railway reinicia el servicio, se reinician.
- Los símbolos y los parámetros de la API los gestiona `ccxt`; si BingX cambia algo, actualiza la librería (`pip install -U ccxt`).
- Haz primero una prueba con cantidad mínima y revisa que SL y TP aparecen en la posición dentro de BingX.

import logging
import threading
import time

import requests

log = logging.getLogger("telegram")


class Telegram:
    def __init__(self, token, chat_id):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.enabled = bool(self.token and self.chat_id)
        self.base = f"https://api.telegram.org/bot{self.token}"
        if not self.enabled:
            log.error("Telegram DESACTIVADO: falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
            return
        try:
            r = requests.get(f"{self.base}/getMe", timeout=15).json()
            if r.get("ok"):
                log.info("Telegram OK: bot @%s | chat_id=%s", r["result"].get("username"), self.chat_id)
            else:
                log.error("Telegram TOKEN INVÁLIDO: %s", r.get("description"))
        except Exception as e:
            log.error("No se pudo contactar con Telegram: %s", e)

    def _post(self, text, parse_mode):
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return requests.post(f"{self.base}/sendMessage", json=payload, timeout=15)

    def send(self, text):
        log.info("TG | %s", text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            r = self._post(text, "HTML")
            if r.status_code != 200:
                log.error("Telegram rechazó el mensaje (%s): %s", r.status_code, r.text[:300])
                if "parse" in r.text.lower():
                    r = self._post(text, None)  # reintento sin formato
                    if r.status_code != 200:
                        log.error("Reintento sin formato también falló: %s", r.text[:300])
        except Exception as e:
            log.error("no se pudo enviar a Telegram: %s", e)

    def listen(self, handler):
        if not self.enabled:
            return
        threading.Thread(target=self._loop, args=(handler,), daemon=True).start()

    def _loop(self, handler):
        offset = None
        while True:
            try:
                r = requests.get(
                    f"{self.base}/getUpdates",
                    params={"timeout": 30, "offset": offset},
                    timeout=40,
                ).json()
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    if str((m.get("chat") or {}).get("id")) != self.chat_id:
                        continue
                    txt = (m.get("text") or "").strip()
                    if txt.startswith("/"):
                        cmd = txt.split()[0].split("@")[0].lower()
                        reply = handler(cmd)
                        if reply:
                            self.send(reply)
            except Exception as e:
                log.warning("polling Telegram: %s", e)
                time.sleep(5)

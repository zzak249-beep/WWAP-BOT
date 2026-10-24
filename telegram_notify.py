import logging
import threading
import time

import requests

log = logging.getLogger("telegram")


class Telegram:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = str(chat_id)
        self.enabled = bool(token and chat_id)
        self.base = f"https://api.telegram.org/bot{token}"

    def send(self, text):
        log.info("TG | %s", text.replace("\n", " | "))
        if not self.enabled:
            return
        try:
            requests.post(
                f"{self.base}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
        except Exception as e:
            log.warning("no se pudo enviar a Telegram: %s", e)

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

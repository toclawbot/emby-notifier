import os
import smtplib
import requests
from email.mime.text import MIMEText
from tenacity import retry, stop_after_attempt, wait_exponential

class BaseNotifier:
    def send(self, message: str, image_url: str = None):
        raise NotImplementedError

class TelegramNotifier(BaseNotifier):
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def send(self, message: str, image_url: str = None):
        if not self.token or not self.chat_id: return
        
        if image_url:
            url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
            payload = {"chat_id": self.chat_id, "caption": message, "photo": image_url, "parse_mode": "HTML"}
        else:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()

class EmailNotifier(BaseNotifier):
    def __init__(self):
        self.smtp_server = os.getenv("EMAIL_SMTP_SERVER")
        self.sender = os.getenv("EMAIL_SENDER")
        self.password = os.getenv("EMAIL_PASSWORD")
        self.receiver = os.getenv("EMAIL_RECEIVER")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def send(self, message: str, image_url: str = None):
        if not self.smtp_server: return
        msg = MIMEText(message)
        msg['Subject'] = "Emby 通知"
        msg['From'] = self.sender
        msg['To'] = self.receiver
        with smtplib.SMTP_SSL(self.smtp_server, 465) as server:
            server.login(self.sender, self.password)
            server.send_message(msg)

def get_notifiers():
    notifiers = []
    if os.getenv("NOTIFY_TELEGRAM_ENABLED") == "true": notifiers.append(TelegramNotifier())
    if os.getenv("NOTIFY_EMAIL_ENABLED") == "true": notifiers.append(EmailNotifier())
    return notifiers

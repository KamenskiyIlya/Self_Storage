import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv


def send_yandex_email(recipient_email: str, subject: str, body: str) -> bool:
    load_dotenv()

    sender_email = os.getenv("YANDEX_LOGIN")
    app_password = os.getenv("YANDEX_TOKEN")

    if not sender_email or not app_password or not recipient_email:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    try:
        server = smtplib.SMTP_SSL("smtp.yandex.ru", 465, timeout=15)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False

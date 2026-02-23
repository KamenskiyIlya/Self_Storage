import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv


def send_yandex_email(recipient_email: str, subject: str, body: str) -> bool:
    ok, _ = send_yandex_email_detailed(recipient_email, subject, body)
    return ok


def send_yandex_email_detailed(recipient_email: str, subject: str, body: str):
    load_dotenv()

    sender_email = os.getenv("YANDEX_LOGIN")
    app_password = os.getenv("YANDEX_TOKEN")

    if not recipient_email:
        return False, "recipient_email пустой"
    if not sender_email:
        return False, "YANDEX_LOGIN не задан в .env"
    if not app_password:
        return False, "YANDEX_TOKEN не задан в .env"

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    try:
        server = smtplib.SMTP_SSL("smtp.yandex.ru", 465, timeout=15)
        server.login(sender_email, app_password)
        server.sendmail(sender_email, [recipient_email], msg.as_string())
        server.quit()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

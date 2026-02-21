from datetime import datetime, date

from db_utils import (
    db_reader,
    save_database,
    find_user,
    get_cell_by_number,
    get_overdue_daily_rate,
)
from mailer import send_yandex_email


def process_rent_reminders(bot, admin_chat_id=None):
    database = db_reader()
    if not isinstance(database, dict):
        return {"sent": 0, "email_sent": 0, "errors": 1}

    today = date.today()
    sent_count = 0
    email_sent_count = 0
    errors = 0
    changed = False

    reminder_plan = [
        (30, "1m", "До окончания аренды остался 1 месяц."),
        (14, "2w", "До окончания аренды осталось 2 недели."),
        (7, "1w", "До окончания аренды осталась 1 неделя."),
        (3, "3d", "До окончания аренды осталось 3 дня."),
    ]

    for rent in database.get("rental_agreements", []):
        if rent.get("status") != "Активна":
            continue

        end_date = _parse_iso_date(rent.get("end_date"))
        qr_code = rent.get("qr_code")
        user_id = rent.get("user_telegram_id")
        if not end_date or not qr_code or not user_id:
            continue

        user = find_user(database, user_id) or {}
        full_name = user.get("full_name") or "Клиент"
        user_email = user.get("email")
        days_left = (end_date - today).days
        message = None
        reminder_type = None
        email_subject = None

        for offset, code, text in reminder_plan:
            if days_left == offset and not _reminder_sent_today(database, qr_code, code, today):
                message = (
                    f"{text}\n"
                    f"Договор: {qr_code}\n"
                    f"Ячейка: {rent.get('cell_number')}\n"
                    f"Дата окончания: {rent.get('end_date')}"
                )
                reminder_type = code
                email_subject = f"SelfStorage: напоминание по договору {qr_code}"
                break

        if message is None:
            days_overdue = (today - end_date).days
            overdue_points = {1, 30, 60, 90, 120, 150}
            if days_overdue in overdue_points:
                overdue_type = "overdue_start" if days_overdue == 1 else f"overdue_m{days_overdue // 30}"
                if not _reminder_sent_today(database, qr_code, overdue_type, today):
                    cell = get_cell_by_number(database, rent.get("cell_number"))
                    cell_size_code = cell.get("cell_size_code") if cell else None
                    daily_rate = get_overdue_daily_rate(database, cell_size_code, today) if cell_size_code else None
                    tariff_text = (
                        f"Повышенный тариф: {daily_rate} руб./день."
                        if daily_rate is not None
                        else "Повышенный тариф применяется согласно вашему договору."
                    )
                    message = (
                        "Срок аренды истёк.\n"
                        f"Договор: {qr_code}\n"
                        f"Дата окончания: {rent.get('end_date')}\n"
                        "Вещи хранятся до 6 месяцев после окончания срока аренды.\n"
                        f"{tariff_text}\n"
                        "Пожалуйста, заберите вещи как можно скорее."
                    )
                    reminder_type = overdue_type
                    email_subject = f"SelfStorage: просрочка по договору {qr_code}"

            if message is None and days_overdue == 180:
                reminder_type = "overdue_6m"
                if not _reminder_sent_today(database, qr_code, reminder_type, today):
                    message = (
                        "Срок просроченного хранения достиг 6 месяцев.\n"
                        f"Договор: {qr_code}\n"
                        "Свяжитесь с нами срочно, чтобы согласовать дальнейшие действия."
                    )
                    email_subject = f"SelfStorage: 6 месяцев просрочки по договору {qr_code}"

        if message is None or reminder_type is None:
            continue

        try:
            bot.send_message(user_id, f"{full_name},\n\n{message}")
            sent_count += 1
        except Exception:
            errors += 1
            if admin_chat_id:
                try:
                    bot.send_message(admin_chat_id, f"Не удалось отправить Telegram-напоминание пользователю {user_id} ({qr_code}).")
                except Exception:
                    pass

        if user_email:
            if send_yandex_email(user_email, email_subject, f"{full_name},\n\n{message}"):
                email_sent_count += 1
            else:
                errors += 1
                if admin_chat_id:
                    try:
                        bot.send_message(admin_chat_id, f"Не удалось отправить email на {user_email} ({qr_code}).")
                    except Exception:
                        pass

        _add_reminder_record(database, qr_code, reminder_type)
        changed = True

    if changed:
        save_database(database)

    return {"sent": sent_count, "email_sent": email_sent_count, "errors": errors}


def _parse_iso_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _reminder_sent_today(database, qr_code, reminder_type, on_date):
    for reminder in database.get("reminders", []):
        if reminder.get("rental_agreement_qr_code") != qr_code:
            continue
        if reminder.get("reminder_type") != reminder_type:
            continue
        sent_at = reminder.get("sent_at", "")
        if sent_at[:10] == on_date.isoformat():
            return True
    return False


def _add_reminder_record(database, qr_code, reminder_type):
    database.setdefault("reminders", []).append(
        {
            "rental_agreement_qr_code": qr_code,
            "sent_at": f"{datetime.utcnow().isoformat(timespec='seconds')}Z",
            "reminder_type": reminder_type,
        }
    )

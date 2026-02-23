import json
from pathlib import Path


DATABASE_FILE = Path("database.json")


def db_reader():
    """Достает информацию из БД."""
    if not DATABASE_FILE.exists():
        return []

    try:
        with DATABASE_FILE.open("r", encoding="utf-8") as file:
            database = json.load(file)
    except json.JSONDecodeError:
        return []

    return database if isinstance(database, dict) else []


def save_database(database):
    with DATABASE_FILE.open("w", encoding="utf-8") as file:
        json.dump(database, file, ensure_ascii=False, indent=2)


def append_order(order):
    database = db_reader()
    if not isinstance(database, dict):
        database = {}

    delivery_requests = database.setdefault("delivery_requests", [])
    order_id = len(delivery_requests) + 1
    if isinstance(order, dict):
        order.setdefault("order_id", order_id)
    delivery_requests.append(order)
    save_database(database)
    return order_id


def upsert_user_profile(
    telegram_id,
    full_name=None,
    username=None,
    phone=None,
    address=None,
    email=None,
    acquisition_source=None,
):
    database = db_reader()
    if not isinstance(database, dict):
        database = {}

    users = database.setdefault("users", [])
    user = None
    for item in users:
        if item.get("telegram_id") == telegram_id:
            user = item
            break

    if user is None:
        user = {"telegram_id": telegram_id}
        users.append(user)

    if full_name:
        user["full_name"] = full_name
    if username:
        user["username"] = username
    if phone:
        user["phone"] = phone
    if address:
        user["address"] = address
    if email:
        user["email"] = email
    if acquisition_source:
        user["acquisition_source"] = acquisition_source

    save_database(database)


def find_user(database, telegram_id):
    for user in database.get("users", []):
        if user.get("telegram_id") == telegram_id:
            return user
    return None


def get_cell_by_number(database, cell_number):
    for cell in database.get("cells", []):
        if cell.get("number") == cell_number:
            return cell
    return None


def get_overdue_daily_rate(database, cell_size_code, on_date):
    for tariff in database.get("overdue_tariffs", []):
        if tariff.get("cell_size_code") != cell_size_code:
            continue

        valid_from = _parse_iso_date(tariff.get("valid_from"))
        valid_until = _parse_iso_date(tariff.get("valid_until"))
        if valid_from and valid_until and valid_from <= on_date <= valid_until:
            return tariff.get("daily_rate")
    return None


def _parse_iso_date(value):
    from datetime import datetime

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

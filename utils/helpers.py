from datetime import date, datetime, timezone


def utc_now_iso(timespec: str = "seconds") -> str:
    return datetime.now(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def normalize_full_name(telegram_user) -> str:
    full_name = f"{telegram_user.first_name or ''} {telegram_user.last_name or ''}".strip()
    return full_name or "Клиент"


def parse_start_source(message_text: str):
    parts = (message_text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    source = parts[1].strip().lower()
    return source or None


def promo_result(raw_code: str | None, promo_catalog: dict):
    if not raw_code:
        return {"status": "none"}
    code = raw_code.strip().lower()
    if not code:
        return {"status": "none"}
    promo = promo_catalog.get(code)
    if promo is None:
        return {"status": "unknown", "code": code}
    today = date.today()
    if not (promo["valid_from"] <= today <= promo["valid_until"]):
        return {
            "status": "inactive",
            "code": code,
            "valid_from": promo["valid_from"].isoformat(),
            "valid_until": promo["valid_until"].isoformat(),
        }
    return {
        "status": "active",
        "code": code,
        "discount_percent": promo["discount_percent"],
    }


def order_id_from_record(order: dict, fallback_index: int) -> int:
    order_id = order.get("order_id")
    if isinstance(order_id, int):
        return order_id
    return fallback_index


def find_monthly_price(database: dict, cell_size_code: str):
    for size in database.get("cell_sizes", []):
        if size.get("code") == cell_size_code:
            try:
                return float(size.get("monthly_price"))
            except (TypeError, ValueError):
                return None
    return None


def is_valid_email(value: str) -> bool:
    email = (value or "").strip()
    if "@" not in email or "." not in email:
        return False
    if email.startswith("@") or email.endswith("@"):
        return False
    return len(email) >= 6


def parse_items_list(raw_text: str):
    prepared = (raw_text or "").replace(";", ",").replace("\n", ",")
    items = [part.strip() for part in prepared.split(",")]
    return [item for item in items if item]


def get_warehouse_address(database: dict, cell):
    warehouse_name = cell.get("warehouse_name") if cell else "Склад"
    warehouse_address = "Адрес уточнит оператор"
    for warehouse in database.get("warehouses", []):
        if warehouse.get("name") == warehouse_name:
            warehouse_address = warehouse.get("address")
            break
    return warehouse_name, warehouse_address


def build_storage_confirm_text(session_data: dict) -> str:
    measure_text = (
        "Курьер замерит габариты на месте."
        if session_data.get("request_type") == "pickup"
        else "Точный объём замерим при приёме вещей на складе."
    )
    route_text = (
        f"Склад: {session_data['warehouse_name']}\n"
        if session_data.get("request_type") == "self_dropoff"
        else ""
    )
    discount_percent = session_data.get("promo_discount_percent", 0)
    base_price = session_data.get("expected_monthly_price_base", session_data.get("expected_monthly_price", 0))
    final_price = session_data.get("expected_monthly_price", 0)
    promo_text = (
        f"Промокод: {session_data.get('promo_code')} (-{discount_percent}%)\n"
        f"Цена без скидки: {base_price} руб./мес.\n"
        f"Цена со скидкой: {final_price} руб./мес.\n"
        if session_data.get("promo_code")
        else f"Промокод: не применён\nОжидаемая стоимость: {final_price} руб./мес.\n"
    )
    seasonal_text = "Сезонные вещи: нет"
    if session_data.get("has_seasonal_items"):
        item_list = session_data.get("seasonal_item_list", [])
        seasonal_text = f"Сезонные вещи: {', '.join(item_list)}" if item_list else "Сезонные вещи: указаны"

    return (
        "Проверьте заявку:\n"
        f"{route_text}"
        f"Адрес: {session_data['address']}\n"
        f"Телефон: {session_data['phone']}\n"
        f"Email: {session_data['email']}\n"
        f"Объём: {session_data['volume']} - {session_data['volume_description']}\n"
        f"Срок хранения: {session_data.get('rent_days', 30)} дн.\n"
        f"{seasonal_text}\n"
        f"{promo_text}\n"
        f"Ожидаемая стоимость за весь срок: {session_data.get('expected_total_price')} руб.\n"
        f"{measure_text}\n\n"
        "Нажмите ДА для подтверждения или НЕТ для отмены"
    )


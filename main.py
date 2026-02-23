import os
import threading
import time
from datetime import datetime, date, timedelta

import telebot
from dotenv import load_dotenv
from telebot.types import InputFile

from utils.keyboards import main_menu, admin_menu, already_stored, delivery_decision, pickup_decision
from utils.keyboards import approval_processing_data, return_main_menu as return_main_menu_keyboard, choose_volume, confirm_request, promo_decision
from utils.db_utils import db_reader, append_order, get_cell_by_number, save_database, sync_cells_occupancy, upsert_user_profile
from utils.helpers import (
    build_storage_confirm_text,
    find_monthly_price,
    get_warehouse_address,
    is_valid_email,
    normalize_full_name,
    order_id_from_record,
    parse_items_list,
    parse_start_source,
    promo_result,
    utc_now_iso,
)
from utils.mailer import send_yandex_email_detailed
from utils.reminders import process_rent_reminders
from utils.ui_helpers import warehouse_keyboard, options_keyboard
from utils.get_qr import build_pickup_qr_file


def main() -> None:
    load_dotenv()
    token = os.getenv('TG_TOKEN')
    chat_id = os.getenv('TG_CHAT_ID')
    admin_id = os.getenv('ADMIN_TG_ID')

    if not token:
        raise RuntimeError('TG_TOKEN не задан в переменных окружения')

    bot = telebot.TeleBot(token)
    sessions: dict[int, dict] = {}
    reminder_lock = threading.Lock()
    last_reminder_date = {"value": None}
    existing_actions = {
        "Забрать частично вещи": {
            "code": "partial_takeout",
            "title": "Частичный забор вещей",
        },
        "Забрать полностью вещи": {
            "code": "full_takeout",
            "title": "Полный забор вещей",
        },
        "Положить обратно в арендованную ячейку": {
            "code": "return_to_cell",
            "title": "Возврат вещей в ячейку",
        },
    }
    legal_rack_monthly_price = 899.0
    promo_catalog = {
        "storage2022": {
            "discount_percent": 20,
            "valid_from": date(2026, 3, 1),
            "valid_until": date(2026, 3, 31),
        },
        "storage15": {
            "discount_percent": 15,
            "valid_from": date(2025, 11, 1),
            "valid_until": date(2026, 4, 30),
        },
    }

    def reset_session(user_id: int):
        sessions.pop(user_id, None)

    def get_session(user_id: int):
        return sessions.get(user_id)

    def read_db_synced():
        database = db_reader()
        if sync_cells_occupancy(database):
            save_database(database)
        return database

    def send_storage_confirm(chat_id_value: int, session_data: dict):
        bot.send_message(
            chat_id_value,
            build_storage_confirm_text(session_data),
            reply_markup=confirm_request()
        )

    def get_main_menu(user_id: int):
        if str(user_id) == str(admin_id):
            return admin_menu()
        return main_menu()

    def send_pickup_qr_to_user(chat_id_value: int, rent: dict, warehouse_name: str, warehouse_address: str, action_code: str):
        qr_code_value = rent.get("qr_code")
        if not qr_code_value:
            bot.send_message(
                chat_id_value,
                "QR-код по договору не найден. Свяжитесь с оператором, пожалуйста.",
                reply_markup=get_main_menu(chat_id_value),
            )
            return

        expires_at = utc_now_iso(timespec="seconds")
        qr_buffer = build_pickup_qr_file(
            qr_code_value=qr_code_value,
            cell_number=rent.get("cell_number"),
            expires_at=expires_at,
        )

        bot.send_photo(chat_id_value, qr_buffer)
        lines = [
            "Ваш QR-код для выдачи вещей готов.",
            f"Договор: {qr_code_value}",
            f"Ячейка: {rent.get('cell_number')}",
            f"Склад: {warehouse_name}",
            f"Адрес выдачи: {warehouse_address}",
            "Если удобнее, можем привезти вещи на дом за доплату: выберите вариант с доставкой.",
        ]
        if action_code == "partial_takeout":
            lines.append("После частичного забора вещи можно вернуть обратно до окончания текущей аренды.")

        bot.send_message(
            chat_id_value,
            "\n".join(lines),
            reply_markup=get_main_menu(chat_id_value),
        )

    def run_daily_reminders():
        with reminder_lock:
            today_str = date.today().isoformat()
            if last_reminder_date["value"] == today_str:
                return None
            result = process_rent_reminders(bot, chat_id)
            last_reminder_date["value"] = today_str
            return result

    def reminders_worker():
        while True:
            try:
                run_daily_reminders()
            except Exception:
                pass
            time.sleep(3600)

    @bot.message_handler(commands=['start'])
    def start(message):
        source = parse_start_source(message.text)
        upsert_user_profile(
            telegram_id=message.from_user.id,
            full_name=normalize_full_name(message.from_user),
            username=message.from_user.username,
            acquisition_source=source,
        )

        text = (
            'Привет! Я помощник компании Self Storage, которая занимается хранением вещей. 📦🚲📚👕\n'

            '\nНаша компания помогает людям освободить пространство для комфортной жизни.\n'
            '- Сезон закончился, а вещи занимают слишком много места?\n'
            '- Жалко выкидывать или продавать любимые вещи, но они мешают?\n'
            '- Вещи сейчас не нужны, уменьшают Ваше свободное пространство, но в будущем они потребуются?\n'
            '\nМы можем подержать эти вещи у себя, Вам остается наслаждаться свободным пространством '
            'Вашего дома, балкона, шкафа, гараже или любого другого места где у Вас обычно хранятся '
            'такие вещи. А Вы в любой момент можете их забрать.\n'

            '\nВыберите, что Вас интересует:'
        )
        reset_session(message.from_user.id)
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=get_main_menu(message.from_user.id),
        )


    @bot.message_handler(func=lambda m: m.text == "Вернуться в главное меню")
    def handle_return_main_menu(message):
        start(message)


    @bot.message_handler(func=lambda m: m.text == 'Согласен ✅')
    def pickup_start(message):
        session = get_session(message.from_user.id)
        if not session or session.get('state') != 'WAIT_CONSENT':
            bot.send_message(
                message.chat.id,
                'Сначала выберите тип оформления в меню "Хочу хранить вещи".',
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        request_type = session['data'].get('request_type')
        if request_type == 'legal_docs_storage':
            session['state'] = 'WAIT_LEGAL_RACKS'
            bot.send_message(
                message.chat.id,
                'Введите количество стеллажей (целое число, например 3):',
                reply_markup=return_main_menu_keyboard(),
            )
            return

        if request_type == 'pickup':
            session['state'] = 'WAIT_ADDRESS'
            bot.send_message(
                message.chat.id,
                'Введите адрес, откуда забрать вещи (город, улица, дом):',
                reply_markup=return_main_menu_keyboard()
            )
            return

        session['state'] = 'WAIT_WAREHOUSE'
        warehouses = session['data'].get('available_warehouses', [])
        bot.send_message(
            message.chat.id,
            'Выберите склад, куда планируете привезти вещи самостоятельно:',
            reply_markup=warehouse_keyboard(warehouses),
        )

    @bot.message_handler(func=lambda m: m.text == 'Не согласен ❌')
    def decline_personal_data_processing(message):
        reset_session(message.from_user.id)
        bot.send_message(
            message.chat.id,
            'Без согласия на обработку персональных данных оформить заявку нельзя. Возвращаю в главное меню.',
            reply_markup=get_main_menu(message.from_user.id),
        )


    @bot.message_handler(func=lambda m: m.text == 'Хочу хранить вещи')
    def want_storage(message):
        database = read_db_synced()
        warehouses = database["warehouses"]
        available_warehouses = []

        for warehouse in warehouses:
            for cell in database["cells"]:
                if warehouse['name'] == cell["warehouse_name"] and cell["is_occupied"] is False:
                    available_warehouses.append(warehouse)

        text = 'На данный момент свободные ячейки есть на следующих складах:\n\n'
        for warehouse in available_warehouses:
            text = text + f'{warehouse["name"]}\n{warehouse["address"]}\n\n'

        text = text + 'Также у нас есть услуга бесплатной доставки Ваших вещей на склад. Интересует ли Вас данная услуга?'

        bot.send_message(
            message.chat.id,
            text,
            reply_markup=pickup_decision(),
        )

    @bot.message_handler(func=lambda m: m.text == 'Услуги для юрлиц')
    def legal_entities_services(message):
        sessions[message.from_user.id] = {
            'state': 'WAIT_CONSENT',
            'data': {
                'request_type': 'legal_docs_storage',
            }
        }
        text = (
            'Услуга для юрлиц: хранение документов на стеллажах.\n'
            f'Стоимость: {legal_rack_monthly_price:.0f} руб./месяц за 1 стеллаж.\n\n'
            'Для оформления заявки нужно согласие на обработку персональных данных.'
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=approval_processing_data(),
        )
        bot.send_document(
            message.chat.id,
            InputFile('Soglasie.pdf'),
            reply_markup=approval_processing_data(),
        )

    want_storage_message = ['Необходимо забрать', 'Отвезу сам']
    @bot.message_handler(func=lambda m: m.text in want_storage_message)
    def already_stored_menu(message):
        database = read_db_synced()
        available_warehouses = []
        for warehouse in database.get("warehouses", []):
            has_free_cells = any(
                warehouse['name'] == cell["warehouse_name"] and cell["is_occupied"] is False
                for cell in database.get("cells", [])
            )
            if has_free_cells:
                available_warehouses.append(warehouse)

        request_type = 'pickup' if message.text == 'Необходимо забрать' else 'self_dropoff'
        sessions[message.from_user.id] = {
            'state': 'WAIT_CONSENT',
            'data': {
                'request_type': request_type,
                'available_warehouses': available_warehouses,
            }
        }

        text = (
            'Для дальнейшего взаимодействия, просьба ознакомиться с правилами обработки '
            'персональных данных и дать свое согласие на их обработку.\n\n'
            'Если согласны, тогда нажмите кнопку "согласен". В ином случае '
            'мы не сможем оформить для Вас доставку и хранение вещей.'
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=approval_processing_data(),
        )
        bot.send_document(
            message.chat.id,
            InputFile('Soglasie.pdf'),
            reply_markup=approval_processing_data(),
        )
        


    @bot.message_handler(func=lambda m: m.text == 'Мои заказы')
    def look_orders(message):
        '''Бот выводит все аренды клиента'''
        user_id = message.from_user.id
        database = db_reader()
        user_rent = []
        for rent in database["rental_agreements"]:
            if rent["user_telegram_id"] == user_id and rent.get("status") != "Закончена":
                user_rent.append(rent)

        if not user_rent:
            bot.send_message(
                message.chat.id,
                'На данный момент у Вас нет заказов.',
                reply_markup=get_main_menu(user_id),
            )
        else:
            bot.send_message(
                message.chat.id,
                'Ваши арендованные ячейки: \n\n',
            )
            for rent in user_rent:
                matched_cell = next(
                    (cell for cell in database.get('cells', []) if cell.get("number") == rent.get("cell_number")),
                    None
                )
                warehouse = matched_cell.get("warehouse_name") if matched_cell else "Неизвестный склад"
                cell_size_code = matched_cell.get("cell_size_code") if matched_cell else "-"

                matched_size = next(
                    (size for size in database.get("cell_sizes", []) if size.get("code") == cell_size_code),
                    None
                )
                cell_description = matched_size.get("description") if matched_size else "Описание недоступно"

                item_record = next(
                    (
                        item for item in database.get("items", [])
                        if item.get("rental_agreement_qr_code") == rent.get("qr_code")
                        and item.get("removed_at") is None
                    ),
                    None
                )
                seasonal_block = ""
                if item_record and item_record.get("has_seasonal_items"):
                    raw_item_list = item_record.get("item_list")
                    if isinstance(raw_item_list, list) and raw_item_list:
                        item_list = ", ".join(str(item_name) for item_name in raw_item_list)
                        seasonal_block = f"\nСписок сезонных вещей: {item_list}"
                    else:
                        seasonal_block = "\nСписок сезонных вещей: не заполнен."

                storage_details = (
                    f"\nАрендуемый объём/площадь: {cell_size_code} - {cell_description}"
                    f"\nПериод аренды: {rent.get('start_date')} — {rent.get('end_date')}"
                    f"{seasonal_block}"
                )

                text = (
                    f'Склад: {warehouse}\n'
                    f'Номер ячейки: {rent["cell_number"]}\n'
                    f'Размер ячейки: {cell_size_code} - {cell_description}\n'
                    f'Начало аренды: {rent["start_date"]}\n'
                    f'Конец аренды: {rent["end_date"]}\n'
                    f'Общая цена: {rent["total_price"]}\n'
                    f'Статус аренды: {rent["status"]}'
                    f'{storage_details}'
                )
                if any(rent["status"] == "Активна" for rent in user_rent):
                    bot.send_message(
                        message.chat.id,
                        text,
                        reply_markup=already_stored(),
                    )
                elif all(rent["status"] == "Закончена" for rent in user_rent):
                    bot.send_message(
                        message.chat.id,
                        text,
                        reply_markup=get_main_menu(user_id),
                    )

    @bot.message_handler(func=lambda m: m.text == "Уже храню вещи")
    def action_with_stored(message):
        text = (
            'Если Вы уже храните вещи в наших кладовках, Вы можете:\n\n'
            '- Забрать частично свои вещи, позже Вы всегда сможете их вернуть.\n'
            '- Забрать полностью свои вещи, аренда в таком случае будет закончена.\n'
            '- Положить обратно в кладовку вещи, которые брали до этого или какие-то'
            'другие, но не забывайте о том, что размер кладовки ограничен.\n\n'

            'Уточните, что Вас интересует?'
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=already_stored(),
        )

    already_stored_message = [
        "Забрать частично вещи",
        "Забрать полностью вещи",
        "Положить обратно в арендованную ячейку"
    ]
    @bot.message_handler(func=lambda m: m.text in already_stored_message)
    def delivery_offer(message):
        database = db_reader()
        user_id = message.from_user.id
        selected_action = existing_actions[message.text]
        active_rents = [
            rent for rent in database.get("rental_agreements", [])
            if rent.get("user_telegram_id") == user_id and rent.get("status") == "Активна"
        ]

        if not active_rents:
            bot.send_message(
                message.chat.id,
                'У вас нет активных аренд для этого действия.',
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        rent_map = {}
        rent_options = []
        for rent in active_rents:
            label = f"{rent.get('cell_number')} | {rent.get('qr_code')} | до {rent.get('end_date')}"
            rent_map[label] = rent
            rent_options.append(label)
        sessions[user_id] = {
            "state": "WAIT_EXISTING_RENT_SELECT",
            "data": {
                "existing_action": selected_action,
                "rent_map": rent_map,
            }
        }
        bot.send_message(
            message.chat.id,
            'Выберите конкретный действующий договор, по которому хотите оформить действие:',
            reply_markup=options_keyboard(rent_options),
        )

    @bot.message_handler(func=lambda m: m.text == "Нужна доставка")
    def existing_need_delivery(message):
        session = get_session(message.from_user.id)
        if not session or session.get("state") != "WAIT_EXISTING_DELIVERY_DECISION":
            bot.send_message(
                message.chat.id,
                'Этот сценарий пока доступен только из раздела "Уже храню вещи".',
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        action_title = session["data"]["existing_action"]["title"].lower()
        session["state"] = "WAIT_EXISTING_ADDRESS"
        bot.send_message(
            message.chat.id,
            f'Введите адрес для услуги доставки ({action_title}):',
            reply_markup=options_keyboard(["Отмена"], include_main_menu=False),
        )

    @bot.message_handler(func=lambda m: m.text == "Заберу сам")
    def existing_self_service(message):
        session = get_session(message.from_user.id)
        if not session or session.get("state") != "WAIT_EXISTING_DELIVERY_DECISION":
            bot.send_message(
                message.chat.id,
                'Этот сценарий пока доступен только из раздела "Уже храню вещи".',
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        selected_rent = session["data"]["selected_rent"]
        action = session["data"]["existing_action"]
        database = db_reader()
        selected_cell = get_cell_by_number(database, selected_rent.get("cell_number"))
        warehouse_name, warehouse_address = get_warehouse_address(database, selected_cell)

        order = {
            "user_telegram_id": message.from_user.id,
            "item_rental_agreement_qr_code": selected_rent.get("qr_code"),
            "request_type": f"{action['code']}_self",
            "address": warehouse_address,
            "requested_at": utc_now_iso(),
            "status": "pending",
            "service_mode": "self_service",
        }
        order_id = append_order(order)
        reset_session(message.from_user.id)

        bot.send_message(
            message.chat.id,
            f"Заявка №{order_id} оформлена.\n"
            f"{action['title']} самостоятельно.\n"
            f"Склад: {warehouse_name}\n"
            f"Адрес: {warehouse_address}",
            reply_markup=get_main_menu(message.from_user.id),
        )

        if action["code"] in {"partial_takeout", "full_takeout"}:
            send_pickup_qr_to_user(
                chat_id_value=message.chat.id,
                rent=selected_rent,
                warehouse_name=warehouse_name,
                warehouse_address=warehouse_address,
                action_code=action["code"],
            )

        if chat_id:
            bot.send_message(
                chat_id,
                f"Новая заявка (самостоятельно) №{order_id}\n"
                f"Тип: {action['title']}\n"
                f"Клиент: {(message.from_user.first_name or '')} {(message.from_user.last_name or '')}\n"
                f"@{message.from_user.username or 'без username'}\n"
                f"Договор: {selected_rent.get('qr_code')}\n"
                f"Ячейка: {selected_rent.get('cell_number')}\n"
                f"Склад: {warehouse_name}\n"
                f"Адрес: {warehouse_address}",
            )


    @bot.message_handler(func=lambda m: m.text == 'Правила хранения')
    def storage_rules(message):
        text = (
            'Что можно хранить:\n'
            '- Одежда, обувь, текстиль\n'
            '- Спортивный инвентарь\n'
            '- Бытовая техника и коробки с личными вещами\n'
            '- Детские вещи, книги, мебель в разобранном виде\n\n'
            'Что нельзя хранить:\n'
            '- Легковоспламеняющиеся и взрывоопасные вещества\n'
            '- Оружие, боеприпасы, токсичные и химически опасные вещества\n'
            '- Скоропортящиеся продукты, растения, животных\n'
            '- Наркотические и иные запрещённые законом вещества\n\n'
            'Если выбираете бесплатный вывоз, курьер замерит габариты на месте.\n'
            'Если привозите вещи сами, мы замерим их при приёме на складе.'
        )
        bot.send_message(message.chat.id, text, reply_markup=get_main_menu(message.from_user.id))


    @bot.message_handler(commands=['run_reminders'])
    def run_reminders_command(message):
        if chat_id and str(message.chat.id) != str(chat_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        result = process_rent_reminders(bot, chat_id)
        bot.send_message(
            message.chat.id,
            f"Готово.\nTelegram: {result['sent']}\nEmail: {result['email_sent']}\nОшибки: {result['errors']}"
        )

    @bot.message_handler(func=lambda m: m.text == "Команды оператора")
    def operator_commands_help(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Раздел доступен только оператору.', reply_markup=get_main_menu(message.from_user.id))
            return

        text = (
            "Памятка оператора:\n\n"
            "1. Количество договоров аренды:\n"
            "/orders\n\n"
            "2. Список новых заявок (pending):\n"
            "/pending_orders\n\n"
            "3. Подтвердить заявку по ID:\n"
            "/approve_order 5\n\n"
            "4. Отменить заявку с причиной:\n"
            "/reject_order 5 нет свободных ячеек\n\n"
            "5. Подтверждённые активные заказы:\n"
            "/approved_orders\n\n"
            "6. Завершить заявку (и освободить ячейку для полного забора):\n"
            "/complete_order 12\n\n"
            "7. Просроченные аренды для обзвона:\n"
            "/overdue_calls\n\n"
            "8. Отчёт по источникам рекламы:\n"
            "/ads_report\n\n"
            "9. Запустить напоминания вручную:\n"
            "/run_reminders\n\n"
            "10. Отправить ручное напоминание клиенту (TG + email):\n"
            "/operator_reminder QR-IVAN-M101-2026 14"
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=get_main_menu(message.from_user.id),
        )

    @bot.message_handler(commands=['operator_reminder'])
    def operator_reminder(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        parts = (message.text or "").split()
        if len(parts) != 3:
            bot.send_message(
                message.chat.id,
                "Формат: /operator_reminder <qr_code> <days_left>\n"
                "Пример: /operator_reminder QR-IVAN-M101-2026 14",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        qr_code = parts[1].strip()
        try:
            days_left = int(parts[2])
        except ValueError:
            bot.send_message(
                message.chat.id,
                "days_left должен быть целым числом.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        database = db_reader()
        agreement = next(
            (rent for rent in database.get("rental_agreements", []) if rent.get("qr_code") == qr_code),
            None,
        )
        if agreement is None:
            bot.send_message(
                message.chat.id,
                f"Договор {qr_code} не найден.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        user_id = agreement.get("user_telegram_id")
        user = next(
            (item for item in database.get("users", []) if item.get("telegram_id") == user_id),
            {}
        )
        user_name = user.get("full_name") or "Клиент"
        user_email = user.get("email")
        days_text = (
            f"До окончания аренды осталось {days_left} дн."
            if days_left >= 0
            else f"Просрочка: {abs(days_left)} дн."
        )
        reminder_text = (
            f"{user_name},\n\n"
            "Ручное напоминание по договору SelfStorage:\n"
            f"Договор: {agreement.get('qr_code')}\n"
            f"Ячейка: {agreement.get('cell_number')}\n"
            f"Дата окончания аренды: {agreement.get('end_date')}\n"
            f"{days_text}"
        )

        tg_ok = False
        email_ok = False
        email_error = ""
        if user_id:
            try:
                bot.send_message(user_id, reminder_text)
                tg_ok = True
            except Exception:
                tg_ok = False
        if user_email:
            email_ok, email_error = send_yandex_email_detailed(
                user_email,
                f"SelfStorage: ручное напоминание по договору {agreement.get('qr_code')}",
                reminder_text,
            )

        bot.send_message(
            message.chat.id,
            "Ручное напоминание выполнено.\n"
            f"Telegram клиенту: {'успешно' if tg_ok else 'ошибка'}\n"
            f"Email клиенту: {'успешно' if email_ok else ('пропущен (email не указан)' if not user_email else f'ошибка ({email_error})')}",
            reply_markup=get_main_menu(message.from_user.id),
        )


    @bot.message_handler(commands=['orders'])
    def orders_count(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return
        else:
            database = db_reader()
            rent_orders = database["rental_agreements"]
            orders_count = len(rent_orders)
            bot.send_message(
                message.chat.id,
                f'Количество заказов на аренду на данный момент: {orders_count}',
                reply_markup=get_main_menu(message.from_user.id)
                )

    @bot.message_handler(commands=['pending_orders'])
    def pending_orders(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return
        send_pending_orders(message)

    @bot.message_handler(func=lambda m: m.text == "Новые заявки")
    def pending_orders_button(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Раздел доступен только оператору.', reply_markup=get_main_menu(message.from_user.id))
            return
        send_pending_orders(message)

    def send_pending_orders(message):
        database = db_reader()
        pending = []
        for idx, order in enumerate(database.get("delivery_requests", []), start=1):
            if order.get("status") != "pending":
                continue
            pending.append((order_id_from_record(order, idx), order))

        if not pending:
            bot.send_message(
                message.chat.id,
                "Новых заявок нет.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        lines = [
            "Новые заявки (status=pending):",
            "",
        ]
        for order_id, order in pending:
            lines.append(
                f"#{order_id} | type={order.get('request_type')} | user={order.get('user_telegram_id')} | "
                f"volume={order.get('volume_code') or '-'} | days={order.get('rent_days') or '-'} | "
                f"phone={order.get('phone') or '-'}"
            )
            lines.append(f"Адрес: {order.get('address') or '-'}")
            lines.append("")
        lines.append("Подтвердить: /approve_order <id>")
        lines.append("Отменить: /reject_order <id> <причина>")

        bot.send_message(
            message.chat.id,
            "\n".join(lines).strip(),
            reply_markup=get_main_menu(message.from_user.id),
        )

    @bot.message_handler(commands=['approved_orders'])
    def approved_orders(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return
        send_approved_orders(message)

    @bot.message_handler(func=lambda m: m.text == "Подтверждённые заказы")
    def approved_orders_button(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Раздел доступен только оператору.', reply_markup=get_main_menu(message.from_user.id))
            return
        send_approved_orders(message)

    def send_approved_orders(message):
        database = db_reader()
        active_agreements = [
            rent for rent in database.get("rental_agreements", [])
            if rent.get("status") == "Активна"
        ]
        if not active_agreements:
            bot.send_message(
                message.chat.id,
                "Подтверждённых активных заказов пока нет.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        users_by_id = {
            user.get("telegram_id"): user
            for user in database.get("users", [])
            if isinstance(user, dict)
        }
        lines = ["Подтверждённые активные заказы:", ""]
        for idx, rent in enumerate(active_agreements, start=1):
            user = users_by_id.get(rent.get("user_telegram_id"), {})
            lines.append(
                f"{idx}. Договор: {rent.get('qr_code')} | user={rent.get('user_telegram_id')} | "
                f"клиент: {user.get('full_name') or 'Неизвестный'}"
            )
            lines.append(
                f"Ячейка: {rent.get('cell_number')} | Срок: {rent.get('start_date')} - {rent.get('end_date')} | "
                f"Телефон: {user.get('phone') or '-'} | Email: {user.get('email') or '-'}"
            )
            lines.append("")
        lines.append("Для ручного напоминания: /operator_reminder <qr_code> <days_left>")
        bot.send_message(
            message.chat.id,
            "\n".join(lines).strip(),
            reply_markup=get_main_menu(message.from_user.id),
        )

    @bot.message_handler(commands=['approve_order'])
    def approve_order(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            bot.send_message(
                message.chat.id,
                "Формат: /approve_order <id>\nПример: /approve_order 5",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        target_order_id = int(parts[1])
        database = read_db_synced()
        delivery_requests = database.get("delivery_requests", [])
        order = None
        for idx, item in enumerate(delivery_requests, start=1):
            current_id = order_id_from_record(item, idx)
            if current_id == target_order_id:
                order = item
                break

        if order is None:
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} не найдена.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        if order.get("status") != "pending":
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} уже в статусе {order.get('status')}.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        request_type = order.get("request_type")
        if request_type not in {"pickup", "self_dropoff"}:
            order["status"] = "approved"
            order["approved_at"] = utc_now_iso()
            save_database(database)
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} подтверждена (без создания аренды для типа {request_type}).",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        requested_size = order.get("volume_code")
        chosen_cell = None
        for cell in database.get("cells", []):
            if cell.get("is_occupied"):
                continue
            if requested_size and cell.get("cell_size_code") != requested_size:
                continue
            chosen_cell = cell
            break
        if chosen_cell is None:
            for cell in database.get("cells", []):
                if not cell.get("is_occupied"):
                    chosen_cell = cell
                    break

        if chosen_cell is None:
            bot.send_message(
                message.chat.id,
                "Нет свободных ячеек для подтверждения заявки.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        cell_size_code = chosen_cell.get("cell_size_code")
        monthly_price = find_monthly_price(database, cell_size_code)
        order_price = order.get("expected_total_price")
        try:
            total_price = float(order_price) if order_price is not None else float(monthly_price or 0)
        except (TypeError, ValueError):
            total_price = float(monthly_price or 0)
        rent_days = order.get("rent_days")
        try:
            rent_days_value = int(rent_days) if rent_days is not None else 30
        except (TypeError, ValueError):
            rent_days_value = 30
        if rent_days_value <= 0:
            rent_days_value = 30

        today = date.today()
        qr_code = f"QR-{order.get('user_telegram_id')}-{chosen_cell.get('number')}-{today.strftime('%Y%m%d')}-{target_order_id}"
        agreement = {
            "user_telegram_id": order.get("user_telegram_id"),
            "cell_number": chosen_cell.get("number"),
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=rent_days_value)).isoformat(),
            "total_price": total_price,
            "status": "Активна",
            "qr_code": qr_code,
            "created_at": utc_now_iso(),
        }
        database.setdefault("rental_agreements", []).append(agreement)
        chosen_cell["is_occupied"] = True
        database.setdefault("items", []).append(
            {
                "rental_agreement_qr_code": qr_code,
                "total_volume_m3": None,
                "has_seasonal_items": bool(order.get("has_seasonal_items")),
                "item_list": order.get("seasonal_item_list", []),
                "added_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "removed_at": None,
            }
        )

        order["status"] = "approved"
        order["approved_at"] = utc_now_iso()
        order["approved_by"] = message.from_user.id
        order["rental_agreement_qr_code"] = qr_code
        order["order_id"] = target_order_id
        save_database(database)

        bot.send_message(
            message.chat.id,
            f"Заявка #{target_order_id} подтверждена.\n"
            f"Создан договор {qr_code}\n"
            f"Ячейка: {chosen_cell.get('number')} ({cell_size_code})",
            reply_markup=get_main_menu(message.from_user.id),
        )

        user_tg_id = order.get("user_telegram_id")
        if user_tg_id:
            try:
                bot.send_message(
                    user_tg_id,
                    f"Ваша заявка #{target_order_id} подтверждена ✅\n"
                    f"Договор: {qr_code}\n"
                    f"Ячейка: {chosen_cell.get('number')}\n"
                    f"Срок: {agreement['start_date']} - {agreement['end_date']}\n"
                    "Теперь заказ доступен в разделе «Мои заказы».",
                    reply_markup=get_main_menu(user_tg_id),
                )
            except Exception:
                pass

    @bot.message_handler(commands=['complete_order'])
    def complete_order(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            bot.send_message(
                message.chat.id,
                "Формат: /complete_order <id>\nПример: /complete_order 12",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        target_order_id = int(parts[1])
        database = db_reader()
        order = None
        for idx, item in enumerate(database.get("delivery_requests", []), start=1):
            if order_id_from_record(item, idx) == target_order_id:
                order = item
                break

        if order is None:
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} не найдена.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        current_status = order.get("status")
        if current_status in {"completed", "rejected"}:
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} уже в статусе {current_status}.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        request_type = str(order.get("request_type") or "")
        if current_status != "approved":
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} должна быть сначала подтверждена командой /approve_order {target_order_id}. "
                f"Текущий статус: {current_status}.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        agreement_qr = order.get("item_rental_agreement_qr_code") or order.get("rental_agreement_qr_code")
        agreement = None
        if agreement_qr:
            agreement = next(
                (rent for rent in database.get("rental_agreements", []) if rent.get("qr_code") == agreement_qr),
                None
            )

        freed_cell_number = None
        if request_type.startswith("full_takeout_") and agreement:
            agreement["status"] = "Закончена"
            agreement["end_date"] = date.today().isoformat()
            cell_number = agreement.get("cell_number")
            for cell in database.get("cells", []):
                if cell.get("number") == cell_number:
                    cell["is_occupied"] = False
                    freed_cell_number = cell_number
                    break
            for item in database.get("items", []):
                if item.get("rental_agreement_qr_code") == agreement_qr and item.get("removed_at") is None:
                    item["removed_at"] = utc_now_iso()
                    item["updated_at"] = utc_now_iso()

        order["status"] = "completed"
        order["completed_at"] = utc_now_iso()
        order["completed_by"] = message.from_user.id
        save_database(database)

        result_text = f"Заявка #{target_order_id} переведена в completed."
        if freed_cell_number:
            result_text += f"\nЯчейка {freed_cell_number} освобождена."
        elif request_type.startswith("full_takeout_"):
            result_text += "\nВнимание: не удалось освободить ячейку (договор не найден)."

        bot.send_message(
            message.chat.id,
            result_text,
            reply_markup=get_main_menu(message.from_user.id),
        )

        user_tg_id = order.get("user_telegram_id")
        if user_tg_id:
            try:
                user_text = f"Ваша заявка #{target_order_id} выполнена ✅"
                if freed_cell_number:
                    user_text += "\nАренда завершена, ячейка освобождена."
                bot.send_message(user_tg_id, user_text, reply_markup=get_main_menu(user_tg_id))
            except Exception:
                pass

    @bot.message_handler(commands=['reject_order'])
    def reject_order(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            bot.send_message(
                message.chat.id,
                "Формат: /reject_order <id> <причина>\nПример: /reject_order 5 нет свободных ячеек",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        target_order_id = int(parts[1])
        reason = parts[2].strip()
        if len(reason) < 3:
            bot.send_message(
                message.chat.id,
                "Укажите более подробную причину отмены (минимум 3 символа).",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        database = db_reader()
        order = None
        for idx, item in enumerate(database.get("delivery_requests", []), start=1):
            current_id = order_id_from_record(item, idx)
            if current_id == target_order_id:
                order = item
                break

        if order is None:
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} не найдена.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        if order.get("status") == "approved":
            bot.send_message(
                message.chat.id,
                f"Заявка #{target_order_id} уже подтверждена и не может быть отменена этой командой.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        order["status"] = "rejected"
        order["rejected_at"] = utc_now_iso()
        order["rejected_by"] = message.from_user.id
        order["rejection_reason"] = reason
        save_database(database)

        bot.send_message(
            message.chat.id,
            f"Заявка #{target_order_id} отменена.\nПричина: {reason}",
            reply_markup=get_main_menu(message.from_user.id),
        )

        user_tg_id = order.get("user_telegram_id")
        if user_tg_id:
            try:
                bot.send_message(
                    user_tg_id,
                    f"Ваша заявка #{target_order_id} была отменена оператором.\n"
                    f"Причина: {reason}",
                    reply_markup=get_main_menu(user_tg_id),
                )
            except Exception:
                pass

    @bot.message_handler(commands=['ads_report'])
    def ads_report(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        send_ads_report(message)

    @bot.message_handler(func=lambda m: m.text == "Отчёт по рекламе")
    def ads_report_button(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Раздел доступен только оператору.', reply_markup=main_menu())
            return

        send_ads_report(message)

    def send_ads_report(message):
        database = db_reader()
        users_by_id = {
            user.get("telegram_id"): user
            for user in database.get("users", [])
            if isinstance(user, dict)
        }
        ordered_users = {
            order.get("user_telegram_id")
            for order in database.get("delivery_requests", [])
            if order.get("user_telegram_id") is not None
        }
        if not ordered_users:
            bot.send_message(
                message.chat.id,
                "Пока нет заказов для отчёта.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        source_stats = {}
        for user_id in ordered_users:
            user = users_by_id.get(user_id, {})
            source = user.get("acquisition_source") or "unknown"
            source_stats[source] = source_stats.get(source, 0) + 1

        lines = [
            "Отчёт по рекламе (уникальные клиенты, оформившие заказ):",
            f"Всего клиентов с заказами: {len(ordered_users)}",
            "",
        ]
        for source, count in sorted(source_stats.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {source}: {count}")
        bot.send_message(
            message.chat.id,
            "\n".join(lines),
            reply_markup=get_main_menu(message.from_user.id),
        )

    @bot.message_handler(commands=['overdue_calls'])
    def overdue_contacts(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Команда доступна только оператору.')
            return

        send_overdue_contacts(message)

    @bot.message_handler(func=lambda m: m.text == "Просрочки (обзвон)")
    def overdue_contacts_button(message):
        if str(message.from_user.id) != str(admin_id):
            bot.send_message(message.chat.id, 'Раздел доступен только оператору.', reply_markup=main_menu())
            return

        send_overdue_contacts(message)

    def send_overdue_contacts(message):
        database = db_reader()
        today = date.today()
        users_by_id = {
            user.get("telegram_id"): user
            for user in database.get("users", [])
            if isinstance(user, dict)
        }
        overdue_rents = []

        for rent in database.get("rental_agreements", []):
            if rent.get("status") != "Активна":
                continue

            end_date_raw = rent.get("end_date")
            try:
                end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue

            days_overdue = (today - end_date).days
            if days_overdue <= 0:
                continue

            user = users_by_id.get(rent.get("user_telegram_id"), {})
            overdue_rents.append(
                {
                    "days_overdue": days_overdue,
                    "full_name": user.get("full_name") or "Неизвестный клиент",
                    "phone": user.get("phone") or "Телефон не указан",
                    "qr_code": rent.get("qr_code") or "—",
                    "cell_number": rent.get("cell_number") or "—",
                    "end_date": end_date_raw,
                }
            )

        if not overdue_rents:
            bot.send_message(
                message.chat.id,
                "Сейчас нет просроченных активных аренд.",
                reply_markup=get_main_menu(message.from_user.id),
            )
            return

        overdue_rents.sort(key=lambda row: row["days_overdue"], reverse=True)

        lines = ["Просроченные аренды (для обзвона):", ""]
        for idx, row in enumerate(overdue_rents, start=1):
            lines.append(
                f"{idx}. {row['full_name']} | {row['phone']} | "
                f"просрочка {row['days_overdue']} дн."
            )
            lines.append(
                f"Договор: {row['qr_code']} | Ячейка: {row['cell_number']} | "
                f"Окончание: {row['end_date']}"
            )
            lines.append("")

        text = "\n".join(lines).strip()
        max_chunk_len = 3500
        if len(text) <= max_chunk_len:
            bot.send_message(message.chat.id, text, reply_markup=get_main_menu(message.from_user.id))
            return

        for start in range(0, len(text), max_chunk_len):
            chunk = text[start:start + max_chunk_len]
            bot.send_message(message.chat.id, chunk)
        bot.send_message(message.chat.id, "Список отправлен.", reply_markup=get_main_menu(message.from_user.id))


    @bot.message_handler(func=lambda m: True)
    def pickup_flow(message):
        database = db_reader()
        user_text = (message.text or '').strip()
        user_id = message.from_user.id

        if user_text.lower() in {'/cancel', 'отмена'}:
            reset_session(user_id)
            bot.send_message(
                message.chat.id,
                'Заявка отменена. Возвращаю в меню.',
                reply_markup=get_main_menu(user_id),
            )
            return

        session = get_session(user_id)
        if not session:
            bot.send_message(
                message.chat.id,
                'Выберите действие в меню или нажмите /start.',
                reply_markup=get_main_menu(user_id),
            )
            return

        state = session['state']

        if state == 'WAIT_ADDRESS':
            if len(user_text) < 8:
                bot.send_message(message.chat.id, 'Адрес слишком короткий. Введите подробнее:')
                return

            session['data']['address'] = user_text
            session['state'] = 'WAIT_PHONE'
            bot.send_message(message.chat.id, 'Введите телефон в формате +79991234567:')
            return

        if state == "WAIT_EXISTING_RENT_SELECT":
            selected_rent = session["data"]["rent_map"].get(user_text)
            if not selected_rent:
                bot.send_message(message.chat.id, 'Выберите договор кнопкой из списка.')
                return

            session["data"]["selected_rent"] = selected_rent
            session["state"] = "WAIT_EXISTING_DELIVERY_DECISION"
            action = session["data"]["existing_action"]
            text = (
                f"{action['title']}\n"
                f"Договор: {selected_rent.get('qr_code')}\n"
                f"Ячейка: {selected_rent.get('cell_number')}\n\n"
                'Можно оформить доставку (курьер) или выполнить действие самостоятельно.\n'
                'Как Вам удобнее?'
            )
            bot.send_message(message.chat.id, text, reply_markup=delivery_decision())
            return

        if state == "WAIT_EXISTING_ADDRESS":
            if len(user_text) < 8:
                bot.send_message(message.chat.id, 'Адрес слишком короткий. Введите подробнее:')
                return

            session["data"]["address"] = user_text
            session["state"] = "WAIT_EXISTING_PHONE"
            bot.send_message(message.chat.id, 'Введите телефон в формате +79991234567:')
            return

        if state == "WAIT_EXISTING_PHONE":
            if not user_text.startswith('+') or len(user_text) < 8:
                bot.send_message(message.chat.id, 'Неверный формат. Пример: +79991234567')
                return

            session["data"]["phone"] = user_text
            session["state"] = "CONFIRM_EXISTING"
            action = session["data"]["existing_action"]
            selected_rent = session["data"]["selected_rent"]
            bot.send_message(
                message.chat.id,
                'Проверьте заявку:\n'
                f"Тип: {action['title']}\n"
                f"Договор: {selected_rent.get('qr_code')}\n"
                f"Ячейка: {selected_rent.get('cell_number')}\n"
                f"Адрес доставки: {session['data']['address']}\n"
                f"Телефон: {session['data']['phone']}\n\n"
                'Нажмите ДА для подтверждения или НЕТ для отмены',
                reply_markup=confirm_request(),
            )
            return

        if state == "WAIT_LEGAL_RACKS":
            try:
                racks_count = int(user_text)
            except ValueError:
                bot.send_message(message.chat.id, 'Введите целое число, например 3.')
                return
            if racks_count <= 0:
                bot.send_message(message.chat.id, 'Количество стеллажей должно быть больше нуля.')
                return

            session["data"]["racks_count"] = racks_count
            session["state"] = "WAIT_LEGAL_MONTHS"
            bot.send_message(message.chat.id, 'Введите срок аренды в месяцах (например 6):')
            return

        if state == "WAIT_LEGAL_MONTHS":
            try:
                rent_months = int(user_text)
            except ValueError:
                bot.send_message(message.chat.id, 'Введите целое число месяцев, например 6.')
                return
            if rent_months <= 0:
                bot.send_message(message.chat.id, 'Срок аренды должен быть больше нуля.')
                return

            session["data"]["rent_months"] = rent_months
            session["state"] = "WAIT_LEGAL_PHONE"
            bot.send_message(message.chat.id, 'Введите телефон контактного лица в формате +79991234567:')
            return

        if state == "WAIT_LEGAL_PHONE":
            if not user_text.startswith('+') or len(user_text) < 8:
                bot.send_message(message.chat.id, 'Неверный формат. Пример: +79991234567')
                return

            session["data"]["phone"] = user_text
            session["state"] = "WAIT_LEGAL_EMAIL"
            bot.send_message(message.chat.id, 'Введите email контактного лица:')
            return

        if state == "WAIT_LEGAL_EMAIL":
            if not is_valid_email(user_text):
                bot.send_message(message.chat.id, 'Неверный email. Пример: name@example.com')
                return

            session["data"]["email"] = user_text.strip()
            monthly_total = session["data"]["racks_count"] * legal_rack_monthly_price
            full_total = monthly_total * session["data"]["rent_months"]
            session["data"]["expected_monthly_price"] = monthly_total
            session["data"]["expected_total_price"] = full_total
            session["state"] = "CONFIRM_LEGAL"
            bot.send_message(
                message.chat.id,
                'Проверьте заявку на хранение документов:\n'
                f"Количество стеллажей: {session['data']['racks_count']}\n"
                f"Срок аренды: {session['data']['rent_months']} мес.\n"
                f"Стоимость в месяц: {monthly_total:.2f} руб.\n"
                f"Общая стоимость: {full_total:.2f} руб.\n"
                f"Телефон: {session['data']['phone']}\n\n"
                f"Email: {session['data']['email']}\n\n"
                'Нажмите ДА для подтверждения или НЕТ для отмены',
                reply_markup=confirm_request(),
            )
            return

        if state == 'WAIT_WAREHOUSE':
            warehouse_names = {w['name'] for w in session['data'].get('available_warehouses', [])}
            if user_text not in warehouse_names:
                bot.send_message(message.chat.id, 'Выберите склад кнопкой из списка.')
                return

            selected_warehouse = next(
                (w for w in session['data']['available_warehouses'] if w['name'] == user_text),
                None
            )
            session['data']['warehouse_name'] = selected_warehouse['name']
            session['data']['address'] = selected_warehouse['address']
            session['state'] = 'WAIT_PHONE'
            bot.send_message(message.chat.id, 'Введите телефон в формате +79991234567:')
            return

        if state == 'WAIT_PHONE':
            if not user_text.startswith('+') or len(user_text) < 8:
                bot.send_message(message.chat.id, 'Неверный формат. Пример: +79991234567')
                return

            session['data']['phone'] = user_text
            session['state'] = 'WAIT_EMAIL'
            bot.send_message(message.chat.id, 'Введите email для напоминаний:')
            return

        if state == 'WAIT_EMAIL':
            if not is_valid_email(user_text):
                bot.send_message(message.chat.id, 'Неверный email. Пример: name@example.com')
                return

            session['data']['email'] = user_text.strip()
            session['state'] = 'WAIT_VOLUME'
            text = 'Уточните, пожалуйста, какой примерный объем вещей Вы хотите хранить у нас?\n\n'
            for size in database["cell_sizes"]:
                text = text + f'{size["code"]} - {size["description"]} ({size["monthly_price"]} руб./мес.)\n'
            text = text + '\nНажмите на кнопку с подходящим объемом.'

            bot.send_message(message.chat.id, text, reply_markup=choose_volume())
            return

        if state == 'WAIT_VOLUME':
            selected_size = next(
                (size for size in database.get('cell_sizes', []) if size['code'] == user_text),
                None
            )
            if selected_size is None:
                bot.send_message(message.chat.id, 'Выберите объём кнопкой: s, m или l.')
                return

            session['data']['volume'] = user_text
            session['data']['volume_description'] = selected_size['description']
            session['data']['expected_monthly_price_base'] = float(selected_size['monthly_price'])
            session['state'] = 'WAIT_RENT_DAYS'
            bot.send_message(
                message.chat.id,
                'Введите срок хранения в днях (например 45):',
                reply_markup=return_main_menu_keyboard(),
            )
            return

        if state == 'WAIT_RENT_DAYS':
            try:
                rent_days = int(user_text)
            except ValueError:
                bot.send_message(message.chat.id, 'Введите целое число дней, например 45.')
                return

            if rent_days <= 0:
                bot.send_message(message.chat.id, 'Срок хранения должен быть больше 0 дней.')
                return

            if rent_days > 3650:
                bot.send_message(message.chat.id, 'Слишком большой срок. Введите значение до 3650 дней.')
                return

            session['data']['rent_days'] = rent_days
            session['state'] = 'WAIT_PROMO'
            bot.send_message(
                message.chat.id,
                'Если у вас есть промокод, введите его сейчас.\n'
                'Или нажмите "Пропустить".',
                reply_markup=promo_decision(),
            )
            return

        if state == 'WAIT_PROMO':
            promo_input = None if user_text == "Пропустить" else user_text
            promo = promo_result(promo_input, promo_catalog)
            base_price = float(session['data'].get('expected_monthly_price_base', 0))

            if promo["status"] == "unknown":
                bot.send_message(
                    message.chat.id,
                    'Промокод не найден. Проверьте ввод или нажмите "Пропустить".',
                    reply_markup=promo_decision(),
                )
                return

            if promo["status"] == "inactive":
                bot.send_message(
                    message.chat.id,
                    f"Промокод {promo['code']} неактивен. "
                    f"Период действия: {promo['valid_from']} - {promo['valid_until']}. "
                    'Введите другой код или нажмите "Пропустить".',
                    reply_markup=promo_decision(),
                )
                return

            discount_percent = promo.get("discount_percent", 0)
            discount_value = round(base_price * discount_percent / 100, 2)
            final_price = round(base_price - discount_value, 2)
            rent_days = int(session['data'].get('rent_days', 30))
            expected_total_price = round((final_price / 30) * rent_days, 2)
            session['data']['promo_code'] = promo.get("code")
            session['data']['promo_discount_percent'] = discount_percent
            session['data']['expected_monthly_price'] = final_price
            session['data']['expected_total_price'] = expected_total_price
            session['state'] = 'WAIT_SEASONAL_FLAG'
            bot.send_message(
                message.chat.id,
                'Планируете хранить сезонные вещи?\n'
                'Выберите вариант кнопкой.',
                reply_markup=options_keyboard(["Да, сезонные вещи", "Нет, обычные вещи"]),
            )
            return

        if state == 'WAIT_SEASONAL_FLAG':
            if user_text == "Да, сезонные вещи":
                session['state'] = 'WAIT_SEASONAL_LIST'
                bot.send_message(
                    message.chat.id,
                    'Перечислите все сезонные вещи текстом (через запятую или с новой строки):',
                    reply_markup=return_main_menu_keyboard(),
                )
                return

            if user_text == "Нет, обычные вещи":
                session['data']['has_seasonal_items'] = False
                session['data']['seasonal_item_list'] = []
                session['state'] = 'CONFIRM'
                send_storage_confirm(message.chat.id, session['data'])
                return

            bot.send_message(message.chat.id, 'Выберите один из вариантов кнопкой.')
            return

        if state == 'WAIT_SEASONAL_LIST':
            items = parse_items_list(user_text)
            if not items:
                bot.send_message(
                    message.chat.id,
                    'Список пустой. Перечислите вещи через запятую или с новой строки.',
                )
                return

            session['data']['has_seasonal_items'] = True
            session['data']['seasonal_item_list'] = items
            session['state'] = 'CONFIRM'
            send_storage_confirm(message.chat.id, session['data'])
            return

        if state == 'CONFIRM':
            answer = user_text.lower()
            if answer.startswith('да') or answer in {'yes', 'y'}:
                upsert_user_profile(
                    telegram_id=user_id,
                    full_name=normalize_full_name(message.from_user),
                    username=message.from_user.username,
                    phone=session['data'].get('phone'),
                    address=session['data'].get('address'),
                    email=session['data'].get('email'),
                )
                order = {
                    'user_telegram_id': user_id,
                    'item_rental_agreement_qr_code': None,
                    'request_type': session['data'].get('request_type', 'pickup'),
                    'address': session['data']['address'],
                    'phone': session['data'].get('phone'),
                    'email': session['data'].get('email'),
                    'volume_code': session['data'].get('volume'),
                    'has_seasonal_items': bool(session['data'].get('has_seasonal_items')),
                    'seasonal_item_list': session['data'].get('seasonal_item_list', []),
                    'rent_days': session['data'].get('rent_days'),
                    'promo_code': session['data'].get('promo_code'),
                    'promo_discount_percent': session['data'].get('promo_discount_percent', 0),
                    'expected_monthly_price': session['data'].get('expected_monthly_price'),
                    'expected_total_price': session['data'].get('expected_total_price'),
                    'requested_at': utc_now_iso(),
                    'status': 'pending',
                }
                order_id = append_order(order)
                reset_session(user_id)

                bot.send_message(
                    message.chat.id,
                    f'Заявка №{order_id} создана ✅ Оператор свяжется с вами.',
                    reply_markup=get_main_menu(user_id),
                )

                if chat_id:
                    promo_admin_text = (
                        f"Промокод: {session['data'].get('promo_code')} (-{session['data'].get('promo_discount_percent', 0)}%)\n"
                        if session['data'].get('promo_code')
                        else "Промокод: не применён\n"
                    )
                    bot.send_message(
                        chat_id,
                        'Новая заявка на вывоз:\n'
                        f'№{order_id}\n'
                        f"Клиент: {(message.from_user.first_name or '')} {(message.from_user.last_name or '')}\n"
                        f"@{message.from_user.username or 'без username'}\n"
                        f"Телефон: {session['data']['phone']}\n"
                        f"Email: {session['data']['email']}\n"
                        f"Адрес: {session['data']['address']}\n"
                        f"Объём: {session['data']['volume']} - {session['data']['volume_description']}\n"
                        f"Срок хранения: {session['data'].get('rent_days')} дн.\n"
                        f"Сезонные вещи: {', '.join(session['data'].get('seasonal_item_list', [])) if session['data'].get('has_seasonal_items') else 'нет'}\n"
                        f"{promo_admin_text}"
                        f"Ожидаемая стоимость: {session['data']['expected_monthly_price']} руб./мес.\n"
                        f"Ожидаемая стоимость за весь срок: {session['data'].get('expected_total_price')} руб.",
                    )
                return

            if answer.startswith('нет') or answer in {'no', 'n'}:
                reset_session(user_id)
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=get_main_menu(user_id))
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

        if state == "CONFIRM_LEGAL":
            answer = user_text.lower()
            if answer.startswith('да') or answer in {'yes', 'y'}:
                upsert_user_profile(
                    telegram_id=user_id,
                    full_name=normalize_full_name(message.from_user),
                    username=message.from_user.username,
                    phone=session['data'].get('phone'),
                    email=session['data'].get('email'),
                )
                order = {
                    "user_telegram_id": user_id,
                    "item_rental_agreement_qr_code": None,
                    "request_type": "legal_docs_storage",
                    "address": None,
                    "phone": session["data"]["phone"],
                    "email": session["data"]["email"],
                    "racks_count": session["data"]["racks_count"],
                    "rent_months": session["data"]["rent_months"],
                    "monthly_price": session["data"]["expected_monthly_price"],
                    "total_price": session["data"]["expected_total_price"],
                    "requested_at": utc_now_iso(),
                    "status": "pending",
                }
                order_id = append_order(order)
                reset_session(user_id)

                bot.send_message(
                    message.chat.id,
                    f"Заявка №{order_id} на услуги для юрлиц создана ✅",
                    reply_markup=get_main_menu(user_id),
                )

                if chat_id:
                    bot.send_message(
                        chat_id,
                        f"Новая заявка юрлица №{order_id}\n"
                        "Тип: хранение документов (стеллажи)\n"
                        f"Клиент: {(message.from_user.first_name or '')} {(message.from_user.last_name or '')}\n"
                        f"@{message.from_user.username or 'без username'}\n"
                        f"Телефон: {session['data']['phone']}\n"
                        f"Email: {session['data']['email']}\n"
                        f"Стеллажей: {session['data']['racks_count']}\n"
                        f"Срок: {session['data']['rent_months']} мес.\n"
                        f"В месяц: {session['data']['expected_monthly_price']:.2f} руб.\n"
                        f"Итого: {session['data']['expected_total_price']:.2f} руб.",
                    )
                return

            if answer.startswith('нет') or answer in {'no', 'n'}:
                reset_session(user_id)
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=get_main_menu(user_id))
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

        if state == "CONFIRM_EXISTING":
            answer = user_text.lower()
            if answer.startswith('да') or answer in {'yes', 'y'}:
                action = session["data"]["existing_action"]
                selected_rent = session["data"]["selected_rent"]
                upsert_user_profile(
                    telegram_id=user_id,
                    full_name=normalize_full_name(message.from_user),
                    username=message.from_user.username,
                    phone=session["data"].get("phone"),
                    address=session["data"].get("address"),
                )
                order = {
                    "user_telegram_id": user_id,
                    "item_rental_agreement_qr_code": selected_rent.get("qr_code"),
                    "request_type": f"{action['code']}_delivery",
                    "address": session["data"]["address"],
                    "phone": session["data"].get("phone"),
                    "requested_at": utc_now_iso(),
                    "status": "pending",
                }
                order_id = append_order(order)
                reset_session(user_id)

                bot.send_message(
                    message.chat.id,
                    f"Заявка №{order_id} создана ✅ Оператор свяжется с вами.",
                    reply_markup=get_main_menu(user_id),
                )

                if chat_id:
                    bot.send_message(
                        chat_id,
                        f"Новая заявка на доставку №{order_id}\n"
                        f"Тип: {action['title']}\n"
                        f"Клиент: {(message.from_user.first_name or '')} {(message.from_user.last_name or '')}\n"
                        f"@{message.from_user.username or 'без username'}\n"
                        f"Телефон: {session['data']['phone']}\n"
                        f"Адрес: {session['data']['address']}\n"
                        f"Договор: {selected_rent.get('qr_code')}\n"
                        f"Ячейка: {selected_rent.get('cell_number')}",
                    )
                return

            if answer.startswith('нет') or answer in {'no', 'n'}:
                reset_session(user_id)
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=get_main_menu(user_id))
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

    run_daily_reminders()
    threading.Thread(target=reminders_worker, daemon=True).start()

    bot.infinity_polling(skip_pending=True)


if __name__ == '__main__':
    main()

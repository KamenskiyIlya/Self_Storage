import os
import threading
import time
from datetime import datetime, date

import telebot
from dotenv import load_dotenv
from telebot.types import InputFile

from keyboards import main_menu, already_stored, delivery_decision, pickup_decision
from keyboards import approval_processing_data, return_main_menu as return_main_menu_keyboard, choose_volume, confirm_request
from db_utils import db_reader, append_order, get_cell_by_number
from reminders import process_rent_reminders
from ui_helpers import warehouse_keyboard, options_keyboard


def main() -> None:
    load_dotenv()
    token = os.getenv('TG_TOKEN')
    chat_id = os.getenv('TG_CHAT_ID')

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

    def reset_session(user_id: int):
        sessions.pop(user_id, None)

    def get_session(user_id: int):
        return sessions.get(user_id)

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
            reply_markup=main_menu(),
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
                reply_markup=main_menu(),
            )
            return

        request_type = session['data'].get('request_type')
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
            reply_markup=main_menu(),
        )


    @bot.message_handler(func=lambda m: m.text == 'Хочу хранить вещи')
    def want_storage(message):
        database = db_reader()
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

    want_storage_message = ['Необходимо забрать', 'Отвезу сам']
    @bot.message_handler(func=lambda m: m.text in want_storage_message)
    def already_stored_menu(message):
        database = db_reader()
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
            if rent["user_telegram_id"] == user_id:
                user_rent.append(rent)

        if not user_rent:
            bot.send_message(
                message.chat.id,
                'На данный момент у Вас нет заказов.',
                reply_markup=main_menu(),
            )
        else:
            bot.send_message(
                message.chat.id,
                'Ваши арендованные ячейки: \n\n',
            )
            for rent in user_rent:
                for cell in database['cells']:
                    if cell["number"] == rent["cell_number"]:
                        warehouse = cell["warehouse_name"]
                        cell_size_code = cell["cell_size_code"]

                for cell in database["cell_sizes"]:
                    if cell["code"] == cell_size_code:
                        cell_description = cell["description"]

                text = (
                    f'Склад: {warehouse}\n'
                    f'Номер ячейки: {rent["cell_number"]}\n'
                    f'Размер ячейки: {cell_size_code} - {cell_description}\n'
                    f'Начало аренды: {rent["start_date"]}\n'
                    f'Конец аренды: {rent["end_date"]}\n'
                    f'Общая цена: {rent["total_price"]}\n'
                    f'Статус аренды: {rent["status"]}'
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
                        reply_markup=main_menu(),
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
                reply_markup=main_menu(),
            )
            return

        if len(active_rents) == 1:
            sessions[user_id] = {
                "state": "WAIT_EXISTING_DELIVERY_DECISION",
                "data": {
                    "existing_action": selected_action,
                    "selected_rent": active_rents[0],
                }
            }
            selected_cell = active_rents[0].get("cell_number")
            text = (
                f"{selected_action['title']}\n"
                f"Ячейка: {selected_cell}\n\n"
                'У нас есть услуга доставки. За Вас все сделает курьер, от Вас нужен адрес и телефон. '
                'Также Вы можете выполнить действие самостоятельно.\n\n'
                'Как Вам удобнее?'
            )
            bot.send_message(
                message.chat.id,
                text,
                reply_markup=delivery_decision(),
            )
            return

        rent_map = {rent["cell_number"]: rent for rent in active_rents}
        sessions[user_id] = {
            "state": "WAIT_EXISTING_RENT_SELECT",
            "data": {
                "existing_action": selected_action,
                "rent_map": rent_map,
            }
        }
        bot.send_message(
            message.chat.id,
            'Выберите номер ячейки, по которой хотите оформить действие:',
            reply_markup=options_keyboard(list(rent_map.keys())),
        )

    @bot.message_handler(func=lambda m: m.text == "Нужна доставка")
    def existing_need_delivery(message):
        session = get_session(message.from_user.id)
        if not session or session.get("state") != "WAIT_EXISTING_DELIVERY_DECISION":
            bot.send_message(
                message.chat.id,
                'Этот сценарий пока доступен только из раздела "Уже храню вещи".',
                reply_markup=main_menu(),
            )
            return

        action_title = session["data"]["existing_action"]["title"].lower()
        session["state"] = "WAIT_EXISTING_ADDRESS"
        bot.send_message(
            message.chat.id,
            f'Введите адрес для услуги доставки ({action_title}):',
            reply_markup=options_keyboard(["Отмена"], include_main_menu=True),
        )

    @bot.message_handler(func=lambda m: m.text == "Заберу сам")
    def existing_self_service(message):
        session = get_session(message.from_user.id)
        if not session or session.get("state") != "WAIT_EXISTING_DELIVERY_DECISION":
            bot.send_message(
                message.chat.id,
                'Этот сценарий пока доступен только из раздела "Уже храню вещи".',
                reply_markup=main_menu(),
            )
            return

        selected_rent = session["data"]["selected_rent"]
        action = session["data"]["existing_action"]
        database = db_reader()
        cell = get_cell_by_number(database, selected_rent.get("cell_number"))
        warehouse_name = cell.get("warehouse_name") if cell else "Склад"
        warehouse_address = "Адрес уточнит оператор"
        for warehouse in database.get("warehouses", []):
            if warehouse.get("name") == warehouse_name:
                warehouse_address = warehouse.get("address")
                break

        order = {
            "user_telegram_id": message.from_user.id,
            "item_rental_agreement_qr_code": selected_rent.get("qr_code"),
            "request_type": f"{action['code']}_self",
            "address": warehouse_address,
            "requested_at": f"{datetime.utcnow().isoformat(timespec='seconds')}Z",
            "status": "self_service",
        }
        order_id = append_order(order)
        reset_session(message.from_user.id)

        bot.send_message(
            message.chat.id,
            f"Заявка №{order_id} оформлена.\n"
            f"{action['title']} самостоятельно.\n"
            f"Склад: {warehouse_name}\n"
            f"Адрес: {warehouse_address}",
            reply_markup=main_menu(),
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


    in_development = []

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
        bot.send_message(message.chat.id, text, reply_markup=main_menu())

    @bot.message_handler(func=lambda m: m.text in in_development)
    def menu_placeholders(message):
        bot.send_message(
            message.chat.id,
            'Раздел в разработке. Выберите другой пункт или нажмите /start.',
            reply_markup=main_menu(),
        )

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
                reply_markup=main_menu(),
            )
            return

        session = get_session(user_id)
        if not session:
            bot.send_message(
                message.chat.id,
                'Выберите действие в меню или нажмите /start.',
                reply_markup=main_menu(),
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
                bot.send_message(message.chat.id, 'Выберите номер ячейки кнопкой.')
                return

            session["data"]["selected_rent"] = selected_rent
            session["state"] = "WAIT_EXISTING_DELIVERY_DECISION"
            action = session["data"]["existing_action"]
            text = (
                f"{action['title']}\n"
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
            session['data']['expected_monthly_price'] = selected_size['monthly_price']
            session['state'] = 'CONFIRM'
            measure_text = (
                'Курьер замерит габариты на месте.'
                if session['data'].get('request_type') == 'pickup'
                else 'Точный объём замерим при приёме вещей на складе.'
            )
            route_text = (
                f"Склад: {session['data']['warehouse_name']}\n"
                if session['data'].get('request_type') == 'self_dropoff'
                else ''
            )
            bot.send_message(
                message.chat.id,
                'Проверьте заявку:\n'
                f'{route_text}'
                f"Адрес: {session['data']['address']}\n"
                f"Телефон: {session['data']['phone']}\n"
                f"Объём: {session['data']['volume']} - {session['data']['volume_description']}\n"
                f"Ожидаемая стоимость: {session['data']['expected_monthly_price']} руб./мес.\n\n"
                f'{measure_text}\n\n'
                'Нажмите ДА для подтверждения или НЕТ для отмены',
                reply_markup=confirm_request()
            )
            return

        if state == 'CONFIRM':
            answer = user_text.lower()
            if answer.startswith('да') or answer in {'yes', 'y'}:
                order = {
                    'user_telegram_id': user_id,
                    'item_rental_agreement_qr_code': None,
                    'request_type': session['data'].get('request_type', 'pickup'),
                    'address': session['data']['address'],
                    'requested_at': f"{datetime.utcnow().isoformat(timespec='seconds')}Z",
                    'status': 'pending',
                }
                order_id = append_order(order)
                reset_session(user_id)

                bot.send_message(
                    message.chat.id,
                    f'Заявка №{order_id} создана ✅ Оператор свяжется с вами.',
                    reply_markup=main_menu(),
                )

                if chat_id:
                    bot.send_message(
                        chat_id,
                        'Новая заявка на вывоз:\n'
                        f'№{order_id}\n'
                        f"Клиент: {(message.from_user.first_name or '')} {(message.from_user.last_name or '')}\n"
                        f"@{message.from_user.username or 'без username'}\n"
                        f"Телефон: {session['data']['phone']}\n"
                        f"Адрес: {session['data']['address']}\n"
                        f"Объём: {session['data']['volume']} - {session['data']['volume_description']}\n"
                        f"Ожидаемая стоимость: {session['data']['expected_monthly_price']} руб./мес.",
                    )
                return

            if answer.startswith('нет') or answer in {'no', 'n'}:
                reset_session(user_id)
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=main_menu())
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

        if state == "CONFIRM_EXISTING":
            answer = user_text.lower()
            if answer.startswith('да') or answer in {'yes', 'y'}:
                action = session["data"]["existing_action"]
                selected_rent = session["data"]["selected_rent"]
                order = {
                    "user_telegram_id": user_id,
                    "item_rental_agreement_qr_code": selected_rent.get("qr_code"),
                    "request_type": f"{action['code']}_delivery",
                    "address": session["data"]["address"],
                    "requested_at": f"{datetime.utcnow().isoformat(timespec='seconds')}Z",
                    "status": "pending",
                }
                order_id = append_order(order)
                reset_session(user_id)

                bot.send_message(
                    message.chat.id,
                    f"Заявка №{order_id} создана ✅ Оператор свяжется с вами.",
                    reply_markup=main_menu(),
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
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=main_menu())
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

    run_daily_reminders()
    threading.Thread(target=reminders_worker, daemon=True).start()

    bot.infinity_polling(skip_pending=True)


if __name__ == '__main__':
    main()

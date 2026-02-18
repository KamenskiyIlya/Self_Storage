import json
import os
from datetime import datetime
from pathlib import Path

import telebot
from dotenv import load_dotenv

from keyboards import main_menu

ORDERS_FILE = Path('orders.json')
VOLUME_MAP = {'1': 'мало', '2': 'средне', '3': 'много'}


def read_orders():
    if not ORDERS_FILE.exists():
        return []

    try:
        with ORDERS_FILE.open('r', encoding='utf-8') as file:
            data = json.load(file)
    except json.JSONDecodeError:
        return []

    return data if isinstance(data, list) else []


def append_order(order) :
    orders = read_orders()
    order_id = len(orders) + 1
    order['id'] = order_id
    orders.append(order)

    with ORDERS_FILE.open('w', encoding='utf-8') as file:
        json.dump(orders, file, ensure_ascii=False, indent=2)

    return order_id


def main() -> None:
    load_dotenv()
    token = os.getenv('TG_TOKEN')
    chat_id = os.getenv('TG_CHAT_ID')

    if not token:
        raise RuntimeError('TG_TOKEN не задан в переменных окружения')

    bot = telebot.TeleBot(token)
    sessions: dict[int, dict] = {}

    def reset_session(user_id: int):
        sessions.pop(user_id, None)

    def get_session(user_id: int):
        return sessions.get(user_id)

    @bot.message_handler(commands=['start'])
    def start(message):
        reset_session(message.from_user.id)
        bot.send_message(
            message.chat.id,
            'Привет! Я бот SelfStorage ✅\nВыбери пункт меню:',
            reply_markup=main_menu(),
        )

    @bot.message_handler(func=lambda m: m.text == 'Оформить вывоз')
    def pickup_start(message):
        sessions[message.from_user.id] = {'state': 'WAIT_ADDRESS', 'data': {}}
        bot.send_message(
            message.chat.id,
            'Введите адрес, откуда забрать вещи (город, улица, дом):',
            reply_markup=main_menu(),
        )

    @bot.message_handler(func=lambda m: m.text in {'Что это', 'Правила', 'Адреса складов', 'Мои заказы', 'Помощь'})
    def menu_placeholders(message):
        bot.send_message(
            message.chat.id,
            'Раздел в разработке. Выберите другой пункт или нажмите /start.',
            reply_markup=main_menu(),
        )

    @bot.message_handler(func=lambda m: True)
    def pickup_flow(message):
        text = (message.text or '').strip()
        user_id = message.from_user.id

        if text.lower() in {'/cancel', 'отмена'}:
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
            if len(text) < 8:
                bot.send_message(message.chat.id, 'Адрес слишком короткий. Введите подробнее:')
                return

            session['data']['address'] = text
            session['state'] = 'WAIT_PHONE'
            bot.send_message(message.chat.id, 'Введите телефон в формате +79991234567:')
            return

        if state == 'WAIT_PHONE':
            if not text.startswith('+') or len(text) < 8:
                bot.send_message(message.chat.id, 'Неверный формат. Пример: +79991234567')
                return

            session['data']['phone'] = text
            session['state'] = 'WAIT_VOLUME'
            bot.send_message(message.chat.id, 'Оцените объём: 1) мало 2) средне 3) много (введите 1/2/3):')
            return

        if state == 'WAIT_VOLUME':
            if text not in VOLUME_MAP:
                bot.send_message(message.chat.id, 'Введите 1, 2 или 3.')
                return

            session['data']['volume'] = VOLUME_MAP[text]
            session['state'] = 'CONFIRM'
            bot.send_message(
                message.chat.id,
                'Проверьте заявку:\n'
                f"Адрес: {session['data']['address']}\n"
                f"Телефон: {session['data']['phone']}\n"
                f"Объём: {session['data']['volume']}\n\n"
                'Ответьте: ДА - подтвердить, НЕТ - отменить.',
            )
            return

        if state == 'CONFIRM':
            answer = text.lower()
            if answer in {'да', 'yes', 'y'}:
                order = {
                    'created_at': datetime.now().isoformat(timespec='seconds'),
                    'user_id': user_id,
                    'username': message.from_user.username,
                    'full_name': f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip(),
                    'address': session['data']['address'],
                    'phone': session['data']['phone'],
                    'volume': session['data']['volume'],
                    'status': 'new',
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
                        f"Клиент: {order['full_name'] or 'Без имени'}\n"
                        f"@{order['username'] or 'без username'}\n"
                        f"Телефон: {order['phone']}\n"
                        f"Адрес: {order['address']}\n"
                        f"Объём: {order['volume']}",
                    )
                return

            if answer in {'нет', 'no', 'n'}:
                reset_session(user_id)
                bot.send_message(message.chat.id, 'Ок, заявка отменена.', reply_markup=main_menu())
                return

            bot.send_message(message.chat.id, 'Ответьте ДА или НЕТ.')
            return

    bot.infinity_polling(skip_pending=True)


if __name__ == '__main__':
    main()

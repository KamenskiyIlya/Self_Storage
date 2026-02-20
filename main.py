import json
import os
from datetime import datetime
from pathlib import Path

import telebot
from dotenv import load_dotenv
from telebot.types import InputFile

from keyboards import main_menu, already_stored, delivery_decision, pickup_decision, approval_processing_data, return_main_menu

DATABASE_FILE = Path('database.json')
VOLUME_MAP = {'1': 'мало', '2': 'средне', '3': 'много'}


def db_reader():
    '''Достает информацию из БД'''
    if not DATABASE_FILE.exists():
        return []

    try:
        with DATABASE_FILE.open('r', encoding='utf-8') as file:
            database = json.load(file)
    except json.JSONDecodeError:
        return []

    return database if isinstance(database, dict) else []


def append_order(order) :
    database = db_reader()
    order_id = len(database['delivery_requests']) + 1
    order['order_id'] = order_id
    updated_orders = database['delivery_requests'].append(order)

    with DATABASE_FILE.open('w', encoding='utf-8') as file:
        json.dump(updated_orders, file, ensure_ascii=False, indent=2)

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

    @bot.message_handler(commands=['start'], func=lambda m: m.text == "Вернуться в главное меню")
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
    def return_main_menu(message):
        start(message)


    @bot.message_handler(func=lambda m: m.text == 'Согласен ✅')
    def pickup_start(message):
        sessions[message.from_user.id] = {'state': 'WAIT_ADDRESS', 'data': {}}
        bot.send_message(
            message.chat.id,
            'Введите адрес, откуда забрать вещи (город, улица, дом):',
            reply_markup=return_main_menu()
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
    def action_with_stored(message):
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
                reply_markup=already_stored(),
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
                bot.send_message(
                    message.chat.id,
                    text,
                    reply_markup=already_stored(),
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
        text = (
            'У нас есть услуга доставки. Не теряйте время на лишние хлопоты, лучше потратьте его на себя. '
            'За Вас все сделает наш курьер, от Вас нужно будет только указать информацию о вещах и адрес. '
            'Также Вы можете заняться перевозкой вещей самостоятельно\n\n'
            'Как Вам было бы удобней?'
        )
        bot.send_message(
            message.chat.id,
            text,
            reply_markup=delivery_decision(),
        )


    in_development = [
        'Правила хранения',
        "Забрать частично вещи",
        "Забрать полностью вещи",
        "Положить обратно в арендованную ячейку",
        "Нужна доставка",
        "Заберу сам",
    ]
    @bot.message_handler(func=lambda m: m.text in in_development)
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

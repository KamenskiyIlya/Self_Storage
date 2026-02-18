from telebot.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    Keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    Keyboard.add(KeyboardButton("Что это"), KeyboardButton("Правила"))
    Keyboard.add(KeyboardButton("Оформить вывоз"), KeyboardButton("Адреса складов"))
    Keyboard.add(KeyboardButton("Мои заказы"), KeyboardButton("Помощь"))
    return Keyboard

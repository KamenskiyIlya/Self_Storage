from telebot.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Правила хранения"), KeyboardButton("Мои заказы"))
    keyboard.add(KeyboardButton("Уже храню вещи"), KeyboardButton("Хочу хранить вещи"))
    return keyboard

def already_stored():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Забрать частично вещи"), KeyboardButton("Забрать полностью вещи"))
    keyboard.add(KeyboardButton("Положить обратно в арендованную ячейку"))
    return keyboard

def delivery_decision():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Заберу сам"), KeyboardButton("Нужна доставка"))
    return keyboard
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Правила хранения"), KeyboardButton("Мои заказы"))
    keyboard.add(KeyboardButton("Уже храню вещи"), KeyboardButton("Хочу хранить вещи"))
    return keyboard

def already_stored():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Забрать частично вещи"), KeyboardButton("Забрать полностью вещи"))
    keyboard.add(KeyboardButton("Положить обратно в арендованную ячейку"), KeyboardButton("Вернуться в главное меню"))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

def delivery_decision():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Заберу сам"), KeyboardButton("Нужна доставка"))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

def pickup_decision():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Отвезу сам"), KeyboardButton("Необходимо забрать"))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

def approval_processing_data():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Согласен ✅"), KeyboardButton("Не согласен ❌"))
    return keyboard

def return_main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

def choose_volume():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("s"), KeyboardButton("m"), KeyboardButton("l"))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

def confirm_request():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Да ✅"), KeyboardButton("Нет ❌"))
    return keyboard
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu():
    Keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    Keyboard.add(KeyboardButton("Правила хранения"), KeyboardButton("Мои заказы"))
    Keyboard.add(KeyboardButton("Уже храню вещи"), KeyboardButton("Хочу хранить вещи"))
    return Keyboard

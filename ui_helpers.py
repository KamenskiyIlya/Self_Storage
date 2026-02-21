from telebot.types import ReplyKeyboardMarkup, KeyboardButton


def warehouse_keyboard(warehouses):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    for warehouse in warehouses:
        keyboard.add(KeyboardButton(warehouse["name"]))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard


def options_keyboard(options, include_main_menu=True):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    for option in options:
        keyboard.add(KeyboardButton(option))
    if include_main_menu:
        keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

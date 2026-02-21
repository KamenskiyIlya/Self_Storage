from telebot.types import ReplyKeyboardMarkup, KeyboardButton


def warehouse_keyboard(warehouses):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    for warehouse in warehouses:
        keyboard.add(KeyboardButton(warehouse["name"]))
    keyboard.add(KeyboardButton("Вернуться в главное меню"))
    return keyboard

"""Клавиатуры бота.

Два вида:
- reply-клавиатура (внизу экрана, вместо обычной) — постоянные кнопки меню;
- inline-клавиатура (под сообщением) — действия с конкретным ответом: оценить, оставить заявку.

callback_data у inline-кнопок ограничена 64 байтами, поэтому передаём короткие
строки вида «rate:+:123» (действие:значение:id), а не JSON.
"""
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup)

BTN_LEAD = "📞 Связаться с менеджером"
BTN_HELP = "❓ Что ты умеешь"
BTN_CANCEL = "Отмена"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_LEAD)], [KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,                      # компактные кнопки, а не на пол-экрана
        input_field_placeholder="Задайте вопрос…",
    )


def answer_actions(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍", callback_data=f"rate:+:{message_id}"),
         InlineKeyboardButton(text="👎", callback_data=f"rate:-:{message_id}")],
    ])


def offer_lead() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_LEAD, callback_data="lead:start")],
    ])


def share_phone() -> ReplyKeyboardMarkup:
    # request_contact: Telegram сам подставит номер телефона пользователя — без опечаток
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
                  [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def cancel_only() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

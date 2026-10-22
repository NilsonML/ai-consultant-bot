"""Главный сценарий: /start, /help и ответы на вопросы по базе знаний.

Как сервисы попадают в обработчики: в main.py мы кладём их в диспетчер
(dp["rag"] = rag, dp["storage"] = storage), а aiogram 3 сам передаёт их
в функцию по имени параметра. Это называется внедрение зависимостей (dependency injection):
обработчики не создают сервисы сами, поэтому их легко подменить в тестах.
"""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..config import Settings
from ..middlewares import ThrottlingMiddleware
from ..rag import RAGService
from ..storage import Storage

log = logging.getLogger(__name__)

NOT_FOUND_TEXT = ("К сожалению, у меня нет точного ответа на этот вопрос. "
                  "Оставьте контакты — менеджер свяжется с вами и всё подскажет.")


async def cmd_start(message: Message, settings: Settings):
    await message.answer(
        f"Здравствуйте! Я консультант магазина «{html.escape(settings.company_name)}».\n\n"
        "Отвечу на вопросы о доставке, оплате, возврате и товарах. Просто напишите вопрос, например:\n"
        "<i>«Сколько стоит доставка?»</i>",
        reply_markup=kb.main_menu())


async def cmd_help(message: Message):
    await message.answer(
        "Я отвечаю на вопросы по информации от магазина: доставка, оплата, возврат, товары, уход.\n\n"
        "Если не найду ответ — помогу связаться с менеджером. "
        "Под каждым ответом есть 👍 и 👎 — оценки помогают мне становиться лучше.")


async def handle_question(message: Message, rag: RAGService, storage: Storage, settings: Settings):
    question = message.text.strip()[:1000]  # обрезаем: огромный текст = дорогой запрос
    user = message.from_user

    # Пока нейросеть думает, в чате видно «печатает…» — пользователь понимает, что бот жив
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        answer = await rag.answer(question, storage.history(user.id, settings.history_turns))
    except Exception:
        log.exception("Ошибка при ответе на вопрос")
        await message.answer("Не получилось ответить из-за технической ошибки. "
                             "Попробуйте ещё раз через минуту или свяжитесь с менеджером.",
                             reply_markup=kb.offer_lead())
        return

    msg_id = storage.log_message(user.id, user.username, question, answer.text,
                                 answer.found, answer.sources, answer.latency_ms)
    if not answer.found:
        await message.answer(NOT_FOUND_TEXT, reply_markup=kb.offer_lead())
        return
    # html.escape — ответ нейросети может содержать «<» или «&», которые сломают HTML-разметку
    await message.answer(html.escape(answer.text), reply_markup=kb.answer_actions(msg_id))


async def handle_rating(callback: CallbackQuery, storage: Storage):
    _, sign, msg_id = callback.data.split(":")
    ok = storage.rate(int(msg_id), callback.from_user.id, 1 if sign == "+" else -1)
    # Убираем кнопки, чтобы нельзя было голосовать повторно
    await callback.message.edit_reply_markup(reply_markup=kb.offer_lead() if sign == "-" else None)
    await callback.answer("Спасибо за оценку!" if ok else "")


def setup(throttle_seconds: float) -> Router:
    """Создаёт роутер вопросов. Фабрика, а не глобальный объект: роутер можно подключить
    только к одному диспетчеру, а в тестах мы собираем новый диспетчер на каждый тест."""
    router = Router(name="questions")
    # Антифлуд — только здесь: тормозим вопросы к нейросети, а не шаги заявки
    router.message.middleware(ThrottlingMiddleware(throttle_seconds))

    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_help, F.text == kb.BTN_HELP)
    # StateFilter(None) — вопрос обрабатывается, только когда человек не заполняет заявку.
    # Иначе имя «Анна» на шаге заявки ушло бы в нейросеть как вопрос.
    router.message.register(handle_question, StateFilter(None), F.text, ~F.text.startswith("/"))
    router.callback_query.register(handle_rating, F.data.startswith("rate:"))
    return router

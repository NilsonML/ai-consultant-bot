"""Точка входа: python -m bot.main

Здесь собираются все части бота. Порядок подключения роутеров важен —
aiogram проверяет их по очереди и отдаёт сообщение первому подходящему обработчику:
  1. admin   — команды владельца;
  2. leads   — шаги заявки и кнопка «Связаться с менеджером»;
  3. questions — всё остальное считается вопросом к базе знаний.
Если поставить questions первым, нажатие кнопки «Связаться с менеджером»
ушло бы в нейросеть как вопрос.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Settings
from .handlers import admin, leads, questions
from .rag import RAGService
from .storage import Storage


def build_dispatcher(settings: Settings, rag: RAGService, storage: Storage) -> Dispatcher:
    # MemoryStorage хранит шаги заявок в памяти: при перезапуске незаконченные заявки сбросятся.
    # Для одного бота это нормально; при масштабировании меняется на RedisStorage.
    dp = Dispatcher(storage=MemoryStorage())
    # Всё, что положено в dp[...], aiogram передаёт в обработчики по имени параметра
    dp["settings"], dp["rag"], dp["storage"] = settings, rag, storage

    dp.include_routers(admin.setup(settings.admin_ids), leads.setup(), questions.setup(settings.throttle_seconds))
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")

    storage = Storage(settings.db_path)
    rag = RAGService(settings)
    if not rag.llm.enabled:
        logging.warning("LLM_API_KEY не задан — демо-режим: бот показывает найденный фрагмент без нейросети")

    # parse_mode=HTML по умолчанию — во всех ответах работают <b>, <i>, ссылки
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # Меню команд, которое Telegram показывает по кнопке «/» рядом с полем ввода
    await bot.set_my_commands([BotCommand(command="start", description="Начать"),
                               BotCommand(command="help", description="Что умеет бот")])

    dp = build_dispatcher(settings, rag, storage)
    logging.info("Бот запущен")
    # drop_pending_updates: не отвечать на сообщения, которые пришли, пока бот был выключен
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())

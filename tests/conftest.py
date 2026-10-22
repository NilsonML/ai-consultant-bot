"""Общие заготовки для тестов.

FakeSession подменяет сетевой слой aiogram: вместо запросов к api.telegram.org
она складывает вызовы в список. Так мы прогоняем настоящий диспетчер с настоящими
роутерами, фильтрами и FSM, но без интернета и без реального бота.
"""
from __future__ import annotations

import itertools
from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from bot.config import Settings
from bot.llm import LLMClient
from bot.main import build_dispatcher
from bot.rag import RAGService
from bot.storage import Storage

KNOWLEDGE = Path(__file__).parent.parent / "knowledge"
ADMIN_ID, USER_ID = 1000, 2000


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self._ids = itertools.count(1)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            # Возвращаем объект сообщения, как это сделал бы Telegram
            return Message(message_id=next(self._ids), date=datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover — не используется
        yield b""

    async def close(self):
        pass

    def sent(self) -> list[SendMessage]:
        return [c for c in self.calls if isinstance(c, SendMessage)]


class FakeLLM(LLMClient):
    """Нейросеть-заглушка: отвечает по сценарию и запоминает, что ей отправили."""

    def __init__(self, reply: str = "Доставка бесплатная от 3000 рублей."):
        self.enabled, self.reply, self.requests = True, reply, []

    async def chat(self, messages, temperature=0.2):
        self.requests.append(messages)
        return self.reply


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.bot_token, s.admin_ids, s.leads_chat_id = "123456:TEST-token", {ADMIN_ID}, None
    s.llm_api_key, s.embedding_model = "", ""
    s.knowledge_dir, s.db_path, s.throttle_seconds = str(KNOWLEDGE), str(tmp_path / "t.db"), 0
    return s


@pytest.fixture
def env(settings):
    """Собранный бот: диспетчер + поддельная сессия + сервисы."""
    session = FakeSession()
    bot = Bot(settings.bot_token, session=session)
    llm = FakeLLM()
    storage = Storage(settings.db_path)
    rag = RAGService(settings, llm=llm)
    dp = build_dispatcher(settings, rag, storage)

    class Env:
        pass

    e = Env()
    e.bot, e.dp, e.session, e.llm, e.storage, e.rag = bot, dp, session, llm, storage, rag
    _update_ids = itertools.count(1)

    async def send(text=None, user_id=USER_ID, contact=None):
        """Имитирует сообщение от пользователя в личке с ботом."""
        msg = Message(message_id=next(_update_ids), date=datetime.now(),
                      chat=Chat(id=user_id, type="private"),
                      from_user=User(id=user_id, is_bot=False, first_name="Тест", username="tester"),
                      text=text, contact=contact)
        await dp.feed_update(bot, Update(update_id=next(_update_ids), message=msg))

    e.send = send
    return e

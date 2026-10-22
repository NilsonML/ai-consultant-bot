"""Middleware — код, который выполняется до каждого обработчика (как «фильтр на входе»).

ThrottlingMiddleware — защита от флуда: один пользователь не может слать вопросы чаще,
чем раз в N секунд. Каждый вопрос — это платный запрос к нейросети, и без ограничения
один шутник с автокликером может потратить заказчику весь баланс API за вечер.

Подключается только к роутеру с вопросами (см. main.py), а не ко всему боту:
шаги заявки (имя → телефон) человек может отправлять быстро, их тормозить не нужно.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.rate = rate_seconds
        self.clock = clock                       # подменяется в тестах
        self.last_seen: dict[int, float] = {}    # user_id → время последнего принятого сообщения
        self.warned: dict[int, float] = {}       # user_id → для какого last_seen уже предупредили

    async def __call__(self, handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
                       event: Message, data: dict[str, Any]) -> Any:
        user = event.from_user
        if user is None:
            return await handler(event, data)

        now = self.clock()
        last = self.last_seen.get(user.id)
        if last is not None and now - last < self.rate:
            # Предупреждаем один раз за «окно», иначе на 10 быстрых сообщений бот сам ответит 10 раз
            if self.warned.get(user.id) != last:
                self.warned[user.id] = last
                await event.answer("Не так быстро 🙂 Подождите пару секунд и задайте вопрос снова.")
            return None  # обработчик не вызывается, запрос к нейросети не тратится

        self.last_seen[user.id] = now
        if len(self.last_seen) > 10_000:
            # Чистим старые записи, чтобы словари не росли бесконечно на тысячах пользователей
            cutoff = now - 3600
            self.last_seen = {k: v for k, v in self.last_seen.items() if v > cutoff}
            self.warned = {k: v for k, v in self.warned.items() if k in self.last_seen}
        return await handler(event, data)

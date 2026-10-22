"""Команды для владельца бота: /reload, /stats, /leads.

Доступ проверяет фильтр F.from_user.id.in_(admin_ids), навешенный на весь роутер в setup():
для остальных пользователей эти команды просто не существуют (бот обработает их как обычный текст
или проигнорирует), поэтому посторонний даже не узнает, что админка есть.
"""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from ..rag import RAGService
from ..storage import Storage


def setup(admin_ids: set[int]) -> Router:
    router = Router(name="admin")
    router.message.filter(F.from_user.id.in_(admin_ids))

    @router.message(Command("reload"))
    async def cmd_reload(message: Message, rag: RAGService):
        # Заказчик обновил файлы в knowledge/ — переиндексируем без перезапуска бота
        n = rag.reload()
        await message.answer(f"База знаний перезагружена: {n} фрагментов.")

    @router.message(Command("stats"))
    async def cmd_stats(message: Message, storage: Storage):
        s = storage.stats(days=7)
        share = f"{s['not_found'] / s['total']:.0%}" if s["total"] else "—"
        lines = [
            "<b>Статистика за 7 дней</b>",
            f"Вопросов: {s['total']} от {s['users']} пользователей",
            f"Без ответа: {s['not_found']} ({share})",
            f"Оценки: 👍 {s['likes']}  👎 {s['dislikes']}",
            f"Заявок: {s['leads']}",
            f"Среднее время ответа: {s['avg_ms'] / 1000:.1f} с",
        ]
        if s["unanswered"]:
            # Это готовый список того, что стоит добавить в базу знаний
            lines += ["", "<b>Вопросы без ответа:</b>"] + [f"• {html.escape(q)}" for q in s["unanswered"]]
        await message.answer("\n".join(lines))

    @router.message(Command("leads"))
    async def cmd_leads(message: Message, storage: Storage):
        leads = storage.recent_leads(10)
        if not leads:
            await message.answer("Заявок пока нет.")
            return
        lines = ["<b>Последние заявки</b>"] + [
            f"#{l['id']} {l['created_at'][:16]} — {html.escape(l['name'])}, {l['phone']}" for l in leads]
        await message.answer("\n".join(lines))

    return router

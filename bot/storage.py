"""SQLite: журнал диалогов, оценки ответов и заявки.

Зачем журнал: заказчик видит, о чём спрашивают клиенты, и на какие вопросы бот
не нашёл ответа (found = 0). Это готовый список того, что нужно дописать в базу знаний.

aiosqlite не используем намеренно: запросы здесь занимают доли миллисекунды и не блокируют
бота заметно, а лишняя асинхронная зависимость усложнила бы код для новичка.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    username   TEXT,
    question   TEXT NOT NULL,
    answer     TEXT,
    found      INTEGER NOT NULL,          -- 1 — ответ найден в базе, 0 — нет
    sources    TEXT,
    latency_ms INTEGER,
    rating     INTEGER,                   -- 1 👍, -1 👎, NULL — не оценили
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, id);

CREATE TABLE IF NOT EXISTS leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    username   TEXT,
    name       TEXT NOT NULL,
    phone      TEXT NOT NULL,
    question   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class Storage:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: aiogram может вызывать обработчики из разных задач asyncio
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def log_message(self, user_id: int, username: str | None, question: str, answer: str,
                    found: bool, sources: list[str], latency_ms: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO messages (user_id, username, question, answer, found, sources, latency_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, question, answer, int(found), ", ".join(sources), latency_ms))
        return cur.lastrowid  # id нужен, чтобы привязать к ответу кнопки оценки

    def rate(self, message_id: int, user_id: int, rating: int) -> bool:
        # user_id в условии — чтобы нельзя было оценить чужой ответ, подделав callback
        with self.conn:
            cur = self.conn.execute("UPDATE messages SET rating = ? WHERE id = ? AND user_id = ?",
                                    (rating, message_id, user_id))
        return cur.rowcount == 1

    def history(self, user_id: int, turns: int) -> list[tuple[str, str]]:
        """Последние вопросы-ответы пользователя для контекста диалога (старые → новые)."""
        rows = self.conn.execute(
            "SELECT question, answer FROM messages WHERE user_id = ? AND found = 1 "
            "AND created_at > datetime('now', '-30 minutes') ORDER BY id DESC LIMIT ?",
            (user_id, turns)).fetchall()
        return [(r["question"], r["answer"]) for r in reversed(rows)]

    def last_question(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT question FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                                (user_id,)).fetchone()
        return row["question"] if row else None

    def add_lead(self, user_id: int, username: str | None, name: str, phone: str, question: str | None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO leads (user_id, username, name, phone, question) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, name, phone, question))
        return cur.lastrowid

    def recent_leads(self, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def stats(self, days: int = 7) -> dict:
        """Сводка для /stats: сколько вопросов, какая доля без ответа, оценки, заявки."""
        since = f"-{int(days)} days"
        q = self.conn.execute(
            "SELECT COUNT(*) total, COUNT(DISTINCT user_id) users, SUM(found = 0) not_found, "
            "SUM(rating = 1) likes, SUM(rating = -1) dislikes, CAST(AVG(latency_ms) AS INTEGER) avg_ms "
            "FROM messages WHERE created_at > datetime('now', ?)", (since,)).fetchone()
        leads = self.conn.execute("SELECT COUNT(*) FROM leads WHERE created_at > datetime('now', ?)",
                                  (since,)).fetchone()[0]
        unanswered = [r[0] for r in self.conn.execute(
            "SELECT question FROM messages WHERE found = 0 AND created_at > datetime('now', ?) "
            "ORDER BY id DESC LIMIT 5", (since,)).fetchall()]
        return {**{k: (q[k] or 0) for k in q.keys()}, "leads": leads, "unanswered": unanswered}

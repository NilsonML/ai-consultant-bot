"""Настройки бота из переменных окружения (файл .env).

Почему .env, а не константы в коде: токены — секреты, их нельзя коммитить в GitHub.
А ещё один и тот же код без изменений запускается у разных заказчиков — меняется только .env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _ids(value: str | None) -> set[int]:
    """«123, 456» → {123, 456}. Пустые и мусорные значения пропускаем."""
    return {int(x) for x in (value or "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    # Кто может пользоваться админ-командами (/reload, /stats, /leads)
    admin_ids: set[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS")))
    # Куда отправлять заявки: id менеджера или группы. По умолчанию — первому админу
    leads_chat_id: int | None = field(default_factory=lambda: int(os.getenv("LEADS_CHAT_ID")) if os.getenv("LEADS_CHAT_ID") else None)

    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", ""))  # пусто — только BM25

    company_name: str = field(default_factory=lambda: os.getenv("COMPANY_NAME", "CozyHome"))
    knowledge_dir: str = field(default_factory=lambda: os.getenv("KNOWLEDGE_DIR", "knowledge"))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/bot.db"))

    top_k: int = 4             # сколько фрагментов базы передавать нейросети
    history_turns: int = 3     # сколько прошлых вопросов-ответов помнить в диалоге
    throttle_seconds: float = 2.0  # не чаще одного вопроса в N секунд от одного человека

    @property
    def leads_target(self) -> int | None:
        return self.leads_chat_id or (min(self.admin_ids) if self.admin_ids else None)

"""Работа с нейросетью через OpenAI-совместимый API (OpenAI, OpenRouter, YandexGPT, Ollama…).

Клиент асинхронный (AsyncOpenAI): бот на aiogram работает в одном потоке на asyncio,
и синхронный запрос к нейросети на 5–10 секунд «заморозил» бы ответы всем остальным
пользователям. С async пока один ждёт нейросеть, бот обслуживает других.
"""
from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from .config import Settings


class LLMClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.enabled = bool(settings.llm_api_key)
        if self.enabled:
            self.client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    async def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        # Низкая температура (0.2): консультанту нужна точность, а не креативность
        resp = await self.client.chat.completions.create(
            model=self.s.llm_model, messages=messages, temperature=temperature)
        return (resp.choices[0].message.content or "").strip()


def make_embed_fn(settings: Settings):
    """Функция для эмбеддингов или None, если они не настроены.

    Синхронная — потому что индексация базы происходит один раз при запуске и /reload,
    а не на каждый вопрос. Запросы идут пачками по 64 текста — так быстрее и дешевле.
    """
    if not (settings.llm_api_key and settings.embedding_model):
        return None
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            resp = client.embeddings.create(model=settings.embedding_model, input=texts[i:i + 64])
            out += [d.embedding for d in resp.data]
        return out

    return embed

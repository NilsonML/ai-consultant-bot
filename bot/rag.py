"""RAG (Retrieval-Augmented Generation) — «ответ с подглядыванием в документы».

Схема на каждый вопрос:
  1. Retrieval: находим в базе знаний 3–5 подходящих фрагментов (kb/retriever.py).
  2. Augmentation: кладём их в промпт вместе с вопросом и историей диалога.
  3. Generation: нейросеть отвечает ТОЛЬКО по этим фрагментам.

Главная проблема RAG — галлюцинации: модель «додумывает» ответ, которого нет в документах
(например, обещает скидку, которой не существует). Защита:
  - в промпте жёсткое правило отвечать только по контексту;
  - если ответа нет, модель обязана вернуть маркер NO_ANSWER — тогда бот не выдумывает,
    а предлагает связаться с менеджером. Этот путь бизнесу выгоден: вопрос → заявка.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import Settings
from .kb.chunker import Chunk, chunk_documents
from .kb.loader import load_documents
from .kb.retriever import BM25Retriever, EmbeddingRetriever, HybridRetriever
from .llm import LLMClient, make_embed_fn

log = logging.getLogger(__name__)

NO_ANSWER = "NO_ANSWER"

SYSTEM_PROMPT = """Ты — вежливый консультант компании «{company}» в Telegram.
Отвечай на вопросы клиентов ТОЛЬКО на основе фрагментов базы знаний ниже.

Правила:
- Если в фрагментах нет ответа на вопрос — ответь ровно одним словом {no_answer}. Не придумывай.
- Не обещай скидок, сроков и условий, которых нет во фрагментах.
- Отвечай кратко: 1–4 предложения или короткий список. Без markdown-разметки и без эмодзи.
- Обращайся на «вы». Не упоминай «фрагменты» и «базу знаний» — просто отвечай.

Фрагменты базы знаний:
{context}"""


@dataclass
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)  # файлы, из которых взят ответ
    found: bool = True       # False — ответа в базе нет, предлагаем менеджера
    latency_ms: int = 0


class RAGService:
    def __init__(self, settings: Settings, llm: LLMClient | None = None):
        self.s = settings
        self.llm = llm or LLMClient(settings)
        self.chunks: list[Chunk] = []
        self.retriever = None
        self.reload()

    def reload(self) -> int:
        """(Пере)индексирует базу знаний. Возвращает число фрагментов."""
        docs = load_documents(self.s.knowledge_dir)
        self.chunks = chunk_documents(docs)
        bm25 = BM25Retriever(self.chunks)
        embed_fn = make_embed_fn(self.s)
        if embed_fn and self.chunks:
            try:
                self.retriever = HybridRetriever(bm25, EmbeddingRetriever(self.chunks, embed_fn))
            except Exception as e:  # эмбеддинги упали (нет денег на счёте, сеть) — работаем на BM25
                log.warning("Эмбеддинги недоступны, только BM25: %s", e)
                self.retriever = bm25
        else:
            self.retriever = bm25
        log.info("База знаний: %d документов, %d фрагментов, поиск: %s",
                 len(docs), len(self.chunks), type(self.retriever).__name__)
        return len(self.chunks)

    @staticmethod
    def _format_context(found: list[tuple[Chunk, float]]) -> str:
        return "\n\n---\n\n".join(f"[{c.source} | {c.title}]\n{c.text}" for c, _ in found)

    async def answer(self, question: str, history: list[tuple[str, str]] | None = None) -> Answer:
        started = time.monotonic()
        history = (history or [])[-self.s.history_turns:]
        # Уточняющий вопрос («а сколько это стоит?») сам по себе не содержит темы разговора.
        # Поэтому ищем по предыдущему вопросу + текущему: тема берётся из первого, уточнение из второго.
        search_query = f"{history[-1][0]} {question}" if history else question
        found = self.retriever.search(search_query, self.s.top_k) if self.retriever else []
        if not found:
            return Answer("", found=False, latency_ms=int((time.monotonic() - started) * 1000))

        if not self.llm.enabled:
            # Демо-режим без ключа: показываем лучший найденный фрагмент как есть
            chunk = found[0][0]
            return Answer(f"Вот что я нашёл по вашему вопросу:\n\n{chunk.text}", [chunk.source],
                          latency_ms=int((time.monotonic() - started) * 1000))

        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(
            company=self.s.company_name, no_answer=NO_ANSWER, context=self._format_context(found))}]
        # История нужна для уточняющих вопросов: «а сколько это стоит?» после вопроса про доставку
        for q, a in history:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": question})

        text = await self.llm.chat(messages)
        latency = int((time.monotonic() - started) * 1000)
        if NO_ANSWER in text or not text:
            return Answer("", found=False, latency_ms=latency)
        # Источники без повторов, в порядке релевантности
        sources = list(dict.fromkeys(c.source for c, _ in found))
        return Answer(text, sources, latency_ms=latency)

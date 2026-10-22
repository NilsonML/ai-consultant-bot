"""Нарезка документов на фрагменты (чанки).

Зачем резать: в запрос к нейросети нельзя положить всю базу знаний — это дорого
и модель теряется в большом контексте. Поэтому мы делим документы на небольшие
фрагменты, находим 3–5 самых подходящих к вопросу и отдаём модели только их.

Как режем:
1. Сначала по заголовкам Markdown (#, ##) — раздел обычно посвящён одной теме,
   и заголовок сохраняется в каждом фрагменте как контекст («Доставка → Сроки»).
2. Длинные разделы — по абзацам, набирая фрагмент до max_chars символов.
3. Между соседними фрагментами оставляем перекрытие (overlap) — последний абзац
   предыдущего фрагмента. Так ответ, который попал на границу, не разрывается.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .loader import Document

HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.M)


@dataclass
class Chunk:
    id: int
    source: str   # файл
    title: str    # путь заголовков, например «Доставка › Сроки»
    text: str

    @property
    def search_text(self) -> str:
        # Заголовок участвует в поиске: вопрос «сколько идёт доставка» найдёт раздел «Доставка»,
        # даже если в самом абзаце слово «доставка» не встречается.
        return f"{self.title}\n{self.text}"


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Делит Markdown на (путь заголовков, текст раздела)."""
    sections, stack = [], []  # stack хранит текущую цепочку заголовков по уровням
    last_end, last_title = 0, ""
    for m in HEADING.finditer(text):
        body = text[last_end:m.start()].strip()
        if body:
            sections.append((last_title, body))
        level, title = len(m.group(1)), m.group(2).strip()
        stack = stack[: level - 1] + [title]  # заголовок уровня N заменяет всё, что глубже
        last_title, last_end = " › ".join(stack), m.end()
    tail = text[last_end:].strip()
    if tail:
        sections.append((last_title, tail))
    return sections


def chunk_documents(docs: list[Document], max_chars: int = 900, overlap: bool = True) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        for title, body in _split_sections(doc.text):
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
            current: list[str] = []
            for para in paragraphs:
                # Абзац сам по себе длиннее лимита — режем по предложениям
                pieces = [para] if len(para) <= max_chars else re.split(r"(?<=[.!?])\s+", para)
                for piece in pieces:
                    if current and len("\n\n".join(current + [piece])) > max_chars:
                        chunks.append(Chunk(len(chunks), doc.source, title, "\n\n".join(current)))
                        current = [current[-1]] if overlap and len(current[-1]) < max_chars // 2 else []
                    current.append(piece)
            if current:
                chunks.append(Chunk(len(chunks), doc.source, title, "\n\n".join(current)))
    return chunks

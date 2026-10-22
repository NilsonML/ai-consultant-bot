"""Поиск фрагментов, подходящих к вопросу.

Два способа с одинаковым интерфейсом search(query, k) → [(chunk, score)]:

BM25Retriever — классический поиск по словам (так работали поисковики до нейросетей).
  + работает без API-ключа и бесплатно, мгновенно;
  + хорошо находит точные термины: артикулы, названия тарифов, «ИНН», «самовывоз»;
  − не понимает синонимы: «сколько везут» не совпадёт с «сроки доставки».

EmbeddingRetriever — смысловой поиск: текст превращается в вектор (эмбеддинг),
  близкие по смыслу тексты дают близкие векторы.
  + понимает перефразирование и синонимы;
  − нужен API и небольшие деньги на индексацию.

HybridRetriever объединяет оба — так делают в продакшене: точные совпадения от BM25
плюс смысл от эмбеддингов. Используется автоматически, если есть ключ API.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .chunker import Chunk

# Частые слова, которые есть почти в каждом тексте и только мешают поиску
STOP_WORDS = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только
ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до
вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем
была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним
здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой
хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой
перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это как ли вам ваш ваша ваши""".split())


def tokenize(text: str) -> list[str]:
    """Слова в нижнем регистре, без стоп-слов, обрезанные до «основы».

    Обрезка до 6 букв — грубая замена морфологическому анализу: «доставка», «доставки»,
    «доставку» превращаются в одно «достав». Для FAQ этого хватает, а pymorphy
    добавил бы тяжёлую зависимость.
    """
    words = re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))
    return [w[:6] for w in words if w not in STOP_WORDS and len(w) > 1]


class BM25Retriever:
    """BM25 — улучшенный TF-IDF: учитывает, как часто слово встречается во фрагменте (k1)
    и делает поправку на длину фрагмента (b), чтобы длинные тексты не выигрывали всегда."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [Counter(tokenize(c.search_text)) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0
        # IDF: редкое слово (встречается в немногих фрагментах) весит больше частого
        df = Counter(word for d in self.docs for word in d)
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        scores = []
        for i, doc in enumerate(self.docs):
            s = 0.0
            for t in terms:
                if t in doc:
                    tf = doc[t]
                    norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg_len or 1))
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + norm)
            scores.append(s)
        order = np.argsort(scores)[::-1][:k]
        return [(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]


class EmbeddingRetriever:
    def __init__(self, chunks: list[Chunk], embed_fn):
        """embed_fn(list[str]) → list[list[float]] — функция из llm.py."""
        self.chunks, self.embed_fn = chunks, embed_fn
        vectors = np.array(embed_fn([c.search_text for c in chunks]), dtype=np.float32)
        # Нормируем векторы один раз: тогда косинусная близость = обычное скалярное произведение
        self.matrix = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        q = np.array(self.embed_fn([query])[0], dtype=np.float32)
        sims = self.matrix @ (q / np.linalg.norm(q))
        order = np.argsort(sims)[::-1][:k]
        return [(self.chunks[i], float(sims[i])) for i in order]


class HybridRetriever:
    """Объединяет выдачу двух поисковиков методом Reciprocal Rank Fusion (RRF).

    Баллы BM25 и косинусная близость в разных шкалах — складывать их напрямую нельзя.
    RRF смотрит только на места в выдаче: фрагмент на 1-м месте получает 1/(60+1),
    на 2-м — 1/(60+2) и т.д. Фрагмент, который высоко у обоих, побеждает.
    """

    def __init__(self, *retrievers, rrf_k: int = 60):
        self.retrievers, self.rrf_k = retrievers, rrf_k

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        scores: dict[int, float] = {}
        by_id: dict[int, Chunk] = {}
        for r in self.retrievers:
            for rank, (chunk, _) in enumerate(r.search(query, k * 3), start=1):
                scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (self.rrf_k + rank)
                by_id[chunk.id] = chunk
        best = sorted(scores, key=scores.get, reverse=True)[:k]
        return [(by_id[i], scores[i]) for i in best]

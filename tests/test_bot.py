from datetime import datetime

import numpy as np
import pytest
from aiogram.types import Contact

from bot.handlers.leads import normalize_phone
from bot.kb.chunker import chunk_documents
from bot.kb.loader import Document, load_documents
from bot.kb.retriever import BM25Retriever, EmbeddingRetriever, HybridRetriever, tokenize
from bot.middlewares import ThrottlingMiddleware
from bot.rag import NO_ANSWER, RAGService
from conftest import ADMIN_ID, KNOWLEDGE, USER_ID, FakeLLM


# ---------- база знаний ----------
def test_chunker_keeps_heading_path_and_limits_size():
    doc = Document("faq.md", "# Доставка\n\n## Сроки\n\n" + "\n\n".join(["Абзац про сроки. " * 10] * 8))
    chunks = chunk_documents([doc], max_chars=400)
    assert len(chunks) > 1
    assert all(c.title == "Доставка › Сроки" for c in chunks)
    assert all(len(c.text) <= 400 for c in chunks)


def test_tokenize_merges_word_forms():
    assert tokenize("Доставка доставки доставку") == ["достав"] * 3


async def test_bm25_finds_right_section():
    chunks = chunk_documents(load_documents(KNOWLEDGE))
    top = BM25Retriever(chunks).search("можно ли оплатить наличными", 1)[0][0]
    assert top.title.endswith("Оплата")


async def test_hybrid_merges_rankings():
    chunks = chunk_documents(load_documents(KNOWLEDGE))
    # Фейковые эмбеддинги: вектор «смысла» = есть ли в тексте слово «стирать»
    fake_embed = lambda texts: [[1.0, 0.0] if "стира" in t.lower() else [0.0, 1.0] for t in texts]  # noqa: E731
    hybrid = HybridRetriever(BM25Retriever(chunks), EmbeddingRetriever(chunks, fake_embed))
    assert "Уход" in hybrid.search("как стирать плед", 1)[0][0].title


# ---------- RAG ----------
async def test_rag_demo_mode_returns_best_fragment(settings):
    rag = RAGService(settings)  # без ключа → демо-режим
    ans = await rag.answer("какая стоимость доставки")
    assert ans.found and "3000 рублей" in ans.text and ans.sources == ["delivery.md"]


async def test_bm25_synonym_limit_is_covered_by_top_k(settings):
    # «стоит» и «стоимость» для поиска по словам — разные слова, поэтому первым может
    # оказаться соседний раздел. Нейросеть получает top_k фрагментов, и нужный среди них.
    rag = RAGService(settings)
    titles = [c.title for c, _ in rag.retriever.search("сколько стоит доставка", settings.top_k)]
    assert any("Стоимость доставки" in t for t in titles)


async def test_rag_no_answer_marker(settings):
    rag = RAGService(settings, llm=FakeLLM(reply=NO_ANSWER))
    assert (await rag.answer("какой курс доллара")).found is False


async def test_rag_sends_context_and_history(settings):
    llm = FakeLLM()
    rag = RAGService(settings, llm=llm)
    await rag.answer("а сколько стоит?", history=[("как доставляете?", "Курьером и в ПВЗ.")])
    msgs = llm.requests[0]
    assert "Стоимость доставки" in msgs[0]["content"]          # контекст из базы в системном промпте
    assert msgs[1] == {"role": "user", "content": "как доставляете?"}  # история диалога
    assert msgs[-1]["content"] == "а сколько стоит?"


# ---------- вспомогательное ----------
@pytest.mark.parametrize("raw,expected", [
    ("+7 (900) 123-45-67", "+79001234567"), ("89001234567", "+79001234567"),
    ("9001234567", "+79001234567"), ("+49 151 2345 6789", "+4915123456789"), ("позвоните", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


async def test_throttling_blocks_and_warns_once():
    now = {"t": 100.0}
    mw = ThrottlingMiddleware(2.0, clock=lambda: now["t"])
    calls, warnings = [], []

    class Msg:
        from_user = type("U", (), {"id": 1})()
        async def answer(self, text): warnings.append(text)  # noqa: E704

    handler = lambda e, d: calls.append(1) or _done()  # noqa: E731

    async def _done():
        return None

    for _ in range(3):
        await mw(handler, Msg(), {})
    now["t"] += 2.5
    await mw(handler, Msg(), {})
    assert len(calls) == 2 and len(warnings) == 1


# ---------- сквозные сценарии через настоящий диспетчер ----------
async def test_start_shows_menu(env):
    await env.send("/start")
    sent = env.session.sent()
    assert "консультант" in sent[0].text and sent[0].reply_markup is not None


async def test_question_answered_and_logged(env):
    await env.send("сколько стоит доставка?")
    reply = env.session.sent()[-1]
    assert reply.text == "Доставка бесплатная от 3000 рублей."
    assert reply.reply_markup.inline_keyboard[0][0].callback_data.startswith("rate:+:")
    assert env.storage.stats()["total"] == 1


async def test_unknown_question_offers_manager(env):
    env.llm.reply = NO_ANSWER
    await env.send("какая погода в Москве?")
    reply = env.session.sent()[-1]
    assert "менеджер" in reply.text and reply.reply_markup.inline_keyboard[0][0].callback_data == "lead:start"


async def test_lead_flow_saves_and_notifies_admin(env):
    await env.send("можно оплатить при получении?")
    await env.send("📞 Связаться с менеджером")
    await env.send("Анна")              # имя — НЕ должно уйти в нейросеть как вопрос
    await env.send("8 900 123 45 67")
    assert len(env.llm.requests) == 1   # нейросеть вызывалась только для вопроса, не для имени и телефона

    lead = env.storage.recent_leads()[0]
    assert (lead["name"], lead["phone"], lead["question"]) == ("Анна", "+79001234567", "можно оплатить при получении?")
    to_admin = [m for m in env.session.sent() if m.chat_id == ADMIN_ID]
    assert to_admin and "Заявка #1" in to_admin[0].text and "+79001234567" in to_admin[0].text


async def test_lead_accepts_shared_contact(env):
    await env.send("📞 Связаться с менеджером")
    await env.send("Олег")
    await env.send(contact=Contact(phone_number="79991112233", first_name="Олег", user_id=USER_ID))
    assert env.storage.recent_leads()[0]["phone"] == "+79991112233"


async def test_admin_commands_hidden_from_users(env):
    await env.send("/stats", user_id=USER_ID)
    assert not any("Статистика" in m.text for m in env.session.sent())
    await env.send("/stats", user_id=ADMIN_ID)
    assert "Статистика за 7 дней" in env.session.sent()[-1].text

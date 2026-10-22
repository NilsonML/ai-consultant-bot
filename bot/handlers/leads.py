"""Сбор заявки «Связаться с менеджером» — пошаговый диалог на конечном автомате (FSM).

FSM (finite state machine) — бот помнит, на каком шаге диалога находится человек:
  нет состояния → [кнопка] → waiting_name → [ввёл имя] → waiting_phone → [номер] → заявка готова.
Без FSM бот не отличил бы имя «Анна» от вопроса «Анна» и отправил бы его в нейросеть.

Заявка сохраняется в базу и сразу пересылается менеджеру в Telegram —
для бизнеса это главная ценность бота: вопрос, на который нет ответа, превращается в клиента.
"""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..config import Settings
from ..storage import Storage

log = logging.getLogger(__name__)


class LeadForm(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


def normalize_phone(raw: str) -> str | None:
    """Приводит российский номер к виду +7XXXXXXXXXX. Возвращает None, если это не номер."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":  # ввели без кода страны: 9001234567
        return "+7" + digits
    if 11 <= len(digits) <= 15 and raw.strip().startswith("+"):  # иностранный номер — принимаем как есть
        return "+" + digits
    return None


async def _start(message: Message, state: FSMContext):
    await state.set_state(LeadForm.waiting_name)
    await message.answer("Как к вам обращаться?", reply_markup=kb.cancel_only())


async def lead_from_menu(message: Message, state: FSMContext):
    await _start(message, state)


async def lead_from_button(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await _start(callback.message, state)


async def lead_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Хорошо, отменил. Можете задать вопрос.", reply_markup=kb.main_menu())


async def lead_name(message: Message, state: FSMContext):
    name = message.text.strip()[:100]
    if len(name) < 2:
        await message.answer("Напишите, пожалуйста, имя.")
        return
    await state.update_data(name=name)  # данные заявки хранятся в FSM между шагами
    await state.set_state(LeadForm.waiting_phone)
    await message.answer(f"{html.escape(name)}, оставьте номер телефона — "
                         "нажмите кнопку ниже или напишите его.", reply_markup=kb.share_phone())


async def lead_phone(message: Message, state: FSMContext, storage: Storage, settings: Settings, bot: Bot):
    raw = message.contact.phone_number if message.contact else message.text
    phone = normalize_phone(raw)
    if not phone:
        await message.answer("Не похоже на номер телефона. Пример: +7 900 123-45-67")
        return

    data = await state.get_data()
    user = message.from_user
    question = storage.last_question(user.id)  # контекст для менеджера: о чём человек спрашивал
    lead_id = storage.add_lead(user.id, user.username, data["name"], phone, question)
    await state.clear()
    await message.answer("Спасибо! Менеджер свяжется с вами в рабочее время.", reply_markup=kb.main_menu())

    if settings.leads_target:
        username = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">профиль</a>'
        text = (f"🆕 <b>Заявка #{lead_id}</b>\n"
                f"Имя: {html.escape(data['name'])}\nТелефон: {phone}\nTelegram: {username}")
        if question:
            text += f"\nПоследний вопрос: <i>{html.escape(question)}</i>"
        try:
            await bot.send_message(settings.leads_target, text)
        except Exception:
            # Менеджер мог не запустить бота (бот не может писать первым) — заявка всё равно в базе
            log.exception("Не удалось отправить заявку менеджеру — она сохранена в базе")
    else:
        log.warning("ADMIN_IDS / LEADS_CHAT_ID не заданы — заявка #%s сохранена только в базе", lead_id)


def setup() -> Router:
    router = Router(name="leads")
    router.message.register(lead_from_menu, F.text == kb.BTN_LEAD)
    router.callback_query.register(lead_from_button, F.data == "lead:start")
    # Отмена работает на любом шаге: StateFilter(LeadForm) — «в любом из состояний формы».
    # Регистрируется раньше шагов, иначе слово «Отмена» записалось бы как имя.
    router.message.register(lead_cancel, StateFilter(LeadForm), F.text == kb.BTN_CANCEL)
    router.message.register(lead_name, LeadForm.waiting_name, F.text)
    router.message.register(lead_phone, LeadForm.waiting_phone, F.contact | F.text)
    return router

# Образ = «коробка» с Python, зависимостями и кодом бота. Запускается одинаково на любом сервере.

# slim — облегчённый Debian с Python: ~50 МБ вместо ~350 МБ у полного образа
FROM python:3.12-slim

# Не создавать .pyc-файлы и сразу выводить логи (без буферизации) — иначе docker logs отстают
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала копируем ТОЛЬКО requirements.txt и ставим зависимости.
# Docker кеширует каждый шаг: пока requirements.txt не меняется, при пересборке
# этот долгий шаг берётся из кеша, и правка кода пересобирается за секунды.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Теперь код и база знаний
COPY bot ./bot
COPY knowledge ./knowledge

# Запуск от обычного пользователя, а не root: если в боте найдут уязвимость,
# злоумышленник не получит права администратора внутри контейнера
# uid 1000 задан явно: под ним папке data на сервере выдаются права (см. README → Деплой на VPS)
RUN useradd --create-home --uid 1000 botuser && mkdir -p /app/data && chown -R botuser /app
USER botuser

CMD ["python", "-m", "bot.main"]

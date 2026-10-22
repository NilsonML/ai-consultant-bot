"""Загрузка документов базы знаний из папки.

Заказчик кладёт в папку knowledge/ свои файлы (FAQ, условия доставки, прайс…),
бот читает их при запуске и по команде /reload. Поддерживаем самые частые форматы:
.md, .txt и .pdf. Добавить новый формат = написать ещё одну функцию-читалку
и зарегистрировать её в READERS.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Document:
    source: str  # имя файла — показываем пользователю как источник ответа
    text: str


def _read_text(path: Path) -> str:
    # utf-8-sig корректно читает и обычный UTF-8, и файлы из Блокнота Windows с BOM
    return path.read_text(encoding="utf-8-sig")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader  # импорт внутри: pypdf нужен только если есть PDF
    reader = PdfReader(str(path))
    # extract_text() может вернуть None для страниц-картинок (сканов) — их пропускаем
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


READERS = {".md": _read_text, ".txt": _read_text, ".pdf": _read_pdf}


def load_documents(folder: str | Path) -> list[Document]:
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Папка базы знаний не найдена: {folder}")

    docs: list[Document] = []
    for path in sorted(folder.rglob("*")):  # rglob — включая вложенные папки
        reader = READERS.get(path.suffix.lower())
        if not reader or not path.is_file():
            continue
        try:
            text = reader(path).strip()
        except Exception as e:  # один битый файл не должен ронять весь бот
            log.warning("Не удалось прочитать %s: %s", path, e)
            continue
        if text:
            docs.append(Document(source=str(path.relative_to(folder)), text=text))
        else:
            log.warning("Файл %s пустой (или PDF-скан без текстового слоя)", path)
    return docs

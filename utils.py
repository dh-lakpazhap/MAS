# utils.py
"""Вспомогательные функции, общие для всех агентов.

- Загрузка и сохранение конфигурации (YAML)
- Простое файловое хранилище истории (JSON)
- Настройка общего логгера
- Диапазоны дат и разбор тем на слова
- Загрузка и очистка текста страницы по URL
"""

import calendar
import json
import re
import requests
from bs4 import BeautifulSoup
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import yaml

# ---------------------------------------------------------------------------
# Базовые пути
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
HISTORY_PATH = BASE_DIR / "history.json"

# ---------------------------------------------------------------------------
# Логгер (один общий экземпляр)
# ---------------------------------------------------------------------------
LOG_PATH = BASE_DIR / "main.log"
logger = logging.getLogger("archiv_tracker")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    # Дублируем журнал в файл main.log – его последние строки видны в интерфейсе
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# ---------------------------------------------------------------------------
# Работа с конфигурацией
# ---------------------------------------------------------------------------
def load_config() -> Dict:
    """Загрузить конфигурацию из ``config.yaml``.

    Если файла нет, возвращает пустой словарь.
    """
    if not CONFIG_PATH.is_file():
        logger.warning(f"Config file not found at {CONFIG_PATH}, returning empty config.")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_model_settings(engine: str, providers: Dict) -> None:
    """Записать активного провайдера и блоки ``llm`` / ``providers`` в config.yaml."""
    providers = {name: {k: v for k, v in values.items() if k in ("base_url", "model", "api_key_env")} for name, values in providers.items()}
    active = providers.get(engine) or providers.get("ollama") or {}
    public_llm = {key: value for key, value in active.items() if value}
    block = "# Чем строить резюме: sumy, ollama или openai.\n" + yaml.safe_dump(
        {"summarizer": engine, "llm": public_llm, "providers": providers},
        allow_unicode=True,
        sort_keys=False,
    )
    text = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.is_file() else ""
    pattern = re.compile(r"(?ms)^# Чем строить резюме:.*?(?=^# How often to check|\Z)")
    if pattern.search(text):
        text = pattern.sub(block + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + block
    CONFIG_PATH.write_text(text, encoding="utf-8")


def _subtract_months(day: date, months: int) -> date:
    """Сдвинуть дату на ``months`` назад, ограничив день длиной целевого месяца."""
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def resolve_date_range(
    preset: str = "week",
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    today: Optional[date] = None,
) -> Tuple[date, date]:
    """Включительные границы поиска.

    ``week`` — с даты 7 дней назад по сегодня, ``month`` — с этой даты
    месяц назад по сегодня. Если заданы обе границы, используется они.
    Иначе по умолчанию берётся неделя.
    """
    today = today or date.today()
    if date_from is not None and date_to is not None:
        return date_from, date_to
    if preset == "month":
        return _subtract_months(today, 1), today
    return today - timedelta(days=7), today


def topic_tokens(topic: str) -> List[str]:
    """Разбить тему так же, как поиск arxiv.org: по пробелам и дефисам.

    «medical multi-agent» → medical, multi, agent. Порядок слов не важен.
    """
    return [part for part in re.split(r"[\s\-]+", topic.strip()) if part]


def token_is_exact(token: str) -> bool:
    """Короткие аббревиатуры вроде RAG ищутся целиком, без префикса."""
    letters = re.sub(r"[^A-Za-z]", "", token)
    return bool(letters) and letters.isupper() and len(letters) <= 5

# ---------------------------------------------------------------------------
# Простой извлекатель текста из HTML (для валидатора и оркестратора)
# ---------------------------------------------------------------------------
def extract_text_from_url(url: str) -> str:
    """Загрузить *url* и вернуть видимый текст страницы.

    Удаляет теги ``script``, ``style`` и ``noscript`` и склеивает оставшиеся
    строки. При ошибке возвращает пустую строку.
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return " ".join(soup.stripped_strings)
    except Exception as e:
        logger.error(f"Failed to extract text from {url}: {e}")
        return ""

# ---------------------------------------------------------------------------
# История (хранит обработанные идентификаторы, чтобы не было повторов)
# ---------------------------------------------------------------------------
def _ensure_history_file():
    if not HISTORY_PATH.is_file():
        HISTORY_PATH.write_text(json.dumps([]), encoding="utf-8")

def get_history() -> List[str]:
    """Вернуть список ранее обработанных идентификаторов."""
    _ensure_history_file()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Failed to read history file: {e}")
        return []

def update_history(new_ids: List[str]):
    """Дописать новые идентификаторы в файл истории с учётом ``history_size``."""
    cfg = load_config()
    max_size = cfg.get("history_size", 1000)
    history = get_history()
    history.extend(new_ids)
    # Убираем дубликаты, сохраняя порядок (новые – в конце)
    seen = set()
    deduped = []
    for item in history:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    # Обрезаем до ``max_size`` (оставляем самые свежие записи)
    if len(deduped) > max_size:
        deduped = deduped[-max_size:]
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(deduped, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to write history file: {e}")



def redact_secrets(value):
    """Mask secret values recursively before presenting configuration."""
    if isinstance(value, dict):
        return {k: ("••••••" if any(word in str(k).lower() for word in ("password", "secret", "token", "api_key")) and not str(k).endswith("_env") else redact_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value

# arxiv_agent.py
"""Агент-источник: arXiv.org.

Ищет свежие препринты по темам через открытый API arXiv
(https://info.arxiv.org/help/api/). Ответ приходит в формате Atom XML,
разбор выполняется стандартной библиотекой – дополнительных зависимостей нет.

Возвращает единый формат записи, общий для всех источников:
    source, identifier, title, url, abstract, authors, published
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import resolve_date_range, token_is_exact, topic_tokens

logger = logging.getLogger("archiv_tracker.arxiv")

SOURCE_ID = "arxiv"
SOURCE_NAME = "arxiv.org"
ARXIV_API_URL = "https://export.arxiv.org/api/query"

_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _clean(text: str) -> str:
    """Убрать переносы строк и лишние пробелы из полей arXiv."""
    return re.sub(r"\s+", " ", text or "").strip()


def _token_clause(token: str) -> str:
    """Одно слово темы.

    Аббревиатуры ищутся целиком. Остальные слова — целиком или по префиксу
    (``agent`` находит ``agents`` и ``agentic``), как подсветка на arxiv.org.
    """
    safe = token.replace('"', "").strip()
    if token_is_exact(safe) or len(re.sub(r"[^A-Za-z0-9]", "", safe)) < 4:
        return f'all:"{safe}"'
    wild = re.sub(r"[^A-Za-z0-9]", "", safe).lower()
    return f'(all:"{safe}" OR all:{wild}*)'


def build_text_query(topics: List[str]) -> str:
    """Темы через OR, слова одной темы через AND, без фиксированного порядка."""
    clauses = []
    for topic in topics:
        tokens = topic_tokens(topic)
        if not tokens:
            continue
        clauses.append("(" + " AND ".join(_token_clause(token) for token in tokens) + ")")
    return " OR ".join(clauses)


def build_query(topics: List[str], date_from: date, date_to: date) -> str:
    """Поиск по всем полям и дате первой отправки препринта.

    Границы ``submittedDate`` задаются как ``YYYYMMDDHHMM`` в GMT:
    начало дня и конец дня, чтобы конечная дата вошла целиком.
    """
    topic_q = build_text_query(topics)
    start = date_from.strftime("%Y%m%d") + "0000"
    end = date_to.strftime("%Y%m%d") + "2359"
    return f"({topic_q}) AND submittedDate:[{start} TO {end}]"


_last_request_at = 0.0


def _api_get(params: Dict) -> requests.Response:
    """Запрос к API с паузой: arXiv просит не чаще одного запроса в 3 секунды."""
    global _last_request_at
    wait = 3.0 - (time.time() - _last_request_at)
    if _last_request_at and wait > 0:
        time.sleep(wait)
    resp = requests.get(ARXIV_API_URL, params=params, timeout=30)
    _last_request_at = time.time()
    resp.raise_for_status()
    return resp


def _parse_entries(root: ET.Element) -> List[Dict]:
    items = []
    for entry in root.findall("atom:entry", _NS):
        abs_url = _clean(entry.findtext("atom:id", default="", namespaces=_NS))
        if "/abs/" not in abs_url:
            continue
        arxiv_id = abs_url.rsplit("/abs/", 1)[-1]
        authors = [
            _clean(a.findtext("atom:name", default="", namespaces=_NS))
            for a in entry.findall("atom:author", _NS)
        ]
        items.append(
            {
                "source": SOURCE_ID,
                "identifier": f"arxiv:{arxiv_id}",
                "title": _clean(entry.findtext("atom:title", default="", namespaces=_NS))
                or "(без названия)",
                "url": abs_url.replace("http://", "https://"),
                "abstract": _clean(entry.findtext("atom:summary", default="", namespaces=_NS)),
                "authors": ", ".join(authors),
                "published": _clean(entry.findtext("atom:published", default="", namespaces=_NS))[:10],
                "updated": _clean(entry.findtext("atom:updated", default="", namespaces=_NS))[:10],
            }
        )
    return items


def _search(query: str, limit: int, sort_by: str) -> List[Dict]:
    """Забрать до ``limit`` записей, листая API страницами по 100."""
    items: List[Dict] = []
    start = 0
    total: Optional[int] = None
    while len(items) < limit:
        page = min(100, limit - len(items))
        resp = _api_get(
            {
                "search_query": query,
                "start": start,
                "max_results": page,
                "sortBy": sort_by,
                "sortOrder": "descending",
            }
        )
        root = ET.fromstring(resp.content)
        if total is None:
            raw_total = root.findtext(
                "{http://a9.com/-/spec/opensearch/1.1/}totalResults", default="0"
            )
            try:
                total = int(raw_total)
            except ValueError:
                total = 0
            logger.info(f"📥 arXiv ({sort_by}): всего совпадений {total}")
        batch = _parse_entries(root)
        if not batch:
            break
        items.extend(batch)
        start += len(batch)
        if start >= total or len(batch) < page:
            break
    return items


def _in_window(value: str, date_from: date, date_to: date) -> bool:
    if not value:
        return False
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return date_from <= day <= date_to


def _recently_updated(
    text_query: str, date_from: date, date_to: date, limit: int,
) -> List[Dict]:
    """Препринты, у которых в диапазон попала дата новой версии, а не первой отправки.

    API фильтрует только ``submittedDate`` (первая версия). Сортировка по
    ``lastUpdatedDate`` и отбор по полю ``updated`` добирает остальные.
    """
    found: List[Dict] = []
    start = 0
    for _ in range(5):
        resp = _api_get(
            {
                "search_query": text_query,
                "start": start,
                "max_results": 100,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
            }
        )
        root = ET.fromstring(resp.content)
        batch = _parse_entries(root)
        if not batch:
            break
        oldest = batch[-1].get("updated") or batch[-1].get("published") or ""
        for item in batch:
            if _in_window(item.get("updated", ""), date_from, date_to) or _in_window(
                item.get("published", ""), date_from, date_to
            ):
                found.append(item)
        start += len(batch)
        if len(batch) < 100 or len(found) >= limit:
            break
        if oldest and not _in_window(oldest, date_from, date_to):
            try:
                if date.fromisoformat(oldest) < date_from:
                    break
            except ValueError:
                break
    return found


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить препринты arXiv по темам и диапазону дат.

    В выборку входят и новые препринты, и новые версии уже известных работ,
    если дата отправки или дата обновления попадает в диапазон.
    """
    date_from, date_to = resolve_date_range(date_from=date_from, date_to=date_to)
    text_query = build_text_query(topics)
    if not text_query:
        logger.warning("Темы для arXiv пустые – поиск не выполняется.")
        return []
    query = build_query(topics, date_from, date_to)
    logger.info(f"🌐 Запрос к arXiv: {query} (до {rows} препринтов)")
    by_id: Dict[str, Dict] = {}
    for item in _search(query, rows, "submittedDate"):
        by_id[item["identifier"]] = item
    try:
        for item in _recently_updated(text_query, date_from, date_to, max(rows, 100)):
            by_id.setdefault(item["identifier"], item)
    except Exception as exc:
        logger.warning(f"arXiv: не удалось добрать обновлённые версии ({exc})")
    items = sorted(
        by_id.values(),
        key=lambda it: it.get("updated") or it.get("published") or "",
        reverse=True,
    )
    if len(items) > rows:
        logger.info(f"📥 arXiv: в диапазоне {len(items)}, отдано последних {rows}")
        items = items[:rows]
    else:
        logger.info(f"📥 arXiv: отдано {len(items)}")
    return items

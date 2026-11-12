"""Агент поиска публикаций в Europe PMC."""

import logging
import re
from datetime import date
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from utils import resolve_date_range, topic_tokens


SOURCE_ID = "europepmc"
SOURCE_NAME = "Europe PMC"

API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

logger = logging.getLogger("archiv_tracker.europepmc")


def _clean(value) -> str:
    """Удалить HTML и лишние пробелы."""
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def build_text_query(topics: List[str]) -> str:
    """Слова темы соединяются AND, разные темы — OR."""
    clauses = []

    for topic in topics:
        words = []

        for token in topic_tokens(topic):
            safe = re.sub(r"[^\w]", " ", token).strip()
            if safe:
                words.append(f'TITLE_ABS:"{safe}"')

        if words:
            clauses.append("(" + " AND ".join(words) + ")")

    return " OR ".join(clauses)


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить публикации по теме и дате первой публикации."""
    if rows <= 0:
        return []

    date_from, date_to = resolve_date_range(
        date_from=date_from,
        date_to=date_to,
    )

    if date_from > date_to:
        raise ValueError("Начальная дата позже конечной.")

    text_query = build_text_query(topics)
    if not text_query:
        return []

    query = (
        f"({text_query}) "
        f"AND FIRST_PDATE:[{date_from.isoformat()} "
        f"TO {date_to.isoformat()}] "
        "sort_date:y"
    )

    items = []
    seen = set()
    cursor = "*"

    while len(items) < rows:
        response = requests.get(
            API_URL,
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": min(rows - len(items), 1000),
                "cursorMark": cursor,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        if "resultList" not in data:
            raise RuntimeError(
                "Europe PMC вернул неожиданный ответ: "
                + str(data)[:300]
            )

        records = data["resultList"].get("result", [])
        if not records:
            break

        for record in records:
            record_id = str(record.get("id") or "")
            source = str(record.get("source") or "")

            if not record_id or not source:
                continue

            identifier = f"europepmc:{source}:{record_id}"

            if identifier in seen:
                continue

            seen.add(identifier)

            items.append({
                "source": SOURCE_ID,
                "identifier": identifier,
                "title": _clean(record.get("title")) or "(без названия)",
                "url": (
                    f"https://europepmc.org/article/"
                    f"{source}/{record_id}"
                ),
                "abstract": _clean(record.get("abstractText")),
                "authors": _clean(record.get("authorString")),
                "published": record.get("firstPublicationDate") or "",
                "doi": record.get("doi") or "",
                "pmid": record.get("pmid") or "",
            })

            if len(items) >= rows:
                break

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break

        cursor = next_cursor

    logger.info("Europe PMC: получено %s публикаций", len(items))
    return items
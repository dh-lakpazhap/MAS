"""Агент поиска публикаций в Semantic Scholar."""

import logging
import os
import re
import time
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import resolve_date_range, topic_tokens


SOURCE_ID = "semantic_scholar"
SOURCE_NAME = "Semantic Scholar"

API_URL = (
    "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
)

logger = logging.getLogger("archiv_tracker.semantic_scholar")


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def build_text_query(topics: List[str]) -> str:
    """В bulk search: + означает AND, | означает OR."""
    clauses = []

    for topic in topics:
        words = []

        for token in topic_tokens(topic):
            # Удаляем специальные операторы из пользовательских слов.
            safe = re.sub(r"[^\w]", " ", token).strip()
            words.extend(safe.split())

        if words:
            clauses.append("(" + " + ".join(words) + ")")

    return " | ".join(clauses)


def _request(params: Dict) -> Dict:
    """Выполнить запрос с повтором при ограничении частоты."""
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()

    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    for attempt in range(3):
        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        if response.status_code in (429, 500, 502, 503, 504):
            if attempt < 2:
                delay = min(5 * (2 ** attempt), 30)

                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(max(float(retry_after), 1), 30)
                    except ValueError:
                        pass

                logger.warning(
                    "Semantic Scholar: HTTP %s, повтор через %s с",
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue

        if response.status_code == 429:
            raise RuntimeError(
                "Semantic Scholar ограничил частоту запросов. "
                "Повторите поиск позже или задайте "
                "SEMANTIC_SCHOLAR_API_KEY."
            )

        response.raise_for_status()
        data = response.json()

        if "data" not in data:
            raise RuntimeError(
                "Semantic Scholar вернул неожиданный ответ: "
                + str(data)[:300]
            )

        return data

    raise RuntimeError("Не удалось получить ответ Semantic Scholar.")


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Получить публикации по теме и диапазону дат."""
    if rows <= 0:
        return []

    date_from, date_to = resolve_date_range(
        date_from=date_from,
        date_to=date_to,
    )

    if date_from > date_to:
        raise ValueError("Начальная дата позже конечной.")

    query = build_text_query(topics)
    if not query:
        return []

    params = {
        "query": query,
        "fields": (
            "title,abstract,url,authors,publicationDate,"
            "year,externalIds"
        ),
        "publicationDateOrYear": (
            f"{date_from.isoformat()}:{date_to.isoformat()}"
        ),
        "sort": "publicationDate:desc",
    }

    items = []
    seen = set()
    used_tokens = set()

    while len(items) < rows:
        data = _request(params)
        records = data["data"]

        if not records:
            break

        for record in records:
            paper_id = str(record.get("paperId") or "")

            if not paper_id or paper_id in seen:
                continue

            seen.add(paper_id)

            external_ids = record.get("externalIds") or {}
            authors = record.get("authors") or []

            items.append({
                "source": SOURCE_ID,
                "identifier": f"semantic_scholar:{paper_id}",
                "title": _clean(record.get("title")) or "(без названия)",
                "url": record.get("url") or (
                    f"https://www.semanticscholar.org/paper/{paper_id}"
                ),
                "abstract": _clean(record.get("abstract")),
                "authors": ", ".join(
                    author["name"]
                    for author in authors
                    if author.get("name")
                ),
                "published": record.get("publicationDate") or "",
                "year": record.get("year"),
                "doi": external_ids.get("DOI") or "",
                "pmid": external_ids.get("PubMed") or "",
            })

            if len(items) >= rows:
                break

        token = data.get("token")
        if not token or token in used_tokens:
            break

        used_tokens.add(token)
        params["token"] = token
        time.sleep(1.1)

    logger.info(
        "Semantic Scholar: получено %s публикаций", len(items)
    )
    return items
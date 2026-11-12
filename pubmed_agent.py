"""Агент-источник PubMed."""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List, Optional

import requests

from utils import resolve_date_range, topic_tokens


logger = logging.getLogger("archiv_tracker.pubmed")

SOURCE_ID = "pubmed"
SOURCE_NAME = "PubMed"

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_last_request_at = 0.0


def _clean(text: str) -> str:
    """Убрать лишние пробелы и переносы строк."""
    return re.sub(r"\s+", " ", text or "").strip()


def _element_text(element) -> str:
    """Получить текст элемента вместе с вложенными тегами."""
    if element is None:
        return ""
    return _clean("".join(element.itertext()))


def _api_get(endpoint: str, params: Dict) -> requests.Response:
    """Выполнить запрос с паузой между обращениями к API."""
    global _last_request_at

    # Небольшой запас относительно лимита 3 запроса в секунду.
    pause = 0.4 - (time.monotonic() - _last_request_at)
    if pause > 0:
        time.sleep(pause)

    try:
        response = requests.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response
    finally:
        _last_request_at = time.monotonic()


def build_text_query(topics: List[str]) -> str:
    """Слова одной темы соединяются AND, разные темы — OR."""
    clauses = []

    for topic in topics:
        tokens = topic_tokens(topic)
        words = []

        for token in tokens:
            safe = token.replace('"', "").strip()
            if safe:
                words.append(f'"{safe}"[Title/Abstract]')

        if words:
            clauses.append("(" + " AND ".join(words) + ")")

    return " OR ".join(clauses)


def _search_ids(
    query: str,
    rows: int,
    date_from: date,
    date_to: date,
) -> List[str]:
    """Найти идентификаторы публикаций — PMID."""
    response = _api_get(
        "esearch.fcgi",
        {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": rows,
            "sort": "pub_date",
            "datetype": "pdat",
            "mindate": date_from.strftime("%Y/%m/%d"),
            "maxdate": date_to.strftime("%Y/%m/%d"),
        },
    )

    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"PubMed: {data['error']}")

    result = data.get("esearchresult")
    if not isinstance(result, dict):
        raise RuntimeError("PubMed вернул неожиданный ответ поиска.")

    if result.get("ERROR"):
        raise RuntimeError(f"PubMed: {result['ERROR']}")

    logger.info(
        "PubMed: всего совпадений %s",
        result.get("count", "0"),
    )

    return result.get("idlist", [])


def _parse_articles(root: ET.Element) -> List[Dict]:
    """Преобразовать XML PubMed в общий формат системы."""
    items = []

    for record in root.findall(".//PubmedArticle"):
        citation = record.find("MedlineCitation")
        if citation is None:
            continue

        pmid = _element_text(citation.find("PMID"))
        article = citation.find("Article")

        if not pmid or article is None:
            continue

        title = _element_text(article.find("ArticleTitle"))

        # Аннотация может состоять из нескольких разделов.
        abstract_parts = []

        for part in article.findall("Abstract/AbstractText"):
            text = _element_text(part)
            label = part.get("Label", "")

            if text:
                abstract_parts.append(
                    f"{label}: {text}" if label else text
                )

        authors = []

        for author in article.findall("AuthorList/Author"):
            collective = _element_text(author.find("CollectiveName"))

            if collective:
                authors.append(collective)
                continue

            first_name = (
                _element_text(author.find("ForeName"))
                or _element_text(author.find("Initials"))
            )
            last_name = _element_text(author.find("LastName"))
            name = _clean(f"{first_name} {last_name}")

            if name:
                authors.append(name)

        items.append(
            {
                "source": SOURCE_ID,
                "identifier": f"pubmed:{pmid}",
                "title": title or "(без названия)",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "abstract": "\n".join(abstract_parts),
                "authors": ", ".join(authors),
            }
        )

    return items


def _fetch_articles(pmids: List[str]) -> List[Dict]:
    """Получить публикации пакетами по 100 записей."""
    items = []

    for start in range(0, len(pmids), 100):
        batch = pmids[start:start + 100]

        response = _api_get(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
            },
        )

        root = ET.fromstring(response.content)

        error = root.find(".//ERROR")
        if error is not None:
            raise RuntimeError(f"PubMed: {_element_text(error)}")

        items.extend(_parse_articles(root))

    return items


def fetch(
    topics: List[str],
    rows: int = 20,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> List[Dict]:
    """Найти публикации PubMed по темам и датам публикации."""
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
        logger.warning("Темы для PubMed пустые.")
        return []

    # ESearch PubMed позволяет получить первые 10 000 PMID.
    limit = min(rows, 10000)

    logger.info(
        "Запрос к PubMed: %s; даты %s — %s; до %s записей",
        query,
        date_from,
        date_to,
        limit,
    )

    pmids = _search_ids(query, limit, date_from, date_to)

    if not pmids:
        logger.info("PubMed: публикации не найдены.")
        return []

    items = _fetch_articles(pmids)
    logger.info("PubMed: получено %s публикаций", len(items))

    return items[:limit]
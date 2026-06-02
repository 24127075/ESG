"""Crawler Tier 1 — mandatory disclosure portals HOSE / HNX (SDAD §3.1).

Walks the full DOM looking for links that are BOTH a document URL (dynamic
``download...id=`` API endpoints or static ``.pdf`` files) AND carry a label
that names an annual / sustainability report. Labels are matched across
Vietnamese-with-diacritics, Vietnamese-without-diacritics, and English.
"""
from __future__ import annotations

import re

# Dynamic download APIs (download.ashx?id=, getfile, view_doc) and static PDFs.
LINK_PATTERN = re.compile(
    r"(?i)(download.*id=\d+|getfile|view_doc|.*\.pdf)"
)

# "báo cáo thường niên" / "bao cao thuong nien" / "annual report"
# "phát triển bền vững" / "phat trien ben vung" / "sustainability report"
TEXT_PATTERN = re.compile(
    r"(?i)("
    r"bao\s*cao\s*thuong\s*nien|bctn|"
    r"b[áa]o\s*c[áa]o\s*th[uư][ờo]ng\s*ni[êe]n|"
    r"annual\s*report|"
    r"phat\s*trien\s*ben\s*vung|"
    r"ph[áa]t\s*tri[êe]n\s*b[ềê]n\s*v[uư]ng|"
    r"sustainability\s*report"
    r")"
)


def is_target_document_link(tag) -> bool:
    """True when a BeautifulSoup ``<a>`` tag points at a target report.

    ``tag`` must expose ``.get('href')`` and ``.get_text(strip=True)``.
    """
    href = tag.get("href", "") or ""
    label = tag.get_text(strip=True)
    return bool(LINK_PATTERN.search(href) and TEXT_PATTERN.search(label))


def extract_document_links(html: str, base_url: str = "") -> list[dict]:
    """Parse a portal page and return matching document links.

    Each item: ``{"url": <absolute href>, "label": <anchor text>}``.
    Requires beautifulsoup4; raises ImportError with guidance if missing.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "beautifulsoup4 is required for Tier 1 scraping (pip install beautifulsoup4)"
        ) from exc
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for tag in soup.find_all("a"):
        if is_target_document_link(tag):
            href = tag.get("href", "")
            results.append(
                {
                    "url": urljoin(base_url, href) if base_url else href,
                    "label": tag.get_text(strip=True),
                }
            )
    return results

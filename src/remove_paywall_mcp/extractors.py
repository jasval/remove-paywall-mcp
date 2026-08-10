from __future__ import annotations

import re

try:
    from readability import Document as ReadabilityDocument
except ImportError:
    ReadabilityDocument = None  # type: ignore[assignment]


_BAD_CLASS = re.compile(
    r"(?:^|\s)(nav|navbar|footer|sidebar|sidemenu|menu|ads?|advertisement|advert|banner"
    r"|social|share|related|recommended|comments?|newsletter|popup|modal|overlay"
    r"|tracking|analytics|interstitial)(?:\s|$)",
    re.IGNORECASE,
)


def extract_body(html: str) -> str:
    if ReadabilityDocument is not None:
        try:
            doc = ReadabilityDocument(html)
            content_html = doc.summary(html_partial=True)
        except Exception:
            content_html = html
    else:
        content_html = html

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content_html, "lxml" if _has_lxml() else "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "iframe", "noscript"]):
        tag.decompose()

    for tag in soup.find_all(attrs={"class": True}):
        classes = tag.get("class")
        if classes and any(_BAD_CLASS.search(c) for c in classes):
            tag.decompose()

    body = soup.find("body")
    text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

    lines = [line.rstrip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    lines = _dedup_lines(lines)

    return "\n".join(lines)


def _dedup_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False

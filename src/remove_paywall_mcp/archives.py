from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import httpx

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
CDX_BASE = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_VIEW = "https://web.archive.org/web/{timestamp}id_/{url}"
ARCHIVE_IS_MIRRORS = [
    "https://archive.is",
    "https://archive.today",
    "https://archive.ph",
    "https://archive.md",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid",
    "ref", "ref_src", "source", "mc_cid", "mc_eid",
    "syn", "ito", "tpcc",
}

GATEWAY_MARKERS = [
    "Welcome to nginx",
    "Welcome to Apache",
    "nginx/",
    "<title>502",
    "<title>503",
    "<title>504",
    "<title>Error 404",
    "Just a moment...</title>",
    "Checking your browser",
    "Please enable cookies",
    "consent.google",
    "google.com/webhp",
    "<title>cache:",
    "google.com/search</title>",
    "Please Don",
    "<title>Wayback Machine</title>",
    "Attention Required! | Cloudflare",
    "Sorry, you have been blocked",
    "/cdn-cgi/challenge-platform",
    "Pardon our interruption",
    "captcha-delivery",
    "Access denied</title>",
    "unusual traffic",
    "cannot be crawled or displayed due to robots.txt",
    "The Wayback Machine has not archived that URL",
]

PAYWALL_MARKERS = [
    "subscribe to continue reading",
    "already a subscriber",
    "create an account to",
    "verify access",
    "this article is for subscribers",
    "sign in to continue",
    "you've read all of your free articles",
    "support quality journalism",
    "create a free account to continue",
    "start your free trial",
    "unlock this article",
    "metered paywall",
    "login to continue reading",
    "please log in",
    "register to read",
    "get unlimited access",
    "purchase a subscription",
    "choose a subscription",
    "become a subscriber",
    "thank you for your patience while we verify",
    "we hope you",
    "enjoying our journalism",
    "subscribe for",
    "view subscription options",
]


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        clean = {
            k: v
            for k, v in qs.items()
            if not any(k.lower() == p or k.lower().startswith(p + "-") for p in _TRACKING_PARAMS)
        }
        parsed = parsed._replace(query=urlencode(clean, doseq=True) if clean else "")
    return urlunparse(parsed._replace(fragment=""))


def _is_gateway_page(html: str) -> bool:
    body = html.lower()
    return any(m.lower() in body for m in GATEWAY_MARKERS)


def _has_paywall_content(text: str) -> bool:
    head = text[:2000].lower()
    return any(m.lower() in head for m in PAYWALL_MARKERS)


@dataclass
class ArchiveResult:
    success: bool
    source: str
    html: str | None
    snapshot_url: str | None
    error: str | None


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": UA},
        follow_redirects=True,
        timeout=TIMEOUT,
    )


def _has_sufficient_content(resp: httpx.Response) -> bool:
    if resp.status_code != 200:
        return False
    size = len(resp.content)
    if size < 500:
        return False
    if size > MAX_RESPONSE_BYTES:
        return False
    return not _is_gateway_page(resp.text)


async def wayback(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        normalized = normalize_url(url)
        cdx_url = (
            f"{CDX_BASE}?url={quote(normalized, safe='')}"
            f"&output=json&limit=-5&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&filter=mimetype:text/html&collapse=digest"
        )
        resp = await client.get(cdx_url)
        if resp.status_code != 200:
            return ArchiveResult(False, "wayback", None, None, f"CDX API error: {resp.status_code}")
        try:
            rows = resp.json()
        except Exception as exc:
            return ArchiveResult(False, "wayback", None, None, f"CDX response not valid JSON: {exc}")

        if not rows or len(rows) < 2:
            return ArchiveResult(False, "wayback", None, None, "No snapshots found in Wayback Machine")

        last_error = None
        for row in rows[1:]:
            timestamp, orig_url, _status = row
            view_url = ARCHIVE_VIEW.format(timestamp=timestamp, url=quote(orig_url, safe=""))
            try:
                view_resp = await client.get(view_url)
                if _has_sufficient_content(view_resp):
                    return ArchiveResult(True, "wayback", view_resp.text, view_url, None)
            except Exception as exc:
                last_error = exc
                continue

        detail = f" (last error: {last_error})" if last_error else ""
        return ArchiveResult(False, "wayback", None, None, f"Archived snapshots are empty or unreachable{detail}")
    except Exception as exc:
        return ArchiveResult(False, "wayback", None, None, str(exc))
    finally:
        if own_client:
            await client.aclose()


async def archive_is(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        normalized = normalize_url(url)
        for variant in ("newest", "oldest"):
            for mirror in ARCHIVE_IS_MIRRORS:
                lookup_url = f"{mirror}/{variant}/{quote(normalized, safe='')}"
                try:
                    resp = await client.get(lookup_url)
                    if _has_sufficient_content(resp):
                        return ArchiveResult(True, "archive_is", resp.text, str(resp.url), None)
                except Exception:
                    continue
        return ArchiveResult(False, "archive_is", None, None, "No archive.is snapshot found")
    except Exception as exc:
        return ArchiveResult(False, "archive_is", None, None, str(exc))
    finally:
        if own_client:
            await client.aclose()


async def wayback_available(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        api_url = f"https://archive.org/wayback/available?url={quote(normalize_url(url), safe='')}"
        resp = await client.get(api_url)
        if resp.status_code != 200:
            return ArchiveResult(False, "wayback_available", None, None, f"Availability API error: {resp.status_code}")
        try:
            data = resp.json()
        except Exception as exc:
            return ArchiveResult(False, "wayback_available", None, None, f"Invalid JSON: {exc}")

        snapshots = data.get("archived_snapshots", {})
        closest = snapshots.get("closest")
        if not closest or not closest.get("available"):
            return ArchiveResult(False, "wayback_available", None, None, "No snapshot available")
        snapshot_url = closest.get("url", "")
        if not snapshot_url:
            return ArchiveResult(False, "wayback_available", None, None, "Snapshot URL missing")
        arc_resp = await client.get(snapshot_url)
        if _has_sufficient_content(arc_resp):
            return ArchiveResult(True, "wayback_available", arc_resp.text, snapshot_url, None)
        return ArchiveResult(False, "wayback_available", None, None, "Snapshot was empty or unreachable")
    except Exception as exc:
        return ArchiveResult(False, "wayback_available", None, None, str(exc))
    finally:
        if own_client:
            await client.aclose()


ARCHIVE_SOURCES: dict[str, object] = {
    "wayback": wayback,
    "archive_is": archive_is,
    "wayback_available": wayback_available,
}

DEFAULT_ARCHIVE_ORDER: list[str] = ["wayback", "archive_is", "wayback_available"]


async def search_all(
    url: str, archive_order: list[str] | None = None
) -> tuple[ArchiveResult, list[ArchiveResult]]:
    if archive_order is None:
        archive_order = list(DEFAULT_ARCHIVE_ORDER)

    async def _try(source: str) -> ArchiveResult:
        handler = ARCHIVE_SOURCES.get(source)
        if handler is None:
            return ArchiveResult(False, source, None, None, f"Unknown source: {source}")
        async with _make_client() as c:
            try:
                return await handler(url, client=c)  # type: ignore[arg-type]
            except Exception as exc:
                return ArchiveResult(False, source, None, None, str(exc))

    tasks: dict[str, asyncio.Task[ArchiveResult]] = {}
    for source in archive_order:
        tasks[source] = asyncio.create_task(_try(source))

    pending = set(tasks.values())
    all_results: list[ArchiveResult] = []
    first_success: ArchiveResult | None = None

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            all_results.append(result)
            if result.success and first_success is None:
                first_success = result

    if first_success is not None:
        return first_success, sorted(
            all_results, key=lambda r: archive_order.index(r.source) if r.source in archive_order else 999
        )
    return ArchiveResult(False, "none", None, None, "All archive sources exhausted"), sorted(
        all_results, key=lambda r: archive_order.index(r.source) if r.source in archive_order else 999
    )

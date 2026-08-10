from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
CDX_BASE = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_VIEW = "https://web.archive.org/web/{timestamp}id_/{url}"
ARCHIVE_IS_BASE = "https://archive.is"
REMOVE_PAYWALL_SEARCH = "https://www.removepaywall.com/search"
GOOGLE_CACHE = "https://webcache.googleusercontent.com/search?q=cache:{url}"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

GATEWAY_MARKERS = [
    "Welcome to nginx",
    "Welcome to Apache",
    "nginx/",
    "<title>502",
    "<title>503",
    "<title>504",
    "Just a moment...</title>",
    "Checking your browser",
    "Please enable cookies",
]


def _is_gateway_page(html: str) -> bool:
    head = html[:2000].lower()
    return any(m.lower() in head for m in GATEWAY_MARKERS)


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


def _valid_page(resp: httpx.Response) -> bool:
    return resp.status_code == 200 and len(resp.text) > 500 and not _is_gateway_page(resp.text)


async def wayback(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        cdx_url = (
            f"{CDX_BASE}?url={quote(url, safe='')}"
            f"&output=json&limit=5&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200"
        )
        resp = await client.get(cdx_url)
        if resp.status_code != 200:
            return ArchiveResult(False, "wayback", None, None, f"CDX API error: {resp.status_code}")
        try:
            rows = resp.json()
        except Exception:
            return ArchiveResult(False, "wayback", None, None, "CDX response not valid JSON")

        if not rows or len(rows) < 2:
            return ArchiveResult(False, "wayback", None, None, "No snapshots found in Wayback Machine")

        for row in rows[1:]:
            timestamp, orig_url, _status = row
            view_url = ARCHIVE_VIEW.format(timestamp=timestamp, url=quote(orig_url, safe=""))
            try:
                view_resp = await client.get(view_url)
                if _valid_page(view_resp):
                    return ArchiveResult(True, "wayback", view_resp.text, view_url, None)
            except Exception:
                continue

        return ArchiveResult(False, "wayback", None, None, "Archived snapshots are empty or unreachable")
    except Exception as e:
        return ArchiveResult(False, "wayback", None, None, str(e))
    finally:
        if own_client:
            await client.aclose()


async def archive_is(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        for variant in ("newest", "oldest"):
            lookup_url = f"{ARCHIVE_IS_BASE}/{variant}/{quote(url, safe='')}"
            try:
                resp = await client.get(lookup_url)
                if _valid_page(resp):
                    return ArchiveResult(True, "archive_is", resp.text, lookup_url, None)
            except Exception:
                continue
        return ArchiveResult(False, "archive_is", None, None, "No archive.is snapshot found")
    except Exception as e:
        return ArchiveResult(False, "archive_is", None, None, str(e))
    finally:
        if own_client:
            await client.aclose()


async def google_cache(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        cache_url = GOOGLE_CACHE.format(url=quote(url, safe=""))
        resp = await client.get(cache_url)
        if _valid_page(resp):
            return ArchiveResult(True, "google_cache", resp.text, cache_url, None)
        return ArchiveResult(
            False, "google_cache", None, None, f"Google cache not available (HTTP {resp.status_code})"
        )
    except Exception as e:
        return ArchiveResult(False, "google_cache", None, None, str(e))
    finally:
        if own_client:
            await client.aclose()


async def removepaywall_com(url: str, client: httpx.AsyncClient | None = None) -> ArchiveResult:
    own_client = client is None
    if own_client:
        client = _make_client()
    try:
        search_url = f"{REMOVE_PAYWALL_SEARCH}?url={quote(url, safe='')}"
        resp = await client.get(search_url)
        if resp.status_code != 200:
            return ArchiveResult(
                False, "removepaywall_com", None, None, f"removepaywall.com error: HTTP {resp.status_code}"
            )

        links = re.findall(r'href="(https?://archive\.(?:is|today|ph)/[^"]+)"', resp.text)
        if links:
            for link in links:
                try:
                    arc_resp = await client.get(link)
                    if _valid_page(arc_resp):
                        return ArchiveResult(True, "removepaywall_com", arc_resp.text, link, None)
                except Exception:
                    continue

        wayback_hint = re.search(r'href="(https?://web\.archive\.org/[^"]+)"', resp.text)
        if wayback_hint:
            try:
                arc_resp = await client.get(wayback_hint.group(1))
                if _valid_page(arc_resp):
                    return ArchiveResult(True, "removepaywall_com", arc_resp.text, wayback_hint.group(1), None)
            except Exception:
                pass

        return ArchiveResult(False, "removepaywall_com", None, None, "No archive links found on removepaywall.com")
    except Exception as e:
        return ArchiveResult(False, "removepaywall_com", None, None, str(e))
    finally:
        if own_client:
            await client.aclose()


ARCHIVE_SOURCES: dict[str, object] = {
    "wayback": wayback,
    "archive_is": archive_is,
    "google_cache": google_cache,
    "removepaywall_com": removepaywall_com,
}

DEFAULT_ARCHIVE_ORDER: list[str] = ["wayback", "archive_is", "google_cache", "removepaywall_com"]


async def search_all(
    url: str, archive_order: list[str] | None = None
) -> tuple[ArchiveResult, list[ArchiveResult]]:
    if archive_order is None:
        archive_order = list(DEFAULT_ARCHIVE_ORDER)

    client = _make_client()
    try:
        all_results: list[ArchiveResult] = []
        for source in archive_order:
            handler = ARCHIVE_SOURCES.get(source)
            if handler is None:
                continue
            try:
                result = await handler(url, client=client)  # type: ignore[arg-type]
            except Exception as exc:
                result = ArchiveResult(False, source, None, None, str(exc))
            all_results.append(result)
            if result.success:
                return result, all_results

        return ArchiveResult(False, "none", None, None, "All archive sources exhausted"), all_results
    finally:
        await client.aclose()

from __future__ import annotations

import asyncio
import os
import re as _re
from urllib.parse import urlparse

import httpx
from mcp.server import MCPServer

from . import domain_store
from .archives import ARCHIVE_SOURCES, _has_paywall_content, search_all
from .extractors import extract_body

server = MCPServer(
    name="remove-paywall",
    version="1.4.0",
    description="MCP server that removes article paywalls via internet archives",
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _title_from_html(html: str) -> str:
    m = _re.search(r"<title[^>]*>([^<]+)</title>", html, _re.DOTALL | _re.IGNORECASE)
    if m:
        import html as _h
        return _h.unescape(m.group(1).strip())
    return "(no title)"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@server.tool()
async def remove_paywall(url: str) -> str:
    """Remove a paywall from an article URL by searching internet archives.

    First tries a direct fetch with Googlebot user-agent (many sites serve
    full content to crawlers), then 12ft.io proxy, iitty textise, Wayback
    Machine (CDX API with dedup + newest-first), archive.is/ph mirrors,
    and Wayback Availability API. Archives are tried in parallel using
    historical success rates to prioritize the best one for each domain.

    Returns extracted article text with title and snapshot info.
    """
    domain = _domain(url)

    dom = await domain_store.get_domain(domain)
    if dom is not None and not dom["has_paywall"]:
        return await _fetch_live(url)

    archive_order = await domain_store.get_best_archives(domain)
    result, all_results = await search_all(url, archive_order=archive_order)

    for r in all_results:
        await domain_store.log_attempt(domain, r.source, r.success)

    if not result.success:
        failures = "\n".join(f"  - {r.source}: {r.error}" for r in all_results)
        return f"Could not remove paywall. All archives exhausted:\n{failures}"

    text = extract_body(result.html) if result.html else ""
    if not text.strip():
        return f"Extracted text was empty from {result.source} ({result.snapshot_url})"

    if _has_paywall_content(text):
        await domain_store.log_attempt(domain, result.source, False)
        remaining = [s for s in archive_order if s not in {r.source for r in all_results if not r.success}]
        if remaining:
            retry_result, retry_all = await search_all(url, archive_order=remaining)
            for r in retry_all:
                await domain_store.log_attempt(domain, r.source, r.success)
            if retry_result.success:
                retry_text = extract_body(retry_result.html) if retry_result.html else ""
                if retry_text.strip() and not _has_paywall_content(retry_text):
                    result = retry_result
                    text = retry_text

    title = _title_from_html(result.html) if result.html else ""
    snapshot = result.snapshot_url or "(no snapshot URL)"

    return (
        f"# {title}\n\n"
        f"**Source:** {result.source}  \n"
        f"**Snapshot:** {snapshot}  \n\n"
        f"{text}"
    )


@server.tool()
async def search_archives(url: str) -> str:
    """Search all archive sources for snapshots of a URL.

    Returns a list of available snapshot URLs from each archive source:
    Googlebot direct fetch, 12ft.io proxy, iitty textise, Wayback Machine,
    archive.is/ph mirrors, and Wayback Availability API.
    Does not extract content — use remove_paywall for full article retrieval.
    """
    archive_order = ["googlebot", "12ft", "iitty", "wayback", "archive_is", "wayback_available"]

    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
        lines: list[str] = []
        for source in archive_order:
            handler = ARCHIVE_SOURCES.get(source)
            if not handler:
                continue
            result = await handler(url, client=client)
            status = "FOUND" if result.success else "NOT FOUND"
            snapshot = result.snapshot_url or "\u2014"
            error = f" ({result.error})" if result.error else ""
            lines.append(f"  [{status}] {source}: {snapshot}{error}")

    return "Archive snapshots:\n" + "\n".join(lines)


@server.tool()
async def get_from_archive(url: str, source: str) -> str:
    """Fetch an archived version of a URL from a specific source.

    source must be one of: googlebot, 12ft, iitty, wayback, archive_is,
    wayback_available.
    Returns the extracted article text.
    """
    handler = ARCHIVE_SOURCES.get(source)
    if handler is None:
        return f"Unknown source: {source}. Valid sources: {', '.join(ARCHIVE_SOURCES.keys())}"

    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True) as client:
        result = await handler(url, client=client)

    if not result.success:
        return f"Archive {source} failed: {result.error}"

    text = extract_body(result.html) if result.html else ""
    title = _title_from_html(result.html) if result.html else ""
    snapshot = result.snapshot_url or "(no snapshot URL)"

    return (
        f"# {title}\n\n"
        f"**Source:** {source}  \n"
        f"**Snapshot:** {snapshot}  \n\n"
        f"{text}"
    )


@server.tool()
async def domain_info(domain: str) -> str:
    """Look up stored knowledge about a paywall domain.

    Returns paywall status, user notes, historical archive success rates,
    and the best archive order for this domain. Use add_domain to register
    new domains.
    """
    domain_clean = domain.lower().removeprefix("www.")
    dom = await domain_store.get_domain(domain_clean)

    if dom is None:
        return f"Domain '{domain_clean}' is not in the knowledge base. Use add_domain to register it."

    stats = await domain_store.get_attempt_stats(domain_clean)
    best = await domain_store.get_best_archives(domain_clean)

    lines = [
        f"## Domain: {domain_clean}",
        f"  Paywall: {'Yes' if dom['has_paywall'] else 'No'}",
        f"  Notes: {dom['notes'] or chr(8212)}",
        f"  Added: {dom['added_at']}",
        f"  Updated: {dom['updated_at']}",
        "",
        "### Archive success rates",
    ]

    if stats:
        for s in stats:
            pct = (s["successes"] / s["total"] * 100) if s["total"] else 0
            lines.append(f"  {s['archive']}: {s['successes']}/{s['total']} ({pct:.0f}%)")
    else:
        lines.append("  (no attempts yet)")

    lines.append(f"\n### Best archive order\n  {' ' + chr(8594) + ' '.join(best)}")

    return "\n".join(lines)


@server.tool()
async def add_domain(domain: str, has_paywall: bool, notes: str | None = None) -> str:
    """Register a domain in the paywall knowledge base.

    Set has_paywall=true for sites that normally have paywalls (so archives
    are tried first). Set has_paywall=false for sites that don't (so
    archive search is skipped and the live page is fetched directly).
    """
    domain_clean = domain.lower().removeprefix("www.")
    await domain_store.add_domain(domain_clean, has_paywall, notes)
    status = "paywalled" if has_paywall else "not paywalled"
    return f"Domain '{domain_clean}' registered as {status}."


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@server.prompt()
async def remove_paywall_prompt(url: str) -> list[dict]:
    """Use this prompt when you encounter a URL blocked by a paywall or login wall.

    The remove_paywall tool tries a direct Googlebot fetch, then 12ft.io proxy,
    iitty textise, Wayback Machine, archive.is/ph mirrors, and Wayback
    Availability API for an archived copy that bypasses the paywall.
    Non-paywalled domains are fetched live.

    Archive sources are tried in parallel, with order optimized per-domain
    based on historical success rates.
    """
    return [
        {
            "role": "user",
            "content": (
                f"I need to bypass the paywall on this article: {url}\n\n"
                "Use the remove_paywall tool to find an archived version. "
                "If no archive has it, try search_archives to see what's available. "
                "Also check domain_info for known paywall patterns on this site."
            ),
        }
    ]


@server.prompt()
async def bypass_paywall(url: str) -> list[dict]:
    """Short alias for remove_paywall_prompt. Use when you hit a paywall, login wall,
    or subscriber-only article — this prompt instructs the assistant to search archives
    for a readable copy.
    """
    return [
        {
            "role": "user",
            "content": (
                f"This article is behind a paywall: {url}\n\n"
                "Call remove_paywall to get the full text from internet archives."
            ),
        }
    ]


@server.prompt()
async def handle_paywalls() -> list[dict]:
    """System prompt fragment. Instructs the assistant to automatically use the
    remove_paywall tool whenever it encounters a URL that is behind a paywall,
    login wall, metered paywall, or subscriber-only content.

    Load this as part of the assistant's system instructions for automatic
    paywall bypass.
    """
    return [
        {
            "role": "user",
            "content": (
                "When you encounter a URL that is behind a paywall, login wall, "
                "metered paywall, or subscriber-only content, call the "
                "remove_paywall tool with that URL to retrieve the full article "
                "from internet archives. "
                "If remove_paywall fails, try search_archives to see what snapshots "
                "are available. "
                "Use domain_info for known paywall patterns on specific sites. "
                "Non-paywalled domains in the knowledge base are fetched directly "
                "without archive search."
            ),
        }
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_live(url: str) -> str:
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = extract_body(resp.text)
            title = _title_from_html(resp.text)
            return (
                f"# {title}\n\n"
                f"**Source:** live (domain marked as non-paywalled)\n\n"
                f"{text}"
            )
    except Exception as exc:
        return f"Failed to fetch live page: {exc}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Seed the domain knowledge base, then run the MCP server.

    Transport is controlled by the MCP_TRANSPORT environment variable:
      - "stdio" (default): standard in/out for local use
      - "streamable-http": HTTP server for Docker / remote use
        Configure with MCP_HOST (default 0.0.0.0) and MCP_PORT (default 8000).
    """
    asyncio.run(domain_store.seed())

    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "streamable-http":
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))
        stateless = os.environ.get("MCP_STATELESS", "1").lower() in ("1", "true", "yes")
        server.run(transport="streamable-http", host=host, port=port, stateless_http=stateless)
    else:
        server.run()


if __name__ == "__main__":
    main()

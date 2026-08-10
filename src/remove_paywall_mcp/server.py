from __future__ import annotations

import asyncio
import os
import re as _re
from urllib.parse import urlparse

import httpx
from mcp.server import MCPServer

from . import domain_store
from .archives import ARCHIVE_SOURCES, search_all
from .extractors import extract_body

server = MCPServer(
    name="remove-paywall",
    version="0.1.0",
    description="MCP server that removes article paywalls via internet archives",
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _title_from_html(html: str) -> str:
    m = _re.search(r"<title[^>]*>([^<]+)</title>", html, _re.IGNORECASE)
    return m.group(1).strip() if m else "(no title)"


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


async def remove_paywall(url: str) -> str:
    """Remove a paywall from an article URL by searching internet archives.

    Archives are tried in order of historical success rate for the domain,
    falling back to: Wayback Machine, archive.is, Google Cache, removepaywall.com.
    Returns the extracted article text with title and snapshot info.
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
    title = _title_from_html(result.html) if result.html else ""
    snapshot = result.snapshot_url or "(no snapshot URL)"

    return (
        f"# {title}\n\n"
        f"**Source:** {result.source}  \n"
        f"**Snapshot:** {snapshot}  \n\n"
        f"{text}"
    )


async def search_archives(url: str) -> str:
    """Search all archive sources for snapshots of a URL.

    Returns a list of available snapshot URLs from each archive source
    (Wayback Machine, archive.is, Google Cache, removepaywall.com proxy).
    Does not extract content — use remove_paywall for full article retrieval.
    """
    archive_order = ["wayback", "archive_is", "google_cache"]

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


async def get_from_archive(url: str, source: str) -> str:
    """Fetch an archived version of a URL from a specific source.

    source must be one of: wayback, archive_is, google_cache, removepaywall_com.
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

    stats = await domain_store.get_attempt_stats(domain)
    best = await domain_store.get_best_archives(domain)

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

    lines.append(f"\n### Best archive order\n  {' → '.join(best)}")

    return "\n".join(lines)


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
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_live(url: str) -> str:
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


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

server._tool_manager.add_tool(remove_paywall)
server._tool_manager.add_tool(search_archives)
server._tool_manager.add_tool(get_from_archive)
server._tool_manager.add_tool(domain_info)
server._tool_manager.add_tool(add_domain)


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

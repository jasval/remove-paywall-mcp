# remove-paywall-mcp

MCP server that removes article paywalls by searching internet archives. Give it a URL, get back the article text.

## How it works

1. You give it a paywalled article URL
2. Tracking params are stripped and the URL is normalized
3. It tries multiple approaches in parallel:
   - Direct fetch with Googlebot user-agent (FT, WSJ, and many others serve full content to crawlers)
   - 12ft.io proxy for hard paywalls
   - iitty textise for plain-text rendering
   - Wayback Machine CDX API, archive.ph/is mirrors, and Wayback Availability API
4. It extracts the article body with [readability-lxml](https://github.com/buriy/python-readability), stripping navigation, ads, and sidebar cruft
5. Post-extraction check: if the result still contains paywall text (e.g., the snapshot captured the paywall itself), it retries with the next archive
6. It returns clean text with the title and snapshot URL

It also learns from every attempt — success rates per domain per archive source are tracked in a local SQLite database, and archive search order is re-ranked automatically using Laplace smoothing.

## Install

```bash
# zero-install (recommended — works everywhere uvx is available)
uvx remove-paywall-mcp

# from PyPI
pip install remove-paywall-mcp

# from source
pip install git+https://github.com/jasval/remove-paywall-mcp.git

# Docker
docker run -i --rm remove-paywall-mcp
docker compose up -d  # HTTP mode on port 8000
```

## Platform configs

Once installed, add this to your MCP client config:

### OpenCode

```json
{
  "mcp": {
    "remove-paywall": {
      "type": "local",
      "command": ["uvx", "remove-paywall-mcp"],
      "enabled": true
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "remove-paywall": {
      "command": "uvx",
      "args": ["remove-paywall-mcp"]
    }
  }
}
```

### LiteLLM

```yaml
mcp_tools:
  remove_paywall:
    type: "stdio"
    command: "uvx"
    args: ["remove-paywall-mcp"]
```

### Docker (any client)

```json
{"command": "docker", "args": ["run", "-i", "--rm", "remove-paywall-mcp"]}
```

## Tools

### `remove_paywall`

Main tool. Removes a paywall from an article URL and returns clean article text.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | The paywalled article URL |

### `search_archives`

Search all archive sources for snapshots without extracting content. Useful to see what's available.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | The article URL to search for |

### `get_from_archive`

Fetch from a specific archive source.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | The article URL |
| `source` | string | `googlebot`, `12ft`, `iitty`, `wayback`, `archive_is`, or `wayback_available` |

### `domain_info`

Look up a domain in the knowledge base — paywall status, notes, and per-archive success rates.

| Parameter | Type | Description |
|-----------|------|-------------|
| `domain` | string | Domain name (e.g. `nytimes.com`) |

### `add_domain`

Register a domain in the knowledge base. Mark paywalled domains so archives are searched first, or non-paywalled domains so the live page is fetched directly.

| Parameter | Type | Description |
|-----------|------|-------------|
| `domain` | string | Domain name |
| `has_paywall` | boolean | `true` if the site has a paywall |
| `notes` | string? | Optional description |

## Prompts

The server provides 3 prompt templates for LLMs to use the tools effectively.

### `remove_paywall_prompt`

Full instruct for bypassing a specific URL. Tells the assistant to use `remove_paywall`, fall back to `search_archives`, and check `domain_info`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | The paywalled article URL |

### `bypass_paywall`

Short alias — just tells the assistant to call `remove_paywall` on the URL.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | string | The paywalled article URL |

### `handle_paywalls`

System prompt fragment. No arguments — returns instructions for the assistant to automatically call `remove_paywall` whenever it encounters a paywall, login wall, or metered content. Paste this into your system prompt or load it as a prompt at session start.

## Domain knowledge base

Seeded with 33 well-known paywalled domains (NYT, WSJ, Bloomberg, Medium, etc.), stored in SQLite at `~/.remove-paywall-mcp/domains.db`. Tracks every archive success/failure per domain and re-ranks archive search order automatically — domains where archive.is consistently fails won't waste time on it.

### Env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | Bind address (HTTP mode) |
| `MCP_PORT` | `8000` | Port (HTTP mode) |
| `MCP_DB_DIR` | `~/.remove-paywall-mcp` | Database directory |

## Archive sources

| Source | Priority | Notes |
|--------|----------|-------|
| Googlebot direct | 1 | Fetches directly with `Googlebot/2.1` UA — many sites (FT, WSJ) serve full content to crawlers |
| 12ft.io | 2 | Proxy at `12ft.io/proxy?q=<url>` — reliable for most hard paywalls |
| iitty | 3 | `textise.iitty.com` — plain-text rendering, works well on FT |
| Wayback Machine | 4 | CDX API, newest-first (`limit=-5`), dedup via `collapse=digest`, HTML-only |
| archive.is/ph mirrors | 5 | Tries newest/oldest across archive.ph, archive.is, archive.today, archive.md |
| Wayback Availability | 6 | `archive.org/wayback/available` — single closest snapshot as fast fallback |

Priority is dynamically re-ranked per domain based on historical success rates recorded in the knowledge base.

## Architecture

```
MCP client (Claude/OpenCode/LiteLLM)
       │  stdio or HTTP
       ▼
┌─────────────────┐
│    server.py     │  MCPServer with 5 tools
│  +3 prompts       │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│archives│ │ extractors│
│  .py   │ │   .py     │
│        │ │           │
│googlebot│ │readability│
│ 12ft   │ │Beautiful  │
│ iitty  │ │Soup       │
│ wayback│ │           │
│ archive│ │           │
│ .is/ph │ │           │
│ wayback│ │           │
│ avail  │ │           │
└───┬────┘ └──────────┘
    │
    ▼
┌──────────────┐
│domain_store  │
│   .py        │
│              │
│ SQLite knows │
│ which domains│
│ have paywalls│
│ and which    │
│ archives work│
│ best (Laplace│
│ smoothed)    │
└──────────────┘
```

## License

MIT

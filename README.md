# remove-paywall-mcp

MCP server that removes article paywalls by searching internet archives. Give it a URL, get back the article text.

## How it works

1. You give it a paywalled article URL
2. It searches internet archives (Wayback Machine, archive.is, Google Cache) for an archived copy — archives don't have paywalls because they were crawled without login gates
3. It extracts the article body with [readability-lxml](https://github.com/buriy/python-readability), stripping navigation, ads, and sidebar cruft
4. It returns clean text with the title and snapshot URL

It also learns from every attempt — success rates per domain per archive source are tracked in a local SQLite database, and archive search order is re-ranked automatically.

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
| `source` | string | `wayback`, `archive_is`, `google_cache`, or `removepaywall_com` |

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

## Domain knowledge base

Seeded with 30 well-known paywalled domains (NYT, WSJ, Bloomberg, Medium, etc.), stored in SQLite at `~/.remove-paywall-mcp/domains.db`. Tracks every archive success/failure per domain and re-ranks archive search order automatically — domains where archive.is consistently fails won't waste time on it.

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
| Wayback Machine | 1 | Most comprehensive, CDX API for snapshot discovery |
| archive.is | 2 | Often has recent snapshots but can serve gateway pages |
| Google Cache | 3 | Lightweight, sometimes the only option |
| removepaywall.com | 4 | Fallback proxy, scrapes archive links from search results |

Priority is dynamically re-ranked per domain based on historical success rates recorded in the knowledge base.

## Architecture

```
MCP client (Claude/OpenCode/LiteLLM)
       │  stdio or HTTP
       ▼
┌─────────────────┐
│    server.py     │  MCPServer with 5 tools
│    (FastMCP)     │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│archives│ │ extractors│
│  .py   │ │   .py     │
│        │ │           │
│ wayback│ │readability│
│ archive│ │Beautiful  │
│ .is    │ │Soup       │
│ google │ │           │
│ remove │ │           │
│ paywall│ │           │
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
│ best         │
└──────────────┘
```

## License

MIT

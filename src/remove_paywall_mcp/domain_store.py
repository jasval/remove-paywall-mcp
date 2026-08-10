import os
from datetime import UTC, datetime

import aiosqlite

DB_DIR = os.environ.get("MCP_DB_DIR", os.path.expanduser("~/.remove-paywall-mcp"))
DB_PATH = os.path.join(DB_DIR, "domains.db")

DEFAULT_ARCHIVES = ["wayback", "archive_is", "memento"]

SEED_DOMAINS = [
    ("nytimes.com", "Soft paywall on most articles"),
    ("wsj.com", "Hard paywall, archives often work"),
    ("bloomberg.com", "Soft paywall, archives usually work"),
    ("medium.com", "Metered paywall, archives work well"),
    ("wired.com", "Soft paywall, archives work well"),
    ("theatlantic.com", "Metered paywall"),
    ("washingtonpost.com", "Soft paywall"),
    ("economist.com", "Hard paywall, limited free articles"),
    ("ft.com", "Hard paywall"),
    ("businessinsider.com", "Soft paywall on some articles"),
    ("newyorker.com", "Metered paywall"),
    ("technologyreview.com", "Soft paywall"),
    ("latimes.com", "Soft paywall"),
    ("bostonglobe.com", "Soft paywall"),
    ("telegraph.co.uk", "Hard paywall"),
    ("thetimes.co.uk", "Hard paywall"),
    ("scientificamerican.com", "Soft paywall"),
    ("harpers.org", "Hard paywall"),
    ("foreignpolicy.com", "Metered paywall"),
    ("nationalgeographic.com", "Soft paywall"),
    ("barrons.com", "Hard paywall"),
    ("seattletimes.com", "Soft paywall"),
    ("chicagotribune.com", "Soft paywall"),
    ("vanityfair.com", "Soft paywall"),
    ("newyorkmag.com", "Soft paywall"),
    ("nymag.com", "Soft paywall"),
    ("vulture.com", "Soft paywall"),
    ("thecut.com", "Soft paywall"),
    ("grubstreet.com", "Soft paywall"),
    ("curbed.com", "Soft paywall"),
    ("fortune.com", "Soft paywall"),
    ("theinformation.com", "Hard paywall"),
    ("statnews.com", "Soft paywall"),
]


async def _ensure_db() -> aiosqlite.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS domains (
            domain      TEXT PRIMARY KEY,
            has_paywall INTEGER NOT NULL DEFAULT 1,
            notes       TEXT,
            added_at    TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS archive_attempts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            domain       TEXT NOT NULL,
            archive      TEXT NOT NULL,
            success      INTEGER NOT NULL,
            attempted_at TEXT DEFAULT (datetime('now'))
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_attempts_domain ON archive_attempts(domain)
    """)
    await db.commit()
    return db


async def seed(db: aiosqlite.Connection | None = None) -> None:
    close_after = db is None
    if db is None:
        db = await _ensure_db()
    try:
        async with db.execute("SELECT COUNT(*) as c FROM domains") as cursor:
            row = await cursor.fetchone()
            if row and row["c"] > 0:
                return

        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        await db.executemany(
            "INSERT INTO domains (domain, has_paywall, notes, added_at, updated_at) VALUES (?, 1, ?, ?, ?)",
            [(d, n, now, now) for d, n in SEED_DOMAINS],
        )
        await db.commit()
    finally:
        if close_after:
            await db.close()


async def get_domain(domain: str) -> dict | None:
    db = await _ensure_db()
    try:
        async with db.execute(
            "SELECT domain, has_paywall, notes, added_at, updated_at FROM domains WHERE domain = ?",
            (domain,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None
    finally:
        await db.close()


async def add_domain(domain: str, has_paywall: bool, notes: str | None = None) -> None:
    db = await _ensure_db()
    try:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            """INSERT INTO domains (domain, has_paywall, notes, added_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
               has_paywall = excluded.has_paywall,
               notes = COALESCE(excluded.notes, domains.notes),
               updated_at = excluded.updated_at""",
            (domain, 1 if has_paywall else 0, notes, now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def log_attempt(domain: str, archive: str, success: bool) -> None:
    db = await _ensure_db()
    try:
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT INTO archive_attempts (domain, archive, success, attempted_at) VALUES (?, ?, ?, ?)",
            (domain, archive, 1 if success else 0, now),
        )
        await db.commit()
    finally:
        await db.close()


async def get_best_archives(domain: str) -> list[str]:
    db = await _ensure_db()
    try:
        async with db.execute(
            "SELECT archive, SUM(success) as wins, COUNT(*) as total "
            "FROM archive_attempts WHERE domain = ? GROUP BY archive "
            "ORDER BY (wins + 1.0) / (total + 2.0) DESC",
            (domain,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return list(DEFAULT_ARCHIVES)

        ordered = [r["archive"] for r in rows]
        for a in DEFAULT_ARCHIVES:
            if a not in ordered:
                ordered.append(a)
        return ordered
    finally:
        await db.close()


async def get_attempt_stats(domain: str) -> list[dict]:
    db = await _ensure_db()
    try:
        async with db.execute(
            "SELECT archive, SUM(success) as successes, COUNT(*) as total "
            "FROM archive_attempts WHERE domain = ? GROUP BY archive "
            "ORDER BY (successes + 1.0) / (total + 2.0) DESC",
            (domain,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()

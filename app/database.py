"""
Database abstraction layer — supports SQLite (default) and PostgreSQL.
Uses aiosqlite for async SQLite and asyncpg for PostgreSQL.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Optional, Iterable

import aiosqlite
from app.config import settings

try:
    import asyncpg
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

_db_conn: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()
_pg_pool: Optional[Any] = None

if settings.database_url:
    if not HAS_POSTGRES:
        raise RuntimeError("DATABASE_URL is configured but asyncpg is not installed")
    DB_BACKEND = "postgresql"
else:
    DB_BACKEND = "sqlite"

INTEGRITY_ERRORS = (aiosqlite.IntegrityError,)
if HAS_POSTGRES and DB_BACKEND == "postgresql":
    INTEGRITY_ERRORS = (aiosqlite.IntegrityError, asyncpg.exceptions.UniqueViolationError)


async def init_db():
    global _db_conn, _pg_pool
    if DB_BACKEND == "postgresql":
        _pg_pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        async with _pg_pool.acquire() as conn:
            await conn.execute(SCHEMA_POSTGRES)
        await run_migrations()
    else:
        db_path = settings.db_path
        try:
            test_file = os.path.join(os.path.dirname(db_path), ".write_test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
        except Exception:
            db_path = "/tmp/panel.db"
        _db_conn = await aiosqlite.connect(db_path)
        _db_conn.row_factory = aiosqlite.Row
        await _db_conn.execute("PRAGMA journal_mode=WAL")
        await _db_conn.executescript(SCHEMA_SQLITE)
        await _db_conn.commit()
        await run_migrations()


async def close_db():
    global _db_conn, _pg_pool
    if DB_BACKEND == "sqlite" and _db_conn:
        await _db_conn.close()
        _db_conn = None
    elif DB_BACKEND == "postgresql" and _pg_pool:
        await _pg_pool.close()
        _pg_pool = None


@asynccontextmanager
async def transaction():
    """Run a group of SQL statements atomically on the active backend.

    Each operation is a tuple of (sqlite_sql, postgres_sql, params). The
    caller gets a small backend-neutral execute function. A failure rolls
    back the complete transaction rather than leaving partially-applied
    state behind.
    """
    if DB_BACKEND == "postgresql":
        async with _pg_pool.acquire() as conn:
            async with conn.transaction():
                async def _tx_execute(sqlite_q, pg_q="", params=()):
                    return await conn.execute(pg_q or sqlite_q, *params)
                yield _tx_execute
    else:
        async with _db_lock:
            try:
                await _db_conn.execute("BEGIN")
                async def _tx_execute(sqlite_q, pg_q="", params=()):
                    return await _db_conn.execute(sqlite_q, params)
                yield _tx_execute
                await _db_conn.commit()
            except Exception:
                await _db_conn.rollback()
                raise


async def execute(sqlite_q, pg_q="", params=()):
    if DB_BACKEND == "postgresql":
        async with _pg_pool.acquire() as conn:
            await conn.execute(pg_q or sqlite_q, *params)
    else:
        async with _db_lock:
            await _db_conn.execute(sqlite_q, params)
            await _db_conn.commit()


async def fetchall(sqlite_q, pg_q="", params=()):
    if DB_BACKEND == "postgresql":
        async with _pg_pool.acquire() as conn:
            rows = await conn.fetch(pg_q or sqlite_q, *params)
            return [dict(r) for r in rows]
    else:
        async with _db_lock:
            cur = await _db_conn.execute(sqlite_q, params)
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def fetchone(sqlite_q, pg_q="", params=()):
    if DB_BACKEND == "postgresql":
        async with _pg_pool.acquire() as conn:
            row = await conn.fetchrow(pg_q or sqlite_q, *params)
            return dict(row) if row else None
    else:
        async with _db_lock:
            cur = await _db_conn.execute(sqlite_q, params)
            row = await cur.fetchone()
            return dict(row) if row else None


async def run_migrations():
    """Apply small, idempotent schema upgrades for existing installations."""
    if DB_BACKEND == "postgresql":
        async with _pg_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_links_active_expires ON links(active, expires_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_timestamp ON login_logs(timestamp DESC)")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id BIGSERIAL PRIMARY KEY, timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'admin', action TEXT NOT NULL,
                    resource TEXT DEFAULT '', ip TEXT DEFAULT '', detail TEXT DEFAULT ''
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC)")
            await conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, NOW()::text) ON CONFLICT (version) DO NOTHING")
    else:
        async with _db_lock:
            await _db_conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            await _db_conn.execute("CREATE INDEX IF NOT EXISTS idx_links_active_expires ON links(active, expires_at)")
            await _db_conn.execute("CREATE INDEX IF NOT EXISTS idx_login_logs_timestamp ON login_logs(timestamp DESC)")
            await _db_conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'admin', action TEXT NOT NULL,
                    resource TEXT DEFAULT '', ip TEXT DEFAULT '', detail TEXT DEFAULT ''
                )
            """)
            await _db_conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC)")
            await _db_conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, datetime('now'))")
            await _db_conn.commit()


async def record_audit(action: str, resource: str = "", ip: str = "", detail: str = "", actor: str = "admin"):
    from datetime import datetime, timezone
    try:
        await execute(
            "INSERT INTO audit_logs (timestamp, actor, action, resource, ip, detail) VALUES (?,?,?,?,?,?)",
            "INSERT INTO audit_logs (timestamp, actor, action, resource, ip, detail) VALUES ($1,$2,$3,$4,$5,$6)",
            (datetime.now(timezone.utc).isoformat(), actor, action, resource, ip, detail),
        )
    except Exception:
        # Auditing must never break the primary operation.
        pass


LINKS_COLS = (
    "uid TEXT PRIMARY KEY, label TEXT NOT NULL, "
    "limit_bytes BIGINT DEFAULT 0, used_bytes BIGINT DEFAULT 0, "
    "max_connections INT DEFAULT 0, created_at TEXT NOT NULL, "
    "active BOOLEAN DEFAULT TRUE, expires_at TEXT, "
    "custom_path TEXT DEFAULT '', custom_sni TEXT DEFAULT '', "
    "custom_host TEXT DEFAULT '', custom_fp TEXT DEFAULT 'chrome', "
    "color TEXT DEFAULT '#39ff14', flag TEXT DEFAULT '', fragment TEXT DEFAULT ''"
)

SCHEMA_SQLITE = (
    "CREATE TABLE IF NOT EXISTS links (" + LINKS_COLS + ");"
    "CREATE TABLE IF NOT EXISTS hourly_traffic (hour TEXT PRIMARY KEY, bytes INTEGER DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS daily_traffic (day TEXT PRIMARY KEY, bytes INTEGER DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS custom_addresses (id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT NOT NULL UNIQUE);"
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
    "CREATE TABLE IF NOT EXISTS login_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, ip TEXT, success INTEGER DEFAULT 1, user_agent TEXT DEFAULT '', path TEXT DEFAULT '');"
)

SCHEMA_POSTGRES = (
    "CREATE TABLE IF NOT EXISTS links (" + LINKS_COLS + ");"
    "CREATE TABLE IF NOT EXISTS hourly_traffic (hour TEXT PRIMARY KEY, bytes BIGINT DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS daily_traffic (day TEXT PRIMARY KEY, bytes BIGINT DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS custom_addresses (id SERIAL PRIMARY KEY, address TEXT NOT NULL UNIQUE);"
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
    "CREATE TABLE IF NOT EXISTS login_logs (id SERIAL PRIMARY KEY, timestamp TEXT NOT NULL, ip TEXT, success BOOLEAN DEFAULT TRUE, user_agent TEXT DEFAULT '', path TEXT DEFAULT '');"
)

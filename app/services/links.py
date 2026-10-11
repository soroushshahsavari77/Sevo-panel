"""
Link (inbound) service layer — business logic for CRUD operations.
Decouples routes from database and state access.
"""
import asyncio
from datetime import datetime, timezone, timedelta
import uuid as uuid_lib
from app import state
from app.database import execute as db_exec, fetchone
from app.vless import generate_vless_link


DEFAULT_ADDRESSES = [
    "69.46.46.1", "69.46.46.2", "69.46.46.3", "69.46.46.4", "69.46.46.5",
    "69.46.46.6", "69.46.46.7", "69.46.46.8", "69.46.46.9", "69.46.46.10",
    "69.46.46.12", "69.46.46.15", "69.46.46.16", "69.46.46.17", "69.46.46.18",
    "69.46.46.19", "69.46.46.20", "69.46.46.21", "69.46.46.22", "69.46.46.23",
    "69.46.46.24", "69.46.46.25", "69.46.46.26", "69.46.46.27", "69.46.46.28",
    "69.46.46.29", "69.46.46.30", "69.46.46.36",
]


async def load_all():
    """Load all links and clean-IP addresses from database into memory."""
    from app.database import fetchall, fetchone
    rows = await fetchall("SELECT * FROM links", "SELECT * FROM links")
    async with state.links_lock:
        for r in rows:
            state.links[r["uid"]] = dict(r)

    addr_rows = await fetchall("SELECT address FROM custom_addresses", "SELECT address FROM custom_addresses")
    addresses = [r["address"] for r in addr_rows]

    # Seed the default clean-IP list once, on a brand new database only.
    if not addresses:
        seeded = await fetchone(
            "SELECT value FROM settings WHERE key = 'default_addresses_seeded'",
            "SELECT value FROM settings WHERE key = 'default_addresses_seeded'",
        )
        if not seeded:
            for addr in DEFAULT_ADDRESSES:
                try:
                    await db_exec(
                        "INSERT INTO custom_addresses (address) VALUES (?)",
                        "INSERT INTO custom_addresses (address) VALUES ($1)",
                        (addr,),
                    )
                except Exception:
                    pass
            await db_exec(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('default_addresses_seeded', '1')",
                "INSERT INTO settings (key, value) VALUES ('default_addresses_seeded', '1') ON CONFLICT (key) DO UPDATE SET value = '1'",
            )
            addresses = list(DEFAULT_ADDRESSES)

    async with state.addresses_lock:
        state.addresses.clear()
        state.addresses.extend(addresses)

    # Calculate total usage
    total = sum(link.get("used_bytes", 0) for link in state.links.values())
    state.stats["total_bytes"] = total

    # Create default link if none exist
    if not state.links:
        default_uid = str(uuid_lib.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        default_link = {
            "uid": default_uid, "label": "This Server is Free",
            "limit_bytes": 0, "used_bytes": 0,
            "max_connections": 0, "created_at": now, "active": 1,
            "expires_at": None, "custom_path": "", "custom_sni": "",
            "custom_host": "", "custom_fp": "chrome", "color": "#39ff14",
            "flag": "", "fragment": "",
        }
        async with state.links_lock:
            state.links[default_uid] = default_link
        await db_exec(
            "INSERT INTO links (uid, label, limit_bytes, max_connections, created_at, active, expires_at, flag, fragment) VALUES (?,?,?,?,?,1,?,'','')",
            "INSERT INTO links (uid, label, limit_bytes, max_connections, created_at, active, expires_at, flag, fragment) VALUES ($1,$2,$3,$4,$5,TRUE,$6,'','')",
            (default_uid, "This Server is Free", 0, 0, now, None),
        )


async def auto_disable_expired():
    """Periodically disable expired links."""
    while True:
        await asyncio.sleep(60)
        try:
            row = await fetchone(
                "SELECT value FROM settings WHERE key='auto_disable_enabled'",
                "SELECT value FROM settings WHERE key='auto_disable_enabled'",
            )
            if row and row["value"] != "1":
                continue
            now = datetime.now(timezone.utc)
            async with state.links_lock:
                for uid, link in state.links.items():
                    if link.get("active") and link.get("expires_at"):
                        exp = _parse_dt(link["expires_at"])
                        if exp and exp < now:
                            link["active"] = 0
                            await db_exec(
                                "UPDATE links SET active = 0 WHERE uid = ?",
                                "UPDATE links SET active = FALSE WHERE uid = $1",
                                (uid,),
                            )
                            state.log_event("Auto", f"Expired inbound {link['label']} auto-disabled")
        except Exception:
            pass


async def count_connections(uid: str) -> int:
    async with state.connections_lock:
        return sum(1 for info in state.connections.values() if info.get("uuid") == uid)


def _parse_dt(raw: str):
    try:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None

"""
Backup and restore routes.
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.security import require_auth
from app import state
from app.database import execute as db_exec, fetchall, DB_BACKEND, transaction, record_audit
from app.utils import validate_address
from datetime import datetime, timezone
import json
import uuid as uuid_lib

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
MAX_RESTORE_SIZE = 5 * 1024 * 1024


@router.get("/api/backup/full")
async def full_backup(_=Depends(require_auth)):
    async with state.links_lock:
        links = list(state.links.values())
    async with state.addresses_lock:
        addrs = list(state.addresses)
    rows = await fetchall("SELECT key, value FROM settings", "SELECT key, value FROM settings")
    protected = {"jwt_secret_key", "admin_password_hash", "auth_version", "tg_bot_token", "mtproxy_secret"}
    settings = {r["key"]: r["value"] for r in rows if r["key"] not in protected}
    return {"links": links, "addresses": addrs, "settings": settings}


@router.post("/api/restore")
@limiter.limit("3/hour")
async def restore_backup(request: Request, _=Depends(require_auth)):
    """Restore a validated backup atomically. Runtime state is updated only after commit."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_RESTORE_SIZE:
                raise HTTPException(status_code=413, detail="Backup too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")

    raw_body = await request.body()
    if len(raw_body) > MAX_RESTORE_SIZE:
        raise HTTPException(status_code=413, detail="Backup too large")
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON backup") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid backup format")

    raw_settings = body.get("settings", {})
    raw_addresses = body.get("addresses", [])
    raw_links = body.get("links", [])
    if not isinstance(raw_settings, dict) or not isinstance(raw_addresses, list) or not isinstance(raw_links, list):
        raise HTTPException(status_code=400, detail="Invalid backup structure")
    if len(raw_addresses) > 10000 or len(raw_links) > 10000:
        raise HTTPException(status_code=413, detail="Backup contains too many records")

    # Fully validate/normalize before touching the database.
    addresses = []
    for value in raw_addresses:
        addr = str(value).strip()
        if addr and validate_address(addr) and addr not in addresses:
            addresses.append(addr)

    links = []
    seen_uids = set()
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        uid = str(link.get("uid") or uuid_lib.uuid4()).strip()
        if not uid or uid in seen_uids:
            raise HTTPException(status_code=400, detail="Duplicate or invalid link UID in backup")
        try:
            uuid_lib.UUID(uid)
        except (ValueError, AttributeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid link UID in backup") from exc
        seen_uids.add(uid)
        try:
            now = datetime.now(timezone.utc).isoformat()
            normalized = {
                "uid": uid,
                "label": str(link.get("label", "Restored"))[:200],
                "limit_bytes": max(0, int(link.get("limit_bytes", 0))),
                "used_bytes": max(0, int(link.get("used_bytes", 0))),
                "max_connections": max(0, int(link.get("max_connections", 0))),
                "created_at": str(link.get("created_at", now)),
                "active": 1 if link.get("active", True) else 0,
                "expires_at": link.get("expires_at"),
                "custom_path": str(link.get("custom_path", ""))[:512],
                "custom_sni": str(link.get("custom_sni", ""))[:253],
                "custom_host": str(link.get("custom_host", ""))[:253],
                "custom_fp": str(link.get("custom_fp", "chrome"))[:64],
                "color": str(link.get("color", "#39ff14"))[:32],
                "flag": str(link.get("flag", ""))[:32],
                "fragment": str(link.get("fragment", ""))[:1024],
            }
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid link record: {exc}")
        links.append(normalized)

    link_sqlite = (
        "INSERT INTO links (uid, label, limit_bytes, used_bytes, max_connections, created_at, active, expires_at, "
        "custom_path, custom_sni, custom_host, custom_fp, color, flag, fragment) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    link_pg = (
        "INSERT INTO links (uid, label, limit_bytes, used_bytes, max_connections, created_at, active, expires_at, "
        "custom_path, custom_sni, custom_host, custom_fp, color, flag, fragment) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)"
    )

    try:
        async with transaction() as tx:
            # Restore only the settings represented by the backup.
            protected = {"jwt_secret_key", "admin_password_hash", "auth_version", "tg_bot_token", "mtproxy_secret"}
            for key, value in raw_settings.items():
                key = str(key)[:128]
                if key in protected:
                    raise HTTPException(status_code=400, detail=f"Protected setting cannot be restored: {key}")
                await tx(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
                    (key, str(value)),
                )

            if "addresses" in body:
                await tx("DELETE FROM custom_addresses", "DELETE FROM custom_addresses")
                for addr in addresses:
                    await tx(
                        "INSERT INTO custom_addresses (address) VALUES (?)",
                        "INSERT INTO custom_addresses (address) VALUES ($1)",
                        (addr,),
                    )

            if "links" in body:
                await tx("DELETE FROM links", "DELETE FROM links")
                for link in links:
                    await tx(
                        link_sqlite, link_pg,
                        (link["uid"], link["label"], link["limit_bytes"], link["used_bytes"],
                         link["max_connections"], link["created_at"],
                         bool(link["active"]) if DB_BACKEND == "postgresql" else link["active"],
                         link["expires_at"], link["custom_path"], link["custom_sni"], link["custom_host"],
                         link["custom_fp"], link["color"], link["flag"], link["fragment"]),
                    )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Restore failed; no changes were committed: {exc}")

    # Commit succeeded: now replace in-memory state so DB and runtime agree.
    if "addresses" in body:
        async with state.addresses_lock:
            state.addresses = list(addresses)
    if "links" in body:
        async with state.links_lock:
            state.links = {link["uid"]: link for link in links}
        state.link_cache.clear()

    await record_audit("backup_restore", resource="full", detail=f"links={len(links)} addresses={len(addresses)}")
    state.log_event("Backup", f"Restore completed: {len(links)} links, {len(addresses)} addresses")
    return {"ok": True, "links": len(links), "addresses": len(addresses)}


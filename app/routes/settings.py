"""
Panel settings routes.
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.security import require_auth
from app import state
from app.database import execute as db_exec, fetchall, fetchone, record_audit

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


SETTING_KEYS = [
    "tg_bot_token", "tg_chat_id", "footer_text", "default_path",
    "log_enabled", "timezone_offset", "default_limit_bytes",
    "default_expiry_days", "default_max_connections",
    "telegram_events", "telegram_interval", "keep_alive_interval",
    "keep_alive_enabled", "keep_alive_mode", "theme_color",
    "telegram_templates_en", "telegram_templates_fa", "telegram_lang",
    "default_lang", "auto_disable_enabled", "telegram_report_enabled",
    "telegram_notify_enabled", "monthly_limit_gb",
]


@router.get("/api/settings")
async def get_settings(_=Depends(require_auth)):
    rows = await fetchall("SELECT key, value FROM settings", "SELECT key, value FROM settings")
    stored = {r["key"]: r["value"] for r in rows}
    return {k: stored.get(k, "") or "" for k in SETTING_KEYS}


@router.post("/api/settings")
@limiter.limit("30/minute")
async def save_settings(request: Request, _=Depends(require_auth)):
    body = await request.json()
    for k in SETTING_KEYS:
        if k in body:
            val = str(body[k]).strip()
            await db_exec(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
                (k, val),
            )

    # Update runtime state
    if "log_enabled" in body:
        state.logging_enabled = body["log_enabled"] == "1"
    if "keep_alive_enabled" in body:
        state.keep_alive_enabled = body["keep_alive_enabled"] == "1"
    if "keep_alive_mode" in body:
        state.keep_alive_mode = body["keep_alive_mode"]
    if "keep_alive_interval" in body:
        try:
            state.keep_alive_interval = max(60, int(body["keep_alive_interval"]))
        except Exception:
            pass
    await record_audit("settings_updated", resource="settings")
    if "timezone_offset" in body:
        try:
            state.timezone_offset = float(body["timezone_offset"])
        except Exception:
            state.timezone_offset = 0.0
    return {"ok": True}


@router.post("/api/settings/reset")
@limiter.limit("5/hour")
async def reset_settings(request: Request, _=Depends(require_auth)):
    PROTECTED = {"jwt_secret_key", "admin_password_hash"}
    all_rows = await fetchall("SELECT key FROM settings", "SELECT key FROM settings")
    for row in all_rows:
        if row["key"] not in PROTECTED:
            await db_exec("DELETE FROM settings WHERE key = ?", "DELETE FROM settings WHERE key = $1", (row["key"],))
    state.logging_enabled = True
    state.keep_alive_interval = 300
    state.timezone_offset = 0.0
    state.keep_alive_enabled = True
    state.keep_alive_mode = "simple"
    await record_audit("settings_reset", resource="settings", ip=request.client.host if request.client else "")
    return {"ok": True}


@router.get("/api/public-settings")
async def public_settings():
    row = await fetchone(
        "SELECT value FROM settings WHERE key = 'footer_text'",
        "SELECT value FROM settings WHERE key = 'footer_text'",
    )
    return {"footer_text": row["value"] if row else ""}

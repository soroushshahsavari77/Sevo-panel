"""
Telegram bot integration - notifications, reports, alerts.
"""
import asyncio
import httpx
from datetime import datetime, timezone
from app.config import settings
from app import state
from app.database import fetchone


async def _get_tg_config():
    token_row = await fetchone(
        "SELECT value FROM settings WHERE key='tg_bot_token'",
        "SELECT value FROM settings WHERE key='tg_bot_token'",
    )
    chat_row = await fetchone(
        "SELECT value FROM settings WHERE key='tg_chat_id'",
        "SELECT value FROM settings WHERE key='tg_chat_id'",
    )
    if token_row and chat_row and token_row["value"] and chat_row["value"]:
        return token_row["value"], chat_row["value"]
    return None


async def _get_lang():
    row = await fetchone(
        "SELECT value FROM settings WHERE key='telegram_lang'",
        "SELECT value FROM settings WHERE key='telegram_lang'",
    )
    return row["value"] if row and row["value"] else "en"


async def _get_templates(lang):
    row = await fetchone(
        "SELECT value FROM settings WHERE key='telegram_templates_" + lang + "'",
        "SELECT value FROM settings WHERE key='telegram_templates_" + lang + "'",
    )
    if row and row["value"]:
        try:
            import json
            return json.loads(row["value"])
        except Exception:
            pass
    return {}


async def _is_notify_enabled():
    row = await fetchone(
        "SELECT value FROM settings WHERE key='telegram_notify_enabled'",
        "SELECT value FROM settings WHERE key='telegram_notify_enabled'",
    )
    return row and row["value"] == "1"


async def notify_login(ip, ua):
    if not await _is_notify_enabled():
        return
    config = await _get_tg_config()
    if not config:
        return
    token, chat_id = config
    lang = await _get_lang()
    templates = await _get_templates(lang)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    if lang == "fa":
        default = "\U0001f510 \u0648\u0631\u0648\u062f SE7O-SNA\n\U0001f310 IP: " + ip + "\n\U0001f916 UA: " + ua + "\n\U0001f4c5 " + now_str
    else:
        default = "\U0001f510 SE7O-SNA Panel login\n\U0001f310 IP: " + ip + "\n\U0001f916 UA: " + ua + "\n\U0001f4c5 " + now_str

    msg = templates.get("login", default)
    msg = msg.replace("{ip}", ip).replace("{ua}", ua).replace("{time}", now_str)
    msg += '\n\n<a href="https://' + settings.domain + '/panel">Open SE7O-SNA Panel</a>'

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                "https://api.telegram.org/bot" + token + "/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            )
    except Exception:
        pass


async def notify_event(event, label, uid):
    if not await _is_notify_enabled():
        return
    config = await _get_tg_config()
    if not config:
        return
    token, chat_id = config
    lang = await _get_lang()
    templates = await _get_templates(lang)

    if lang == "fa":
        default = "\u0631\u0648\u06cc\u062f\u0627\u062f: " + event + " \u0628\u0631\u0627\u06cc " + label
    else:
        default = "Event: " + event + " for " + label

    msg = templates.get(event, default)
    msg = msg.replace("{label}", label).replace("{uid}", uid)
    msg += '\n\n<a href="https://' + settings.domain + '/panel">Open SE7O-SNA Panel</a>'

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                "https://api.telegram.org/bot" + token + "/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            )
    except Exception:
        pass


async def report_loop():
    while True:
        interval_hours = 1
        row = await fetchone(
            "SELECT value FROM settings WHERE key='telegram_interval'",
            "SELECT value FROM settings WHERE key='telegram_interval'",
        )
        if row and row["value"]:
            try:
                interval_hours = float(row["value"])
            except Exception:
                pass
        await asyncio.sleep(3600 * interval_hours)

        en_row = await fetchone(
            "SELECT value FROM settings WHERE key='telegram_report_enabled'",
            "SELECT value FROM settings WHERE key='telegram_report_enabled'",
        )
        if not en_row or en_row["value"] != "1":
            continue

        config = await _get_tg_config()
        if not config:
            continue
        token, chat_id = config

        from app.utils import fmt_bytes
        secs = int(abs(datetime.now(timezone.utc).timestamp() - state.stats["start_time"]))
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        uptime_str = "{:02d}:{:02d}:{:02d}".format(h, m, s)
        traffic_str = fmt_bytes(state.stats["total_bytes"])
        conns_str = str(len(state.connections))
        reqs_str = str(state.stats["total_requests"])
        errs_str = str(state.stats["total_errors"])

        msg = "\U0001f4ca SE7O-SNA Panel Stats\n"
        msg += "\U0001f552 Uptime: " + uptime_str + "\n"
        msg += "\U0001f517 Conns: " + conns_str + "\n"
        msg += "\U0001f4e6 Traffic: " + traffic_str + "\n"
        msg += "\U0001f4e1 Requests: " + reqs_str + "\n"
        msg += "\u274c Errors: " + errs_str

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://api.telegram.org/bot" + token + "/sendMessage",
                    json={"chat_id": chat_id, "text": msg},
                )
        except Exception:
            pass

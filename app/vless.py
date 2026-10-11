"""
VLESS protocol utilities - link generation, subscription content,
and binary header parsing.
"""
import json
import time
import base64
from urllib.parse import quote
from app.config import settings
from app import state


def get_domain():
    return settings.domain


def format_host_port(host, port=443):
    import ipaddress
    host = host.strip("[]")
    try:
        ipaddress.IPv6Address(host)
        return f"[{host}]:{port}"
    except ipaddress.AddressValueError:
        return f"{host}:{port}"


def code_to_flag(code):
    if not code or len(code) != 2:
        return ""
    code = code.upper()
    try:
        return chr(ord(code[0]) + 127397) + chr(ord(code[1]) + 127397)
    except Exception:
        return ""


def generate_vless_link(uid, remark="SE7O", address=None, extra=None):
    """
    extra["transport"] selects the wire transport:
      - "ws" (default)   -> WebSocket over the main HTTPS port (unchanged)
      - "xhttp"           -> Xray XHTTP/splithttp, stream-up mode, same
                              HTTPS port as ws (no new port needed)
      - "tcp"             -> raw VLESS TCP, no TLS/WS/HTTP framing at all;
                              connects directly to raw_tcp_port and requires
                              that port to actually be exposed by the host
                              (see config.py raw_tcp_* settings)
    """
    transport = (extra.get("transport") or "ws") if extra else "ws"
    cache_key = f"{uid}:{remark}:{address}:{transport}:{json.dumps(extra) if extra else ''}"
    cached = state.link_cache.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached["link"]

    domain = get_domain()
    addr = address or domain
    sni = (extra.get("custom_sni") or domain) if extra else domain
    host = (extra.get("custom_host") or domain) if extra else domain
    fp = (extra.get("custom_fp") or "chrome") if extra else "chrome"
    fragment = extra.get("fragment", "") if extra else ""

    if transport == "tcp":
        # Plain TCP, no TLS/WS — the raw listener terminates VLESS directly.
        port = int(settings.raw_tcp_public_port or settings.raw_tcp_port)
        params = {"encryption": "none", "security": "none", "type": "tcp"}
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        safe_remark = quote(remark.encode("utf-8", errors="replace").decode("utf-8"))
        link = f"vless://{uid}@{format_host_port(addr, port)}?{query}#{safe_remark}"
        state.link_cache[cache_key] = {"link": link, "expires": time.time() + settings.link_cache_ttl}
        return link

    if transport == "xhttp":
        path = (extra.get("custom_path") or f"/xhttp/{uid}") if extra else f"/xhttp/{uid}"
        params = {
            "encryption": "none", "security": "tls", "type": "xhttp", "mode": "stream-up",
            "host": host, "path": path, "sni": sni, "fp": fp, "alpn": "http/1.1",
        }
    else:
        path = (extra.get("custom_path") or f"/ws/{uid}") if extra else f"/ws/{uid}"
        params = {
            "encryption": "none", "security": "tls", "type": "ws",
            "host": host, "path": path, "sni": sni, "fp": fp, "alpn": "http/1.1",
        }
    if fragment:
        params["fragment"] = fragment

    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    safe_remark = quote(remark.encode("utf-8", errors="replace").decode("utf-8"))
    link = f"vless://{uid}@{format_host_port(addr, 443)}?{query}#{safe_remark}"

    state.link_cache[cache_key] = {"link": link, "expires": time.time() + settings.link_cache_ttl}
    return link


def generate_subscription_content(link, uid, addresses, extra=None, status="active"):
    used = link.get("used_bytes", 0)
    limit = link.get("limit_bytes", 0)
    usage_str = f"{_fmt_bytes(used)} / \u221e" if limit == 0 else f"{_fmt_bytes(used)} / {_fmt_bytes(limit)}"

    secs_left = _seconds_until_expiry(link.get("expires_at"))
    expiry_str = "\u221e" if secs_left is None else ("Expired" if secs_left == 0 else f"{secs_left // 86400} Days Left")

    status_remark = ""
    if status == "quota_exceeded":
        status_remark = " Quota Exceeded"
    elif status == "expired":
        status_remark = " Expired"
    elif status == "blocked":
        status_remark = " Blocked"

    full_remark = f"{usage_str} | {expiry_str}"
    if status_remark:
        full_remark += f" | {status_remark}"

    flag_emoji = code_to_flag(link.get("flag", ""))
    if flag_emoji:
        full_remark = flag_emoji + " " + full_remark

    status_node = generate_vless_link(uid, remark=full_remark, address="0.0.0.0", extra=extra)
    lbl = link.get("label", "")
    server_remark = f"{flag_emoji}SE7O-SNA / This Service is Free" if flag_emoji else "SE7O-SNA / This Service is Free"
    server_node = generate_vless_link(uid, remark=server_remark, extra=extra)

    links_list = [status_node, server_node]
    for i, addr in enumerate(addresses):
        if flag_emoji:
            r = flag_emoji + "SE7O-" + lbl + "-IP" + str(i + 1)
        else:
            r = "SE7O-" + lbl + "-IP" + str(i + 1)
        links_list.append(generate_vless_link(uid, remark=r, address=addr, extra=extra))

    return "\n".join(links_list)


def encode_subscription(content):
    return base64.b64encode(content.encode("utf-8")).decode("utf-8")


async def parse_vless_header(first_chunk):
    if len(first_chunk) < 24:
        raise ValueError("VLESS header too small")
    pos = 1 + 16
    addon_len = first_chunk[pos]
    pos += 1 + addon_len
    if len(first_chunk) < pos + 3:
        raise ValueError("Malformed VLESS header")
    command = first_chunk[pos]
    pos += 1
    port = int.from_bytes(first_chunk[pos:pos + 2], "big")
    pos += 2
    addr_type = first_chunk[pos]
    pos += 1

    if addr_type == 1:
        if len(first_chunk) < pos + 4:
            raise ValueError("Incomplete IPv4")
        address = ".".join(str(b) for b in first_chunk[pos:pos + 4])
        pos += 4
    elif addr_type == 2:
        if len(first_chunk) < pos + 1:
            raise ValueError("Missing domain length")
        domain_len = first_chunk[pos]
        pos += 1
        if len(first_chunk) < pos + domain_len:
            raise ValueError("Incomplete domain")
        address = first_chunk[pos:pos + domain_len].decode("utf-8", errors="ignore")
        pos += domain_len
    elif addr_type == 3:
        if len(first_chunk) < pos + 16:
            raise ValueError("Incomplete IPv6")
        ab = first_chunk[pos:pos + 16]
        address = ":".join(f"{ab[i]:02x}{ab[i + 1]:02x}" for i in range(0, 16, 2))
        pos += 16
    else:
        raise ValueError(f"Unsupported address type: {addr_type}")

    return command, address, port, first_chunk[pos:]


def _fmt_bytes(b):
    if not b:
        return "0 B"
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f}GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f}MB"
    return f"{b / 1024:.1f}KB"


def _seconds_until_expiry(expires_at_str):
    if not expires_at_str:
        return None
    try:
        from datetime import datetime, timezone
        s = expires_at_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None

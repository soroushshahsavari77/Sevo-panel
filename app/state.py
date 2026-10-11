"""
In-memory state — replaces scattered global variables with a single,
organized state object. All mutable state lives here, all access is
through documented attributes.
"""
import asyncio
import time
from collections import defaultdict, deque
from typing import Any


class AppState:
    """Centralized in-memory state for the panel."""

    def __init__(self, max_log_entries: int = 2000):
        # Links (inbound configs)
        self.links: dict[str, dict] = {}
        self.links_lock = asyncio.Lock()

        # Custom clean-IP addresses
        self.addresses: list[str] = []
        self.addresses_lock = asyncio.Lock()

        # Active WebSocket connections
        self.connections: dict[str, dict] = {}
        self.connections_lock = asyncio.Lock()
        self.connection_sockets: dict[str, Any] = {}
        self.link_ip_map: dict[str, set] = defaultdict(set)

        # Traffic stats
        self.stats = {
            "total_bytes": 0,
            "total_requests": 0,
            "total_errors": 0,
            "start_time": time.time(),
            "upload_bytes": 0,
            "download_bytes": 0,
        }

        # Traffic buffer (flushed to DB periodically)
        self.traffic_buffer_lock = asyncio.Lock()
        self.traffic_buffer: dict[str, defaultdict] = {
            "hourly": defaultdict(int),
            "daily": defaultdict(int),
        }

        # Error/event logs (in-memory ring buffer)
        self.error_logs: deque = deque(maxlen=max_log_entries)

        # Link generation cache
        self.link_cache: dict[str, dict] = {}

        # Runtime settings (loaded from DB, mutable)
        self.timezone_offset: float = 0.0
        self.keep_alive_enabled: bool = True
        self.keep_alive_mode: str = "simple"
        self.keep_alive_interval: int = 300
        self.logging_enabled: bool = True

        # JWT secret (loaded from DB)
        self.secret_key: str = ""
        self.admin_username: str = ""
        self.admin_password_hash: str = ""
        # Incremented whenever admin credentials change; invalidates old JWTs.
        self.auth_version: int = 0

        # MTProto proxy
        self.mtproxy_secret: str = ""
        self.mtproxy_config: dict[str, str] = {
            "bind_port": "",
            "public_host": "",
            "public_port": "",
        }

        # XHTTP transport — sessions correlate a GET (download) request with
        # its matching POST (upload) request. Kept separate from
        # state.connections/connection_sockets because those assume a
        # Starlette WebSocket object (close(code=...)); xhttp sessions carry
        # a raw asyncio StreamWriter instead, and mixing the two would break
        # any code that iterates connection_sockets expecting a WebSocket.
        self.xhttp_sessions: dict[str, dict] = {}
        self.xhttp_lock = asyncio.Lock()

        # Raw-TCP VLESS listener (plain type=tcp inbound, no WS/HTTP framing)
        self.raw_tcp_connections: dict[str, dict] = {}
        self.raw_tcp_lock = asyncio.Lock()

    def log_event(self, etype: str, message: str, ip: str = "", ua: str = ""):
        """Append an event to the in-memory log buffer."""
        from datetime import datetime, timezone
        self.error_logs.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "type": etype,
            "error": message or "(no detail)",
            "ip": ip,
            "ua": ua,
        })


# Global singleton
state = AppState()

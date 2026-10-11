<div align="center">

<img src="app/static/img/logo.png" alt="SE7O-SNA" width="140">

# SE7O-SNA Panel v3

**A self-hosted subscription panel for VLESS over WebSocket + TLS.**

Modular Python application · FastAPI · SQLite or PostgreSQL

[English](README.md) · [فارسی](README.fa.md)

</div>

---

## Overview

SE7O-SNA Panel manages VLESS inbounds and subscription links from one dashboard. Version 2
replaces the single-file design of v1 with a modular package: configuration, database access,
state, security, routes, and background services each live in their own module, while the web UI
remains a single embedded HTML template. Deployment is unchanged — push the repository to any
container platform and set a handful of environment variables. No database server, reverse proxy,
or build pipeline is required.

## Features

**Access control**
Username and password login, JWT sessions in HTTP-only cookies, a rate-limited login endpoint, an enforced
password policy, and an audit log of every login attempt with IP and user agent.

**Inbound management**
Create, edit, enable, disable, and delete VLESS configurations. Set per-inbound traffic quotas,
expiry dates, and concurrent-connection limits. Override path, SNI, host, and TLS fingerprint
individually, add packet-fragmentation ranges against DPI, and assign country flags. Bulk actions
cover activation, quota resets, and deletion. Inbounds can be exported to and imported from JSON.

**Analytics**
Live upload and download charts, 24-hour timezone-aware traffic history, per-inbound
distribution, monthly quota tracking, and CPU, memory, and disk monitoring.

**Clean IP list**
Add, edit, bulk-import, and bulk-delete IPv4 and IPv6 addresses that are attached to generated
subscriptions. A default set of clean addresses is seeded on first run and can be cleared or
replaced entirely from the dashboard.

**Per-user page**
Every inbound has a public page at `/user/{uid}` showing status, data usage, expiry, a QR code,
and one-tap copy buttons for the subscription and single VLESS links — shareable with end users
without granting panel access.

**Telegram notifications**
Bilingual (English/Persian) templates for panel logins, expired inbounds, errors, and quota
warnings, with a live template preview in the dashboard.

**Telegram MTProxy**
An MTProto proxy (`mtg`) runs as a child process inside the same container. The FakeTLS secret is
generated on first start and persisted, and the panel builds the `tg://proxy` link automatically
from the platform's TCP proxy address. The MTProxy tab shows process state, the public endpoint,
a QR code, and lets you rotate the secret or change the fronting domain.

**Keep-alive**
Simple and advanced anti-sleep modes with a configurable interval, for platforms that idle out
free instances.

---

## Quick start

### 1. Fork

Fork this repository to your GitHub account.

### 2. Deploy

Connect the fork to a platform that supports WebSocket and Docker builds. The included
`Dockerfile` is detected automatically; no start command is needed.

Recommended platforms, none of which require a credit card or phone number:

| Platform | Free tier | Sleeps | Persistent volume |
|----------|-----------|--------|-------------------|
| Railway  | $5 credit / month | No, with keep-alive | Yes (1 GB) |
| Render   | 750 hours / month | Yes, after 15 min | Yes (1 GB) |
| Dockfly  | 1 project, 256 MB | No | Yes |
| Back4app | 0.25 CPU, 256 MB | No | No |
| Scalingo | 30-day trial | No | No |

Koyeb, Fly.io, Northflank, and Zeabur also work but require a card or phone number at signup.

### 3. Configure

Add these environment variables in the platform dashboard:

| Variable | Required | Example | Description |
|----------|----------|---------|-------------|
| `ADMIN_USERNAME` | Recommended | `se7oadmin` | Panel login username. Defaults to `admin`. |
| `ADMIN_PASSWORD` | Yes | `StrongPass!123` | Panel login password. Minimum 8 characters with upper case, lower case, and a digit. |
| `SECRET_KEY` | Yes | *(long random string)* | Signs JWT session cookies. If unset, a new key is generated on first start and stored in the database. |
| `DOMAIN` | Recommended | `se7o-sna.up.railway.app` | Public domain, used to build subscription links correctly. Auto-detected on Railway and Render. |
| `DB_PATH` | Recommended | `/data/panel.db` | SQLite file location. Mount a persistent volume at `/data` to keep data across restarts. |
| `DATABASE_URL` | Optional | `postgres://…` | External PostgreSQL connection string. Overrides `DB_PATH`. |
| `PORT` | Optional | `8080` | Listening port. Most platforms inject their own value. |
| `MTPROXY_ENABLED` | Optional | `true` | Set to `false` to run the panel without the Telegram proxy. |
| `MTPROXY_PORT` | Optional | `443` | Port mtg binds to *inside* the container. This is the value you enter when creating the TCP proxy. |
| `MTPROXY_FAKE_DOMAIN` | Optional | `www.cloudflare.com` | Domain the FakeTLS secret impersonates. |
| `MTPROXY_SECRET` | Optional | `ee…` | Pin a specific secret instead of generating one. |
| `MTPROXY_PUBLIC_HOST` | Optional | `shuttle.proxy.rlwy.net` | Override the public host, for platforms that do not expose it automatically. |
| `MTPROXY_PUBLIC_PORT` | Optional | `15140` | Override the public port. |

### 4. Enable the Telegram proxy

Railway can expose HTTP and TCP from the same service. In the service settings open
**Networking → TCP Proxy** and enter the *internal* port `443` (or whatever `MTPROXY_PORT` is set
to). Railway responds with a public address such as `shuttle.proxy.rlwy.net:15140`.

Nothing else is required. Railway injects `RAILWAY_TCP_PROXY_DOMAIN` and `RAILWAY_TCP_PROXY_PORT`
into the container, the panel reads them, and the MTProxy tab shows the finished link:

```
tg://proxy?server=shuttle.proxy.rlwy.net&port=15140&secret=ee…
```

The link must always carry that public address. The container's own IP is private and unreachable
from the internet, so it never appears in the link. Until a TCP proxy exists the tab shows a
warning and a provisional address.

On platforms that do not publish those variables, set `MTPROXY_PUBLIC_HOST` and
`MTPROXY_PUBLIC_PORT` yourself.

### 5. Sign in

Open your deployment URL at `/panel` and log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
Both credentials can be changed later from **Settings**; changes persist in the database and
take precedence over the environment variables.

---

## Data persistence

Platforms with volume support keep the SQLite database across restarts — mount the volume at
`/data` and set `DB_PATH=/data/panel.db`. Where volumes are unavailable, set `DATABASE_URL` to an
external PostgreSQL instance instead; the application detects it at startup and uses it in place
of SQLite.

## Repository layout

| Path | Purpose |
|------|---------|
| `main.py` | Entry point: logging, lifespan, app factory, and router registration. |
| `app/config.py` | Environment-driven configuration singleton. |
| `app/database.py` | Async SQLite/PostgreSQL abstraction and schema. |
| `app/state.py` | Centralized in-memory state and event log. |
| `app/security.py` | Bcrypt hashing, JWT creation, and the auth dependency. |
| `app/vless.py` | VLESS link generation, subscription content, and header parsing. |
| `app/utils.py` | Pure formatting and validation helpers. |
| `app/models.py` | Pydantic request models. |
| `app/routes/` | HTTP and WebSocket endpoints, grouped by domain. |
| `app/services/` | Background loops: traffic, keep-alive, Telegram, link expiry. |
| `app/services/mtproxy.py` | MTProto proxy supervisor, secret handling, link building. |
| `app/templates/panel_html.py` | The embedded single-page admin frontend. |
| `app/templates/user_html.py` | The public per-user subscription page. |
| `app/static/img/` | Optimized logo and favicon assets. |
| `smoke_test.py` | End-to-end test suite against a running instance. |
| `test_mtproxy.py` | Unit tests for secrets, endpoint resolution and the supervisor. |

## Production security

- `railway.toml` intentionally contains no administrator password or JWT secret. Configure `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `SECRET_KEY` as hosting-provider secrets.
- Production startup fails closed when those required secrets are missing or too weak.
- CORS is same-origin by default; use `CORS_ORIGINS` only when a separate trusted frontend is required.
- Backup restore is transactional: validation happens before the database is changed, and failed restores are rolled back.
- Background workers are tracked and cancelled cleanly during shutdown.
- Database startup applies idempotent schema migrations and indexes.

## Local development

```bash
pip install -r requirements.txt
DB_PATH=./panel.db ADMIN_USERNAME=admin ADMIN_PASSWORD="LocalStrong123" ENVIRONMENT=development python main.py
```

The panel is then served at `http://127.0.0.1:8080/panel`.

For production, set `ENVIRONMENT=production`, `SECRET_KEY` (32+ chars), `ADMIN_USERNAME`, and
`ADMIN_PASSWORD` (12+ chars) through your hosting provider's secret/environment settings.
Do not put those values in `railway.toml` or Git.

To run the smoke test, start the server on port 8899 and pass the test credentials through
`SE7O_TEST_USERNAME` and `SE7O_TEST_PASSWORD` if they differ from the defaults, then run:

```bash
python smoke_test.py
```

The MTProxy unit tests need no server and no Docker:

```bash
python test_mtproxy.py
```

## Bandwidth costs

The panel is free; outbound bandwidth is billed by your hosting provider. Approximate figures,
subject to change — always confirm on the provider's pricing page.

| Platform | Included bandwidth | Cost per extra GB |
|----------|--------------------|-------------------|
| Railway  | Pay as you go | ~$0.10 |
| Render   | 5 GB / month | ~$0.10 |
| Back4app | 100 GB / month | See provider |
| Dockfly  | Not specified | See provider |
| Scalingo | Not specified | See provider |

Use per-inbound quotas to keep consumption predictable.

---

## Disclaimer

This software is provided free of charge for personal, educational, and experimental use.
It is not for sale, and it must not be used to sell VPN subscriptions or to abuse the free tiers
of hosting providers through duplicate accounts. You are solely responsible for your traffic and
for compliance with your provider's terms of service. The author accepts no liability for
damages, billing overages, or service violations.

## Acknowledgements

Built with [FastAPI](https://fastapi.tiangolo.com/). Charts by [Chart.js](https://www.chartjs.org/).
Icons are inline SVG in the Lucide/Feather style, themed with `currentColor`.

## License

See [LICENSE](LICENSE).

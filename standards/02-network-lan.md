# 02 — Network / LAN Architecture

| Field | Value |
|---|---|
| Type | Reference / Standard |
| Audience | All agents, all projects |
| Status | **Active** (pre-filled from evidence 2026-06-09; topology resolved to VLAN-segmented; some sub-fields still UNKNOWN) |
| Owner | Filipe Coelho |
| Pre-filled | 2026-06-09, by mining Claude Code session transcripts + project configs |

> `FACT` / `INFER` / `CONFLICT` / `UNKNOWN` as in `01`. The **port table (§C)** and the
> **bind/posture rules (§D–E)** are the highest-value, well-evidenced parts. The
> **topology (§A)** has a genuine conflict you must resolve.

---

## A. Topology & addressing — VLAN-segmented (FortiGate live)

The network is **segmented into VLANs behind a FortiGate** firewall (with FortiSwitch
`LS-S148FN`); the FortiGate is the **DNS server**. (It was historically a single flat
`192.168.1.0/24` on the Huawei router, DHCP `.100–.199` — that's the **pre-segmentation
past**, not current.)

**VLANs (confirmed):**
- **`VLAN_PRODUCAO` = `192.168.10.0/24`** (gw `.254`) — production-floor clients.
- **`VLAN_WIFI` = `172.30.1.0/24`** (gw `.254`, 2.4 GHz) — **CNC machines live here**.
- **`VLAN_MGMT`** — host seen at `192.168.100.7`.
- **`VLAN_NAS`** — the Synology DS220+ at `192.168.2.254` (`192.168.2.0/24`).
- App-host subnet `192.168.1.0/24` — Windows Docker host at `.114`.
- Named, **subnet not yet captured (UNKNOWN)**: `VLAN_FATURACAO`, `VLAN_USERS`,
  `VLAN_IMPRESSORAS`, `VLAN_GUEST`, `VLAN_LAN`.

**Addressing:** servers/hosts use **fixed IPs**; clients via FortiGate **DHCP**.

## B. Key hosts

| Hostname(s) | IP | Role | Conf |
|---|---|---|---|
| `custos`, `vector-process` (`*.lan.lindoservico.pt`) | `192.168.1.114` | Windows Docker/app host + file share (`Z:`) | FACT |
| `maquinabilidade`, `wiki`, `oldwiki`, `inventario-3018`, `z` | `192.168.2.254` | Synology DS220+ (NAS + Docker apps + DokuWiki) | FACT |
| `cnc-01..05` (`*.cnc.lindoservico.pt`) | **`172.30.1.11–.15`** (on `VLAN_WIFI`) | CNC controllers | FACT |
| FluidNC / VigoStick | `fluidnc.local`, `vigostick.local`; VigoSticks seen at `192.168.1.157/.158`, one FluidNC at `192.168.1.25` (SSID `LS-NET`) | machine controllers on Wi-Fi | FACT |

**DNS:** FortiGate provides **split-horizon DNS**; internal domains
**`*.lan.lindoservico.pt`** (apps) and **`*.cnc.lindoservico.pt`** (machines), plus
mDNS. **FACT.**

## C. Port allocation table — FACT (high value)

> **Claim a port here before hard-coding it.** Bind `0.0.0.0` only when the service is
> meant to be LAN-reachable behind Caddy; otherwise `127.0.0.1`.

| Port | App / service | Host | Bind | Conf |
|---|---|---|---|---|
| **80** | **Caddy** reverse proxy → app:8080 (HTTP only) | Windows `.114` | 0.0.0.0 | FACT |
| **8080** | Default FastAPI/uvicorn **container** port; inventory app on NAS; FluidNC orchestrator gateway (`/health`) | NAS / various | 0.0.0.0 or localhost | FACT |
| **8081 → 8080** | **materials-costing** (faturação) host:container | Windows `.114` | host map | FACT |
| **8042** | **email-2-data** API | dev/loopback | **127.0.0.1** | FACT |
| **8010** | **svg-to-dxf-optimizer** web | dev | **127.0.0.1** | FACT |
| **5432** | PostgreSQL 16 (materials) — **NOT host-exposed in prod** (SSH tunnel only) | Windows `.114` | loopback | FACT |
| **3001** | FluidNC orchestrator-client adapter | localhost | — | FACT |
| **81** | DokuWiki | NAS `.254` | — | FACT |
| **993 (SSL)** | IMAP `mail.lindoservico.pt` | mail server | — | FACT |
| **9100 / 515 / 631 / 3911** | Network printer (RAW/LPR/IPP) | `192.168.20.3` | — | FACT |
| **8000** | ⛔ **BANNED — never reuse.** Squatted by the VFMO container (`host.docker.internal:8000`) | — | — | **RULE** |
| **8765 / 8766 / 8767** | **Ephemeral dev-server ports only — NO stable owner** (correction: these are transient, not assignments) | — | — | FACT |

**Port-range convention (ADOPTED 2026-06-09):** internal web apps draw from
**`8010–8099`**, **claimed per app in the table above** before hard-coding. **`8000`
is permanently banned.** Bind `127.0.0.1` for local-only; `0.0.0.0` only when the
service sits behind Caddy.

## D. Access & security posture — FACT

| Field | Value | Conf |
|---|---|---|
| Internal transport | **Plain HTTP on the LAN** (Caddy publishes `:80` only; HTTPS/cert blocks deferred) | FACT |
| Auth (materials-costing) | **JWT + httpOnly cookies + Google OAuth**, invite-only + admin-approved registration, RBAC (default `viewer`), rate-limited login, append-only **audit log**; app refuses to start without `JWT_SECRET` | FACT |
| Auth (other internal tools) | Varies; some open on the trusted LAN — **confirm per app** | INFER |
| Email | **Self-hosted SMTP/IMAP only**, no third-party SaaS | FACT |
| Reverse proxy | **Caddy 2.8-alpine**, container name **`reverse-proxy`** (compose project `lan_reverse_proxy` — use the container name for `docker exec`), Caddyfile on the Windows host | FACT |

## E. Secrets, data residency & internet posture — FACT

- **Secrets** live in **gitignored** `.env` / `.env.prod` / `.env.windows`; **SSH
  passwords are prompted at runtime**, never stored; vendor/portal logins in
  `LINDO_SERVICO/credentials/pws_portais.txt`; Google via **ADC** (`~/.config/gcloud`)
  or service-account JSON. **FACT.**
- **Data residency:** company data stays **on-prem / LAN**. The materials-costing app
  is **offline-only**; only one-way cold backups leave (Google Drive + SFTP/DDNS).
  **FACT.**
- **Internet posture:** a real WAN uplink exists (FortiGate WAN, Windows Update /
  Docker Hub policies). Machine network segmentation exists but a **confirmed
  air-gap is not documented**. **FACT / WEAK.**
- **Default bind:** `0.0.0.0` for LAN-reachable-behind-Caddy, `127.0.0.1` for
  local-only dev viewers. **FACT.**

---

## ⚠️ Security findings surfaced during the scan

1. **`JWT_SECRET` — RESOLVED 2026-06-10.** A live `JWT_SECRET` value was surfaced from
   the working-tree `.env` of `calculo-custos-producao/materials/materials-costing`.
   Investigation across 367 commits confirmed it was **never committed** (the file is
   gitignored). The secret was **rotated** (new 64-char value, app verified). A reusable
   remediation process now lives in that repo (`scripts/secret-rotate.sh`,
   `secret-scan.sh`, `secret-scrub-history.sh`, `SECURITY.md`).
2. **`INVOICE_INTAKE_TELEGRAM_BOT_TOKEN` — OPEN.** Also a live credential in that repo's
   `.env`/`.env.local` (not in git history). Needs **BotFather `/revoke`** + re-write.
3. **`8000` ban is real** — keep enforcing it; it's an active collision source.

## Remaining UNKNOWN / to confirm
- Per-VLAN subnets for `VLAN_FATURACAO` / `VLAN_USERS` / `VLAN_IMPRESSORAS` /
  `VLAN_GUEST` / `VLAN_LAN` (the VLANs exist; their CIDRs aren't captured).
- Auth posture of the non-faturação internal tools (open-on-LAN vs login).
- Whether the CNC `VLAN_WIFI` is truly air-gapped from the internet.

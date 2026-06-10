# 01 — Hardware Baseline

| Field | Value |
|---|---|
| Type | Reference / Standard |
| Audience | All agents, all projects |
| Status | **Active** (pre-filled from evidence 2026-06-09; a few fields still UNKNOWN — see foot) |
| Owner | Filipe Coelho |
| Pre-filled | 2026-06-09, by mining Claude Code session transcripts + project configs |

> **Confidence tags:** `FACT` = configured or explicitly stated in a file/transcript ·
> `INFER` = reasoned from context · `CONFLICT` = sources disagree, you decide ·
> `UNKNOWN` = no evidence. **Zero hallucination still holds** — verify the non-FACT
> rows before relying on them. Source hints in _italics_.

---

## A. Developer / build machine — mostly FACT (live readout)

| Field | Value | Conf |
|---|---|---|
| Model | MacBook Pro `Mac16,8`, asset tag **LS-DT-2025-020** (hostname `Filipes-MacBook-Pro`) | FACT |
| CPU / cores | **Apple M4 Pro, 12 cores, arm64** | FACT |
| RAM | **24 GB** | FACT |
| OS | **macOS 26.x (Darwin 25.3.0)** | FACT |
| Python | **3.14.0**, via **pyenv**; projects also use 3.11–3.13 venvs | FACT |
| Containers | Docker / `docker compose` | FACT |
| Serial to devices | `/dev/cu.usbserial-*` (screen, 115200) | FACT |

_Sources: live `sysctl`/`sw_vers`; FortiGate asset tag; CNC serial-probe notes._

## B. Servers / always-on app hosts

| Host | IP | Role | Specs | Conf |
|---|---|---|---|---|
| **Windows Docker host** | `192.168.1.114` | Primary app server: runs `materials-costing` (faturação) stack + **Caddy** reverse proxy; also the **file-share host** (`Z:` = `\\192.168.1.114\partilha`). SSH user `docker`, key `~/.ssh/windows_docker_192_168_1_114` | "Windows 10/server"; CPU/RAM/build **UNKNOWN** | FACT (host) / UNKNOWN (specs) |
| **Synology DS220+ (NAS)** | `192.168.2.254` | Also a Docker app host (inventory app, DokuWiki) — see §C | DSM 7.2, 2 GB RAM, 2 cores | FACT |
| **ARTSOFT billing server** | on `VLAN_FATURACAO` | Regulated billing (Windows) | **UNKNOWN** | FACT (exists) / UNKNOWN (specs) |

## C. NAS — FACT

| Field | Value | Conf |
|---|---|---|
| Model | **Synology DS220+**, DSM 7.2 (2 GB RAM, 2 cores) | FACT |
| IP | `192.168.2.254`, SSH port 22 | FACT |
| SSH user | **`NetMaster`** (port 22) | FACT |
| Share name | **`LS-NETDISK`**, host path `/volume1/LS-NETDISK/` | FACT |
| Client mount | **SMB/CIFS** → mapped `Z:` / `\\…\partilha`; Mac mounts `/Volumes/LS-NETDISK` | FACT |
| Docker root | `/volume1/docker/...` (Container Manager, `/usr/local/bin/docker`) | FACT |
| Capacity / RAID / NAS-self-backup | **UNKNOWN** | UNKNOWN |

## D. Edge / machine devices — FACT (Eurolaser weak)

| Device | What | Interface | Conf |
|---|---|---|---|
| **CNC 3018 (tiling)** | **MKS DLC32 V2.1** board (ESP32-WROOM-32) running **FluidNC v3.9.9 custom fork** (repo `filipercoelho/fluidnc-3.9.9-modified`), GRBL-compatible; PWM spindle gpio.32 | **Wi-Fi WebUI** + **serial 115200**, mDNS `fluidnc.local` | FACT |
| **CNC router** | **SEGMAG ALFA PRO 10-15T**, **UCCNC v1.2113** (RS274/RS274NGC dialect), Windows-only, 5-axis, work area 1000×600×120 mm | **File-based G-code** via UCCNC PC | FACT |
| **VigoStick (×4)** | VEVOR **VigoStick** ESP-based offline GRBL controller, fw v1.32 — acts as a **G-code file server, not a live stream bridge** | Wi-Fi (AP self-IP `192.168.0.1`) + serial 115200 | FACT |
| **Eurolaser M-1600** | Large-format laser, controller "in LCS", driven from a **dedicated Windows 10/11 Pro PC** via Eurolaser software | "a documentar" — **UNKNOWN** | WEAK |

> Marlin appears only as a comparison; no confirmed Marlin hardware.

## E. Deploy targets & mechanism — FACT

| Target | Mechanism | Conf |
|---|---|---|
| NAS (`LS-NETDISK`) | `deploy-to-nas.sh` — SSH/SCP to `/volume1/LS-NETDISK/...`, docker build/run | FACT |
| Windows host `.114` | `deploy.sh`/`deploy-windows.ps1` — SSH → PowerShell (pure ASCII, PS 5.1), git pull, docker build/run, Caddy reload | FACT |
| Off-host backups | Weekly Postgres export → **Google Drive** + **SFTP via DDNS** (one-way only) | FACT |

## F. Standing constraints

- **Offline operation:** the materials-costing app is **offline-only** (ADR-040); only one-way cold-storage backups leave the LAN. **FACT.**
- **Min hardware baseline:** no formal company minimum. The tightest observed target is the **DS220+ (2 GB / 2 cores)** with per-container caps (inventory app: 0.25 CPU / 96 MB). Treat the **DS220+ as the practical floor** for NAS-hosted apps. **INFER.**
- **Architecture:** dev is **arm64 (Apple M4)**; deploy targets are **x86 Windows** (host `.114`) and the **NAS (DS220+ = ARM/Realtek)** — build/ship accordingly. **FACT/INFER.**

## G. Supporting network fleet (from IT asset wiki) — FACT / planned

Router **Huawei OptiXstar HG8247X6-8N**; switch **TP-LINK DGS-1024D** (EOL, replacement planned); **TP-LINK TD-W8970** in AP mode for IoT (`192.168.1.250`); **Cisco CBS350-48T-4G** + **FortiGate** firewall (under acquisition / recently added) with **FortiSwitch LS-S148FN**; **~25 PCs**.

---

### Remaining UNKNOWN / to confirm
- NAS capacity/RAID; Windows host `.114` specs + exact Windows build; ARTSOFT specs.
- Eurolaser interface and its `Z:` folder path.
- A formal minimum-hardware number (currently inferred as DS220+).

# HOME OS — Project Status & Milestone Tracker

**Last Updated:** 2026-11-21  
**Current Branch:** `arena/7bd92795-home-os`  
**Active Milestone:** Milestone 2 — Core Application (Milestone 1 Complete)

---

## Milestone Progress

| Milestone | Description | Status | Verification Tier |
|---|---|---|---|
| **M1** | Repository Inspection, Platform Research, Architecture & Risk Register | **Completed** | Reviewed & Committed |
| **M2** | Core Backend, SQLite + Alembic DB, Auth, Vault, Device Registry, Permissions, 8-Step Command Orchestrator, Audit Chain | **In Progress** | Pending Automated Tests |
| **M3** | Real Network Discovery (mDNS, SSDP, ARP), Identification, QR/PIN Pairing, Mutual HMAC Auth, Live Status | Planned | — |
| **M4** | Device Integrations (Linux live agent, Android, Windows, macOS, Google TV v2, Home Assistant, iOS, Matter) | Planned | — |
| **M5** | Unified Communications Centre & Authorized Media / File Management + Device-to-Device Transfers | Planned | — |
| **M6** | Cross-Device Automation Engine (Event, Schedule, State Triggers, Conditions, Retries, Confirmations) | Planned | — |
| **M7** | Desktop Command Shell (Electron + React/TS 9 Pages) + Full Security, Performance & E2E Hardening | Planned | — |
| **M8** | Packaging, Installers, CI Workflows, User/Developer/Security Documentation & Release Verification | Planned | — |

---

## Active Blockers & External Hardware Constraints

1. **Physical Mobile/TV/Appliance Hardware:** Not physically attached to the isolated `169.254.0.21/30` Linux build sandbox. Mitigated for automated verification via real socket/wire-protocol test harnesses (mDNS, SSDP, Android TV Remote v2 TLS, Home Assistant REST/WS, Matter WS, Agent WS) and **real host verification** using the native Linux Agent on this Debian 12 x86_64 machine. Physical device sign-off on Android, iOS, Windows, macOS, and Google TV hardware remains explicitly marked *Blocked by external hardware availability*.

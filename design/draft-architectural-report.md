# DRAFT - Architectural Report

## Multi-Inbox Email Intelligence Service

### (Canonical Ingestion → Extraction → Event Platform)

---

## 1. Executive Summary

This document defines the architecture for a **provider-agnostic email intelligence service** that reads multiple email inboxes and transforms unstructured email communication into **structured, auditable business signals** consumable by downstream clients (dashboards, CRMs, automation engines, AI agents).

The system treats email not as UI content but as an **event source**, producing **canonical business events** (tasks, leads, status updates, decisions, blockers, etc.) with ownership, progression, and confidence metadata.

Key architectural principles:

* **Separation of concerns** (ingestion ≠ interpretation ≠ consumption)
* **Immutability and replayability**
* **Security by design** (least privilege, minimization, auditability)
* **Incremental intelligence** (rules first, AI later)
* **Provider independence**

---

## 2. Problem Statement

Organizations rely heavily on email as an implicit coordination layer, yet email systems provide:

* No explicit task ownership
* No unified activity state
* No structured lead tracking
* No systemic visibility across inboxes

Manual triage does not scale and introduces risk (missed commitments, stale leads, unclear accountability).

**Objective:**
Build a service that:

1. Reads multiple inboxes
2. Extracts actionable business meaning
3. Produces structured outputs for downstream systems
4. Preserves data integrity, security, and auditability

---

## 3. Non-Goals (Explicitly Out of Scope)

To keep architecture focused:

* ❌ Replacing email clients
* ❌ Writing/sending email (read-only system)
* ❌ Human-facing UI beyond reference dashboards
* ❌ Fully autonomous decision-making (human oversight preserved)
* ❌ Provider-specific features as hard dependencies

---

## 4. Architectural Principles

### 4.1 Email as an Event Stream

Email messages are **immutable inputs**, not state.
All state is **derived**, not edited.

### 4.2 Canonical Contracts

Downstream systems **never parse emails**.
They consume **typed business events**.

### 4.3 Replayability

The system must be able to:

* Reprocess historical emails
* Rebuild state deterministically
* Support model upgrades without data loss

### 4.4 Progressive Intelligence

* Phase 1: deterministic rules, high precision
* Phase 2+: probabilistic ML/LLM with confidence and review

### 4.5 Tenancy, Isolation, and Data Boundaries

This system is inherently **multi-tenant**. Every persisted record MUST be scoped to a tenant.

#### Tenancy model (recommended default)

* `tenant_id` is present on all immutable and derived tables.
* Encryption keys are logically scoped per tenant (KMS-managed keys or equivalent).
* All downstream access is mediated by the delivery layer with tenant-scoped authorization.

#### Isolation requirements

* No cross-tenant joins in application logic.
* Background jobs and queues always carry `tenant_id`.
* Metrics/logs MUST avoid leaking raw content across tenants (redaction rules apply).

### 4.6 Idempotency and Deterministic Rebuilds

The system MUST tolerate at-least-once ingestion and reprocessing.

* All writes are idempotent (unique constraints + upserts).
* Derived outputs are deterministic given the same inputs and extractor version.
* Consumers can treat streams/webhooks as at-least-once; they must dedupe using event IDs.

---

## 5. High-Level System Overview

### Logical Pipeline

```text
Email Sources
   ↓
Ingestion Layer
   ↓
Parsing & Normalization
   ↓
Threading & Conversation State
   ↓
Extraction & Enrichment
   ↓
Canonical Event Store
   ↓
Delivery Interfaces (APIs / Streams / Webhooks)
```

Each stage is **independently testable, replaceable, and observable**.

---

## 6. Component Architecture

### 6.1 Ingestion Layer

#### Ingestion Responsibility

* Connect to multiple inboxes
* Fetch new messages reliably
* Guarantee idempotency

#### Inputs

* IMAP
* Provider APIs (Gmail, Graph, etc.)
* Optional SMTP journaling/archive mailbox

#### Key Design Decisions

* Per-inbox cursor state (`UID`, `watermark`)
* Read-only credentials
* Backpressure handling

#### Identifiers and deduplication (must specify upfront)

To make ingestion replayable and safe across providers, the system uses a layered identity strategy:

* `provider_message_key`: provider-native stable ID when available (e.g., Gmail message id, Graph message id)
* `rfc822_message_id`: parsed from the RFC822 `Message-ID` header when present
* `content_hash`: hash of canonicalized raw bytes (fallback for pathological cases)

#### Idempotency rule

Persisted raw messages MUST be unique per `(tenant_id, mailbox_id, provider_message_key)` when available; otherwise fall back to `(tenant_id, mailbox_id, rfc822_message_id)`; otherwise fall back to `(tenant_id, mailbox_id, content_hash)`.

#### Ingestion Deliverables

* Inbox connector interface
* Cursor persistence
* Retry and error classification

---

### 6.2 Parsing & Normalization Layer

#### Parsing & Normalization Responsibility

* Convert raw RFC822 into canonical objects
* Eliminate provider variance

#### Parsing & Normalization Key Operations

* MIME decoding
* HTML → plaintext normalization
* Quoted reply trimming
* Signature isolation
* Attachment metadata hashing

#### Parsing & Normalization Output: EmailEnvelope

* Stable, versioned schema
* Immutable once persisted

#### Parsing & Normalization Deliverables

* Parser module
* Normalization spec
* Schema versioning strategy

---

### 6.3 Threading & Conversation Model

#### Threading Responsibility

* Group emails into conversations meaningful to humans

#### Threading Strategy (Priority Order)

1. `References` / `In-Reply-To` graph
2. Provider thread IDs (if available)
3. Subject normalization + participant window

#### Derived Objects

* `thread_id`
* participant set
* last activity timestamp
* conversation state

#### Threading Deliverables

* Threading algorithm
* Thread state reducer
* Reconciliation logic for ambiguous cases

---

### 6.4 Extraction & Enrichment Engine

#### Extraction & Enrichment Responsibility

* Generate business meaning from conversations

#### 6.4.1 Classification

Determine **what kind of thing** this is:

* TASK
* LEAD
* SUPPORT
* DECISION
* STATUS_UPDATE
* BLOCKER
* FYI

#### 6.4.2 Entity Extraction

* People
* Organizations
* Dates / deadlines
* Monetary values
* Products / projects

#### 6.4.3 Ownership Inference

* Mailbox ownership
* Explicit assignment language
* First responder heuristics

#### 6.4.4 Progression Logic

Conversation lifecycle:

* OPEN
* WAITING_INTERNAL
* WAITING_EXTERNAL
* DONE
* STALE

#### Confidence Scoring

Every extracted signal includes a confidence value.

#### Extraction & Enrichment Deliverables

* Rules engine (phase 1)
* Extraction contracts
* Confidence model
* Review queue interface (optional)

---

## 7. Canonical Data Model

### 7.1 Immutable Layer (Source of Truth)

* RawEmail (encrypted blob)
* EmailEnvelope (normalized metadata)

### 7.2 Derived Layer (Business Semantics)

* BusinessEvent (append-only)
* ThreadState (materialized)
* Task
* Lead
* SLA / timers

### 7.3 Canonical Event Store Semantics

The **Canonical Event Store** is the append-only, immutable record of business signals.

#### Event immutability rules

* Events are never updated in place.
* Corrections are represented as new events (e.g., `TASK_RETRACTED`, `TASK_UPDATED`).
* Materialized views (`ThreadState`, `Task`, `Lead`) are rebuilt from the event log.

#### Delivery and the Outbox pattern (recommended)

If emitting to streams/webhooks, use a transactional outbox table in PostgreSQL so:

* writing `BusinessEvent` and enqueueing delivery happen atomically
* delivery can be retried safely with dedupe by `event_id`

#### Key Rule

> Derived data can be rebuilt. Raw data must never be lost or mutated.

---

## 8. Storage Architecture

### Recommended Stack (Scalable but Minimal)

| Purpose            | Storage              |
| ------------------ | -------------------- |
| Raw emails         | Encrypted blob store |
| Canonical metadata | PostgreSQL           |
| Derived views      | PostgreSQL           |
| Search (optional)  | OpenSearch           |
| Event streaming    | Redis Streams / NATS |

#### Rationale

* SQL for integrity and traceability
* Streams for decoupled consumers
* Search only if needed (avoid premature complexity)

### 8.1 Retention, Deletion, and Legal Holds

Replayability must be reconciled with privacy and retention obligations.

#### Default posture (recommended)

* RawEmail blobs: short retention window by default (configurable) unless a tenant explicitly opts in
* EmailEnvelope + BusinessEvent: retained longer (still configurable), as they represent minimized, structured data

#### Deletion requirements

* Support tenant-wide deletion (offboarding) and mailbox-level deletion.
* Deleting raw blobs MUST not corrupt the ability to reason about previously emitted events; instead, treat the event store as the audit of what was derived at the time.

#### Legal holds (optional)

If legal hold is required, it must be explicitly enabled per tenant and auditable.

---

## 9. Delivery Interfaces (Downstream Consumption)

### 9.1 REST API

* Query tasks by owner
* Query threads by state
* Query leads by stage

### 9.2 Event Streams

* `business.events`
* `tasks.created`
* `leads.updated`
* `threads.stale`

### 9.3 Webhooks

* External system integration
* Push-based automation

#### Design Rule

Consumers **subscribe**, never poll raw storage.

---

## 10. Security & Safety Architecture

### 10.1 Identity & Access

* Read-only inbox credentials
* Per-account isolation
* RBAC for consumers

### 10.2 Data Protection

* TLS everywhere
* Encryption at rest
* Optional field-level encryption for sensitive data

### 10.3 Data Minimization

* Downstream systems default to **derived data only**
* Raw email access audited and restricted

### 10.4 Auditability

* Immutable event log
* Access logs
* Replay capability

### 10.5 Secrets, Key Management, and Redaction

* Store connector credentials/tokens in a dedicated secrets system (not in application DB in plaintext).
* Rotate credentials and encryption keys; log rotation events.
* Apply log redaction: avoid logging raw bodies, full headers, or attachment names by default.

---

## 11. Operational Concerns

### Observability

* Per-stage metrics (lag, errors, throughput)
* Dead-letter queues
* Confidence drift monitoring

### Backfills and Reprocessing

Two operational modes must be supported:

* **Tail mode**: incremental ingestion using per-mailbox cursors
* **Backfill mode**: bounded historical replay (by time window / folder / message count)

Backfills MUST:

* respect provider rate limits and backoff
* write idempotently (safe to restart)
* record `extractor_version` and schema versions used

### Failure Modes

* Inbox temporarily unavailable
* Parsing failures
* Ambiguous classification

#### Mitigation

* Retry with backoff
* Mark as “unclassified”
* Human review fallback

---

## 12. Incremental Delivery Plan

### Phase 1 — Foundation

* IMAP ingestion
* Parsing & normalization
* Threading
* Rule-based extraction
* Postgres + REST

### Phase 2 — Intelligence

* Ownership inference
* SLA timers
* Staleness detection
* Event streaming

### Phase 3 — Augmentation

* ML/LLM-assisted extraction
* Summarization
* Adaptive classification
* Confidence learning

---

## 13. Risks & Trade-offs

| Risk                | Mitigation                     |
| ------------------- | ------------------------------ |
| Over-classification | Confidence + review            |
| Privacy overreach   | Minimization + RBAC            |
| Threading ambiguity | Conservative fallback          |
| AI hallucination    | Derived-only, confidence-gated |
| Scope creep         | Strict contract boundaries     |

---

## 14. Key Architectural Insight

> **This system is not an email parser.
> It is a business-signal compiler fed by email.**

Email is merely the **input language**.

---

## Appendix A — Example Canonical Contracts (Illustrative)

These examples are intentionally minimal; the key goal is stable IDs, versioning, and audit fields.

### A.1 EmailEnvelope (normalized)

```json
{
   "schema_version": "envelope.v1",
   "tenant_id": "tnt_123",
   "mailbox_id": "mbx_456",
   "email_id": "eml_01J...",
   "provider": "imap",
   "provider_message_key": "uid:88421",
   "rfc822_message_id": "<CA+abc123@example.com>",
   "thread_hint": {
      "in_reply_to": "<CA+prev@example.com>",
      "references": ["<CA+root@example.com>"]
   },
   "from": {"name": "Alice", "email": "alice@example.com"},
   "to": [{"name": "Ops", "email": "ops@example.com"}],
   "cc": [],
   "subject": "Re: Contract signature",
   "sent_at": "2025-12-26T10:11:12Z",
   "received_at": "2025-12-26T10:11:30Z",
   "body": {
      "text": "Hi — attached is the signed contract...",
      "has_html": true,
      "quote_trimmed": true
   },
   "attachments": [
      {"filename": "contract.pdf", "content_type": "application/pdf", "sha256": "...", "size_bytes": 349221}
   ],
   "audit": {
      "ingested_at": "2025-12-26T10:12:00Z",
      "source": "connector.imap.v1"
   }
}
```

### A.2 BusinessEvent (append-only)

```json
{
   "schema_version": "event.v1",
   "tenant_id": "tnt_123",
   "event_id": "evt_01J...",
   "event_type": "TASK_CREATED",
   "occurred_at": "2025-12-26T10:11:30Z",
   "produced_at": "2025-12-26T10:12:10Z",
   "extractor": {
      "name": "rules",
      "version": "ruleset.2025-12-26",
      "mode": "deterministic"
   },
   "source": {
      "email_id": "eml_01J...",
      "thread_id": "thr_01J..."
   },
   "confidence": 0.92,
   "payload": {
      "task": {
         "task_id": "tsk_01J...",
         "title": "Countersign contract",
         "assignee": {"email": "ops@example.com"},
         "due_date": "2025-12-31",
         "status": "OPEN"
      }
   }
}
```

## Appendix B — Contract Versioning Rules

* Version **schemas**, not only code.
* Additive changes are preferred; breaking changes require a new major schema version.
* Persist the `schema_version` and `extractor.version` with every record/event.

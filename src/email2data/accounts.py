"""C1a/C1b — deterministic account clustering (see docs/05-reference/cockpit-design.md).

Groups CRM contacts into account clusters by domain and NIF. Recomputed per-request
from ``crm.all_contacts()`` + ``crm.contacts_by_nif()`` — no durable ``accounts`` table,
so no irreversible merges and no schema to migrate.

Merge rules (highest-confidence first, each email joins at most one cluster):
  1. ``identity_links`` — a human explicitly confirmed "this email belongs to cluster X"
     (C1b, written via Workspace.set_identity_link). Overrides everything.
  2. NIF match — two emails that share a validated NIF are the same legal entity.
  3. Domain match — emails on the same non-free-mail domain belong to the same company.
  4. Remainder — each free-mail address is its own single-email cluster.

Internal (lindoservico.pt) contacts are excluded from the output; the Contrapartes
lens is about external counterparties only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Domains that identify a person, not a company. Extend as needed.
FREE_MAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com",
    "hotmail.com", "hotmail.pt", "hotmail.co.uk",
    "outlook.com", "outlook.pt",
    "live.com", "live.pt",
    "yahoo.com", "yahoo.pt", "yahoo.co.uk",
    "icloud.com", "me.com",
    "sapo.pt", "mail.pt", "iol.pt", "clix.pt",
})

# Our own domain — always excluded from clustering.
_OUR_DOMAIN = "lindoservico.pt"


@dataclass
class AccountCluster:
    """One external counterparty, aggregated from one-or-more email addresses."""
    key: str            # unique identifier: domain (e.g. "acme.pt"), "nif:501234567",
                        #   "linked:<account_key>", or "free:<email>" for sole free-mail contacts
    kind: str           # "domain" | "nif" | "linked" | "free_mail"
    emails: list[str] = field(default_factory=list)
    display_name: str = ""   # best human-readable label (domain or gazetteer name)
    nif: str = ""
    last_counterparty: str = ""
    last_seen: str = ""
    msg_count: int = 0
    from_count: int = 0


def cluster(
    contacts: list[dict[str, Any]],
    *,
    nif_refs: dict[str, list[str]] | None = None,
    identity_links: dict[str, str] | None = None,
    gazetteer_hints: dict[str, str] | None = None,
) -> list[AccountCluster]:
    """Build account clusters from CRM contact rows.

    ``contacts``       — rows from ``crm.all_contacts()`` (dicts with email, domain, …).
    ``nif_refs``       — {nif: [email_addresses]} from ``crm.contacts_by_nif()``.
    ``identity_links`` — {email: account_key} from ``Workspace.identity_links()``.
    ``gazetteer_hints``— {domain: display_name} from ``store.KnowledgeStore``.
    Returns clusters sorted by ``msg_count`` descending.
    """
    links = identity_links or {}
    hints = gazetteer_hints or {}
    by_nif: dict[str, list[str]] = nif_refs or {}

    # Index contacts by email for fast lookup.
    by_email: dict[str, dict[str, Any]] = {}
    for c in contacts:
        e = (c.get("email") or "").lower().strip()
        if e:
            by_email[e] = c

    # Build email → cluster_key mapping (precedence: links > NIF > domain > free_mail).
    assignment: dict[str, str] = {}  # email → cluster_key

    # Rule 1: explicit identity links override everything.
    for email, acc_key in links.items():
        assignment[email.lower().strip()] = acc_key

    # Rule 2: NIF — all emails sharing a NIF belong to the domain cluster (or nif: key).
    for nif, emails in by_nif.items():
        normed = [e.lower().strip() for e in emails if e]
        # Find the best domain key in this NIF group (prefer a non-free-mail domain).
        domain_key = _best_domain_key(normed, by_email)
        key = domain_key or f"nif:{nif}"
        for e in normed:
            if e not in assignment:
                assignment[e] = key

    # Rule 3: same non-free-mail domain.
    for email, c in by_email.items():
        if email in assignment:
            continue
        domain = (c.get("domain") or "").lower()
        if _is_internal(domain) or not domain:
            assignment[email] = "__internal__"
        elif domain not in FREE_MAIL_DOMAINS:
            assignment[email] = domain
        # free-mail falls through to Rule 4 below

    # Rule 4: free-mail — each is its own cluster.
    for email in by_email:
        if email not in assignment:
            assignment[email] = f"free:{email}"

    # Aggregate into clusters.
    clusters: dict[str, AccountCluster] = {}
    for email, key in assignment.items():
        if key == "__internal__":
            continue
        c_row = by_email.get(email) or {}
        cl = clusters.setdefault(key, AccountCluster(
            key=key,
            kind=_kind(key),
            display_name=hints.get(key.lstrip("free:"), key) if key.startswith("free:") else hints.get(key, key),
        ))
        if email not in cl.emails:
            cl.emails.append(email)
        cl.msg_count += c_row.get("msg_count", 0)
        cl.from_count += c_row.get("from_count", 0)
        _update_last(cl, c_row)

    result = [cl for cl in clusters.values() if cl.emails]
    result.sort(key=lambda c: -c.msg_count)
    return result


def _is_internal(domain: str) -> bool:
    return domain == _OUR_DOMAIN or domain.endswith("." + _OUR_DOMAIN)


def _best_domain_key(emails: list[str], by_email: dict[str, dict[str, Any]]) -> str:
    """Return the first non-free-mail domain found in the email list, or ''."""
    for e in emails:
        domain = (by_email.get(e) or {}).get("domain", "").lower()
        if domain and not _is_internal(domain) and domain not in FREE_MAIL_DOMAINS:
            return domain
    return ""


def _kind(key: str) -> str:
    if key.startswith("nif:"):
        return "nif"
    if key.startswith("free:"):
        return "free_mail"
    if key.startswith("linked:"):
        return "linked"
    return "domain"


def _update_last(cl: AccountCluster, c_row: dict[str, Any]) -> None:
    seen = c_row.get("last_seen") or ""
    if seen > (cl.last_seen or ""):
        cl.last_seen = seen
        cl.last_counterparty = c_row.get("last_counterparty") or cl.last_counterparty

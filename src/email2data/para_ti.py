"""C3 — Para ti gate builders (pure functions, no I/O).

Each builder takes already-computed data and returns a list of decision items.
Items are shown in the Para ti lens and cleared when the human acts (each item
carries the API endpoint + payload for its accept action).

Three gates shipped for the enhanced MVP (a fourth — approve-draft — is deferred):
  1. rever_classificacao  — dominant verdict confidence below floor, not yet corrected.
  2. propor_projeto       — LEAD/PO thread not yet attached to any project.
  3. confirmar_identidade — free-mail contact matching an existing domain cluster by name.
"""

from __future__ import annotations

from typing import Any

from .accounts import AccountCluster
from .schema import HIGH_VALUE_PURPOSES

# Confidence below this → surface for human review (mirrors schema's IGNORE floor).
CONFIDENCE_FLOOR: float = 0.60


def _thread_context(r: dict[str, Any]) -> dict[str, Any]:
    """Thread context pulled from a Fila row — the information a human needs to act."""
    clock = r.get("clock") or {}
    trust = r.get("trust") or {}
    return {
        "contact": r.get("contact") or "",
        "counterparty": r.get("counterparty") or "",
        "purpose": r.get("purpose") or "",
        "n_messages": r.get("n_messages") or 1,
        "has_attachment": bool(r.get("has_attachment")),
        "clock_label": clock.get("label") or "",
        "clock_band": clock.get("band") or "none",
        "reason": trust.get("reason") or "",         # AI's one-line summary of the email
        "confidence": trust.get("confidence") or 0.0,
        "decided_by": trust.get("decided_by") or "",
    }


def low_confidence_items(
    fila_rows: list[dict[str, Any]],
    *,
    floor: float = CONFIDENCE_FLOOR,
) -> list[dict[str, Any]]:
    """Gate 1 — threads where the AI verdict is uncertain and the human hasn't corrected it yet.

    A ``committed`` trust block means the human already reclassified → skip (no redundant prompt).
    """
    items = []
    for r in fila_rows:
        tr = r.get("trust") or {}
        if tr.get("committed"):
            continue
        conf = float(tr.get("confidence") or 0.0)
        if conf < floor:
            decided = tr.get("decided_by") or ""
            items.append({
                "kind": "rever_classificacao",
                "thread_root": r["thread_root"],
                "title": r.get("subject") or "(sem assunto)",
                "why": (
                    f"Confiança {int(conf * 100)}% ({decided}) — "
                    f"classificado como {r.get('counterparty', '?')} · {r.get('purpose', '?')}"
                ),
                "context": _thread_context(r),
                "accept": {
                    "label": "Ver na Fila",
                    "href": f"/?focus={r['thread_root']}",
                },
            })
    return items


def propose_project_items(
    fila_rows: list[dict[str, Any]],
    project_threads: set[str],
) -> list[dict[str, Any]]:
    """Gate 2 — LEAD/PO threads not yet attached to any project.

    ``project_threads`` is the set of all ``thread_root`` values already in a project
    (obtained from ``ProjectStore.threads_for`` over all projects).
    Only surfaces threads whose purpose is clearly job-relevant.
    """
    items = []
    for r in fila_rows:
        if r["thread_root"] in project_threads:
            continue
        if r.get("purpose") not in HIGH_VALUE_PURPOSES:
            continue
        cp = r.get("counterparty") or ""
        if cp not in {"CLIENT", "LEAD"}:
            continue
        items.append({
            "kind": "propor_projeto",
            "thread_root": r["thread_root"],
            "title": r.get("subject") or "(sem assunto)",
            "why": (r.get("contact") or "").rstrip(),
            "context": _thread_context(r),
            "accept": {
                "label": "Criar projeto",
                "api": "/api/projects",
                "payload": {
                    "title": r.get("subject") or "(sem assunto)",
                    "from_message": r["thread_root"],
                },
                "nav": "/projetos",
            },
        })
    return items


def identity_candidate_items(
    clusters: list[AccountCluster],
    *,
    min_msg_count: int = 2,
) -> list[dict[str, Any]]:
    """Gate 3 — free-mail contacts that may belong to an existing domain cluster.

    A free-mail cluster is a candidate when:
      • It has written at least ``min_msg_count`` times (worth resolving).
      • Its display name/email is similar enough to an existing domain cluster name
        (heuristic: the domain cluster's key appears in the free-mail address or vice versa).

    The human confirms with ``/api/identity/confirm``; that writes a precious
    ``identity_link`` and re-clusters on the next request.
    """
    domain_keys = {c.key for c in clusters if c.kind == "domain"}
    items = []
    for cl in clusters:
        if cl.kind != "free_mail":
            continue
        if cl.msg_count < min_msg_count:
            continue
        email = cl.emails[0] if cl.emails else ""
        local = email.split("@")[0].lower().replace(".", "").replace("_", "")
        for dk in domain_keys:
            dk_stem = dk.rsplit(".", 1)[0].lower().replace("-", "").replace("_", "")
            if dk_stem and (dk_stem in local or local in dk_stem):
                items.append({
                    "kind": "confirmar_identidade",
                    "email": email,
                    "title": f"{email}",
                    "why": f"nome parece corresponder a {dk}",
                    "context": {
                        "contact": email,
                        "n_messages": cl.msg_count,
                        "proposed_cluster": dk,
                        "counterparty": cl.last_counterparty or "",
                        "last_seen": cl.last_seen or "",
                        "reason": (
                            f"Endereço pessoal com {cl.msg_count} mensagem"
                            f"{'ns' if cl.msg_count != 1 else ''}, "
                            f"possivelmente da empresa {dk}"
                        ),
                        "clock_label": "", "clock_band": "none",
                        "has_attachment": False, "confidence": 0.0, "decided_by": "",
                        "purpose": "",
                    },
                    "accept": {
                        "label": f"Confirmar → {dk}",
                        "api": "/api/identity/confirm",
                        "payload": {"email": email, "account_key": dk},
                        "nav": f"/contrapartes/{dk}",
                    },
                })
                break  # one candidate per free-mail address
    return items


def all_items(
    fila_rows: list[dict[str, Any]],
    clusters: list[AccountCluster],
    project_threads: set[str],
    *,
    confidence_floor: float = CONFIDENCE_FLOOR,
) -> list[dict[str, Any]]:
    """Convenience: all three gates concatenated, ordered by kind priority."""
    return (
        low_confidence_items(fila_rows, floor=confidence_floor)
        + propose_project_items(fila_rows, project_threads)
        + identity_candidate_items(clusters)
    )

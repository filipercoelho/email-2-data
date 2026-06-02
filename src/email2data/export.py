"""Export adapter — offload a finished Project to an external estimating system.

The Project layer gathers a job *brief* across many email threads; the costing happens elsewhere.
``build_payload`` renders a Project's canonical spec into the materials-costing ``ProjectCreate``
shape (``POST /api/projects``, ADR-076). Two adapters share the ``export`` port:

  * ``JsonFileAdapter`` — writes the payload to ``out/exports/<project_id>.json``. The offline,
    fully-testable MVP and an exact dry-run preview of what would be POSTed.
  * ``MaterialsCostingAdapter`` — POSTs to the live API with an ``X-API-Key`` header (stdlib
    urllib, no new deps). base_url from settings; the key from env ``MATERIALS_COSTING_API_KEY``,
    never stored in settings.json (mirrors the IMAP-password rule).

**Honesty boundary:** materials-costing line items (``ProjectLineCreate``) reference catalog
materials + pricing snapshots that our free-text spec does not carry. So we export the *shell*
(brief in project_name/cliente/descricao/notas); the estimator builds the costed lines there. We do
NOT synthesise fake line rows. Export never auto-fires — it is always an explicit human action.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

from . import jobspec as js
from . import project as _project

_LABEL = {k: lbl for k, lbl, _, _, _ in js.FIELDS}

API_KEY_ENV = "MATERIALS_COSTING_API_KEY"


@dataclass
class ExportResult:
    ok: bool
    external_id: Optional[str] = None
    detail: str = ""


def render_brief(spec: js.JobSpec, readiness: dict[str, Any],
                 thread_roots: list[str], message_ids: list[str]) -> str:
    """Human-readable brief for the ``notas`` field: job-level fields, then each line item, then
    provenance (linked threads / source messages) and the Gate-1 readiness summary."""
    lines: list[str] = ["[email-2-data] Resumo do pedido", ""]
    for k in js.JOB_KEYS:
        fld = spec.job_fields.get(k)
        if fld and fld.value:
            lines.append(f"- {_LABEL.get(k, k)}: {fld.value}")
    for i, item in enumerate(spec.items, 1):
        parts = [f"{_LABEL.get(k, k)}={item[k].value}" for k in js.ITEM_KEYS
                 if item.get(k) and item[k].value]
        if parts:
            lines.append(f"- Peça {i}: " + "; ".join(parts))
    lines.append("")
    lines.append(f"Estimável (Gate-1): {'sim' if readiness.get('estimable') else 'não'} "
                 f"· cobertura {int((readiness.get('coverage') or 0) * 100)}%")
    if readiness.get("missing"):
        lines.append("Em falta: " + ", ".join(readiness["missing"]))
    if thread_roots:
        lines.append(f"Threads ({len(thread_roots)}): " + ", ".join(thread_roots))
    if message_ids:
        lines.append(f"Mensagens de origem ({len(message_ids)}): " + ", ".join(message_ids))
    return "\n".join(lines)


def build_payload(project: dict[str, Any], spec: js.JobSpec, readiness: dict[str, Any],
                  thread_roots: list[str], message_ids: list[str]) -> dict[str, Any]:
    """Render a Project into the materials-costing ``ProjectCreate`` shape.

    Only the shell fields are filled; owner/margin_pct/default_waste_pct are left for the estimator.
    ``descricao`` is a one-line item summary; ``notas`` carries the full brief + provenance.
    """
    item_summ = "; ".join(
        spec.items[i]["item"].value for i in range(len(spec.items))
        if spec.items[i].get("item") and spec.items[i]["item"].value
    )
    return {
        "project_name": project.get("title") or f"Projeto {project.get('project_id', '')}",
        "cliente": project.get("client_name") or project.get("client_email") or None,
        "status": "ATIVO",
        "currency": "EUR",
        "descricao": item_summ or None,
        "notas": render_brief(spec, readiness, thread_roots, message_ids),
    }


class ExportAdapter(Protocol):
    def export(self, project_id: str, payload: dict[str, Any]) -> ExportResult: ...


class JsonFileAdapter:
    """Write the payload to ``<out_dir>/exports/<project_id>.json``. Dry-run / offline MVP."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)

    def export(self, project_id: str, payload: dict[str, Any]) -> ExportResult:
        dest = self.out_dir / "exports"
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{project_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # No remote id; record the local path as the external reference so re-export is detectable.
        return ExportResult(ok=True, external_id=f"file:{path.name}", detail=str(path))


class MaterialsCostingAdapter:
    """POST the payload to the materials-costing API (``POST {base_url}/api/projects``).

    base_url from ``settings['materials_costing']['base_url']``; the API key from env
    ``MATERIALS_COSTING_API_KEY``. Returns the remote ``project_id`` on success.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "MaterialsCostingAdapter":
        cfg = settings.get("materials_costing") or {}
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError("settings['materials_costing']['base_url'] is required")
        api_key = os.environ.get(API_KEY_ENV, "")
        if not api_key:
            raise ValueError(f"set the {API_KEY_ENV} env var (the X-API-Key secret)")
        return cls(base_url, api_key, timeout=float(cfg.get("timeout", 15.0)))

    def export(self, project_id: str, payload: dict[str, Any]) -> ExportResult:
        url = f"{self.base_url}/api/projects"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
            return ExportResult(ok=False, detail=f"HTTP {exc.code}: {detail}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return ExportResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        ext = body.get("project_id") if isinstance(body, dict) else None
        if not ext:
            return ExportResult(ok=False, detail=f"no project_id in response: {body!r}")
        return ExportResult(ok=True, external_id=ext, detail=url)


def export_project(store, ws, jobspecs: dict[str, Any], adapter: ExportAdapter, pid: str,
                   *, crm_store=None, force: bool = False) -> ExportResult:
    """Gate + run an export. Refuses a non-estimable project or a re-export (already has an
    external_id) unless ``force``. On success records the external id and advances the stage."""
    proj = store.get(pid)
    if proj is None:
        return ExportResult(ok=False, detail=f"unknown project {pid}")
    if proj.get("external_id") and not force:
        return ExportResult(ok=False, external_id=proj["external_id"],
                            detail=f"already exported as {proj['external_id']}; pass force to re-export")
    spec, readiness, _prov, _conf = _project.build_canonical(store, ws, jobspecs, pid, crm_store)
    if not readiness.get("estimable") and not force:
        missing = ", ".join(readiness.get("missing") or [])
        return ExportResult(ok=False, detail=f"not estimable (missing: {missing}); pass force to override")
    payload = build_payload(
        proj, spec, readiness,
        store.threads_for(pid), _project.message_ids_for(store, pid, crm_store),
    )
    result = adapter.export(pid, payload)
    if result.ok and result.external_id:
        store.set_external(pid, result.external_id)
        store.set_stage(pid, "QUOTED")
    return result

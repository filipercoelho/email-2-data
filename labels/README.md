# Hand labels

Ground truth for the `eval` gate. Copy `labels.example.csv` to `labels.csv` (gitignored) and label
~40 emails from `corpus/`.

- One row per email. Columns: `message_id,type,priority,notes`.
- `message_id` must match the value in `out/results.jsonl` exactly (canonical form, e.g.
  `mid:abc@host` or `sha256:...`). Easiest: run `email2data classify` first, then copy ids from the
  results table.
- `type` ∈ `CLIENT_JOB_REQUEST, QUOTE_FOLLOWUP, REMINDER_EVENT, SUPPLIER_INVOICE, CLIENT_COMPLAINT,
  SUPPORT_INTERNAL, PUBLICITY, OTHER`.
- `priority` ∈ `HIGH, MEDIUM, IGNORE` only. **Never label `NEEDS_REVIEW`** — that is a model-only
  routing state, not ground truth.
- `notes` is free text for your own reference; `eval` ignores it.

`eval` reports rows in labels with no matching result (and vice-versa) loudly — it never silently
drops them. Lines whose `type` is not in the list above are skipped with a warning.

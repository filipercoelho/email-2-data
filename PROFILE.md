# Profile — Data extraction / migration (`data-extraction`)

Pull structured data out of an existing system (Excel, Outlook, vCards, email) into a
usable format. **Zero-hallucination is the dominant risk** — the temptation to
"complete" missing logic from a pattern is exactly what must not happen. Inherits all
of `standards/00`–`06`; this profile **adds** the items below.

## Default shape
- **Read-only** access to the source — the source is never mutated.
- Output **classified** per item: FACT (with a trace to where it came from) /
  INFERENCE / UNKNOWN.
- A **reconciliation report** of what was extracted, inferred, and left unknown.
- **Idempotent** re-runs (running twice yields the same result).

## Profile must-verify (append to standards/04)
- [ ] Every extracted value is **FACT with a provenance trace**, or explicitly
      INFERENCE / UNKNOWN (`standards/03 §1`).
- [ ] **No invented logic** — missing formulas/rules are UNKNOWN, not "completed."
- [ ] Source is accessed **read-only** and is unchanged after the run.
- [ ] Re-running is **idempotent**.
- [ ] A **reconciliation / coverage report** exists (what was captured vs left
      unknown).

## Common failure modes
- "Completing" a gap from a plausible pattern (the cardinal sin here).
- Inventing a formula that looked right.
- Losing provenance — a value with no record of where it came from.
- Mutating the source while reading it.

<!--OPEN_QUESTIONS_START-->
- [ ] **What is the source** (Excel workbook? Outlook store? vCard export?) and where
      does it live?
- [ ] What is the **target format/schema** for the extracted data?
- [ ] How do we **classify** each output (FACT/INFERENCE/UNKNOWN) and record
      provenance?
- [ ] What is **out of scope** — what should be left UNKNOWN rather than guessed?
- [ ] Is the source **read-only guaranteed**, or could the process mutate it?
- [ ] What does the **reconciliation report** need to show to be trusted?
<!--OPEN_QUESTIONS_END-->

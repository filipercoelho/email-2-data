# Client Email Template — Seguimento / sem resposta (Projetos composer)

Deterministic skeleton for the **follow-up / chase** email. It is **not** an LLM prompt — the page
splices the still-open questions into this skeleton, with no model in the loop. **A human reviews,
edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{perguntas}` token on its own line — it is
replaced by the numbered list of questions the user ticked. Everything after the `---` fence below
is the body; the text above it (this note) is ignored.

---

Bom dia,

Voltamos ao contacto sobre este pedido. Para conseguirmos avançar, faltava-nos confirmar:

{perguntas}

Obrigado.

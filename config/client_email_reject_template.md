# Client Email Template — Recusar o trabalho (Projetos composer)

Deterministic skeleton for the **recusa** email. It is **not** an LLM prompt — the page splices
the chosen reason (and any free note) into this skeleton, with no model in the loop. **A human
reviews, edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{motivo}` token on its own line — it is
replaced by the reason the user picked plus any note they wrote. Everything after the `---` fence
below is the body; the text above it (this note) is ignored.

---

Bom dia,

Agradecemos o vosso contacto e o interesse na Lindo Serviço.

Depois de analisarmos o pedido, de momento não conseguimos avançar com este trabalho:

{motivo}

Ficamos ao dispor para futuros projetos.

Com os melhores cumprimentos,
Lindo Serviço

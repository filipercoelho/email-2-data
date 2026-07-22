# Client Email Template — Pedir sinal / pagamento (Projetos composer)

Deterministic skeleton for the **sinal / pagamento** email. It is **not** an LLM prompt — the page
splices the amount/terms the user wrote into this skeleton, with no model in the loop. **A human
reviews, edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{conteudo}` token on its own line — it is
replaced by the free-text the user typed. **Amounts and dates the user writes are protected: the
optional AI polish may reword the prose but is checked to never alter a number.** Everything after
the `---` fence below is the body; the text above it (this note) is ignored.

---

Bom dia,

Para agendarmos a produção, segue o pedido de pagamento:

{conteudo}

Obrigado.

Com os melhores cumprimentos,
Lindo Serviço

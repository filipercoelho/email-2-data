# Client Email Template — Atualização de prazo / atraso (Projetos composer)

Deterministic skeleton for the **prazo / atraso** email. It is **not** an LLM prompt — the page
splices the update the user wrote into this skeleton, with no model in the loop. **A human reviews,
edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{conteudo}` token on its own line — it is
replaced by the free-text the user typed (new date + reason). **Dates the user writes are protected:
the optional AI polish may reword the prose but is checked to never alter a date.** Everything after
the `---` fence below is the body; the text above it (this note) is ignored.

---

Bom dia,

Uma atualização sobre o prazo deste trabalho:

{conteudo}

Pedimos desculpa por qualquer transtorno e ficamos ao dispor.

Com os melhores cumprimentos,
Lindo Serviço

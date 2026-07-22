# Client Email Template — Pronto para entrega / recolha (Projetos composer)

Deterministic skeleton for the **pronto / entrega** email. It is **not** an LLM prompt — the page
splices the text the user wrote into this skeleton, with no model in the loop. **A human reviews,
edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{conteudo}` token on its own line — it is
replaced by the free-text the user typed (pickup/delivery details). Everything after the `---` fence
below is the body; the text above it (this note) is ignored.

---

Bom dia,

O vosso trabalho está pronto:

{conteudo}

Combinamos a entrega/recolha?

Com os melhores cumprimentos,
Lindo Serviço

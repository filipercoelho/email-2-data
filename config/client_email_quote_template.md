# Client Email Template — Aceitar / enviar custos (Projetos composer)

Deterministic skeleton for the **orçamento / custos** email. It is **not** an LLM prompt — the page
splices the cost text the user wrote into this skeleton, with no model in the loop. **A human
reviews, edits, and sends; the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{conteudo}` token on its own line — it is
replaced by the free-text costs/total/validity the user typed. **Prices and dates the user writes
are protected: the optional AI polish may reword the prose but is checked to never alter a number.**
Everything after the `---` fence below is the body; the text above it (this note) is ignored.

---

Bom dia,

Obrigado pelo pedido. Segue a nossa proposta:

{conteudo}

Ficamos a aguardar a vossa confirmação para avançarmos.

Com os melhores cumprimentos,
Lindo Serviço

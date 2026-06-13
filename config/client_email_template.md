# Client Email Template (Projetos composer)

Deterministic skeleton for the **email para o cliente** composed on the Projetos page. It is
**not** an LLM prompt — the page assembles the email by splicing the selected clarifying
questions into this skeleton, with no model in the loop. **A human reviews, edits, and sends;
the system never sends.**

Edit the prose freely (pt-PT). The only rule: keep the `{perguntas}` token on its own — it is
replaced by the numbered list of questions the user ticked. Everything after the `---` fence
below is the body; the text above it (this note) is ignored.

---

Bom dia,

Para conseguirmos avançar com o orçamento, precisávamos de confirmar:

{perguntas}

Obrigado.

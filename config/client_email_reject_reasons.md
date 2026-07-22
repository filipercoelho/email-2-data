# Client Email — Reject reasons (Projetos composer)

The editable list of reasons offered when the user picks **Recusar o trabalho** in the composer.
The user chooses one (and may add a free note); the deterministic draft splices it into
`client_email_reject_template.md`. Nothing here is sent automatically — **a human reviews and
sends**, and a message is never binned silently (VISION non-negotiable).

Edit freely (pt-PT): one reason per line after the `---` fence below. Blank lines and a leading
`-`/`*` bullet are ignored. If this file is missing or empty, the built-in defaults are used
(`clientdraft.DEFAULT_REJECT_REASONS`).

---

Sem capacidade / agenda no período pedido
Prazo pedido não é exequível
Fora do âmbito técnico da oficina
Quantidade abaixo do mínimo para produção
Preço-alvo inviável para a margem necessária
Material indisponível / lead time incompatível
Ficheiros / desenho não executáveis como estão
Informação essencial em falta, nunca fornecida

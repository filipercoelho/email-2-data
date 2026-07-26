# Signature Template — the install-wide default closing (ADR-047)

The closing appended to every reply draft, for people who have **not** written their own in
«A minha conta → A minha assinatura». Someone's personal signature always wins over this file.

Edit the prose freely (pt-PT). Everything after the `---` fence below is the template; the text
above it is this note and is ignored. Placeholders are filled from the person's profile:

| Token | Vem de |
| --- | --- |
| `{nome}` | o nome da pessoa |
| `{cargo}` | a função, escrita por ela em «A minha conta» |
| `{telefone}` | o contacto telefónico, escrito por ela em «A minha conta» |
| `{email}` | o endereço da pessoa (definido por um administrador) |

**Two rules.** Only those four tokens exist — any other `{token}` makes this file fall back to the
built-in default, rather than printing a literal `{token}` in a client's email. And a line whose
placeholders are ALL empty is dropped, so someone with no phone number simply has no phone line.
Put one field per line if you want it to disappear cleanly.

**Nothing here is ever sent.** The app writes drafts; a human reviews, edits and sends.

---

Com os melhores cumprimentos,
{nome}
{cargo}
Lindo Serviço
{telefone}
{email}

# ADR-035 — Dark mode: token-level theming, OS-aware, with a toggle

- **Status:** Accepted (owner-requested 2026-07-24). Shipped for the shared shell + Fila + Para ti +
  Contrapartes + Capturas + Admin; only Projetos' spec editor and the legacy /inbox report remain (§4).
- **Extends:** the ADR-033/-034 token system.

## 1 · Context

The app was light-only: the shell's `:root` defined a single light palette, and a lot of component
CSS still hardcoded raw light surfaces (`background:#fff`, `#f8f9fb`, teal/purple tints). A dark theme
was requested "across the app."

## 2 · Decision

Theme entirely at the **token level** — no per-component dark rules:

1. **Dark palette** under `:root[data-theme="dark"]` using the design-proposal's validated dark values
   (dark graphite surfaces `#10151B`/`#171E26`, a lighter steel accent `#7FB0D0`, and the CVD-checked
   dark counterparty trio cliente `#219980` · fornecedor `#6E85DE` · lead `#BA8628` — the trio passes
   the dataviz validator on the dark surface, and bands are lightened for contrast). Light stays the
   `:root` default.
2. **No-flash, OS-aware, remembered.** A tiny inline script in `<head>` (before the stylesheet) stamps
   `data-theme` from the saved choice (`localStorage['e2d-theme']`), falling back to
   `prefers-color-scheme`. So the first paint is already correct, the OS preference is honored by
   default, and an explicit choice persists.
3. **A nav toggle** (moon in light → sun in dark) flips `data-theme`, persists it, and repaints its
   icon. `color-scheme` is set per theme so native form controls/scrollbars match.
4. **Tokenize, don't special-case.** Every raw light surface/tint in the shared shell and the Fila was
   replaced with a token (`--surface2`, `--int-bg`/`--int-line`, `--purple-bg` were added for the
   teal/purple chips). Because components speak only in `var(--…)`, they recolor for free — and a
   test forbids `background:#fff`/`#f8f9fb`/… from re-appearing, so dark mode can't regress into white
   patches.

## 3 · Consequences

- The **shared shell** (nav + the whole component kit: rows, cards, menus, palette, toast, thread
  messages, badges, trust chips, timeline) and the **Fila** are fully dark-capable — verified in both
  themes.
- Every lens gets the toggle and dark tokens (they all render through the shell); their **shared**
  components are dark everywhere.

## 4 · Coverage & deferred (honest scope)

Fully tokenized and dark-verified: the shared shell, **Fila**, **Para ti**, **Contrapartes**,
**Capturas**, **Admin** (each lens's `extra_css` swept to tokens — 0 raw hexes remain).

Still deferred: **Projetos'** `extra_css` (≈85 raw hexes — the spec editor is the largest, most
custom surface) and the legacy **/inbox report** (its own older palette). Those will show
light-palette remnants in dark mode until a follow-up sweep, which also doubles as a light-mode
palette-consistency pass (several of those hexes are off the ADR-033 palette even in light). Tracked,
not silently dropped.

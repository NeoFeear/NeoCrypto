---
name: crypto-sim
description: Tableau totalisateur d'un laboratoire de trading papier — chaque paire est un partant.
colors:
  board: "#0c0c0b"
  panel: "#141412"
  panel-2: "#1b1b18"
  rule: "#2a2924"
  rule-2: "#3a3832"
  lamp: "#ffb42c"
  chalk: "#eeede6"
  muted: "#9a978c"
  gain: "#62d67d"
  loss: "#ff6a5a"
  banner: "#a3160e"
  saddle-1: "#d7261e"
  saddle-2: "#f1f0ea"
  saddle-3: "#1f5fd6"
  saddle-4: "#f5cf17"
  saddle-5: "#14864d"
  saddle-6: "#0b0b0b"
  saddle-7: "#f57c1f"
  saddle-8: "#f5a8c8"
  saddle-9: "#15b3a6"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "30px"
    fontWeight: 800
    letterSpacing: "0.04em"
  headline:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    letterSpacing: "0.08em"
  label:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    letterSpacing: "0.14em"
  body:
    fontFamily: "Barlow, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  lamp:
    fontFamily: "Doto, Courier New, monospace"
    fontSize: "28px"
    fontWeight: 800
rounded:
  board: "4px"
  saddle: "3px"
  tag: "2px"
spacing:
  gutter: "24px"
  gutter-phone: "14px"
  cell: "16px"
components:
  saddle:
    backgroundColor: "{colors.saddle-1}"
    textColor: "{colors.chalk}"
    rounded: "{rounded.saddle}"
    size: "30px"
  panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.board}"
  chip-active:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.chalk}"
    rounded: "{rounded.board}"
  button-filter:
    backgroundColor: "{colors.lamp}"
    textColor: "{colors.board}"
---

# Design System: crypto-sim

## Overview

**Creative North Star: "Le tableau totalisateur"**

The dashboard is a racecourse tote board. Each live pair is a runner wearing its standard saddle-cloth colour (the number is its position in `live.pairs`). The field is ranked live by return against each pair's *real* starting cash. Figures glow as amber lamp digits on a matte black board, and every label is condensed painted lettering. The red simulation marquee on top never leaves: it is the product's first rule.

This is an Operate surface. It must be scannable in one glance ("who leads, by how much, how steady"), honest about thin data, and pedagogical. The Analyses page explains each ratio in one sentence and gives a bon/moyen/faible verdict, which turns to "peu fiable" when annualised ratios rest on less than 30 days of data.

**Key Characteristics:**
- Matte board, 1px rules, 4px corners; depth comes from panels, never from glow cards.
- Amber lamps for figures, green/red only for signed performance, chalk white for neutral counts.
- Saddle-cloth colours are the only categorical palette, used identically in the board, track, charts, chips and transactions.

## Colors

### Primary
- **Lampe ambre** (#ffb42c): figures, the active tab underline, the primary action, the sell tag.

### Signal
- **Vert gain** (#62d67d) / **Rouge perte** (#ff6a5a): signed performance only (returns, PnL, drawdown).
- **Rouge marquise** (#a3160e): the simulation banner and nothing else.

### Saddle cloths (categorical, 1–9)
1 rouge #d7261e (white numeral) · 2 blanc #f1f0ea (black) · 3 bleu #1f5fd6 (white) · 4 jaune #f5cf17 (black) · 5 vert #14864d (white) · 6 noir #0b0b0b (yellow numeral, yellow keyline) · 7 orange #f57c1f (black) · 8 rose #f5a8c8 (black) · 9 turquoise #15b3a6 (black). Numbers 10–12 are fallbacks.

### Neutral
Board #0c0c0b · panel #141412 · panel-2 #1b1b18 · rules #2a2924 / #3a3832 · chalk #eeede6 · muted #9a978c.

### Named Rules
**The Saddle Rule.** A pair's colour is its runner number, everywhere. Never recolour a pair per page or per chart.

**The Honest Number Rule.** Thin history is said, not dressed up. Under 30 days, annualised ratios get "peu fiable". Infinite ratios show ∞ with the reason. Missing data shows "N/A" or "—".

## Typography

**Painted:** Barlow Condensed (display, headings, labels, tabs; uppercase, tracked).
**Body:** Barlow.
**Lamp:** Doto, the dot-matrix bulb face, for every performance figure.

- **Display** (800, 30px, 0.04em, uppercase): the wordmark.
- **Headline** (800, 22px, 0.08em, uppercase): section titles, each with a muted `small` subtitle.
- **Label** (700, 12px, 0.14em, uppercase, muted): every figure's caption.
- **Lamp** (Doto 800, 20–52px): figures. The portfolio return is the one 52px lamp.

## Layout

Max width 1240px, 24px gutter (14px on phones). The Principal page reads totalisateur → course → tableau des partants → détail du partant → backtest. Grids share 1px rules. At 980px and 640px the totalisateur and stat tiles re-grid (2 columns on phones), the board drops the Forme/Départ/Trades columns, and the track keeps only the saddles.

## Elevation & Depth

Flat. Depth comes from panel tone (#141412 on #0c0c0b) and 1px rules. Only lamps glow (a soft text-shadow in their own colour), because bulbs emit light. The saddle has an inset keyline plus a 2px drop, like a cloth on a board.

## Shapes

4px panels, 3px saddles, 2px tags. No pills, no large radii.

## Components

### Totalisateur
Six cells: Engagé, Valeur, Rendement du portefeuille (the big lamp), Partants, Trades, Jour. Each has a label on top, the lamp figure, and an optional muted sub-line.

### La course (signature)
One dashed lane per pair in runner order, a chalk start post at 0 %, and each saddle placed by its return between the field's min and max. On load the runners gallop from the post to their place (1.6s, ease-out-expo, 90ms stagger).

### Tableau des partants
Ranked rows showing position lamp, saddle + symbol + strategy, a form sparkline in the saddle colour over a dashed start line, starting cash, value, and a signed return lamp. The whole row links to the detail.

### Rapport des commissaires
A 4×2 grid of metrics. Each has a label + verdict tag, a lamp figure, and a one-sentence explanation (max 34ch).

### Filters (Transactions)
Ruled cells with the label printed inside and a borderless value. The amber "Filtrer" button and the "Exporter CSV" link sit flush at the end of the strip.

## Do's and Don'ts

### Do:
- **Do** put every performance figure in a lamp; keep captions in painted labels.
- **Do** measure returns against `starting_capital()`, never `capital_per_pair`.
- **Do** keep the banner text exactly "⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé" (it is tested).

### Don't:
- **Don't** add neon glows, gradients on panels, or glass: the board is matte.
- **Don't** introduce a second categorical palette; the saddle cloths are it.
- **Don't** show odds, stakes or anything betting-like: the race is a way to compare, not to wager.

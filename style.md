# Cadence Deck — Style Guide

Reusable design system for the weekly Optilogic cadence presentations.
Reference implementation: [build_week1_deck.py](build_week1_deck.py) → `melbourne_week1_cadence_deck.pptx`.
Tooling: **python-pptx 1.0.2** (in `.venv`). Build with `.venv/bin/python build_weekN_deck.py`.

---

## 1. Canvas

| Property | Value |
|---|---|
| Aspect ratio | 16:9 widescreen |
| Slide width | `Inches(13.333)` |
| Slide height | `Inches(7.5)` |
| Base layout | blank (`prs.slide_layouts[6]`) — everything is drawn, no placeholders |
| Body font | Calibri |
| Shadows | always off (`shape.shadow.inherit = False`) |

---

## 2. Palette

| Role | Name | Hex | RGB |
|---|---|---|---|
| Header band, headings, key numbers | **NAVY** | `#0F2E4E` | `(15, 46, 78)` |
| Primary accent (rules, bullets, "our set-up") | **TEAL** | `#1F8A8C` | `(31, 138, 140)` |
| Body text | **SLATE** | `#3A4A5A` | `(58, 74, 90)` |
| Panel / card fill | **LIGHT** | `#F2F5F8` | `(242, 245, 248)` |
| Question / caution accent | **AMBER** | `#C87A1E` | `(200, 122, 30)` |
| White text & card fill | **WHITE** | `#FFFFFF` | `(255, 255, 255)` |
| Footer, muted labels | **GREY** | `#6B7682` | `(107, 118, 130)` |
| Kicker text on navy | (tint) | `#9FC5C6` | `(159, 197, 198)` |
| Subtitle text on navy | (tint) | `#CFDAE4` | `(207, 218, 228)` |
| Card border | (hairline) | `#D5DDE4` | `(213, 221, 228)` |

**Colour meaning is consistent — keep it:**
- **NAVY** = structure / facts / "what we built".
- **TEAL** = our decisions, our set-up, positive confirmations.
- **AMBER** = questions to ask, cautions, "what it cannot do".

---

## 3. Typography scale

| Use | Size (pt) | Weight | Colour |
|---|---|---|---|
| Title-slide headline | 40 | bold | WHITE |
| Title-slide subtitle | 24 | regular | `#CFDAE4` |
| Slide title (in header band) | 25 | bold | WHITE |
| Kicker (above title) | 12 | bold | `#9FC5C6` |
| Section / card heading | 14–15 | bold | NAVY or accent |
| Panel header label (on colour bar) | 12 | bold | WHITE |
| Body / bullets | 10.5–11.5 | regular | SLATE |
| Question label `Q1` | 11–13 | bold | accent |
| Footer | 9 | regular | GREY |

Line spacing for dense bullets: `0.95–1.0`. Space after paragraphs: `4–8 pt`.

---

## 4. Standing elements

**Header band** (every content slide):
- Navy rectangle, full width × `Inches(1.15)` tall.
- A `Pt(4)` accent stripe directly beneath it (TEAL default; AMBER for question slides).
- Kicker at `(0.55", 0.14")`, title at `(0.55", 0.42")`.

**Footer** (every content slide):
- Left `(0.55", 7.08")`: `"Optilogic cadence · Week N — <theme>"` in 9 pt GREY.
- Right `(12.2", 7.08")`: page number, right-aligned, 9 pt GREY.

**Full-bleed slides** (title & closing): navy fills the whole canvas; one TEAL `Pt(4)` divider stripe; no header/footer band.

---

## 5. Slide templates (the deck's vocabulary)

Reuse these five in this order-ish each week. Numbers = the Week-1 deck.

1. **Title** — full navy. Kicker + headline + subtitle + one-line context strip.
2. **Summary: stream / balance** *(2 panels)* — left = a vertical stage list with teal tick-blocks; right = a data table + a highlighted takeaway box.
3. **Summary: card grid** *(2×2)* — four `LIGHT` cards, each with a coloured left spine (`Inches(0.12)` wide), bold NAVY heading, accent bullets. Good for "schema / capacities / cost / logic".
4. **Summary: two-column** — left `WHITE` bordered panel (e.g. cost stack), right `LIGHT` panel (e.g. business logic). Bold lead-in term + regular description per line.
5. **Agenda** — numbered navy circles (`add_shape(9)` oval) + title + one-line description per row, on `LIGHT` bands.
6. **Question** *(2 panels — the workhorse)* — via `question_slide(n, topic, agreed, questions, accent)`:
   - Left `LIGHT` panel, navy header **"OUR SET-UP / ASSUMPTION"**, teal bullets.
   - Right `WHITE` panel, accent header **"QUESTIONS TO ASK"**, numbered `Q1…Qn`.
   - Questions auto-space to fill height.
7. **Foundational / three-panel** — left "how it works now", right split into "what it cannot do" (amber) + "questions" (teal). Use for the one deep issue of the week.
8. **Closing / exit criteria** — full navy, headline + list of `term — description` rows with teal tick-blocks.

---

## 6. Layout grid (the measurements that make it line up)

- Outer margin: **`0.55"`** left/right.
- Content starts at **`y = 1.5"`** (just below the header band + stripe).
- Two-panel split: left panel `x=0.55"` w≈`6.15"`; right panel `x=6.95"` w≈`5.83"`; **`0.4"` gutter**.
- 2×2 card grid: columns at `x = 0.55"` / `6.95"`; rows at `y = 1.5"` / `4.5"`; each card `5.83" × 2.85"`.
- Coloured left spine on cards: `Inches(0.12)` wide, full card height.
- Panel header bar: `Inches(0.45–0.5)` tall, label inset `0.2"`.
- Footer baseline: `y = 7.08"`.

---

## 7. How to reuse next week

1. Copy `build_week1_deck.py` → `build_weekN_deck.py`.
2. Keep the whole top block unchanged: **palette**, and the helpers `slide()`, `box()`, `txt()`, `header()`, `footer()`, `question_slide()`. That block *is* the style engine — don't edit it.
3. Change only:
   - `kicker` / `title` strings and `footer(s, n)` week number + theme.
   - The summary content (recap of last session's decisions).
   - The `question_slide(...)` calls for the new week's topic (see the cadence markdown for that week's Key questions).
   - The exit-criteria list on the closing slide.
4. Rebuild: `.venv/bin/python build_weekN_deck.py`.

**Recurring shape each week** (mirrors the cadence doc's session shape):
Title → 2–3 summary/recap slides → agenda → question slides (one per topic) → exit criteria.

**Rules of thumb**
- Max ~4 bullets per panel; ~11 pt body is the floor for readability.
- One idea per slide; if a panel overflows, split it — don't shrink below 10.5 pt.
- Every question slide pairs **our assumption (teal, left)** with **the ask (amber/accent, right)** — never a wall of questions with no context.
- Keep colour meaning fixed (§2) so the audience reads structure vs decision vs question at a glance.

---

## 8. Helper API (from the reference script)

```python
slide()                              # add a blank slide
box(s, l, t, w, h, fill, line)       # filled/bordered rectangle, no shadow
txt(s, l, t, w, h, runs, align, ...) # multi-run textbox; runs = list of paragraphs,
                                     #   each paragraph = list of (text, size, bold, color[, italic])
header(s, kicker, title, accent)     # navy band + accent stripe + kicker + title
footer(s, n)                         # left caption + right page number
question_slide(n, topic, agreed,     # full two-panel question slide
               questions, accent)
```

All coordinates are `pptx.util.Inches` / `Pt`. Colours are `pptx.dml.color.RGBColor`.

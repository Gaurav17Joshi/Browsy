# Building a good single-file web page

How to build a page worth looking at, under the constraints Browsy actually has.
Attach this file to the chat along with the task and follow it.

## The hard constraints

The page is delivered as a `blob:` URL created inside `run_js`. That means:

- **One file. No network.** No CDN stylesheet, no Google Font, no remote image,
  no icon library. Anything fetched will simply not arrive. All CSS goes in one
  `<style>` block, all JS in one `<script>` block.
- **It must be built in a single `run_js` call.** Nothing carries between calls.
- **System fonts only.** Use the stack below; it looks native everywhere.
- **Images must be inline SVG** or CSS. See "Logos without a network".

None of this has to look cheap. Constraints on assets are not constraints on
design.

## Start from data, not markup

Write the content as a JS array first, then render from it. Sorting, filtering
and detail panes then cost almost nothing, and the page stays consistent.

```js
const MODELS = [
  { id: 'qwen', name: 'Qwen 3.8-27B', maker: 'Alibaba', accent: '#7c5cff',
    arch: 'Dense', params: '27B', active: '27B', released: '2026-08-15',
    ctx: '256K', licence: 'Apache 2.0',
    vram: { fp16: '54 GB', int8: '27 GB', int4: '15 GB' },
    price: { in: 0.20, out: 0.60 },
    benchmarks: [ { name: 'MMLU-Pro', score: 78.4 } ],
    strengths: ['...'], weaknesses: ['...'],
    sentiment: { mood: 'positive', quotes: [{ text: '...', video: '...' }] } },
  // ...
];
```

Then `MODELS.map(renderCard).join('')` into the DOM. Never hand-write the same
card five times.

## Layout

- One column of stacked sections, max width **1100px**, centred, `padding: 0 24px`.
- Vertical rhythm on a **4px** scale: 8, 12, 16, 24, 32, 48, 64. Nothing else.
- Sections separated by 64px, not by horizontal rules.
- Cards in a responsive grid, never a fixed column count:
  ```css
  .grid { display: grid; gap: 20px;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  ```

## Type

```css
font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
/* numbers in tables must line up */
.num { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
```

A scale with real contrast — timid type is the most common way these pages look
amateur:

| role | size | weight | notes |
|---|---|---|---|
| page title | 40–52px | 700 | tight leading, `letter-spacing: -0.02em` |
| section head | 24–28px | 650 | |
| card title | 18px | 600 | |
| body | 15px | 400 | `line-height: 1.6` |
| label / meta | 12–13px | 500 | uppercase, `letter-spacing: .06em`, dimmed |

Body text never wider than **70 characters**: `max-width: 62ch`.

## Colour

Define tokens once. Dark theme, one accent per model so cards are
distinguishable at a glance.

```css
:root {
  --bg: #0d0f14; --surface: #161a21; --surface-2: #1d222b;
  --line: #262c37; --text: #e8ecf2; --dim: #98a2b3; --faint: #6b7280;
  --good: #35d07f; --warn: #f0b429; --bad: #f2565a;
}
```

Rules that matter:

- Body text at `--text`, secondary at `--dim`. Never below `--faint` for
  anything a reader needs.
- Accent colours are for identity and emphasis, not for paragraphs.
- Depth comes from surface steps and a 1px `--line` border, not heavy shadows.
- One accent per card, used in the border-top, the logo tile and the active
  state — three touches, not twenty.

## Logos without a network

Real logo files cannot be fetched. Two honest options:

**Monogram tile** — reliable, looks deliberate:

```html
<div class="logo" style="--c:#7c5cff">Q</div>
```
```css
.logo { width: 44px; height: 44px; border-radius: 11px; display: grid;
        place-items: center; font-weight: 700; font-size: 19px; color: #fff;
        background: linear-gradient(145deg, var(--c), color-mix(in srgb, var(--c) 60%, #000));
        box-shadow: inset 0 1px 0 rgba(255,255,255,.22); }
```

**Hand-drawn inline SVG** — only if you can render the mark from simple shapes
and it is recognisable. A bad approximation of a company logo looks worse than a
clean monogram. When in doubt, monogram.

Never hotlink a logo, and never claim a mark is official.

## Interaction

Keep it to a few things that earn their place. All vanilla JS, delegated from
one listener.

**Expanding cards** — the main one. Click a card to reveal detail.

```js
document.addEventListener('click', (e) => {
  const card = e.target.closest('[data-model]');
  if (!card) return;
  const open = card.classList.toggle('open');
  card.querySelector('.head').setAttribute('aria-expanded', open);
});
```
```css
.detail { display: grid; grid-template-rows: 0fr;
          transition: grid-template-rows .28s cubic-bezier(.2,.8,.3,1); }
.open .detail { grid-template-rows: 1fr; }
.detail > div { overflow: hidden; }   /* required for the 0fr->1fr trick */
```

That `grid-template-rows: 0fr → 1fr` animates to auto height, which
`max-height` hacks never do properly.

**Sortable table** — click a header to sort by that column:

```js
let dir = 1, key = 'params';
ths.forEach(th => th.onclick = () => {
  const k = th.dataset.key;
  dir = k === key ? -dir : 1; key = k;
  render([...MODELS].sort((a, b) => (a[k] > b[k] ? 1 : -1) * dir));
});
```

**Filter chips** — dense vs MoE, small vs large. Toggle a class, re-render.

**A bar per number** — a benchmark score or a VRAM figure reads far faster as a
bar than as a digit:

```html
<div class="bar"><span style="width:78.4%"></span></div>
```

Good defaults: hover lifts a card 2px, transitions 160–280ms, `ease-out`.
Always honour:

```css
@media (prefers-reduced-motion: reduce) { * { transition: none !important;
                                              animation: none !important; } }
```

Do not build carousels, parallax, typewriter effects or anything that moves on
its own. They read as filler.

## Tables

- `border-collapse: collapse`, horizontal `--line` rules only, no vertical ones.
- Header row: uppercase 12px, `--dim`, `position: sticky; top: 0`.
- Numbers right-aligned and tabular; text left-aligned.
- Zebra striping only past ~8 rows.
- Wrap in `<div style="overflow-x:auto">` so a narrow window scrolls the table
  rather than the page.

## Responsive

Test the layout mentally at 1400px, 900px and 500px. The grid handles most of
it; add one breakpoint where the header stacks:

```css
@media (max-width: 640px) {
  .topbar { flex-direction: column; align-items: flex-start; gap: 12px; }
  h1 { font-size: 32px; }
}
```

## Accessibility, cheaply

- Real `<button>` for anything clickable, so it works from the keyboard.
- `aria-expanded` on expanders, kept in sync.
- `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`
- Never carry meaning in colour alone — pair a red dot with a word.

## The finishing pass

Before returning the URL, look at what you built and ask:

1. Is there a clear first thing to read, and does it say something?
2. Can someone skim the page in 20 seconds and come away with the shape of it?
3. Is every number attributed, and every quote traceable to its source?
4. Is anything a placeholder? Remove the section rather than ship "TBD".
5. Would this look at home on a real product's site — or does it look like a
   default HTML page with colours added?

## What makes these pages bad

- Content dumped into `<p>` tags with no hierarchy.
- Everything the same size and weight, so nothing leads.
- Cramped: padding under 16px inside cards, sections 24px apart.
- Pure `#000` on `#fff`, or a neon accent used as body text.
- Emoji standing in for icons.
- Fabricated numbers to fill a column. Leave the cell as "not published" and say
  so — an honest gap is worth more than a confident invention.

# Browser Agent Architectures — Summary

Condensed from [RESEARCH-browser-agent-architectures.md](RESEARCH-browser-agent-architectures.md). Aug 2026.

**Product:** floating chat panel over Chrome. You type a task, it drives the browser you already have open. Continuous conversation like Claude Code; ↻ starts a fresh context.

---

## The one thing that matters

Online-Mind2Web scores went **42% (Aug 2025) → 97% (Jun 2026)**. That was **harness design, not better models**. Spend your effort on context management and error recovery, not prompts.

---

## Four architectures

| | Mechanism | Verdict |
|---|---|---|
| **1. Vision / coordinates** | Screenshot → `click(x,y)`. Refinements: crop-and-refine (zoom into a region, re-click), Set-of-Marks (numbered overlay boxes). | Universal but ~1200 tok/screenshot and fails on dense UI (date pickers, grids). **Fallback only.** |
| **2. A11y tree + refs** | Accessibility tree → compact text, each element gets `@e17`. Model says `click @e17`. Screenshot is a tool it calls, not a per-turn push. | ~10× cheaper, states roles outright. **The 2026 consensus primary layer.** |

| **3. Code-as-action** | No click/type tools — give it a JS/Python runtime with the page as an object. It writes programs. | What the **97%** system did. Best for extract/compare/loop. **Highest ceiling.** Layer on top of #2. |
| **4. WebMCP** | Site declares typed tools via `document.modelContext`; agent calls them directly. | Live now (W3C, Chrome 149 origin trial, Expedia/Shopify/Etsy/Target). Coverage tiny, ~30 lines to support. **Top rung, not a foundation.** |

### What an "accessibility tree" is
Screen readers can't see pixels, so browsers keep a second description of the page built out of *meaning* — not the picture, not the HTML, just a labeled list:
```
button   "Add to cart"
textbox  "Search"       value: ""
combobox "Size"         expanded: false
```
Role + name + state. A search box that's ~15 lines of nested divs and hashed class names in HTML becomes one line, with the role **stated rather than guessed**. Same idea as macOS `AXUIElement` (what Mac computer-use agents read) and Windows UI Automation. Get it over CDP with `Accessibility.getFullAXTree`.

**Plus a cross-cutting layer: skill caching.** Record successful trajectories, replay deterministically with zero LLM calls, re-ground only on failure. This is where felt speed comes from for repeated tasks.

### Why #2 needs care
A11y trees depend on decent ARIA, and most real sites are bad at it. A 2026 Berkeley/Michigan study: success dropped **78% → 42%** on degraded trees. Fix: augment the tree with a DOM pass that synthesizes refs for anything with a click handler / `tabindex` / `cursor:pointer`.

### The details that separate 50% from 85%
Snapshot versioning (reject actions against stale refs) · single-snapshot retention · trim the tree with a cheap model (10k → 800 tok) · explicit `memory` param · bulk actions (38 tool calls → 10 on forms) · prompt caching (~75% cache hit, ~89% cost cut). Together these flatten token use to **~12.6k/step regardless of task length** vs 43k+ and climbing.

---

## The Chrome attachment problem

**CDP** (Chrome DevTools Protocol) is Chrome's built-in remote control: launch with `--remote-debugging-port=9222` and anything on the machine can drive that browser. Playwright and every browser agent run on it.

That port has **no auth** — whoever connects inherits all live logins. Malware abused it to lift session cookies wholesale. So **Chrome 136** made `--remote-debugging-port` be **silently ignored unless you also pass `--user-data-dir=<non-default folder>`** (different folder = different encryption key = can't touch the real profile's secrets).

**So: you can't attach to the Chrome you already have open.** Every "connect to port 9222" tutorial written before mid-2025 is wrong. A custom user-data-dir starts logged out — but it **persists**, so it's "log in once, then it's the agent's own browser."

### Chosen v1: dedicated profile + CDP + injected panel
```
chrome.exe --remote-debugging-port=9222 --user-data-dir=<project>/.chrome-profile
```
New window, full CDP, no extension, no service-worker lifecycle problems, no debugger infobar.

**Chat panel injected into the page**, so it genuinely floats over the site with no second app window:
- `Page.addScriptToEvaluateOnNewDocument` → panel auto-mounts on every navigation and new tab (the key call — survives navigation without manual re-injection)
- Mount into a **Shadow DOM** root, `position: fixed` — isolates it from page CSS, which is what makes it work on arbitrary sites
- Panel ↔ daemon via `Runtime.addBinding` or a localhost WebSocket
- Conversation + panel position live in the **daemon**, not the page (the page is wiped on every navigation)
- Gaps, all fine for v1: invisible on `chrome://` pages, the Web Store, and the PDF viewer
- `.gitignore` the profile folder — it holds live session cookies

### Later upgrade: MV3 extension + `chrome.debugger`
The only clean path into the user's *real* logged-in Chrome (permission-gated by installing it). Cost: an unhideable "started debugging this browser" infobar. Note **MV3 service workers die at ~30s idle**, which would kill a 3-minute run — so the LLM loop must live in a local daemon regardless.

→ **Build v1 with the daemon owning the loop and the browser side as a thin CDP transport.** Then A→B is swapping the transport; loop, prompts, and UI carry over unchanged.

---

## Recommended stack

**Ladder — take the highest rung available each step:**
```
WebMCP tool exists?      → call it
extract / loop / compare? → write and run code
cached skill matches?     → replay deterministically
default                   → a11y snapshot + refs
element missing / canvas? → screenshot + SoM + crop-refine
```

- **Shell:** Node daemon launches its own Chrome on a dedicated profile, connects CDP, injects the Shadow-DOM chat panel. No extension in v1.
- **Brain:** the daemon owns conversation state, API key, and the agent loop. Browser side stays a thin CDP transport.
- **Perception:** `Accessibility.getFullAXTree` over CDP, augmented with the DOM pass. Version refs, retain one snapshot, trim with Luna.
- **Action:** ref tools + `run_js` as first-class, batched.
- **Models:** Terra drives, Luna ($0.20/Mtok in — 25× cheaper than Sol) trims and extracts, escalate to Sol after 2 failed steps on the same subgoal. Stable prompt prefix for caching.
- **Security from day one:** per-task domain allowlist (blocks the exfiltration chain), danger-word click interception (`delete`/`refund`/`transfer`) as a string match in your executor not a prompt instruction, confirm-before-sensitive. Prompt injection is unsolved — Anthropic got 23.6% → 11.2% with heavy mitigation and that still lands 1-in-9. Design for permanent risk.

**Build order:** a11y+refs → `run_js` → vision fallback → skill caching → WebMCP.

---

## UX notes

- One append-only thread; browser is shared mutable state across turns (that's what makes "now filter under $50" work).
- **↻ clears the message history, not the browser** — say so: *"New chat — still on amazon.com/cart."* Offer "new chat + new tab" separately.
- Stream the action *before* executing ("→ clicking **Sign in**"), then the result. Perceived latency collapses.
- Show the JS it ran. Users trust it more when they can see it.
- Typing mid-run queues as the next instruction; hard stop aborts the in-flight CDP command.

---

## Model reference (GPT-5.6, all support vision + `computer` tool)

| | Context | $/Mtok in | $/Mtok out |
|---|---|---|---|
| Sol | 1.05M | $5 | $30 |
| Terra | 1.05M | $2 | $12 |
| Luna | 1.05M | $0.20 | $1.20 |

`computer` tool emits **batched** `actions[]` (renamed from `computer_use_preview`). Viewport 1440×900, screenshots at `detail: "original"`.

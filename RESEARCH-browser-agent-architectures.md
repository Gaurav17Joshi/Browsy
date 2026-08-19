# Browser-Use Agent: Architecture Research (Aug 2026)

**Goal being designed for:** a small floating chat rectangle in front of Chrome. You type a task, it drives the browser you already have open. Conversation is continuous (Claude-Code style: you watch it work, then ask the next thing, all in one context). A ↻ button starts a fresh context.

This document covers (1) what the design space actually is, (2) four cutting-edge ways to structure the agent core, (3) the shell/UI + Chrome-attachment problem, which is a genuinely hard constraint in 2026, (4) a recommended stack.

---

## 0. Ground facts worth knowing before choosing

**Models.** GPT-5.6 shipped 9 Jul 2026 as a three-tier family. All three tiers support vision and the `computer` tool:

| Model | Context | $/Mtok in | $/Mtok out | Role in a browser agent |
|---|---|---|---|---|
| GPT-5.6 Sol | 1.05M | $5 | $30 | Planner / hard recovery |
| GPT-5.6 Terra | 1.05M | $2 | $12 | Default driver |
| GPT-5.6 Luna | 1.05M | $0.20 | $1.20 | Per-step actor, snapshot trimming, extraction |

Luna at $0.20/Mtok input is ~25× cheaper than Sol. That price gap is the single biggest architectural lever you have — it makes a **two-model loop** (Luna does the steps, Sol/Terra plans and unsticks) the economically obvious default, and it makes "trim the snapshot with a cheap model before it hits the expensive one" a real technique rather than a micro-optimization.

**The `computer` tool** (Responses API) works with `gpt-5.6` and `gpt-5.4`. It emits batched `actions[]` — `click`, `double_click`, `drag` (multi-point path), `scroll` (pixel deltas), `type`, `keypress`, `move`, `wait`, `screenshot` — you execute them and return a new screenshot. Note: renamed from `computer_use_preview`, and actions are now **batched arrays**, not one-at-a-time. Recommended viewport 1440×900 or 1600×900, screenshots at `detail: "original"` for click accuracy.

**Benchmarks (2026).** Online-Mind2Web = 300 tasks on 136 live sites, the benchmark that matters for this project.

- Browser Use Cloud (`bu-max`): **97.0%** — top of the Steel.dev leaderboard (Jun 2026)
- Microsoft Fara1.5-27B: **72.3%** Online-Mind2Web
- Fara1.5-9B: **63.4%** Online-Mind2Web / **86.6%** WebVoyager
- GPT-5 Medium + SeeAct scaffold (Aug 2025): **42.3%**

That spread from 42% → 97% happened in ~10 months and was **mostly not model improvement — it was harness improvement**. This is the central finding of the year and it should shape how you spend your effort.

**The thesis everyone converged on:** *"model capability does not limit agent performance; architectural decisions determine success or failure."* (arXiv 2511.19477). Context management, action-space design, and error recovery matter more than which frontier model you pick.

---

## 1. The design space (three independent axes)

Most people conflate these. They're orthogonal, and you pick one value on each:

**Axis A — Perception: how the page enters the model's context**
1. Pixels (screenshot)
2. Accessibility tree (ARIA roles/names, semantic, compact text)
3. Raw/processed DOM (full truth, including hidden and off-screen nodes)
4. Site-declared tools (the site tells you what it can do — no perception needed)

**Axis B — Action: how the model expresses intent**
1. Coordinates (`click(x=412, y=908)`)
2. Element refs / indices (`click(@e17)` or `click(23)`)
3. Semantic locators (`find role=button name="Submit"`)
4. Code (model writes JS/Python that runs against the page)
5. Named site tools (`book_flight({...})`)

**Axis C — Attachment: where the browser lives**
1. External CDP to a browser you launched (Playwright/Puppeteer)
2. Chrome extension inside the user's real session (MV3 + `chrome.debugger`)
3. Embedded browser in your own app (Electron/CEF)
4. Remote cloud browser (Browserbase, Steel, Anchor)

**For your product, Axis C is nearly forced** and it's the thing to decide first — see §3. Sections 2.1–2.4 are the four leading combinations of A and B.

---

## 2. The four architectures

### 2.1 — Pure Vision / Coordinate CUA
*(this is the one you described, plus the refinement you intuited)*

**Mechanism.** Screenshot → model → `click(x,y)` / `type` / `scroll` → execute → new screenshot → repeat. Your zoom-in instinct is real and has a name: **crop-and-refine grounding** — the model emits a coarse region (top-left/bottom-right), you crop and upscale that region, re-send it, and the model emits a precise coordinate in the cropped frame which you map back to page space. It measurably improves clicks on dense UI. A related trick is **Set-of-Marks (SoM)**: overlay numbered boxes on interactive elements in the screenshot so the model picks a number instead of a pixel — which is really a bridge to §2.2.

**Who runs this.** OpenAI's `computer` tool, Anthropic Computer Use, native CUA models (Fara1.5, UI-TARS). Note Fara1.5 is a *native* CUA — trained end-to-end on screenshot→action, ~2M trajectory samples — and that's why a 9B model beats generic frontier models prompted to act like a CUA. Generic vision models are bad at grounding; specialized ones are good.

**Pros**
- Universal. Works on canvas apps, `<iframe>` soup, WebGL, Flash-era enterprise junk, PDFs in the viewer, native dialogs.
- Zero site cooperation, framework-agnostic, no selector rot.
- Matches human demonstration data, so it's the mode frontier labs actively train.

**Cons**
- **Expensive.** A 1440×900 screenshot is ~1000–1500 tokens. 30 steps of full-fidelity screenshots ≈ 40k+ tokens of pure image, and it grows linearly.
- **Grounding failures on dense UI.** The documented killer: "vision models must place a bounding box precisely over a specific 24px cell in a tightly packed grid" — date pickers, spreadsheet grids, dense tables. This is the #1 failure cluster.
- Can't see off-screen or hidden state; requires scrolling to discover, which burns steps.
- Coordinate answers are resolution/DPI-fragile — you must pin viewport and device pixel ratio.

**Verdict.** Necessary as a **fallback layer**, wrong as the **primary** layer. Every top-scoring 2026 system keeps vision available and uses it for maybe 5–15% of steps.

---

### 2.2 — Accessibility Snapshot + Refs (hybrid) — *the 2026 consensus*

**What an accessibility tree is.** Screen readers can't look at pixels, so browsers and operating systems maintain a second, parallel description of the UI built purely out of *meaning*, alongside the visual one. Not the picture, not the code — a list of what things are:

```
button      "Add to cart"
textbox     "Search"        value: ""
combobox    "Size"          expanded: false
checkbox    "Gift wrap"     checked: true
```

Each node has a **role** (what kind of control), a **name** (what it's called), and **state** (checked/expanded/disabled/value). One real search box is often ~15 lines of nested divs, SVG icons and hashed class names in HTML — and the model has to *infer* it's a search box from a placeholder attribute. In the a11y tree it's `textbox "Search products"`, with the role **stated, not guessed**.

Same idea exists outside the browser: macOS `AXUIElement`/NSAccessibility (what Mac computer-use agents read), Windows UI Automation. The accessibility layer built for blind users turned out to be the best machine-readable interface anyone ever shipped, and agents quietly inherited it.

**Mechanism.** Pull the browser's accessibility tree (via CDP `Accessibility.getFullAXTree`, or Playwright's `_snapshot`), flatten it to compact text where every interactive node gets a stable **ref** (`@e17`, or an integer index). Model replies `click @e17` / `fill @e23 "hello"`. A screenshot is available as a *tool the model calls when it needs it*, not something you push every turn.

```
- button "Add to cart" @e17
- textbox "Search" @e23 (value: "")
- link "Sign in" @e31
- combobox "Size" @e44 (expanded=false)
```

**Why it wins.** A form field that is 100+ lines of nested divs in HTML compresses to 2 lines of a11y data. It's ~10× cheaper than screenshots, resolution-independent, and it states the *role* outright — the model never has to guess which pixels are clickable.

**The critical implementation details** (these are what separate 50% systems from 85% systems):

1. **Snapshot versioning.** Every ref carries a snapshot version ID. If the page mutated since the snapshot the model was looking at, the action is rejected rather than clicking the wrong thing. This kills the single most common silent failure in browser agents.
2. **Single-snapshot retention.** Only the *most recent* tree stays in context. Older ones collapse to a one-line summary.
3. **Snapshot trimming with a cheap model.** Run Luna over the raw tree with the current subgoal: 10,000 tokens → 500–1,000 tokens. At $0.20/Mtok this costs approximately nothing.
4. **Explicit `memory` parameter.** The agent writes a running summary of what it has learned/done; verbose history gets dropped. Combined with 1–3, token usage **stabilizes around ~12,600/step regardless of task length**, versus 43,000+ and climbing with naive full history.
5. **Bulk actions.** Let the model emit several independent actions in one call. Form-filling drops from 38 tool calls to 10 — **~74% fewer round trips**, which is mostly a *latency* win and latency is what makes a chat UI feel alive.
6. **Prompt caching.** With a stable system prompt + tool schema prefix, ~75% of input tokens serve from cache — reported **~89% cost reduction** on long sessions. A 30-step e-commerce comparison task ran for **$0.1454**.

**Reported result:** ~85% on WebGames (53 challenges) vs ~50% for prior approaches; human baseline 95.7%. The 8 failures: 5 needed real vision, 2 needed sub-second real-time reaction, 1 needed precision dragging. **Architectural limits, not reasoning limits.**

**Cons**
- Depends on decent ARIA. Modern web apps are frequently terrible at it — `<div onclick>` that should be a button, custom comboboxes exposing no state. On a large fraction of real sites the tree under-represents the page.
- A 2026 UC Berkeley/U-Michigan study: task success fell **78% → 42%** on sites with a degraded accessibility tree. That's the size of the risk.
- Screen-reader semantics ≠ agent semantics; some things that matter (visual grouping, layout adjacency) simply aren't in the tree.

**Verdict.** The right **primary** layer. But it needs a DOM-derived augmentation pass (synthesize refs for anything with a click handler, `role`, `tabindex`, or cursor:pointer — not just what ARIA exposes) and a vision escape hatch. Reference implementations: Playwright MCP, Chrome DevTools MCP, `vercel-labs/agent-browser` (Rust CDP daemon, `@e1` refs, MCP tool profiles so agents load only the tools they need).

---

### 2.3 — Code-as-Action / Browser-as-Runtime — *the 97% architecture*

**Mechanism.** Stop giving the model `click`/`type` tools. Give it a **persistent JS/Python runtime with the page as an object**, and let it write code. It emits a program; you execute it in a sandbox with CDP access; it sees stdout/exceptions and iterates.

```python
# model emits this, not 40 tool calls
rows = page.eval("""[...document.querySelectorAll('.product-row')]
  .map(r => ({name: r.querySelector('h3').innerText,
              price: parseFloat(r.querySelector('.price').innerText.slice(1))}))""")
cheapest = min(rows, key=lambda r: r['price'])
page.click(f"text={cheapest['name']}")
```

**Why this is the frontier.** The team that hit **97.0% on Online-Mind2Web** described the change bluntly: *"Instead of only tools like click and type, it added Python to parse HTML and extract data."* Code-as-action aligns with the model's training distribution far better than bespoke JSON tool schemas — the original CodeAct result showed up to **+20% success rate** across 17 LLMs versus JSON action spaces, and that gap has widened as models got more code-native. OpenAI's own computer-use guide now lists a **code-execution harness** as one of three official integration paths, calling it better "for loops, DOM inspection, and longer workflows."

It also collapses whole classes of task into one step. "Find the cheapest flight under 6 hours across these 3 tabs" is one program, not 60 perceive-act rounds. And GPT-5.6's headline capability is exactly this: *"write and run lightweight programs that coordinate tools, process intermediate results, monitor progress, and choose the next action... with fewer tokens, fewer model round trips."*

**Pros**
- Best-in-class on extraction, comparison, aggregation, pagination, "do this for all 40 items."
- Loops and conditionals are free — no model call per iteration.
- Radically fewer round trips → lower latency and lower cost on long tasks.
- Naturally produces **replayable artifacts** (see §2.5).

**Cons**
- The model still needs *some* perception to write correct code — so this is a layer *on top of* 2.2, not a replacement. In practice: a11y snapshot for orientation, code for execution.
- Sandboxing is a real engineering cost, and arbitrary JS in the user's authenticated session is a serious blast radius. Needs a domain allowlist and a policy layer enforced *in code*, not in the prompt.
- Harder to render nicely in a chat UI (though showing the code it ran is arguably *better* UX — it's exactly what Claude Code does).

**Verdict.** Highest ceiling. Best combined with 2.2 rather than used alone.

---

### 2.4 — Site-Declared Tools: WebMCP — *the strategic bet*

**Mechanism.** The site registers typed JS functions as tools via `document.modelContext`; the agent discovers them (name + description + JSON Schema) and **calls them directly**. No pixels, no DOM, no clicking.

```js
navigator.modelContext.registerTool({
  name: "search_flights",
  description: "Search available flights",
  inputSchema: { type: "object", properties: { from: {...}, to: {...}, date: {...} } },
  execute: async ({from, to, date}) => ({ results: await api.search(from, to, date) })
});
```

**Status — this is live, not speculative.**
- Jan 2025: MCP-B prototype
- Aug 2025: Google + Microsoft joint proposal
- Sep 2025: accepted by the W3C Web Machine Learning Community Group
- Feb 2026: Chrome 146 Early Preview behind `chrome://flags` → "WebMCP"
- May 21 2026: origin trial announced at Google I/O
- Chrome 149: **enabled for real traffic**, not just flag-gated
- Partners experimenting: Expedia, Booking.com, Shopify, Credit Karma, TurboTax, Redfin, Etsy, Instacart, Target

**Pros:** near-perfect reliability where available; trivially cheap (no page representation in context at all); no prompt-injection surface from page text on the action path; the site *wants* you there, so no anti-bot war.

**Cons:** coverage is a rounding error today — a few dozen major sites out of the web. Useless as a primary strategy in 2026. But it's ~30 lines of code to support and it's the highest-leverage path you'll ever add per site.

**Verdict.** Not an architecture you build *on*. It's a **top rung on a fallback ladder** that costs almost nothing to add and gets more valuable every quarter. Detect `document.modelContext` on page load; if tools exist, use them; else fall through.

---

### 2.5 — Cross-cutting layer: Skill / Action Caching

Not a fifth architecture — a layer that multiplies any of the above, and one that basically every serious 2026 product now has.

**Idea:** the first time the agent does "log into Gmail and archive newsletters," it reasons the whole way. You record the successful trajectory — selectors, refs, or the generated code — keyed by `(instruction, domain, page fingerprint)`. Next time: **replay deterministically, zero LLM calls**, and only fall back to reasoning when a step fails validation.

- **Stagehand/Browserbase**: every `act()` is cached server-side with the resolved selector + metadata; cache key = instruction + page content + options; on hit, no inference and no token cost.
- **Anchor** frames it as procedural memory / skill caching — "replay deterministic clicks instead of full reasoning passes."
- Self-healing repair: when a cached selector misses, use semantic/visual context to re-ground and *update* the cache entry rather than dumping to a cold reasoning pass.

For a personal assistant that does the same 20 things over and over, this is where the felt speed comes from. Step 2 of a cached flow lands in 50ms instead of 4 seconds.

---

## 3. The shell: where the browser lives and where the chat panel goes

### Background: what CDP is, and what Chrome 136 changed

Chrome has a built-in remote-control interface — the **Chrome DevTools Protocol (CDP)**. Launch Chrome with `--remote-debugging-port=9222` and anything on the machine can connect to `localhost:9222` and fully drive that browser: click, type, read the page, screenshot, intercept network. Playwright, Puppeteer, and essentially every browser agent are built on it.

That port has **no authentication**. Anyone who reaches it inherits everything the browser has: session cookies, saved passwords, open tabs. Malware exploited exactly this — silently relaunch the victim's Chrome with the debug port on, connect, and walk off with every logged-in session. No password needed, no 2FA, because the sessions are already authenticated.

So **Chrome 136** added one rule: `--remote-debugging-port` and `--remote-debugging-pipe` are **ignored unless you also pass `--user-data-dir=<non-default folder>`**. The user-data-dir is where Chrome keeps cookies, passwords, history, and extensions; each folder gets its own encryption key. So a debug-enabled Chrome physically cannot decrypt the real profile's secrets. The flag fails **silently** — Chrome launches normally, the port just isn't listening.

**Consequence:** you cannot CDP-attach to the user's normal, already-running Chrome. Every "connect Playwright to port 9222" tutorial written before mid-2025 is wrong now. Anything launched with a custom `--user-data-dir` starts **logged out**.

### The four options

| Option | Real logins? | "In front of Chrome"? | Effort | Notes |
|---|---|---|---|---|
| **A. Dedicated profile + CDP** | ⚠️ log in once, then persists | ✅ via injected panel | **Low** | **Chosen for v1** |
| **B. MV3 extension + `chrome.debugger`** | ✅ user's real session | ✅ side panel or overlay | Medium | Later upgrade |
| **C. Electron with embedded Chromium** | ❌ own profile | ✅ full control | High | You're building a browser now |
| **D. Cloud browser (Browserbase/Steel)** | ❌ | ❌ | Low | Wrong shape for this product |

---

### v1 — Option A: dedicated profile + CDP + injected panel

```
chrome.exe --remote-debugging-port=9222 --user-data-dir=<project>/.chrome-profile
```

New window, full CDP, no extension, no MV3 service-worker lifecycle problems, no debugger infobar. Plain Node + a CDP client (`chrome-remote-interface` or Playwright's `connectOverCDP`) and you're driving.

**The profile folder persists.** Log into Gmail once in that window and the cookie stays for every future run. So it isn't "logged out forever" — it's "logged out once, then it's the agent's own browser." For a personal tool that's arguably *better* than sharing the main profile: the agent can't touch real cookies, and the folder can be deleted to reset everything. Add `.chrome-profile/` to `.gitignore` — it will contain live session cookies.

**The floating chat rectangle: inject it into the page over CDP.** No second app window. From the daemon:

- `Page.addScriptToEvaluateOnNewDocument` to register the panel bootstrap, so it mounts automatically on every navigation and every new tab — this is the important call; it survives navigation without manual re-injection.
- `Runtime.evaluate` to mount it immediately in the already-loaded page on first attach.
- Mount into a **Shadow DOM** root attached to `<body>`, `position: fixed`, high `z-index`. Shadow DOM isolates it from page CSS, which is what makes this robust on arbitrary sites.
- Talk to the daemon from the panel over a WebSocket to `localhost`, or via `Runtime.addBinding` (exposes a native function to page JS that fires a CDP event back to the daemon — cleaner, no extra server).
- Persist panel position and the conversation in the daemon, not the page; the page is wiped on every navigation.

**Known gaps of the injected approach** (all acceptable for v1): invisible on `chrome://` pages, the Chrome Web Store, and the built-in PDF viewer; a page can technically see the panel exists in the DOM; and heavy CSP sites occasionally interfere with inline styles (use a constructed stylesheet inside the shadow root).

**Sequence:** daemon launches Chrome → connects CDP → registers panel bootstrap → user types → daemon runs the agent loop, streaming actions to the panel and CDP commands to the page.

---

### Later — Option B: MV3 extension for the user's real session

The only clean path back into the user's actual logged-in Chrome, since it's permission-gated by the user installing the extension.

- `chrome.debugger` gives an MV3 extension **most of CDP** from inside the browser — `Input.dispatchMouseEvent` (real trusted events, not synthetic `.click()`), `Accessibility.getFullAXTree`, `Runtime.evaluate`, `Page.captureScreenshot`, `Network.*`. No Chromium fork, no `--remote-debugging-port`.
- **Cost of admission:** a persistent *"[Extension] started debugging this browser"* infobar whenever the debugger is attached, with no supported way to hide it. Every extension-based agent lives with it.
- **UI surface:** `chrome.sidePanel` is the documented pattern for "AI apps requiring continuous conversational context" — docked, survives navigation, no CSS conflicts. Or the same injected Shadow-DOM overlay as v1, this time from a content script. Pragmatic combination: real UI in the side panel, plus a small draggable status pill over the page showing the current action with a stop button.
- **Keep the LLM loop outside the extension.** An MV3 service worker is killed at ~30s idle, which would murder a 3-minute run mid-task. Either `nativeMessaging` → local daemon (what reverse-engineers found in Anthropic's own Claude Chrome extension) or **WebSocket → localhost daemon** (simpler, no host-manifest registration). Hold a long-lived port to keep the worker alive during a run, and keep the API key in the daemon, never the extension bundle.
- **Framework:** WXT is the 2026 default for extension dev (Vite-based, HMR, MV3 out of the box), largely displacing Plasmo/CRXJS.

**Migration note:** if the daemon owns the agent loop from day one and the browser side is a thin CDP transport, moving from A to B is swapping that transport — the loop, prompts, and UI code carry over unchanged. Build it that way.

### Conversation model (the Claude-Code feel)

- **One append-only message thread** per context, exactly like Claude Code: user turn → assistant reasoning → tool calls rendered as collapsible cards ("Clicked *Search*", "Read 12 products", "Ran script") → assistant text → next user turn. The browser is *shared mutable state* across the whole thread, which is the point: "now filter to under $50" works because turn 3 inherits the page state from turn 2.
- **The ↻ button** should clear the *message history* while explicitly leaving the browser where it is (and say so in the empty state: "New chat — still on amazon.com/cart"). Also offer "new chat + new tab." Conflating the two is the mistake.
- **Streaming matters more than raw speed.** Show the action *before* executing it ("→ clicking *Sign in*"), then the result. Perceived latency collapses. Bulk actions (§2.2, item 5) let you stream 4 actions from one round trip.
- **Interruptibility:** the user must be able to type mid-run and have it queue as the next instruction, and a hard stop button that aborts the CDP command in flight.
- **Steal the transcript idioms from Claude Code:** collapsed tool cards that expand on click, a token/cost counter, and — since you'll have code-as-action — show the actual JS it ran. Users trust it more when they can see it.

---

## 4. Security — non-optional for this product shape

You're building an agent that runs inside the user's authenticated session on arbitrary websites. That is the maximum-blast-radius configuration.

**Prompt injection is unsolved and will stay unsolved.** Researchers confirmed it cannot be fully patched in Atlas, Comet, or Dia; OpenAI's stated position is to *design for permanent risk rather than pursue elimination*. Anthropic got Claude for Chrome from a **23.6% → 11.2%** injection success rate with heavy mitigation — that's a 9× improvement that still leaves 1-in-9 attacks landing. Brave and Guardio published working injection + phishing exploits against Comet.

**The consensus defense is deterministic, not probabilistic.** From arXiv 2511.19477: *"security must be enforced through deterministic, programmatic constraints instead of probabilistic reasoning."* Concretely:

1. **Domain allowlist per task.** The agent declares which origins it needs before starting; CDP-level network policy blocks everything else. A task on amazon.com cannot navigate to attacker.com.
2. **Action-class blocking in code.** Never let the model click elements whose accessible name matches a danger list (`delete`, `refund`, `transfer`, `confirm payment`, `deactivate`) without an explicit human confirmation step in the chat UI. This is a string match in your executor, not an instruction in the prompt.
3. **Untrusted-content framing.** Page text, PDFs, emails are *data*, never instructions. Wrap every snapshot in explicit delimiters and state that content inside cannot issue commands. This helps; it does not solve.
4. **Sensitive-origin pause.** Banks, payment flows, email settings, OAuth consent screens → stop and ask. Atlas ships exactly this.
5. **Never auto-type credentials.** Confirm before entering anything that looks like a secret.
6. **Exfiltration is the real threat model,** not vandalism. The classic chain is: injected text on page A tells the agent to read data from authenticated page B and encode it into a URL on attacker-controlled domain C. Rule 1 breaks this chain; nothing in the prompt does.
7. Dia's lesson is worth internalizing: they **removed** the web-fetch tool entirely rather than ship it with detection-based mitigations, and only reintroduced it two months later with architectural controls that structurally prevent the bad output regardless of what the LLM is told.

---

## 5. Recommendation

**Build a ladder, not a single architecture.** Per step, take the highest rung available:

```
1. WebMCP tools present?          → call the tool                    (§2.4)
2. Task is extract/loop/compare?  → write and run code               (§2.3)
3. Cached skill matches?          → replay deterministically          (§2.5)
4. Default:                       → a11y snapshot + refs             (§2.2)
5. Element missing/canvas/dense?  → screenshot + SoM + crop-refine   (§2.1)
```

**Concrete stack for your product:**

- **Shell (v1):** Node daemon launches its own Chrome with `--remote-debugging-port=9222 --user-data-dir=<project>/.chrome-profile`, connects over CDP, and injects the chat panel into the page as a Shadow-DOM overlay via `Page.addScriptToEvaluateOnNewDocument`. No extension. Log in once per site; the profile persists. Upgrade path to an MV3 extension later for the user's real session (§3).
- **Transport:** panel ↔ daemon over `Runtime.addBinding` or a localhost WebSocket. **The daemon owns conversation state, the API key, and the agent loop** — the browser side is a thin CDP transport, which is what makes the later extension swap cheap.
- **Perception:** `Accessibility.getFullAXTree` over CDP, **augmented** with a DOM pass that synthesizes refs for click-handler/`tabindex`/`cursor:pointer` elements ARIA misses — this is what protects you from the 78%→42% cliff. Version every ref. Retain one snapshot. Trim with Luna.
- **Action:** ref-based tools + `run_js` as a first-class action, batched.
- **Models:** Terra as the driver, Luna for snapshot trimming and extraction, escalate to Sol after two consecutive failed steps on the same subgoal. Stable system-prompt prefix so prompt caching does its ~89% work.
- **Vision:** a `screenshot` tool the model calls, not a per-turn push. Add crop-and-refine only when you actually hit grounding failures.
- **Security:** per-task domain allowlist + danger-word click interception + confirm-before-sensitive from day one. Retrofitting this is much worse than building it in.

**Build order:** ladder rung 4 first (a11y + refs + versioning + trimming) — that alone gets you most of the way. Then rung 2 (`run_js`), which is the biggest single quality jump. Then 5 (vision fallback), then 3 (caching, once you know which flows repeat), then 1 (WebMCP, ~30 lines, do it when bored).

**What to spend your effort on:** context management and error recovery, not prompt engineering. The 42% → 97% jump in 2026 came from harness design. Instrument every failure — which rung was active, what the model saw, what it tried — because the top system got there by grinding "hundreds of edge-case fixes" against a real eval set, not by finding a clever prompt.

---

## Sources

- [GPT-5.6 — OpenAI](https://openai.com/index/gpt-5-6/) · [Models list](https://developers.openai.com/api/docs/models) · [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [Computer use guide — OpenAI API](https://developers.openai.com/api/docs/guides/tools-computer-use) · [Equipping the Responses API with a computer environment](https://openai.com/index/equip-responses-api-computer-environment/)
- [Building Browser Agents: Architecture, Security, and Practical Solutions (arXiv 2511.19477)](https://arxiv.org/html/2511.19477v1)
- [The Three Architectures of Browser Agents — DEV](https://dev.to/alexey_sokolov_10deecd763/runtime-snapshots-16-the-three-architectures-of-browser-agents-4gkc)
- [How we built the best browser agent with Auto-Research — Browser Use](https://browser-use.com/posts/online-mind2web-benchmark) · [Online-Mind2Web Leaderboard — Steel.dev](https://leaderboard.steel.dev/leaderboards/online-mind2web/)
- [Fara-1.5: Scalable Learning Environments for Computer Use Agents (arXiv 2606.20785)](https://arxiv.org/html/2606.20785) · [microsoft/fara](https://github.com/microsoft/fara) · [UI-TARS](https://github.com/bytedance/ui-tars)
- [Executable Code Actions Elicit Better LLM Agents (CodeAct, arXiv 2402.01030)](https://arxiv.org/html/2402.01030v4)
- [The Accessibility Tree Is How AI Agents Read Your Site — Search Engine Journal](https://www.searchenginejournal.com/the-accessibility-tree-is-how-ai-agents-read-your-site-its-breaking/578171/)
- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) · [browser-use/browser-use](https://github.com/browser-use/browser-use) · [browserbase/stagehand](https://github.com/browserbase/stagehand)
- [Stagehand caching — Browserbase](https://www.browserbase.com/blog/stagehand-caching) · [Browser Agents Have an Amnesia Problem — Anchor](https://anchorbrowser.io/blog/browser-agent-amnesia-skill-caching-anchor)
- [WebMCP: Google's browser standard — Developers Digest](https://www.developersdigest.tech/blog/webmcp-google-browser-agent-standard-2026) · [The State of WebMCP: July 2026 — Spronta](https://www.spronta.com/blog/state-of-webmcp-july-2026/) · [What is WebMCP — Zuplo](https://zuplo.com/blog/what-is-webmcp)
- [Changes to remote debugging switches — Chrome for Developers](https://developer.chrome.com/blog/remote-debugging-port) · [What's new in web extensions: I/O 2026](https://developer.chrome.com/blog/extensions-io-2026)
- [CDP from Extensions: You Don't Need to Fork Chromium — Medium](https://medium.com/@dzianisv/vibe-engineering-chrome-devtools-protocol-from-extensions-you-dont-need-to-fork-chromium-72a9ffb68b6d) · [Reversing the Claude AI Agent Chrome Extension — CHEQ](https://cheq.ai/blog/the-cyborg-session-reversing-detecting-claude-ai-agent-chrome-extension/)
- [The Agentic Browser Landscape in 2026 — No Hacks](https://nohacks.co/blog/agentic-browser-landscape-2026) · [ceLLMate: Sandboxing Browser AI Agents (arXiv 2512.12594)](https://arxiv.org/pdf/2512.12594)

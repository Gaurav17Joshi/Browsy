# Plan

A general-purpose browser agent: a floating chat panel over Chrome, continuous conversation like Claude Code, ↻ for a fresh context.

**Design stance:** all three interaction modes (a11y refs / code / vision) are exposed as ordinary tools, and the model picks per step. No mode gets deeply tuned up front — breadth first, then optimize whichever one the failures actually point at.

Background and sources: [RESEARCH-browser-agent-architectures.md](RESEARCH-browser-agent-architectures.md) · [SUMMARY.md](SUMMARY.md)

---

## 0. Stack and harness

**Language: Python 3.12.** *(Changed during the build: Node is not installed on this machine and Python 3.12 is. The reason for TypeScript was "one language everywhere", but the panel and `run_js` payloads are JS strings whatever the daemon is written in, so the benefit was mostly cosmetic. The Python Agents SDK is also the more exercised of the two.) Deps live in `.venv/`; the global env was left as it was found.*

**Harness: OpenAI Agents SDK (TypeScript). Decided.** `@openai/agents`. The agent loop, tool dispatch, sessions, streaming, approvals, and tracing come from the SDK; we do not hand-roll them.

Two reasons it beat the alternatives, both specific rather than general framework-preference:
- **`computerTool` + the `AsyncComputer` interface is exactly the vision-path contract** — screenshot in, actions out, backend supplied by us. The SDK ships no browser on purpose, so we implement that interface over CDP, which was already the plan.
- **vLLM serves an OpenAI-compatible API**, so the Fara1.5 step in §7 becomes a base-URL change instead of a rewrite.

Runners-up, for the record: **Claude Agent SDK** is the stronger harness for environment-controlling agents (better auto-compaction, native bash/file tools, `max_budget_usd`) and would be the pick on Claude models — but it's Claude-shaped and complicates §7. **Vercel AI SDK** is the fallback if we ever want provider-agnosticism with less scaffolding. **Hermes Agent** (Nous) and **OpenCode** are finished products, not build-on-it libraries — wrong shape, though Hermes's skill distillation is worth stealing for caching later. **LangGraph / CrewAI / Mastra** solve multi-agent orchestration problems we don't have.

**What the SDK gives us, mapped to this plan:**

| Plan item | Covered by |
|---|---|
| Vision path (§2 `screenshot`) | `computerTool` + our `AsyncComputer` implementation over CDP — the interface is exactly this contract |
| `snapshot` / `click` / `run_js` | function tools |
| Continuous context, ↻ = fresh | Sessions |
| Streaming into the panel | streamed run events |
| Confirm-before-irreversible (§5) | tool approval / guardrails |
| "Instrument failures" (§6) | built-in tracing |
| Local Fara later (§7) | vLLM serves an OpenAI-compatible API — **a base-URL swap, not a rewrite** |

That last row is the clincher: it keeps the GPU-pod plan a config change.

**What stays ours** — and this is the boundary that matters: Chrome, CDP, the injected panel, the a11y snapshot, ref versioning, and snapshot trimming. Generic compaction summarizes text; it doesn't know that snapshot N−1 must be dropped *verbatim* while the ref map stays valid. The SDK runs **inside** the daemon and can be swapped out without touching the browser layer. If it ever fights us, the fallback is a ~200-line loop — most production agents are hand-written loops or thin wrappers, so that's a real escape hatch, not a threat.

**Libraries:** `playwright-core`'s `connectOverCDP` (or `chrome-remote-interface`) for the browser; `ws` for panel transport; Preact or vanilla DOM inside the Shadow root.

**Worth reading, not adopting:** the [browser-use](https://github.com/browser-use/browser-use) DOM extraction code. Sweeping the DOM for clickables that ARIA missed is the fiddliest part of §2 and there's no reason to derive it from scratch.

---

## 1. Architecture

Three processes, one boundary that matters.

```
┌─ Node daemon ────────────────────────┐
│  OpenAI Agents SDK  (loop, sessions, │      CDP over WebSocket
│    streaming, approvals, tracing)    │ ◄──────────────────────► Chrome
│  ── our layer ──                     │                          (own profile)
│  CDP client · snapshot + refs        │                               │
│  API key · security policy           │                               │
└──────────────────────────────────────┘                               │
                    ▲                                                  │
                    │  Runtime.addBinding / CDP events                  │ injected
                    └──────────────────────────────────────────── chat panel
                                                                  (Shadow DOM)
```

**Daemon (Node).** Hosts the Agents SDK run and everything stateful around it: the CDP client, the snapshot/ref layer, the API key, the domain allowlist. Launches Chrome, drives the page, streams run events to the panel.

**Chrome.** Launched by the daemon with its own profile:

```
chrome --remote-debugging-port=9222 --user-data-dir=./.chrome-profile
```

Required since Chrome 136 — the debug port is ignored on the default profile. The folder persists, so you log into Gmail etc. once and it stays. Gitignore it; it holds live session cookies.

**Chat panel.** Injected into the page rather than being a second app window, so it genuinely floats over the site. Registered with `Page.addScriptToEvaluateOnNewDocument` so it auto-mounts on every navigation and every new tab. Mounted into a Shadow DOM root (`position: fixed`, high z-index) so page CSS can't touch it. It is a **pure renderer** — all state lives in the daemon, because the page is destroyed on every navigation.

**The one architectural rule:** the browser side stays a thin CDP transport. The loop, prompts, and UI never talk to Chrome directly. That's what makes the later swap to an MV3 extension — the only way to reach your *real* logged-in Chrome — a transport change rather than a rewrite.

---

## 2. Tools exposed to the model

| Tool | Implementation | When the model should use it |
|---|---|---|
| `snapshot()` | `Accessibility.getFullAXTree` + a DOM sweep that synthesizes refs for click-handler / `tabindex` / `cursor:pointer` elements ARIA missed | Default way to see the page |
| `click(ref)` `fill(ref, text)` `select(ref, value)` | ref → `backendDOMNodeId` → `DOM.scrollIntoViewIfNeeded` → `getContentQuads` → `Input.dispatchMouseEvent` | Default way to act |
| `run_js(code)` | `Runtime.evaluate`, returns JSON | Extract, loop, compare, bulk edits — anything that would otherwise be 20 clicks |
| `screenshot()` | `Page.captureScreenshot` | Canvas apps, dense grids, "the tree doesn't show what I need" |
| `navigate(url)` `key(combo)` `scroll(dir)` `wait(ms)` `tabs()` | direct CDP | — |

Notes:
- **Real input events, not `element.click()`.** Synthetic clicks have `isTrusted: false`, skip hover/mousedown, and break on a meaningful fraction of sites.
- **Screenshot is model-called, never pushed every turn.** That's the difference between ~1200 tokens/step and ~100.
- **Retina trap:** screenshots come back at 2× on a Mac. Divide vision coordinates by the device pixel ratio before dispatching, or every click lands in the top-left quadrant.
- **Batch actions** — let the model emit several in one turn. Big latency win on forms.
- Keyboard is often the best tool in canvas apps (Sheets, Figma) since it needs no coordinates. Say so in the system prompt.

---

## 3. The agent loop

The SDK runs the loop. What we add on top is the context discipline it can't know about — a generic compactor summarizes text, but it has no idea that snapshot N−1 must be dropped *verbatim* while its ref map stays valid.

Each step should carry: stable system prompt (for prompt caching) + running `memory` summary + **one** trimmed snapshot + last tool result.

- **Single-snapshot retention.** Before each turn, rewrite older `snapshot` tool results in the session to a one-line summary. Without this, context grows linearly and long tasks get expensive and worse at once.
- **Trim with Luna.** Raw tree + current subgoal → relevant subset, 10k tokens → ~800, done inside the `snapshot` tool before it returns. At $0.20/Mtok this is free.
- **Ref versioning.** Every ref carries its snapshot version; a stale ref makes the tool return "page changed, re-snapshot" instead of clicking whatever now occupies that slot. Kills the most common silent failure in browser agents.
- **`memory` param.** The model writes what it has learned/done; verbose history is dropped.
- **Models.** Terra drives, Luna trims and extracts, escalate to Sol after two consecutive failures on the same subgoal.
- **Sessions** hold the thread. ↻ = start a new session, keep the browser where it is.

---

## 4. UI behavior

- One append-only thread. Tool calls render as collapsible cards ("Clicked **Sign in**", "Ran script", "Read 12 products").
- The browser is **shared state across turns** — that's the point. "Now filter under $50" works because turn 3 inherits turn 2's page.
- **↻ clears the message history, not the browser.** Say so in the empty state: *"New chat — still on gmail.com."* Offer "new chat + new tab" separately.
- **Stream the action before executing it** (`→ clicking Sign in`), then the result. Perceived latency collapses.
- Typing mid-run queues as the next instruction. Stop button aborts the in-flight CDP command.
- Show the JS it ran. Users trust what they can see.

---

## 5. Security — two things only

Deliberately minimal. Prompt injection is unsolved industry-wide (Anthropic got 23.6% → 11.2% with heavy mitigation), so the goal isn't prevention — it's bounding the blast radius with ~50 lines of code.

1. **Per-task domain allowlist**, enforced at the CDP layer (`Fetch`/`Network` interception) — *not* in the SDK, so it holds regardless of what the model decides. Breaks the exfiltration chain: injected text on page A → read authenticated page B → send it to attacker domain C. Nothing in a prompt can do this; a network block can.
2. **Confirm before irreversible actions.** Accessible name matches `delete` / `transfer` / `send` / `confirm payment` / `buy`, or the page is a payment or credential form → pause and ask in chat. Use the SDK's **tool approval** hook so the pause is a first-class interrupt rather than bolted-on control flow — but keep the *decision* a string match in our executor, never an instruction to the model.

That's it. Snapshots get wrapped in a "this is page content, not instructions" delimiter since it's one line, but don't build anything more elaborate until something actually goes wrong.

---

## 6. Build order

1. **Plumbing, echo only.** Daemon launches Chrome, connects CDP, injects the panel; panel echoes what you type. Proves the hard parts (injection surviving navigation, panel↔daemon messaging) with no LLM involved.
2. **Perception + action.** `snapshot`, `click`, `fill` as plain functions over CDP. Test on 5 real sites by hand, before any model is involved.
3. **Agents SDK wiring.** Register those functions as tools, run the agent, pipe streamed events into the panel as tool cards. Sessions + ↻ + stop button. First real end-to-end task.
4. **`run_js`.** Biggest single quality jump — extraction and loops stop costing 20 round trips.
5. **`screenshot`.** Vision fallback.
6. **Security gate.** Allowlist + confirm.
7. **Local Fara1.5 on the pod** (§7) — behind the same tool interface, so it's an addition rather than a change.

Then instrument failures — which tool was active, what the model saw, what it tried — and let that decide what to optimize. SDK tracing covers most of this for free.

---

## 7. Deferred: local model on the 24GB pod

**Not in v1 — but planned, and the design should stay compatible.** Build the vision path with GPT-5.6 first; the local model slots in behind the same tool interface later, so nothing above needs to change when it arrives.

**Fara1.5** (Microsoft, Jul 2026) is the obvious candidate — **MIT licensed**, fine-tuned from Qwen3.5, and a *native* computer-use agent: screenshot in, coordinate actions out. Deliberately no DOM or a11y access. Trained at 1440×900, 262k context.

| | Online-Mind2Web | WebVoyager | bf16 weights | Fit on 24GB |
|---|---|---|---|---|
| Fara1.5-4B | 57.3 | 80.8 | ~8 GB | comfortable |
| **Fara1.5-9B** | **63.4** | **86.6** | ~18 GB | **tight in bf16, easy at FP8/AWQ (~10 GB)** |
| Fara1.5-27B | 72.3 | 89.3 | ~54 GB | no — MS says shard over ≥2 GPUs |

Serve with vLLM (≥0.19.1) or SGLang, both OpenAI-compatible so it drops into the existing client. **Fara1.5-9B at FP8 is the pick** — leaves ~14GB for KV cache and image tokens. Skip the 27B: 4-bit would fit but coordinate regression is exactly what quantization degrades, and precision is the whole point here.

**Where it plugs in — best use first:**

1. **Grounding specialist.** Add a `locate(description) → (x, y)` tool. GPT-5.6 plans and reads the a11y tree as usual; when it hits a canvas or dense grid, it asks the local model *where* the thing is. This directly patches the documented weak spot of the vision path, costs nothing per call, and may well be **faster than a frontier API round trip**. Low risk, bounded scope.
2. **Full local driver — the 4th mode.** Whole loop runs on the pod. ~63% vs a frontier harness is a real quality drop, but marginal cost is zero and nothing leaves the machine. Worth having as a mode the planner can pick for simple or sensitive tasks.
3. **Not worth it:** replacing Luna as the snapshot trimmer. At $0.20/Mtok the API is nearly free and the network round trip is comparable to local inference.

**Setup trap:** Fara was trained at 1440×900, so pin the Chrome viewport to exactly that and feed it **CSS-pixel screenshots, not 2× Retina ones**. Its coordinates come back in that same space.

Alternative family if Fara disappoints: ByteDance **UI-TARS**. Fara1.5 currently benchmarks better and the MIT license is cleaner.

---

## 8. What the build actually taught us

Built and running — see [README.md](README.md). Five things contradicted or
sharpened the plan above, all found by measurement rather than reasoning.

**1. Hidden pages silently swallow input.** Chrome drops synthesized input events
for a page whose `visibilityState` is `hidden`, which is what a window launched
by a background process is. `Input.dispatchMouseEvent` returns success at the
protocol level and the renderer never sees the event — so every click was a
silent no-op while the log said "clicked". `Page.bringToFront` does **not** fix
it; `Emulation.setFocusEmulationEnabled` does. This one bug was invisible because
the agent kept routing around it with `run_js` and still produced right answers.
Worth remembering: an agent smart enough to work around your bug will hide it.

**2. Trimming context can cost more than it saves.** §3 assumed single-snapshot
retention is straightforwardly good. But we send the whole item list each call,
so the prefix is byte-stable and **~87% of input tokens served from cache at 1/10
the price**. Placeholdering an older snapshot rewrites the middle of that prefix
and invalidates the cache from there on. On a short task that is a net loss.
Resolution: shrink each snapshot always (cheap, cache-safe), and only start
placeholdering past a 60k-char threshold, where unbounded growth is the worse
bill. Measured 86–93% cache hit rates in real runs.

**3. Reply on the session the event came from.** A link opened a new tab; `Fetch`
interception was still enabled on the old session while our handler answered on
the *current* one. Paused navigations were never continued, and the tab wedged —
turning a 25s task into 480s of 45-second timeouts. Related: don't enable
interception at all in permissive mode.

**4. Native `<select>` is unclickable, by construction.** The option popup is
drawn by the OS, so no synthesized click can reach it. Cost a full 40-turn budget
before we added `select_option`, which sets `.value` and fires `input`/`change`,
and returns the real option list on a miss so the model stops guessing.

**5. Failed runs still cost money.** Usage was read after the stream completed,
so an exception meant the cost log recorded $0.00 for a run that burned 40 tool
calls. Accounting now happens in a `finally`, and running out of turns produces a
partial answer from what was seen instead of throwing the run away.

Deferred from the plan and still deferred: SDK-native tool approvals (the confirm
gate is enforced inside the tool instead — same property, less machinery).

### Round two — from the 11-task eval suite

Full numbers in [evals/ANALYSIS.md](evals/ANALYSIS.md). 11/11 passed, $0.51.

**6. A native `alert()` freezes the renderer.** Every `Runtime.evaluate` times
out and the tab looks dead. Nothing in the page can dismiss it — script
execution is precisely what's blocked; only the protocol can. It bit us *at the
moment tasks succeeded*, because sites announce success in popups. Dialogs are
now auto-dismissed and their text handed to the agent as an observation.

**7. Silently-dropped input is worse than an error.** `"Right"` wasn't in the key
map (only `"ArrowRight"`), so `press_sequence` sent **zero keys** and the agent
replanned three times against a board that had never moved. Unknown keys are now
a named failure, and a sequence is validated whole before any of it is sent.

**8. Cost tracks tool count, near-linearly** — `$0.012 + $0.0072 × calls`,
r² ≈ 0.93. So the lever is doing more per call, not thinking less. Batching 48
key presses into one `press_sequence` cut that task 3× in cost and time.

**9. Prompt wording is a cost lever.** `web_search` existed but went unused for
11 runs because the instruction didn't make it the obvious opener. One rewritten
paragraph took a task from 11 tool calls to 3.

**10. Weak eval checks flatter you.** The first Sokoban run passed a check
containing the word `level` while having solved nothing. Checks are now strict
and corroborated, and the final solve was verified independently by replaying
the move list (Level 1 → Level 2).

### Round three — panel state, and a $0.58 lesson

**11. Assigning to `className` on an SVG element silently does nothing.** It is a
read-only `SVGAnimatedString`. The mascot was therefore pinned to `idle`
forever — it looked like "the animation only blinks", because idle *is* the
blink. `setAttribute('class', …)` fixes it, and every other state (think / work /
done / error) then appeared for the first time.

**12. Orphaned on-new-document scripts win the race.** Re-registering the panel
script without *removing* the previous one leaves both registered. Chrome runs
them in registration order and the script self-guards with a mounted flag, so
the **oldest** copy runs first, paints its stale seed, and makes the current one
return early. Symptom: after a navigation the conversation came back missing the
user's own question while the tool cards survived — the daemon's transcript was
perfectly intact the whole time.

**13. `window` does not survive navigation.** The live transcript kept on
`window` covers in-page re-mounts (SPA guards) but not real page loads; the
injected seed covers page loads but is frozen at document-start. Both are needed,
plus an authoritative `restore` that no-ops when it already matches.

**14. Big tool results are charged for repeatedly — measured, then deliberately
left alone.** A session that reverse-engineered a puzzle game (fetching its JS
bundle, decoding an embedded board, solving it in-page) cost **$0.81**, mostly
re-sending blobs it had already extracted the answer from. Capping `run_js`
output at 3k and extending stale-trimming to any bulky tool result took the same
task to **$0.2335, a 71 % cut**, with the same answer.

Both were **reverted on purpose**. They constrain how the agent is allowed to
explore, and that freedom is worth more right now than the token saving — an
agent that can pull down a bundle and read it is an agent that can solve things
it was not designed for. Revisit when cost actually bites; the change is small
and the numbers above are the expected payoff.

### Round four — the panel bugs had one shape, and a test suite now holds them

**15. An overlay that isolates itself must listen in the *capture* phase.** The
panel stops mouse and keyboard events at its shadow host so the page underneath
never sees them (that is what stopped Space reaching a YouTube player). The drag
handler then registered `mousemove`/`mouseup` on `window` in the bubble phase --
where its own isolation swallowed them. Releasing the button over the panel
therefore never ended the drag: the panel followed the cursor around with no
button held, and because it kept moving there was no way to click into the input
box. Capture-phase runs on the way *down*, before the host, so it always fires.
The same rule already applied, by luck, to the virtual cursor.

Two guards on top, because a lost mouseup should never be able to strand the UI
again: any move with no button held ends the drag, and so does losing focus.

**16. Clamping "a corner of the header" is not clamping.** Position and size are
remembered across pages and runs, so they routinely describe a window that no
longer exists. Keeping the top-left corner on screen sounds sufficient and is
not -- the panel hangs off the edge with its *input box* outside the viewport,
visible and untypeable. Clamp the whole rectangle, on mount and on resize, and
refuse to drag it off the edge at all.

**17. A boolean "already mounted" guard hands the page to a dead process.** We
do not kill Chrome when the daemon stops, and the next run reuses it -- along
with the panel script the old daemon registered, which still runs first on every
document. With a boolean guard that stale copy won outright, so the panel a user
sees could be code from a process that no longer exists, including bugs already
fixed. Every injected script now carries a BUILD stamp: newer replaces older,
same build is a no-op.

**18. A bounded websocket receive queue can kill a healthy CDP connection.**
Everything -- panel, cursor, every click -- rides one socket. Under a flood of
events the library stops reading it to apply backpressure; pongs arrive on that
same socket, so the keepalive then times out and closes a connection nobody had
a problem with. Reproduced deterministically: with the library defaults it dies
with `keepalive ping timeout`, with `max_queue=None, ping_timeout=None` it
survives the identical flood. Two related fixes: `Network.enable` was on with
nothing consuming its events (thousands a minute on a media page, pure load),
and a *clean* close ended the read loop with no exception at all -- so the one
case where Chrome told us politely was the one case we never reconnected from.
The socket now reconnects with backoff and re-injects everything target-bound.

**19. "Log loudly when the injected script breaks" only covered SyntaxError.**
An unsubstituted `__CUAEXP_BUILD__` placeholder is valid syntax and throws a
*ReferenceError* at run time, which was logged at debug -- so the virtual cursor
was dead on every page and the log said nothing. Any JS-level error from an
injected script is now an ERROR.

**20. None of these are visible by reading the code**, which is why
`tests/panel_check.py` exists: it drives the real panel in a real Chrome over
CDP -- presses, drags, releases, types, scrolls, navigates, drops the socket --
and asserts what the page and the panel each ended up with. 54 checks, about two
minutes. It caught finding 16 on its own, and caught a broken cursor I had just
introduced. Run it after touching anything in `panel.py`, `cursor.py` or
`cdp.py`.

### Round five - what running a real benchmark found that the tests could not

The panel suite passed 54/54 while all three of these were live. They only
showed up once an *agent* was driving, on *real* sites, for minutes at a time.

**21. Re-registering the injected script removed the old one first.** Every
streamed tool call updates the seed, which re-registers the panel script:
remove, then add. That leaves a window with no panel registered on that target
at all -- and if the add then fails or times out (short timeout, mid-navigation,
which is exactly when this fires) the window never closes and the panel is gone
from every page that loads afterwards. Caught by watching a real run: the panel
was missing from 75 of 118 samples on booking.com/kayak, and on flightaware.com
`window.__cuaexpBuild` was `null` while the cursor script -- registered once and
never replaced -- was still working on the very same page. Add first, remove
second, and never drop the old id unless the new one is confirmed.

**22. A page that steals focus eats what you type.** Sites move focus to their
own search box after load; ad frames and modals do it repeatedly. Against a
fixture that grabs focus every 300ms, *all eleven* keystrokes typed into the
chat landed on the page and the chat box stayed empty -- silent loss, and on a
site with single-key shortcuts, silent actions. The panel now takes focus back,
but only within four seconds of the user actually using the box and only a
bounded number of times, and a click anywhere outside the panel ends the claim
immediately -- otherwise this becomes a focus fight with the page.

**23. Every one of those was found by watching, not by testing.** So the daemon
now runs a two-second watchdog: if the panel or the cursor is missing from the
current page, put it back. Each root cause so far (a failed registration, a tab
switch, a dropped socket, a page wiping the DOM) got its own fix, and each was
found only because a user noticed. The watchdog is the general answer: the next
unknown cause costs a two-second flicker instead of a session.

**25. A modal focus trap beats any overlay in the same document.** budget.com's
promo modal enforces focus on every change, the way Bootstrap's does. Reclaiming
focus just started a fight we lost, and every character typed into the chat went
into the modal -- a person would lose their typing there in exactly the same way.
Two fixes, and both are needed: focus events no longer escape the panel, so a
trap listening on `document` never learns focus left; and when the chat box is in
use but something else holds focus, keystrokes are taken at window-capture and
put into the box before the page can see them. That second one is the general
guarantee -- whatever holds DOM focus, what you type in the chat reaches the chat.

**26. The benchmark bill was one task.** Ten OSWorld tasks came to $4.78, and
$3.68 of that was a single task: 6.1M input tokens, context peaking at 199k, 26
of its 40 calls `run_js` on a page that returns large blobs. Every one of those
results is re-sent on every later request. This is finding 14's trade-off with a
much bigger number attached, on a task that failed anyway. The revert still
stands -- nothing has been re-applied -- but the price of that freedom is now
measured rather than estimated.

**24. The agent had no clock.** Nothing in the prompt said what day it was, so
"a car from next Monday to Friday" was unanswerable -- the model picks a date
from its training data and searches the wrong week, confidently. Four of the ten
benchmark tasks are relative-dated. One line in the prompt.

**27. A build stamp has to guard the moment that decides, not the moment you
wrote it.** Injected scripts stamp a build number so a newer panel replaces one
left behind by a dead daemon (finding 17). It checked at *script evaluation* --
but both copies evaluate at document-start, before there is a body to mount into,
so the mount happens later and there the first one to arrive simply won. Result:
after any change to the panel, a Chrome reused from the previous run kept showing
the OLD panel, with its old saved state and none of the fixes, until Chrome was
restarted. The host element now carries the build number and a newer script
replaces an older host wherever it finds one. The test was checking
`window.__cuaexpBuild` -- set by the newest script that ran -- rather than the
build of the panel actually on screen, which is why it passed throughout.

## Out of scope for now

Skill/action caching · WebMCP · crop-and-refine grounding · MV3 extension for the real profile · cloud browsers · multi-tab parallelism.

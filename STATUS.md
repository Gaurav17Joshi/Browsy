# Browsy — status and architecture

_As of 18 August 2026. Written to be the one page you read before touching
anything. Design rationale lives in [PLAN.md](PLAN.md), usage in
[README.md](README.md); this is what exists, how it fits, and where it stands._

---

## 1. What it is

A browser-use agent that drives **real Chrome over CDP**, with its own chat panel
injected into whatever page is open. You talk to it in that panel; it looks at
the page, clicks, types, runs JavaScript, and answers. One conversation, one
browser, kept across navigations and tabs.

Three ways of interacting with a page are exposed as ordinary tools and the model
picks per step — no mode switch, no router:

| mode | tools | when the model reaches for it |
|---|---|---|
| **a11y refs** | `snapshot` → `click` / `fill` / `press` / `select_option` | default; cheap, semantic, stable |
| **code** | `run_js` | extract, loop, compare, collect — one script beats twenty clicks |
| **vision** | `screenshot` + `click_at` | canvas, dense grids, "what does this look like" |

Everything runs on **GPT-5.6 Terra** through the **OpenAI Agents SDK** — the SDK
owns the agent loop, tool dispatch, streaming and tracing; we own perception,
actions, context discipline and the security policy.

---

## 2. Shape of the system

```
      you                                    Chrome (own profile, port 9222)
       |                                     +-----------------------------+
       v                                     |  page                       |
 +-----------+   Runtime.addBinding msgs     |   +---------------------+   |
 |  panel.py |<--------------------------->  |   | shadow-DOM chat UI  |   |
 |  (JS in   |   Runtime.evaluate pushes     |   | + virtual cursor    |   |
 |   page)   |                               |   +---------------------+   |
 +-----------+                               +-----------------------------+
       ^                                                    ^
       |                                                    | CDP (one websocket,
 +-----------+     +-----------+     +-----------+          | flattened sessions)
 | daemon.py | --> | agent.py  | --> | tools.py  | ---------+
 |  queue,   |     | SDK loop, |     | 14 tools  |
 |  transcript     | trimming  |     +-----------+
 |  watchdog |     +-----------+           |
 +-----------+           |                 v
       |                 |          +--------------+   +-------------+
       |                 |          | session.py   |-->| snapshot.py |  a11y tree
       |                 |          | policy, tabs |   | actions.py  |  + DOM sweep
       |                 |          +--------------+   | cursor.py   |  bezier mouse
       v                 v                             +-------------+
  logs/<run>/       history/queries.jsonl
```

**The panel is a pure renderer.** All state lives in the daemon; the panel paints
what it is told and sends back what you type. That is why it can be destroyed and
re-mounted at any moment without losing anything.

---

## 3. How it actually works

### Starting up

`daemon.py` launches Chrome with `--remote-debugging-port` against a **dedicated
profile directory** — mandatory since Chrome 136, which silently ignores the debug
port on the default profile. It then opens one websocket to the *browser*, not to
a tab, and attaches to a page target with `flatten: true`, so that single
connection carries every tab through session ids rather than needing a socket
each.

On attach it enables `Page`, `DOM` and `Runtime` (not `Network` — nothing
consumes those events and a media-heavy page emits thousands a minute), turns on
**focus emulation**, and primes the accessibility tree. Focus emulation matters
more than it sounds: Chrome silently *drops* synthesized input for a page whose
`visibilityState` is hidden, so without it every click succeeds at the protocol
level while the renderer never sees a thing.

Then three things get injected into the page and re-injected on every navigation
and every new tab: the **allowlist gate** (only in restricted mode), the **virtual
cursor**, and the **chat panel**. Each is registered with
`Page.addScriptToEvaluateOnNewDocument` so it runs at document-start, before the
site's own scripts. Finally the daemon starts its queue loop and a two-second
watchdog, and waits.

### What happens when you type a message

You type into a `<textarea>` that lives inside a **shadow root** attached to a
`<div>` we appended to `documentElement`. Pressing Enter calls a function that
Chrome exposed to the page via `Runtime.addBinding` — calling it fires a
`Runtime.bindingCalled` event back over the same websocket. That is the whole
panel → daemon channel: no server, no port, no polling.

The daemon puts the message on its queue, marks itself busy, pushes a `busy`
event back to the panel (which starts the timer and swaps the mascot to its
thinking state), and hands the text to the agent. From there the **OpenAI Agents
SDK** owns the loop: it calls the model, gets a tool call, dispatches it, feeds
the result back, and repeats until the model produces a final message or hits the
40-turn ceiling.

Every step of that streams. Each tool call becomes a card in the chat as it
happens, each result fills in underneath it, and the final answer arrives as a
message. Meanwhile the recorder is writing an event log, and when the turn ends
it appends one row to `history/queries.jsonl` with what *that question* cost — its
own tokens, its context peak, its dollar figure — rather than a run total.

### How it sees a page

`snapshot()` is the default sense and it is not a screenshot. It pulls Chrome's
**accessibility tree** (`Accessibility.getFullAXTree`) — the same structure a
screen reader uses, already resolved to roles, names and states — and merges in a
**DOM sweep** for elements that are clearly clickable but that ARIA misses, which
is roughly a third of them on a modern site. The result is a compact list like
`[e12] button "Search"` plus the page's text.

Those `e12` handles are the currency of the whole system. They are stable within
one snapshot and **deliberately invalidated by the next one** — a ref that no
longer resolves is a hard error rather than a click in the wrong place. If the
page comes back blank, the session tries to recover (stop the load, re-attach,
possibly to a different tab) and says so in the result rather than pretending.

Native `alert()` and `confirm()` get special handling: they block the renderer
completely, so *every* subsequent evaluate times out and the tab looks dead. The
session catches the dialog event, dismisses it over the protocol — the only thing
that can, since script execution is exactly what is blocked — and surfaces the
message text into the next snapshot, because "Level complete!" is usually the
answer to what was being attempted.

### How it acts

A click on `[e12]` resolves the ref to a backend node id, asks Chrome for the
element's **content quads** to get a real point in CSS pixels, sends the virtual
mouse travelling there along a **cubic Bezier curve**, and then dispatches actual
`Input.dispatchMouseEvent` presses. Nothing is faked at the JS level: the page
receives the same hover-then-press sequence a person produces, so `:hover` menus,
tooltips and hover-loaded content behave normally.

Typing, scrolling and key presses work the same way, through real input events.
Two cases needed special treatment. Native `<select>` popups are drawn by the
operating system and **cannot be clicked at all**, so `select_option` sets the
value and fires the events the page listens for. And keyboard-driven flows send a
whole `press_sequence` in one call, because one key per tool call costs a full
model round trip each — on a puzzle game that was the difference between 63 tool
calls and 13.

When clicking is the wrong shape of tool, `run_js` runs JavaScript in the page and
returns JSON, and `screenshot` returns an image for canvas and layout questions.
The model chooses; nothing routes for it.

### What you actually see

The resting state is **Browsy**: a small robot on the page with a button on his
chest, and no window at all. Hovering makes his left arm reach across and press
that button;
clicking it unfolds the chat out of his shoulder and turns the **+** into a
**−**. The chat always hangs **down and to the right** with him standing on its
top-left corner — if there is no room below, he glides somewhere there is. He is
also the drag handle for the whole assembly: move him, the chat comes too.

Folding away is a paint, not a state change: the daemon still holds the
conversation and the turn keeps running, and an answer that lands while you are
folded away leaves a dot on him until you look. Because there is no title bar to put a
spinner in, Browsy carries the status himself — thinking face, pulsing antenna,
and the elapsed time in a bubble beside him, on whichever side has room.

### How the chat survives the page

This is where most of the engineering went, because an injected UI has to survive
a hostile, constantly-reloading host.

The panel is **stateless**. The daemon holds the real transcript; the panel paints
what it is handed. That means it can be wiped and rebuilt at any moment with
nothing lost — and it *is* wiped, on every navigation.

To avoid a visible flash of empty chat on every page load, the injected script
carries a **seed**: the transcript and your panel geometry, baked into its source.
The daemon re-registers the script whenever either changes, so a new page paints
an already-populated panel at document-start instead of appearing empty and
filling in a round trip later. A live copy on `window` covers in-page re-mounts
(SPA route changes) that the seed would roll back, and an authoritative `restore`
from the daemon settles any disagreement.

Each injected script also carries a **build stamp**, because Chrome runs
document-start scripts in registration order and a Chrome left running by an
earlier daemon still has that daemon's script registered — it runs *first*. Newer
build replaces older; same build is a no-op.

The panel sits in a shadow root so page CSS cannot reach it, and carries
`aria-hidden` plus a sweep exclusion so **the agent never sees its own Send
button**. Events are stopped at the shadow host so the page never receives what
you do inside the chat — typing a space cannot pause the video behind it. The
consequence of that isolation is a rule the code depends on everywhere: **our own
window-level listeners must use the capture phase**, because the bubble phase is
exactly what we are blocking.

Focus is defended in two layers. Sites move focus to their own search box after
load, so the panel takes it back while you are actively using the box — bounded,
and cancelled the moment you click outside. And because a modal *focus trap* is a
fight nothing in the same document can win, there is a fallback: if something else
holds focus while the chat box is in use, keystrokes are intercepted at
window-capture and put into the box before the page can see them.

Finally, a two-second **watchdog** checks that the panel and cursor are still on
the current page and reinstalls them if not. Four separate root causes have made
the panel vanish over this project's life; each got its own fix, and the watchdog
is the general answer, so the next unknown one costs a flicker instead of a
session.

### The connection underneath

Everything — panel messages, snapshots, clicks, screenshots — rides one websocket.
That makes it a single point of failure, so it is treated as one. The receive
queue is unbounded and the pong deadline disabled, because a bounded queue makes
the library stop reading the socket under load and the keepalive then kills a
perfectly healthy connection. A drop, clean or abrupt, is logged as an error and
triggers reconnect with backoff, followed by re-injecting everything that is bound
to a target: allowlist, cursor, panel.

### Context, and why it is barely trimmed

The whole item list is re-sent on every model call, which sounds wasteful and is
not: the prefix stays byte-stable, so **80–87% of input tokens serve from cache at
a tenth of the price**. Placeholdering an old snapshot rewrites the middle of that
prefix and invalidates the cache from there on, which on a short task costs more
than the tokens it saves. So trimming only starts once accumulated tool output
passes 60k characters, and even then only stale snapshots are replaced. This was
measured, and it contradicted the original plan.

### Where the security actually lives

In code, on the execution path — never in the prompt. Page text is untrusted
input, so a model that gets talked into something by injected page content still
cannot get through:

- **Domain allowlist**, enforced at the CDP `Fetch` layer on top-level document
  requests. Sub-resources are not gated: the exfiltration path worth caring about
  is a navigation to an attacker's origin, and gating CDN and XHR traffic would
  break ordinary pages without adding protection.
- **Confirm-gate**: a click whose accessible name matches an irreversible-sounding
  pattern (delete, transfer, place order, pay now…) is refused and handed back to
  you, with an instruction to the model not to route around it.
- **The API key is never in this repo** — only a path to it, read at run time,
  redacted from logs.

### Where state lives, in one list

| state | lives in | survives |
|---|---|---|
| conversation truth | daemon process | everything except a restart |
| what you see | panel DOM | nothing; repainted from the above |
| in-page working copy | `window.__cuaexpItems` | SPA re-mounts, not navigation |
| anti-flicker copy | the injected script's seed | navigation and new tabs |
| panel size/position | seed + daemon | navigation; clamped to the window on mount |
| durable notes | `memory.json` | across sessions, via `remember` / `recall` |
| what happened | `logs/<run-id>/` | forever |
| what it cost | `history/queries.jsonl` | forever, one row per question |


## 4. The modules

| file | lines | what it owns |
|---|---:|---|
| `cuaexp/cdp.py` | 258 | CDP client: one browser-level websocket, flattened sessions, reconnect with backoff, unbounded receive queue |
| `cuaexp/session.py` | 263 | Browser session: attach, follow new tabs, domain allowlist, confirm-gate, dialog auto-dismiss, re-attach hooks |
| `cuaexp/snapshot.py` | 265 | Perception: `Accessibility.getFullAXTree` + a DOM sweep for click-ish elements ARIA misses, rendered as `[e12] role "name"` refs |
| `cuaexp/actions.py` | 343 | ref → geometry → real input events; `fill`, `select_option`, `press`, `press_sequence`, `scroll`, `navigate`, `run_js`, `screenshot` |
| `cuaexp/cursor.py` | 266 | The visible mouse: cubic Bezier path, real `mouseMoved` events, trail and click ripple |
| `cuaexp/panel.py` | 961 | The chat UI: shadow DOM, mascot, seed-based anti-flicker, event isolation, focus defence |
| `cuaexp/agent.py` | 297 | System prompt, SDK `Agent`, `TrimmingModel`, attachments, per-turn accounting |
| `cuaexp/tools.py` | 247 | The 14 tools the model sees, plus `web_search` from the SDK |
| `cuaexp/recorder.py` | 237 | `events.jsonl`, `summary.json`, `transcript.md`, screenshots, per-turn rows |
| `cuaexp/history.py` | 115 | Cross-run query log and its index |
| `cuaexp/memory.py` | 76 | `remember` / `recall` across sessions (`memory.json`) |
| `cuaexp/chrome.py` | 57 | Launch Chrome on a dedicated profile (mandatory since Chrome 136) |
| `cuaexp/keyfile.py` | 54 | Read the API key from an external path; never copy, never log |
| `cuaexp/config.py` | 68 | Models, pricing, limits, paths, per-process BUILD stamp |
| `daemon.py` | 258 | Interactive mode: panel plumbing, message queue, turn lifecycle, 2s watchdog |
| `run_task.py` | 112 | One-shot / multi-turn CLI runs, no panel |

**~2,900 lines of Python** in `cuaexp/` + entry points, plus ~43k characters of
injected JavaScript (panel 25.6k, CSS 12.7k, cursor 4.5k, mascot 2.2k).

### The tools

`snapshot` `click` `fill` `select_option` `press` `press_sequence` `scroll`
`navigate` `go_back` `run_js` `screenshot` `click_at` `remember` `recall`
— plus the SDK's hosted `web_search`.

---

## 5. Key design decisions, and why

- **A11y tree first, not vision.** Cheaper, stable across renders, and gives real
  element identity. The tree alone misses ~a third of clickable things, so a DOM
  sweep is merged in.
- **The panel is injected, not a second window.** Registered with
  `Page.addScriptToEvaluateOnNewDocument`, so it re-mounts on every navigation and
  every new tab. It lives in a shadow root so page CSS cannot reach it, carries
  `aria-hidden` and a sweep exclusion so **the agent never sees its own UI**.
- **Anti-flicker by seed.** The injected script carries the transcript and panel
  geometry baked in, re-registered whenever they change, so a new page paints an
  already-populated panel at document-start.
- **Security lives in code, on the execution path** — not in the prompt. The
  domain allowlist gates top-level navigations at the CDP layer; the confirm-gate
  blocks clicks whose accessible name looks irreversible. A page that talks the
  model into something still cannot get past either.
- **The key is never in this repo.** Only a path to it (`CUAEXP_KEYFILE`).
- **Context is deliberately *not* aggressively trimmed.** Measured: ~80–87% of
  input tokens serve from cache at 1/10 price, and rewriting the prefix to save
  tokens costs more than it saves on short runs. Trimming is threshold-gated at
  60k characters.

---

## 6. Where it stands

### Internal suite — 11 tasks, easy → very hard

**11/11 passed · $0.588 · 236s**, median 12.9s / 4 tools / ~3¢ per task,
including an independently verified Sokoban level-1 solve.
Detail: [evals/ANALYSIS.md](evals/ANALYSIS.md).

### OSWorld browser tasks — 10 hard, login-free, verbatim

**5 done · 2 partial · 3 failed · $4.78 · 626s**, 80% of input tokens cached.
Detail: [evals/OSWORLD-RESULTS.md](evals/OSWORLD-RESULTS.md).

Best result was the longest task: MBTA appointment — worked out that "first
Monday eight months later" is 5 April 2027, picked a time inside the 9–12 window,
filled the details, and **stopped before booking** as instructed.

Three real failures, and they are the honest picture of what is weak:

1. **Date pickers.** Tasks 1 and 2 (rentalcars, budget) both burned the entire
   40-tool budget inside date widgets without reaching a results page.
2. **Answering from memory.** apple.com was answered with **zero tool calls** —
   the model never opened the site. The prompt forbids exactly that.
3. **Cost blow-up.** One task cost **$3.68** of the $4.78: 6.1M input tokens,
   context peaking at 199k, 26 of 40 calls `run_js` returning large blobs that
   then ride along in every later request.

### Panel and cursor, measured under load

Sampled once a second through every benchmark task: panel missing from **11 of
560 samples**, cursor from 9 — each a single sample mid-page-load, back on the
next. Before this session's fixes the same measurement was **75 of 118 on one
task**, and gone for the rest of the run once it went.

### Tests

`tests/panel_check.py` — **88 checks, all passing**, ~2.5 minutes, its own Chrome
on port 9333. Drives the real panel with synthesized input: folding and
unfolding, hover-to-point, drag and release, a deliberately lost mouseup, every
resize edge, typing with spaces, what the page underneath sees, wheel routing, off-screen rescue, re-mount across
navigation, a stale script from a dead daemon, Trusted Types, focus theft, a
modal focus trap, the Bezier arc of the cursor, and killing the CDP socket to
watch it reconnect.

Run it after touching `panel.py`, `cursor.py` or `cdp.py`. Every panel bug so far
has been an interaction bug, invisible when reading the code.

---

## 7. What is deliberately switched off

- **`run_js` output cap (3k) and bulky-result trimming.** Measured at a 71% cost
  cut on one hard task, and **reverted on purpose** — it constrains how freely the
  agent can explore, and that freedom was judged worth more. The $3.68 task above
  is the current price of that decision. Not re-applied.
- **Sokoban eval checks.** Left alone by request.
- **Fetch interception in permissive mode.** An interceptor that will only ever
  say yes is pure failure surface.

## 8. Not built yet

| | why it is interesting |
|---|---|
| **Upload to a page** (`DOM.setFileInputFiles` + `Page.setInterceptFileChooserDialog`) | the other half of "here's a PDF, put it on Drive". `read_file` now exists; handing a real file to a page's file input does not |
| **A shell tool** | the obvious next capability, and the one that breaks the current threat model rather than extending it. Browsy ingests untrusted page text by design, so a shell means web content can reach command execution. If it is built it wants the `DANGER` confirm-gate in front of it and no permissive browsing alongside |
| **Date-picker helper** | the single biggest failure mode in the benchmark; set the value and fire the events the widget listens for |
| **Skill / action caching** | replay a known-good trajectory instead of re-deriving it |
| **Local Fara1.5 on the 24GB pod** | vision-only CUA, MIT licensed; best first use is a `locate(description) → (x,y)` grounding tool, not a whole driver |
| **WebMCP** | ~30 lines: detect `document.modelContext`, call site-declared tools directly |
| **MV3 extension** | to drive the real logged-in Chrome instead of a dedicated profile |

## 9. Known limits

- Snapshots cover the **main frame only** — cross-origin iframes are invisible.
- Canvas apps need `screenshot` + `click_at`.
- Native OS dialogs (file chooser, print, native `<select>` popups) cannot be
  clicked; `select_option` works around the last one, the others need CDP
  interception.
- 40 tool calls per turn is a hard ceiling; hard sites hit it.
- Chrome must run on its own profile (`.chrome-profile/`) — since Chrome 136 the
  debug port is silently ignored on the default one.

---

## 10. Running it

```bash
.venv/Scripts/python daemon.py --start google.com      # interactive, with panel
.venv/Scripts/python run_task.py "find X on Y"         # one-shot, no panel
.venv/Scripts/python evals/run_suite.py                # internal suite
.venv/Scripts/python evals/osworld_run.py              # OSWorld tasks
.venv/Scripts/python tests/panel_check.py              # 59 panel checks
python history.py                                      # what every query cost
```

Every run writes `logs/<run-id>/` (events, summary, transcript, screenshots);
every *question* appends a row to `history/queries.jsonl` with its own tokens,
context peak and cost. Both are gitignored — they contain your questions and page
content from logged-in sessions.

**27 queries recorded so far, $5.80 total.**

---

## 11. If you are picking this up cold

1. This page for the shape of it — section 3 explains it in prose.
2. [PLAN.md](PLAN.md) §8 — 26 findings from actually building it, the most useful
   page in the repo.
3. [evals/OSWORLD-RESULTS.md](evals/OSWORLD-RESULTS.md) — what it can and cannot
   do on real sites.
4. `Session/2026-08-17_session-handoff.txt` — chronological, including everything
   that was tried and reverted.
5. `logs/<newest>/transcript.md` — what the agent actually did last.

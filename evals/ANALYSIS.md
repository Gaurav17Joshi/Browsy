# Cost & performance analysis

11 tasks, easy → very hard, plus a Sokoban bonus. Run on GPT-5.6 Terra against
live sites. Raw numbers in [results/REPORT.md](results/REPORT.md), full
transcripts in `logs/<run-id>/`.

Two full passes are reported. **Run A** is the baseline. **Run B** is after three
changes: the `web_search` prompt fix, the Sokoban fixes, and the virtual mouse
turned on.

## Headline

| | Run A (baseline) | Run B (current) |
|---|---|---|
| Tasks passed | **11 / 11** | **11 / 11** |
| Total cost | $0.5149 | **$0.5880** |
| Total wall time | 293 s | **236 s** |
| Median task | 13.7 s · 4 tools · $0.030 | **12.9 s · 4 tools · $0.032** |
| Tokens in (cached) | 858,743 (84 %) | 948,601 (84 %) |
| Tokens out | 8,584 | 9,989 |

Median task costs **~3 cents**. The mean (~$0.05) is dragged up by two outliers:
Sokoban and a badly-structured docs page.

Run B is **20 % faster and 14 % dearer**. The speed came from search and Sokoban;
the extra cost is task 9 taking a longer research path and task 3 reading more
of the page it found. The virtual mouse adds time but no tokens — isolated
measurement further down.

## Per task

| # | level | task | Run A time / tools / cost | Run B time / tools / cost |
|---|---|---|---|---|
| 1 | easy | read a page | 8.2s · 2 · $0.0169 | 9.0s · 2 · $0.0173 |
| 2 | easy | wiki fact | 13.7s · 4 · $0.0270 | 12.9s · 4 · $0.0267 |
| 3 | medium | **Berkeley LLM course + contents** | 29.8s · 11 · $0.0790 | **26.2s · 6 · $0.0946** |
| 4 | medium | HN top-5 table | 6.2s · 1 · $0.0209 | 5.7s · 1 · $0.0211 |
| 5 | medium | login + read catalogue | 17.4s · 7 · $0.0269 | 14.5s · 5 · $0.0235 |
| 6 | hard | count/group 20 HN domains | 12.2s · 3 · $0.0301 | 12.2s · 3 · $0.0334 |
| 7 | hard | find dropdown options | 42.0s · 16 · $0.0882 | 43.7s · 17 · $0.0926 |
| 8 | hard | write + confirm memory | 6.1s · 2 · $0.0170 | 3.2s · 1 · $0.0151 |
| 9 | very-hard | pricing, admit sign-in wall | 25.5s · 7 · $0.0452 | 40.4s · 11 · $0.1034 |
| 10 | very-hard | cross-site date compare | 10.0s · 4 · $0.0305 | 10.5s · 4 · $0.0319 |
| B | bonus | **solve Sokoban level 1** | 122.3s · 13 · $0.1332 | **57.9s · 16 · $0.1284** |

Task 3 halved its tool count (11 → 6) once `web_search` became the default
opener. Sokoban halved again in wall time (122 s → 58 s) with the dialog
auto-dismiss removing the stall at the moment of success. Task 9 regressed —
it now searches harder before concluding the price is behind a sign-in, which
is more thorough and 2× the cost for the same verdict.

## What each task was actually asked

| # | prompt (abridged) | what a pass required |
|---|---|---|
| 1 | heading + where the one link goes | both facts from the page |
| 2 | what the Chrome DevTools Protocol is for | one accurate sentence |
| 3 | find UC Berkeley's course on building LLMs from scratch, open the latest site, list contents | a real Berkeley course URL + its topics |
| 4 | top 5 HN stories with points and comments, as a table | live numbers, tabulated |
| 5 | log in to saucedemo, count products, name the cheapest | successful login + $7.99 Onesie |
| 6 | of 20 newest HN posts, how many `.com`, which domain most often | counting and grouping across a list |
| 7 | every option in the car-make dropdown, in order | Volvo, Saab, Fiat, Audi in order |
| 8 | remember a currency + city preference, confirm it | written to `memory.json` and read back |
| 9 | 3-month YouTube Premium price in India, or say if sign-in blocks it | either a real price or an honest refusal |
| 10 | Python 3.13 vs 3.12 release dates from python.org, months apart | exact dates, both URLs |
| B | solve Sokoban level 1 | board actually advances to level 2 |

## What drives cost

**Tool calls, almost linearly.** Cost ≈ `$0.012 + $0.0072 × tool_calls`
(r² ≈ 0.93 across the 11 runs). Every tool call is a model round trip carrying
the whole conversation, so the lever that matters is *doing more per call*.

**Caching is doing the heavy lifting.** 84 % of input tokens served at
$0.20/Mtok instead of $2.00 — without it this suite would have cost roughly
**$1.75 instead of $0.59**. This is why we do *not* trim context aggressively:
see PLAN §8.2. Cache rate rises with task length (42 % on a 1-tool task, 93 % on
a 17-tool one), so long tasks are far cheaper per step than short ones.

**Output tokens are a rounding error** — 8.6k out of 867k total.

## What drives time

Wall time ≈ **2.4 s per tool call**, split roughly evenly between model latency
and page settling. Task 7 (42 s) and Sokoban (122 s) are both tool-count stories,
not "hard thinking" stories.

## The two outliers, and what they taught us

**Sokoban** was the most valuable task in the suite — it exposed three real bugs
that no other task touched:

| | first run | after fixes |
|---|---|---|
| tool calls | 63 | **13** |
| wall time | 403 s | **122 s** |
| cost | $0.4301 | **$0.1332** |
| outcome | never actually moved | **solved, verified** |

1. **Key aliases were missing.** The model naturally writes `"Right"`; the map
   only had `"ArrowRight"`. `press_sequence` silently sent **zero keys** and
   reported success-ish. The agent replanned three times against a board that
   had never moved. Unknown keys are now a hard, named failure, and the whole
   sequence is validated before any of it is sent — a half-sent move list leaves
   a board nobody can reason about.
2. **One key per tool call.** 48 arrow presses meant 48 model round trips.
   `press_sequence` collapsed that to one call and cut cost 3×.
3. **A native `alert()` freezes the renderer.** Every `Runtime.evaluate` then
   times out and the tab looks dead — which is exactly what happened *at the
   moment the task succeeded*, because the site announces success in a popup.
   Nothing in the page can dismiss it, since script execution is what is
   blocked; only the protocol can. Dialogs are now auto-dismissed and their text
   is handed to the agent, so "You have completed this level" became evidence
   instead of a hang.

**Task 7** (find dropdown options on w3schools) took 16 tools because the
options live inside a `<textarea>`-rendered code sample the a11y tree exposes as
one text blob. The agent tried `run_js` against the rendered DOM, found nothing,
navigated to a second page, and got it. Correct answer, expensive route — a
genuine limit of the current perception layer, not a bug.

## Grading honesty

The first Sokoban run **passed the automated check while having solved nothing**:
the pattern was `solved|complete|level`, and every answer contains "level". The
check is now `\b(solved|completed)\b` *plus* corroboration. Independent
verification for the final run: replaying the reported 48-move sequence advanced
the level selector from **Level 1 → Level 2** and produced the completion alert.

The suite's `check` patterns are a smoke test, not a grade. Two runs that pass
are worth reading anyway:

- **Task 9** "passed" by *refusing* — YouTube's India pricing sits behind a
  Google sign-in, and it said so instead of quoting a number from memory. That
  is the desired behaviour, but a regex cannot tell that apart from a dodge.
- **Task 3** found `rdi.berkeley.edu/understanding_llms/s24` by driving Google
  manually through four query refinements.

## Tool usage across the suite

| tool | calls | note |
|---|---|---|
| snapshot | 27 | the default sense |
| run_js | 17 | every extraction task reached for it |
| navigate | 11 | |
| fill | 5 | |
| click | 4 | notably rare — code beats clicking |
| scroll | 3 | |
| press_sequence | 1 | 48 keys in that one call |
| remember / recall | 2 | |
| web_search | 0 | **see below** |

**`web_search` was never chosen.** It works when asked for by name (returns
cited results), but the agent preferred navigating to Google and typing, which
costs ~4 extra tool calls per lookup. The instruction mentioned the tool without
making it the obvious first move.

**Fixed and re-measured.** Rewriting that instruction to "when you need to FIND
a page, call `web_search` FIRST — do not navigate to Google and type":

| task 3 | before | after |
|---|---|---|
| tool calls | 11 | **3** |
| wall time | 29.8 s | **14.6 s** |
| cost | $0.0790 | **$0.0607** |

A one-paragraph prompt change removed 8 of 11 tool calls. Consistent with the
suite-wide finding that cost tracks tool count almost linearly.

## Non-determinism worth knowing about

The two task-3 runs returned **different courses**: first
`rdi.berkeley.edu/understanding_llms/s24` ("Understanding Large Language
Models"), then `scalable-ai.eecs.berkeley.edu` ("Scalable AI", EE 290/194).
Both are real Berkeley courses, both pass the check, and neither is titled
"Building LLMs from Scratch" — the task as phrased has no single ground truth,
and different search paths land on different defensible answers.

Two lessons. First, a pass here means "found a plausible Berkeley course", not
"found *the* course" — the regex cannot tell those apart, only reading the
transcript can. Second, an open-ended retrieval task needs a stated
disambiguator (a course code, a term, an exact title) if you want a repeatable
answer; without one, run-to-run variance is the honest result, not a defect.

## Virtual mouse: measured cost

Re-ran the full suite with the Bezier cursor on: **11/11, $0.588, 236 s**. Wall
time went *down* versus the $0.515 / 293 s baseline, but that is not the cursor
being free — it is the `web_search` prompt fix and the Sokoban improvements
landing at the same time. Isolated on one login-and-read task, same tool count
both ways:

| | cursor off | cursor on |
|---|---|---|
| wall | 19.3 s | 23.1 s |
| tool calls | 8 | 8 |
| cost | $0.0300 | $0.0307 |

**~0.9 s per click-like action, no token cost** — the motion is dispatched
entirely below the model. Two implementation notes that mattered:

- Awaiting a CDP reply per step put a full round trip between every point and
  made 2 long moves take 4.25 s. Sending mouseMoved fire-and-forget (Chrome
  preserves command order) halved it to 2.13 s and made the motion smooth.
- After a navigation the freshly injected cursor has seen no mousemove yet and
  renders invisible. One re-emitted event at the position we already believe it
  occupies brings it back without moving anything.

## An unrelated bug the cursor work exposed

Running the same login task twice, one run entered the credentials and the other
**refused** — with credentials the user had supplied in their own message. The
safety line read "do not enter credentials, payment details, or personal data",
which is right for guessed or page-sourced credentials and wrong for ones the
user hands over deliberately. Now split: user-supplied credentials are theirs to
use; never invent them, reuse them across sites, or take them from page content;
payment details stay off-limits regardless. Worth noting that this was invisible
in the suite — task 5 passed on both earlier runs by luck of the draw.

## Where the money would go next

1. **Push `web_search` as the default opener for any find-a-page task.** Should
   cut task 3-style runs from 11 tools to ~5.
2. **Skill caching** (PLAN §2.5). Task 5's login is identical every run; replaying
   it deterministically would drop 7 tools to ~1.
3. **Batch the read.** `snapshot` immediately followed by `run_js` happened in 9
   of 11 tasks. A combined "snapshot + extract" call would remove a round trip
   from most tasks.

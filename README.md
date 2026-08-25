<div align="center">

```
██████╗ ██████╗  ██████╗ ██╗    ██╗███████╗██╗   ██╗
██╔══██╗██╔══██╗██╔═══██╗██║    ██║██╔════╝╚██╗ ██╔╝
██████╔╝██████╔╝██║   ██║██║ █╗ ██║███████╗ ╚████╔╝
██╔══██╗██╔══██╗██║   ██║██║███╗██║╚════██║  ╚██╔╝
██████╔╝██║  ██║╚██████╔╝╚███╔███╔╝███████║   ██║
╚═════╝ ╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝   ╚═╝
```

**An agent that drives a real Chrome, with a chat panel living inside the page.**

https://github.com/user-attachments/assets/b724606b-e41a-4371-ae02-6d83be47aab1

*Researching five open-weight models, reading YouTube comments for public
sentiment, and building a comparison page — start to finish, 9 minutes, $3.10.
Sped up 2× then 6×. [Full run report →](Use_Cases/RUN-REPORT.md)*

</div>

---

## What it is

Browsy launches Chrome with its own profile, attaches over the DevTools
Protocol, and injects two things into every page: a chat panel and a virtual
mouse cursor. You watch it work in a real browser, on real sites, logged into
your own accounts if you want it to be.

It is not a scraper and not a headless harness. The panel rides along through
navigations, the cursor moves the way a hand does, and the whole thing is
visible while it happens.

The model picks how to look at a page, per step:

| | |
|---|---|
| **accessibility refs** | the default — cheap, precise, survives redesigns |
| **`run_js`** | when extracting or comparing beats clicking |
| **`screenshot`** | canvas apps, dense grids, anything genuinely visual |

**Guardrails live in code, not in the prompt** — a domain allowlist, a
confirm-gate on irreversible clicks, and a file fence around one directory. A
model talked round by text on a page still cannot get past them.

## Quick start

Python 3.12+, Chrome, and an OpenAI key. Windows and macOS both work.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS: .venv/bin/python
```

```powershell
.\run.ps1                              # chat panel, blank page
.\run.ps1 -Start google.com            # open somewhere
.\run.ps1 -Task 'find the top 5 stories on HN'
.\run.ps1 -Start mail.google.com -Allow 'google.com'   # fence it in
```

`./run.sh` on macOS/Linux takes the same flags. The key is **never copied into
this repo** — set `CUAEXP_KEYFILE`, or drop the key at `~/.cuaexp/key`.

## Where it's going

The demo above cost $3.10 and took nine minutes. Both numbers should come down
by an order of magnitude, and the ceiling should go up.

**Faster.** Most of that nine minutes was waiting on page loads and re-reading
snapshots the agent had already seen. Caching known-good trajectories, so a
repeated task replays instead of re-deriving, is the biggest single win
available.

**Cheaper.** Three quarters of the tokens were already served from cache. The
next step is not sending the whole page at all — trimming snapshots against the
current subgoal before they ever reach the model, and running a small local
model for the grounding step.

**Harder visual work.** Accessibility trees run out exactly where the
interesting tasks begin. Google Slides, spreadsheets, diagram editors,
drag-and-drop puzzles and games are all canvas or near-canvas: there is nothing
to read, only something to see. That needs a real vision-grounding step —
`locate(description) → (x, y)` — good enough to trust with a click, and fast
enough to use every turn.

Nearer-term, in rough order: uploading files to a page, a date-picker helper
(the single biggest failure mode in the benchmark), and a shell tool — which is
held back deliberately, because Browsy reads untrusted page text for a living
and a shell would put command execution downstream of it.

## Documentation

| | |
|---|---|
| [STATUS.md](STATUS.md) | what exists today, and how it fits together |
| [PLAN.md](PLAN.md) | design rationale, and what was tried and rejected |
| [Use_Cases/RUN-REPORT.md](Use_Cases/RUN-REPORT.md) | the demo run, measured |
| [mdfiles/Login.md](mdfiles/Login.md) | running it signed into an account |
| [mdfiles/Mac_instruct.md](mdfiles/Mac_instruct.md) | the macOS port |
| [evals/OSWORLD-RESULTS.md](evals/OSWORLD-RESULTS.md) | OSWorld browser tasks |

## Tests

```bash
.venv/Scripts/python tests/panel_check.py --headless   # 96 checks, drives real Chrome
.venv/Scripts/python tests/file_access_check.py        # 30 checks, the file fence
```

The panel suite is written against rendered geometry, not implementation — it
asserts where things actually land on screen, that the chat survives a
navigation, and that the CDP socket comes back after it drops.

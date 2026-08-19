"""Eval suite: 10 tasks, easy -> very hard, plus a bonus.

`check` is a substring/regex list used only as a coarse signal -- the real
grading is reading transcript.md. It flags "the answer at least mentions the
things a correct answer must mention".
"""

TASKS = [
    dict(
        id=1, level="easy", name="read-page",
        start="https://example.com",
        task="What is the heading of this page and where does its only link go?",
        check=[r"Example Domain", r"iana\.org"],
    ),
    dict(
        id=2, level="easy", name="wiki-fact",
        start="https://en.wikipedia.org/wiki/Chrome_DevTools_Protocol",
        task="Search Wikipedia for the Chrome DevTools Protocol and tell me in one "
             "sentence what it is used for.",
        check=[r"(?i)debug|instrument|inspect"],
    ),
    dict(
        id=3, level="medium", name="berkeley-llm-course",
        start="about:blank",
        task="Go find the course by UC Berkeley on building LLMs from scratch, open "
             "the latest course website, and find the course contents. List the "
             "actual topics/lectures you see on the site, and give me the URL.",
        check=[r"(?i)berkeley", r"(?i)http"],
    ),
    dict(
        id=4, level="medium", name="hn-extract",
        start="https://news.ycombinator.com",
        task="Give me the top 5 stories with their points and comment counts, as a table.",
        check=[r"\d+\s*point|\|"],
    ),
    dict(
        id=5, level="medium", name="form-fill",
        start="https://www.saucedemo.com/",
        task="Log in with username 'standard_user' and password 'secret_sauce', then "
             "tell me how many products are listed and the cheapest one with its price.",
        check=[r"(?i)6|six", r"(?i)\$7\.99|onesie"],
    ),
    dict(
        id=6, level="hard", name="multi-step-compare",
        start="https://news.ycombinator.com/newest",
        task="Look at the 20 newest Hacker News submissions and tell me: how many are "
             "from domains ending in .com, and which single domain appears most often.",
        check=[r"(?i)\.com"],
    ),
    dict(
        id=7, level="hard", name="dropdown-and-filter",
        start="https://www.w3schools.com/html/html_forms.asp",
        task="On this page find the example form with a dropdown of car makes. Tell me "
             "every option in that dropdown, in order.",
        check=[r"(?i)volvo", r"(?i)saab", r"(?i)fiat|audi"],
    ),
    dict(
        id=8, level="hard", name="memory-write",
        start="about:blank",
        task="Remember that I prefer prices shown in GBP and that I am based in London. "
             "Then confirm what you have stored.",
        check=[r"(?i)gbp", r"(?i)london"],
    ),
    dict(
        id=9, level="very-hard", name="research-synthesis",
        start="about:blank",
        task="Find the current price of a 3-month YouTube Premium individual plan in "
             "India, from YouTube's own site, and tell me what it includes. If the page "
             "requires sign-in, say so rather than guessing.",
        check=[r"(?i)youtube", r"(?i)premium"],
    ),
    dict(
        id=10, level="very-hard", name="cross-site-compare",
        start="about:blank",
        task="Compare the Python 3.13 and 3.12 release dates using the official "
             "python.org downloads pages, and tell me how many months apart they were. "
             "Give the exact dates you saw and the URLs.",
        check=[r"(?i)python\.org", r"20\d\d"],
    ),
    dict(
        id="bonus", level="bonus", name="sokoban",
        start="https://www.mathsisfun.com/games/sokoban.html",
        task="Play the Sokoban puzzle on this page and solve Level 1. The goal is to "
             "push every box onto a target square. Work out the grid layout first "
             "(reading it from the page however you can), plan your moves, then use "
             "arrow keys to move. Tell me the move sequence you used and whether the "
             "level was actually solved.",
        # Deliberately strict: an earlier version accepted the word "level",
        # which every answer contains, so a run that never solved anything
        # still scored a pass.
        check=[r"(?i)\b(solved|completed)\b", r"(?i)verified|confirmed|congratulations|level 2"],
    ),
]

"""Ten hard browser tasks from the OSWorld benchmark family.

WHY NOT OSWorld 2.0 VERBATIM: the 2.0 task set is not public. Its task classes
and assets sit behind gated Hugging Face datasets (`xlangai/osworld_v2_tasks`,
`xlangai/osworld_v2_assets_gated`), its 108 workflows run against *mocked*
self-hosted websites (site.hku.icu, or a self-deployed OSWorld-web), several
need a self-hosted GitLab with a private token, and most span desktop apps
(FreeCAD, LibreOffice) inside a VM. None of that is reachable from a browser
agent driving the live internet, and every one of those sites needs an account.

So these are the hardest login-free, browser-only tasks from the public OSWorld
task set (xlang-ai/OSWorld, `evaluation_examples/examples/chrome`), taken
verbatim -- instruction text and start URL both -- with their original task ids
so anything here can be traced back. They are chosen for the property OSWorld
2.0 pushes on: multi-step workflows on real sites (date pickers, filter stacks,
sort orders, multi-page forms) rather than one-shot lookups.

Grading is by reading the answer, not by string match. `expect` only records
what a correct answer has to contain, to keep the review honest.
"""

TASKS = [
    dict(
        id=1, osworld="1704f00f-79e6-43a7-961b-cedd3724d5fd", site="rentalcars.com",
        start="https://www.rentalcars.com/",
        task="Find a large car from next Monday to Friday in Zurich, sorted by price.",
        why="relative dates, a location autocomplete, a car-class filter and a sort order",
        expect="a large/full-size car with a price, for the correct Mon-Fri dates, "
               "on a price-sorted results page",
    ),
    dict(
        id=2, osworld="47543840-672a-467d-80df-8f7c3b9788c9", site="budget.com",
        start="https://www.budget.com/",
        task="On the current website, show me the cars available for pickup at Boston "
             "Logan Intl Airport from the 10th to the 11th of next month, sorted by "
             "the number of seats to find the largest capacity.",
        why="airport code lookup, two dates, then a sort the site may not offer directly",
        expect="a results page for BOS 10-11 Sep 2026 and the highest-capacity vehicle",
    ),
    dict(
        id=3, osworld="b7895e80-f4d1-4648-bee0-4eb45a6f1fa8", site="tripadvisor.com",
        start="https://www.tripadvisor.com/",
        task="Find a Hotel in New York City with lowest price possible for 2 adults "
             "next weekend. Sort the search results by price and stay on the results page.",
        why="heavy JS site, date range, occupancy, sort, and an explicit 'do not "
            "navigate away' constraint",
        expect="a NYC hotel results page sorted by price, with the cheapest named",
    ),
    dict(
        id=4, osworld="82279c77-8fc6-46f6-9622-3ba96f61b477", site="cars.com",
        start="https://www.cars.com/",
        task="Find electric cars with a maximum price of $50,000 within 50 miles of 10001.",
        why="three filters that interact: fuel type, price cap, radius from a zip",
        expect="a filtered listing page (EV, <=$50k, 50 mi of 10001) and what it found",
    ),
    dict(
        id=5, osworld="da46d875-6b82-4681-9284-653b0c7ae241", site="mbta.com",
        start="https://www.mbta.com/",
        task="Book an appointment to apply for a transportation access pass at the "
             "Charlie Card store on the first Monday eight months later at any "
             "available time from 9:00 am to 12:00 pm, fill in my details (James Smith, "
             "james.smith@gmail.com). And do not click \"book\" directly. Let me review it.",
        why="longest horizon here: find the booking system, a date eight months out, "
            "a time window, a multi-field form -- and stop before committing",
        expect="a filled booking form for Mon 5 Apr 2027, 9-12, not submitted",
    ),
    dict(
        id=6, osworld="a96b564e-dbe9-42c3-9ccf-b4498073438a", site="flightaware.com",
        start="https://www.flightaware.com/",
        task="In the FlightAware Discussions forum, navigate to the FlightAware > "
             "General category and open the topic with the most posts or replies.",
        why="navigation into a nested forum, then a comparison across a list",
        expect="the General category opened and the highest-reply topic named",
    ),
    dict(
        id=7, osworld="121ba48f-9e17-48ce-9bc6-a4fb17a7ebba", site="store.steampowered.com",
        start="https://store.steampowered.com",
        task="Find Dota 2 game and add all DLC to cart.",
        why="'all DLC' is open-ended: find the store page, enumerate DLC, act on each",
        expect="the Dota 2 store page, the DLC list, and what ended up in the cart",
    ),
    dict(
        id=8, osworld="b4f95342-463e-4179-8c3f-193cd7241fb2", site="recreation.gov",
        start="https://www.recreation.gov/",
        task="Find the Next Available dates for Diamond.",
        why="ambiguous target ('Diamond' is several campgrounds) plus an availability grid",
        expect="the Diamond campground page and its next available date(s)",
    ),
    dict(
        id=9, osworld="f5d96daf-83a8-4c86-9686-bada31fc66ab", site="apple.com",
        start="https://www.apple.com/",
        task="Compare iPhone 15 Pro Max with iPhone 14 Pro Max and iPhone 13 Pro Max",
        why="a three-way comparison built through a picker UI, then reading it back",
        expect="the comparison set up with all three models and concrete spec differences",
    ),
    dict(
        id=10, osworld="f0b971a1-6831-4b9b-a50e-22a6e47f45ba", site="nfl.com",
        start="https://www.nfl.com/",
        task="Please help me find the score record for the Super Bowl of the 2019 NFL "
             "season (played in 2020) in the NFL website.",
        why="a fact buried in a stats section, with an off-by-one-year trap",
        expect="Super Bowl LIV, Chiefs 31 - 49ers 20, sourced from nfl.com",
    ),
]

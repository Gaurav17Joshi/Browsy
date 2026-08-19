# OSWorld browser tasks -- run report

_Generated 2026-08-18 13:15 by `evals/osworld_run.py`._

Ten login-free browser tasks taken verbatim from the public OSWorld task set (instruction text, start URL and task id all unchanged). Why these and not OSWorld 2.0's own 108: that set is gated on Hugging Face, runs against mocked self-hosted websites, needs a self-hosted GitLab token, and most of its tasks span desktop applications inside a VM -- none of it reachable from a browser agent on the live internet. See `evals/osworld_tasks.py` for the full note.

Each task was asked the way a user asks: typed into the chat panel inside Chrome and sent with Enter, with the panel and the virtual mouse watched once a second throughout.

## Totals

| tasks | wall | tool calls | input tokens | cached | output | cost |
|---|---|---|---|---|---|---|
| 10 | 626s | 176 | 8,046,936 | 6,402,229 (80%) | 17,184 | **$4.7761** |

**5 done, 2 partial, 3 failed** of 10 graded. Grading is by reading the answer against the page, not by string match; the reasoning for each is under its task below.

Median task: **$0.0796**. The mean is not the story here — one task (#2) cost $3.68, more than the other nine put together. See *Where the money went*.

## Per task

| # | site | result | s | tools | in | cached | out | cost |
|---|---|---|---|---|---|---|---|---|
| 1 | rentalcars.com | **failed** | 120 | 40 | 573,973 | 486,383 | 2,671 | $0.3045 |
| 2 | budget.com | **failed** | 152 | 40 | 6,116,990 | 4,782,701 | 4,674 | $3.6812 |
| 3 | tripadvisor.com | done | 76 | 27 | 396,764 | 344,243 | 1,992 | $0.1978 |
| 4 | cars.com | done | 54 | 11 | 186,269 | 159,833 | 2,191 | $0.1111 |
| 5 | mbta.com | done | 120 | 34 | 460,130 | 407,740 | 1,904 | $0.2092 |
| 6 | flightaware.com | done | 20 | 7 | 76,751 | 63,567 | 371 | $0.0435 |
| 7 | store.steampowered.com | partial | 17 | 6 | 59,064 | 48,421 | 372 | $0.0354 |
| 8 | recreation.gov | done | 23 | 7 | 92,923 | 75,235 | 558 | $0.0571 |
| 9 | apple.com | **failed** | 26 | 0 | 27,475 | 0 | 2,055 | $0.0796 |
| 10 | nfl.com | partial | 17 | 4 | 56,597 | 34,106 | 396 | $0.0566 |

## The chat and the mouse, while all this was happening

Sampled once a second for the whole of every task: is the panel still mounted on whatever page the agent has navigated to, is the virtual cursor still there, and how far did it travel per click.

| # | samples | panel missing | cursor missing | cursor journeys |
|---|---|---|---|---|
| 1 | 119 | 1 | 1 | 15 cursor journeys, avg 404px in 185ms |
| 2 | 143 | 1 | 1 | 3 cursor journeys, avg 717px in 430ms |
| 3 | 74 | 2 | 2 | 2 cursor journeys, avg 186px in 171ms |
| 4 | 52 | 2 | 1 | 2 cursor journeys, avg 513px in 247ms |
| 5 | 119 | 1 | 1 | 14 cursor journeys, avg 137px in 49ms |
| 6 | 20 | 2 | 2 | 1 cursor journeys, avg 829px in 772ms |
| 7 | 17 | 1 | 1 | 2 cursor journeys, avg 219px in 30ms |
| 8 | not counted | 0 | 0 | 3 cursor journeys, avg 401px in 235ms |
| 9 | not counted | 0 | 0 | no clicks |
| 10 | 16 | 1 | 0 | no clicks |
| **all** | **560** | **11** | **9** | |

The panel was missing from 11 of 560 counted samples and the cursor from 9 (two rows predate the sample counter and recorded no misses at all). Every one of those is a single sample landing in the middle of a page load, before document-start script has a body to mount into; the following sample always has it back. For comparison, the same measurement before this session's fixes: **75 of 118 samples on one task**, with the panel gone for the rest of the run once it went.

## What each run did

### 1. rentalcars.com -- `1704f00f-79e6-43a7-961b-cedd3724d5fd`

> Find a large car from next Monday to Friday in Zurich, sorted by price.

*Hard because:* relative dates, a location autocomplete, a car-class filter and a sort order  
*A correct answer contains:* a large/full-size car with a price, for the correct Mon-Fri dates, on a price-sorted results page

**120s · 40 tools · $0.3045**

`navigatex1  snapshotx16  fillx1  clickx15  run_jsx7`

Answer:

> I set the search location to **Zurich** and confirmed the pick-up date as **Monday, August 24, 2026** at **10:00 AM**, with prices set to **British pounds**.
> 
> I could not complete the drop-off date, large-car filter, or price-sorted results before the browsing session ran out of steps. The search was blocked at the drop-off date picker, so I did **not** obtain any actual car options or prices.

**Failed.** Resolved the dates correctly (Mon 24 - Fri 28 Aug 2026) and set Zurich, but rentalcars.com redirects into booking.com's car search and it was still fighting the drop-off date picker when the 40-tool budget ran out. Said so plainly and reported no cars rather than inventing any.

Panel and mouse: panel MISSING in 1/119 samples; cursor MISSING in 1/119; 15 cursor journeys, avg 404px in 185ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 2. budget.com -- `47543840-672a-467d-80df-8f7c3b9788c9`

> On the current website, show me the cars available for pickup at Boston Logan Intl Airport from the 10th to the 11th of next month, sorted by the number of seats to find the largest capacity.

*Hard because:* airport code lookup, two dates, then a sort the site may not offer directly  
*A correct answer contains:* a results page for BOS 10-11 Sep 2026 and the highest-capacity vehicle

**152s · 40 tools · $3.6812**

`snapshotx9  clickx2  run_jsx26  screenshotx1  navigatex1  fillx1`

Answer:

> I’m setting the airport and dates directly in the booking form, then I’ll open the available-car results.

**Failed.** Same shape: 40 tools spent on budget.com's booking form without reaching a results page. The final line is a statement of intent, not an answer. This is also the $3.68 run -- see the cost note below.

Panel and mouse: panel MISSING in 1/143 samples; cursor MISSING in 1/143; 3 cursor journeys, avg 717px in 430ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 3. tripadvisor.com -- `b7895e80-f4d1-4648-bee0-4eb45a6f1fa8`

> Find a Hotel in New York City with lowest price possible for 2 adults next weekend. Sort the search results by price and stay on the results page.

*Hard because:* heavy JS site, date range, occupancy, sort, and an explicit 'do not navigate away' constraint  
*A correct answer contains:* a NYC hotel results page sorted by price, with the cheapest named

**76s · 27 tools · $0.1978**

`navigatex2  snapshotx8  clickx2  run_jsx11  click_atx1  pressx1  fillx1  press_sequencex1`

Answer:

> Search results are open for **New York City**, **2 adults**, **Friday, August 21–Sunday, August 23, 2026**, and are set to **“Price (lowest first)”** in GBP.
> 
> The first listed option is **Manhattan Bowery Lodge** at **£77**.

**Done.** NYC results for 2 adults, sorted price-lowest, stayed on the results page, cheapest named (Manhattan Bowery Lodge, GBP 77). Read 'next weekend' as Fri 21 - Sun 23 Aug; the narrow reading is Sat 22 - Sun 23, so the Friday night is arguable.

Panel and mouse: panel MISSING in 2/74 samples; cursor MISSING in 2/74; 2 cursor journeys, avg 186px in 171ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 4. cars.com -- `82279c77-8fc6-46f6-9622-3ba96f61b477`

> Find electric cars with a maximum price of $50,000 within 50 miles of 10001.

*Hard because:* three filters that interact: fuel type, price cap, radius from a zip  
*A correct answer contains:* a filtered listing page (EV, <=$50k, 50 mi of 10001) and what it found

**54s · 11 tools · $0.1111**

`navigatex2  snapshotx4  run_jsx3  clickx2`

Answer:

> I found these **fully electric** listings priced below **$50,000** within **50 miles of ZIP 10001**:
> 
> | Vehicle | Price | Mileage | Seller location / distance |
> |---|---:|---:|---|
> | 2021 Tesla Model Y Long Range AWD | $31,294 | 31,346 mi | Wall Township, NJ — 38 mi |
> | 2023 Tesla Model Y Long Range | $30,593 | 49,308 mi | Robbinsville Twp, NJ — 48 mi |
> | 2024 Tesla Model Y Performance AWD | $36,294 | 40,398 mi | Wall Township, NJ — 38 mi |
> | 2024 Tesla Model Y Long Range RWD | $36,475 | 22,500 mi | Ramsey, NJ — 23 mi |
> | 2023 Tesla Model Y Long Range | $35,998 | 13,950 mi | Edison, NJ — 26 mi |
> | 2021 Tesla Model 3 Standard Range Plus | $27,900 | 9,399 mi | Manasquan, NJ — 43 mi |
> | 2023 Tesla Model Y Long Range AWD | $28,447 | 69,253 mi | Westfield, NJ — 20 mi |
> | 2021 Tesla Model 3 Performance | $30,811 | 22,786 mi | North Plainfield, NJ — 26 mi |
> | 2024 Cadillac LYRIQ Tech | $35,175 | 23,273 mi | Rockville Centre, NY — 15 mi |
> | 2024 Acura ZDX Type S | $40,049 | 10,879 mi | Ramsey, NJ — 28 mi |
> | 2022 Ford Mustang Mach‑E GT | $32,673 | 31,822 mi | Ramsey, NJ — 28 mi |
> | 2023 Polestar 2 Long Range Dual Motor Performance Plus | $30,889 | 15,833 mi | East Hanover, NJ — 25 mi |
> 
> **Standouts:**  
> - **Lowest-mileage option:** 2021 Tesla Model 3 Standard Range Plus — 9,399 miles, $27,900.  
> - **Best newer crossover choices:** 2024 Tesla Model Y Long Range RWD, Cadillac LYRIQ Tech, and Acura ZDX Type S.  
> - **Performance-oriented:** Tesla Model Y Performance, Model 3 Performance, Mustang Mach‑E GT, Polestar 2 Performance.  
> 
> These are live dealer listings, so confirm availability, final out-the-door price, accident history, battery condition, and eligibility for any used-EV tax credit before visiting. Cars.com’s current local electric-vehicle listings support the New York-area inventory search.

**Done.** Applied all three filters and came back with real listings, prices, mileage and seller distances -- every one inside 50 miles of 10001 and under $50k.

Panel and mouse: panel MISSING in 2/52 samples; cursor MISSING in 1/52; 2 cursor journeys, avg 513px in 247ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 5. mbta.com -- `da46d875-6b82-4681-9284-653b0c7ae241`

> Book an appointment to apply for a transportation access pass at the Charlie Card store on the first Monday eight months later at any available time from 9:00 am to 12:00 pm, fill in my details (James Smith, james.smith@gmail.com). And do not click "book" directly. Let me review it.

*Hard because:* longest horizon here: find the booking system, a date eight months out, a time window, a multi-field form -- and stop before committing  
*A correct answer contains:* a filled booking form for Mon 5 Apr 2027, 9-12, not submitted

**120s · 34 tools · $0.2092**

`snapshotx17  navigatex1  clickx12  pressx1  fillx2  run_jsx1`

Answer:

> Draft appointment is ready for your review — **not booked**.
> 
> - **Service:** Apply for Transportation Access Pass (TAP) CharlieCard — non-auto approval  
> - **Date/time:** **Monday, April 5, 2027 at 11:45 AM** (Eastern Time)  
> - **Name:** James Smith  
> - **Email:** james.smith@gmail.com  
> - **Location:** MBTA Charlie Service Center, 296 Washington St, Boston  
> 
> The other available times within 9:00 AM–12:00 PM were 11:50 AM and 11:55 AM.

**Done.** The hardest task here and the cleanest result. First Monday eight months out is 5 April 2027 and it got that exactly, picked 11:45 AM inside the 9-12 window, filled James Smith / james.smith@gmail.com, stopped before booking as instructed, and listed the other times still free.

Panel and mouse: panel MISSING in 1/119 samples; cursor MISSING in 1/119; 14 cursor journeys, avg 137px in 49ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 6. flightaware.com -- `a96b564e-dbe9-42c3-9ccf-b4498073438a`

> In the FlightAware Discussions forum, navigate to the FlightAware > General category and open the topic with the most posts or replies.

*Hard because:* navigation into a nested forum, then a comparison across a list  
*A correct answer contains:* the General category opened and the highest-reply topic named

**20s · 7 tools · $0.0435**

`navigatex2  snapshotx3  clickx1  run_jsx1`

Answer:

> Opened **“The Banter Thread”** in **FlightAware > General**. It has **5,518 replies** (5,519 posts total), the most in the category.

**Done.** Into the nested category, compared the list, opened the right topic and quoted the reply count (5,518).

Panel and mouse: panel MISSING in 2/20 samples; cursor MISSING in 2/20; 1 cursor journeys, avg 829px in 772ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 7. store.steampowered.com -- `121ba48f-9e17-48ce-9bc6-a4fb17a7ebba`

> Find Dota 2 game and add all DLC to cart.

*Hard because:* 'all DLC' is open-ended: find the store page, enumerate DLC, act on each  
*A correct answer contains:* the Dota 2 store page, the DLC list, and what ended up in the cart

**17s · 6 tools · $0.0354**

`navigatex1  snapshotx3  clickx2`

Answer:

> Added all available Dota 2 DLC to the Steam cart. The cart contains:
> 
> - **The Dota 2 Official Soundtrack** — **$9.99**
> 
> The other listed DLC, **The Dota 2 Remixes EP**, is free and was not added as a cart item. No checkout or payment step was taken.

**Partly.** Found the store page and put the paid DLC (Official Soundtrack, $9.99) in the cart, and explained that the other listed DLC is free so it never becomes a cart line. 'All DLC' with two items is thin -- correct as far as the page goes, but it did not prove the list was complete.

Panel and mouse: panel MISSING in 1/17 samples; cursor MISSING in 1/17; 2 cursor journeys, avg 219px in 30ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 8. recreation.gov -- `b4f95342-463e-4179-8c3f-193cd7241fb2`

> Find the Next Available dates for Diamond.

*Hard because:* ambiguous target ('Diamond' is several campgrounds) plus an availability grid  
*A correct answer contains:* the Diamond campground page and its next available date(s)

**23s · 7 tools · $0.0571**

`snapshotx4  fillx1  clickx2`

Answer:

> For **Diamond Campground near Spanish Fork, Utah**, the next reservable availability begins **Saturday, August 22, 2026**.
> 
> - **August 18–21:** marked **First-come, first-served** for most sites (not reservable online).
> - **August 22–27:** marked **Available** across the displayed family sites, including A007, A009, A010, A017, A018, A020, A022 and multiple B-loop sites.
> - Site **B057** is reserved on August 18, then first-come, first-served through August 21, and available from August 22.

**Done.** Disambiguated 'Diamond' to Diamond Campground near Spanish Fork, Utah, read the availability grid and gave the next reservable date (22 Aug 2026) with the site numbers behind it.

Panel and mouse: panel up; cursor up; 3 cursor journeys, avg 401px in 235ms. Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 9. apple.com -- `f5d96daf-83a8-4c86-9686-bada31fc66ab`

> Compare iPhone 15 Pro Max with iPhone 14 Pro Max and iPhone 13 Pro Max

*Hard because:* a three-way comparison built through a picker UI, then reading it back  
*A correct answer contains:* the comparison set up with all three models and concrete spec differences

**26s · 0 tools · $0.0796**

Answer:

> ## At a glance
> 
> | Feature | iPhone 15 Pro Max | iPhone 14 Pro Max | iPhone 13 Pro Max |
> |---|---|---|---|
> | Released | 2023 | 2022 | 2021 |
> | Chip | **A17 Pro** | A16 Bionic | A15 Bionic |
> | Build / weight | Titanium, **221g** | Stainless steel, 240g | Stainless steel, 240g |
> | Display | 6.7" OLED, 120Hz, Dynamic Island, Always-On | 6.7" OLED, 120Hz, Dynamic Island, Always-On | 6.7" OLED, 120Hz, notch |
> | Outdoor brightness | 2,000 nits | 2,000 nits | 1,200 nits HDR peak |
> | Main camera | 48MP | 48MP | 12MP |
> | Telephoto | **5× optical** | 3× optical | 3× optical |
> | Port | **USB‑C, USB 3 up to 10Gb/s** | Lightning | Lightning |
> | Main extra control | **Action button** | Ring/Silent switch | Ring/Silent switch |
> | Wireless | Wi‑Fi 6E, Bluetooth 5.3, Thread | Wi‑Fi 6, Bluetooth 5.3 | Wi‑Fi 6, Bluetooth 5.0 |
> | Apple Intelligence | **Yes** | No | No |
> | Apple-rated video playback | Up to 29 hours | Up to 29 hours | Up to 28 hours |
> 
> The iPhone 15 Pro Max is notably lighter—**19g less** than either older model—despite retaining the same large-screen class. ([support.apple.com](https://support.apple.com/en-us/111828))
> 
> ## What changes in practice
> 
> ### 1. Performance and longevity
> The **iPhone 15 Pro Max** is the clear choice for demanding games, intensive photo/video work, and newer on-device features. Its A17 Pro has a 6-core GPU versus the A16’s 5-core GPU and is the only one of these three compatible with Apple Intelligence. ([support.apple.com](https://support.apple.com/en-us/111828?utm_source=openai))
> 

**Failed.** Answered a three-way iPhone comparison with ZERO tool calls -- never opened apple.com at all. The table it produced looks right, but it came from the model's own knowledge, which is exactly what the prompt forbids. The task was to build the comparison on the site.

Panel and mouse: panel up; cursor up; no clicks (nothing to move to). Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

### 10. nfl.com -- `f0b971a1-6831-4b9b-a50e-22a6e47f45ba`

> Please help me find the score record for the Super Bowl of the 2019 NFL season (played in 2020) in the NFL website.

*Hard because:* a fact buried in a stats section, with an off-by-one-year trap  
*A correct answer contains:* Super Bowl LIV, Chiefs 31 - 49ers 20, sourced from nfl.com

**17s · 4 tools · $0.0566**

`navigatex2  snapshotx2`

Answer:

> For the **2019 NFL season’s Super Bowl**—**Super Bowl LIV**, played on **February 2, 2020**—the **Kansas City Chiefs beat the San Francisco 49ers, 31–20**.
> 
> Note: the NFL page I opened was for **Super Bowl LIII**, played February 3, 2019, where the **New England Patriots beat the Los Angeles Rams 13–3**.

**Partly.** Right answer (Super Bowl LIV, Chiefs 31-20 49ers) and unusually honest about provenance: it said outright that the nfl.com page it opened was Super Bowl LIII. So the fact is correct but was not the one verified on the page, which is half the task.

Panel and mouse: panel MISSING in 1/16 samples; cursor up; no clicks (nothing to move to). Question kept in chat: yes. Typed exactly: yes. Panel alive at the end: yes.

## Where the money went

One task cost more than the other nine combined. Task 2 spent **6.1M input tokens** across 41 requests, with the context peaking at **199k tokens** -- against a median of ~90k input tokens elsewhere. The tool counts say why: 26 of its 40 calls were `run_js`, on a page whose scripts return large blobs, and every one of those results stays in the conversation and is re-sent on every subsequent request.

This is the exact trade-off measured earlier in the project and then **deliberately reverted** (PLAN.md section 8, finding 14): capping `run_js` output at 3k and trimming bulky tool results cut a similar run by 71%, and it was reverted because it constrains how freely the agent can explore. That decision stands -- nothing here re-applies it. But the number is now much larger than the one it was made against: $3.68 in a single task, on a task that failed anyway.

The other nine behaved normally: cost tracks tool count almost linearly, and caching carried 80% of input tokens at a tenth of the price.

## What this run changed in the product

Three real bugs, all found by watching rather than by testing -- the panel suite passed 54/54 while every one of them was live.

1. **The panel de-registered itself mid-run.** Re-seeding the injected script removed the old registration before adding the new one; if the add then failed or timed out mid-navigation, the panel was gone from every page after that. Seen as *panel missing in 75 of 118 samples* on task 1, and confirmed on flightaware where `window.__cuaexpBuild` was null while the cursor script -- registered once, never replaced -- still worked on the same page. Now it adds first and only removes the old one once the new id is confirmed.
2. **Pages that steal focus ate what you type.** Against a fixture that grabs focus every 300ms, all eleven keystrokes typed into the chat landed on the page instead. The panel now reclaims focus while you are using it, bounded, and stops the moment you click outside.
3. **A modal focus trap beat that outright.** budget.com's promo modal enforces focus on every change, which no overlay in the same document can win -- a person would lose their typing to it exactly as this did. Two fixes: focus events no longer escape the panel (so a trap never learns focus left), and if focus is held elsewhere while the chat box is in use, keystrokes are taken at window-capture and put into the chat before the page can see them. Task 2 could not even be *asked* before this; the three attempts to type it are in the log.

Plus: the daemon now checks every two seconds that the panel and cursor are still on the current page and reinstalls them if not, and the agent is told today's date -- without it, four of these ten tasks ("next Monday", "the 10th of next month", "eight months later") are unanswerable and the model just picks a plausible week.

# Strategy Proposal: Faster Capital Turnover Without Proportionally More Risk

## The honest framing first

There's no strategy that returns more without taking more risk somewhere — return and
risk are linked, not because of a rule but because of arithmetic (bigger moves in your
favor require bigger moves being possible against you). What *does* exist, and what this
document is actually proposing, are three legitimate ways to make capital work faster
that are **not** the same as "take on more risk per trade":

1. **Turn capital over faster** — the same edge, applied more times per year, compounds
   faster than the identical edge applied once and held for months. Ten 3% winners in a
   quarter beat one 20%-and-hope-it-keeps-going position, *if* the smaller trades have a
   real edge and the fees/slippage don't eat it (paper trading sidesteps that cost, real
   money won't).
2. **Use defined-risk structures instead of naked directional bets** — an options position
   where the max loss is the premium paid is a different risk shape than owning the stock
   outright, even when the underlying view is identical.
3. **Add uncorrelated edges** — a market-neutral trade (pairs/relative-value) makes money
   from a spread converging, not from the market going up. Stacking that alongside
   directional equity picks doesn't multiply risk, it diversifies it.

None of these make a losing edge profitable. They're about *structure*, not about finding
a shortcut. Below are concrete proposals ranked roughly by effort-to-build vs. how much
they actually change the platform's risk/return shape.

---

## Comparison table

| # | Proposal | Turnover | Risk shape | Build effort | Uses existing infra? |
|---|---|---|---|---|---|
| 1 | Shorten hold times / tighten harvest on current picks | Medium→High | Same as today, smaller per-trade | Low | Yes — `auto_selector.py`, `exits.py` |
| 2 | Rotate the auto-selector onto mean-reversion strategies, not just trend | High | Same as today | Low | Yes — `backtest.py` already has 6+ mean-reversion strategies unused by the selector |
| 3 | Sector-rotation overlay via Smart Universe | Medium | Directional, but always in the strongest sector | Medium | Yes — `routers/universe.py` already ranks sectors, just not wired to the selector |
| 4 | Market-neutral pairs/relative-value sleeve | Medium | Lower — market-direction risk removed | Medium-High | Partially — `relval.py` has the math, no execution wiring |
| 5 | Defined-risk options income (covered calls / cash-secured puts) | Medium | Lower per-trade, income-shaped | High | No — `options.py` has GEX only, no chains/orders |
| 6 | Leveraged directional exposure via long options instead of stock | High | **Higher**, not lower — flagged as the one that doesn't fit "not a ton of risk" | High | No |
| 7 | Intraday gamma-level mean reversion (GEX) | Very High | Higher, needs intraday engine | High | Partially — `gex.py` computes levels, no signal/backtest loop |
| 8 | COT crowding as a contrarian filter | N/A (weekly) | Lowers risk on existing picks, not a standalone speed play | Low | Yes — `cot.py` already computes crowding z-scores, not wired to selection |

---

## Proposal 1 — Shorten hold times and harvest winners faster (start here)

**What it is:** The auto-selector currently assigns `ema_cross_9_21` (max hold 30 days) or
`rsi_revert` (max hold 20 days). Positions sit until a stop, target, or the strategy signal
flips — often weeks. The fastest, lowest-risk lever available today is simply tightening
that cycle: smaller ATR multiples on targets (currently 2x stop / 3x target), a shorter
max-hold default, and — since we just added the 15-minute intraday exit checker — winners
that hit target get harvested same-day instead of waiting for the 4pm run, freeing that
capital for the next pick within hours instead of weeks.

**Why it's low risk:** it doesn't change *what* gets bought or *how much* — only how
quickly a winning or losing trade gets closed out and the capital redeployed. Same edge,
faster compounding.

**What it needs:** tuning `STOP_ATR_MULT` / `TARGET_ATR_MULT` in `engine.py` and
`DEFAULT_MAX_HOLD_DAYS`, plus deciding whether tighter targets reduce average win size
enough to hurt the win/loss ratio (worth backtesting via the existing Backtest page before
changing live defaults).

## Proposal 2 — Point the auto-selector at mean-reversion strategies it already has

**What it is:** `backtest.py`'s `STRATEGIES` registry has `zscore_revert`, `cci_revert`,
`stochastic`, `keltner_revert`, and `bollinger_revert` — all short-hold (20-day cap),
buy-the-dip/sell-the-bounce strategies that are currently *never assigned* by
`auto_selector._pick_strategy()`, which only ever picks `ema_cross_9_21` or `rsi_revert`.
Widening that selection to pick whichever of these backtests best on a given ticker (the
Health Check page already runs this kind of backtest-and-compare) turns over positions
faster by construction — mean-reversion trades resolve (hit target or fail) faster than
trend-following ones by design.

**Why it's low risk:** these are strategies already in the codebase, already backtested
individually — this is exposing more of what exists, not inventing new logic.

**What it needs:** extending `_pick_strategy()` (or reusing the "Auto-deploy" backtest-and-pick-the-best logic already built for manual instance creation) to choose from the
full mean-reversion set instead of a hardcoded pair.

## Proposal 3 — Sector-rotation overlay using Smart Universe

**What it is:** `routers/universe.py` already ranks the 11 SPDR sector ETFs by blended
1-month/3-month momentum and can hand back the top sectors' holdings as a candidate list
— but nothing currently feeds that ranking into the auto-selector. Restricting (or
up-weighting) candidates to tickers in the top-2 momentum sectors means capital
continuously rotates toward whatever is currently working, rather than sitting in a name
whose sector has gone cold.

**Why it's a moderate risk change, not a risk-free one:** this is still directional
equity exposure — it doesn't reduce market risk, it concentrates it into strength. That's
a real, if measured, risk shift, not just a turnover improvement.

**What it needs:** wiring `_gather_candidates()`'s existing `_smart_universe_tickers()`
helper (already pulled in as an informational tag) into an actual filter/boost rather than
just a label.

## Proposal 4 — Market-neutral relative-value sleeve

**What it is:** `relval.py` already computes a z-scored spread, rolling correlation, and
mean-reversion half-life for six pairs (QQQ/SPY, GLD/SLV, XLE/CL=F, HYG/TLT, IWM/SPY,
XLF/^TNX). A pairs trade — long the cheap leg, short the rich leg when the spread is
>2 standard deviations from its mean — makes money on convergence, independent of whether
the broad market goes up, down, or sideways. The half-life estimate also gives a genuine
expected holding period, rather than an arbitrary max-hold default.

**Why this is the most interesting "faster and lower-risk" candidate:** it's the only
proposal here that actually **removes** a risk factor (market direction) rather than just
compressing the timeline on the same risk. Paired with proposal 1's tighter harvesting,
this is likely the best risk-adjusted addition on this list.

**What it needs:** the auto-selector and paper engine currently assume one ticker per
instance; a pairs trade needs a linked long+short pair that opens/closes together. That's
a real (not huge) architectural change — plan for a "pair instance" concept rather than
forcing it into the existing single-symbol instance model.

## Proposal 5 — Defined-risk options income (covered calls / cash-secured puts)

**What it is:** Selling a covered call against a held long position (or a cash-secured put
to enter a position you'd buy anyway) generates income on a roughly weekly/monthly cadence
without needing a big directional move — you profit if the stock stays flat, moves up
modestly, or even drops a bit less than the premium collected.

**Why it's lower risk per trade, but a bigger build:** this needs option chain data,
strike/expiry selection, and assignment handling — none of which exist yet (`options.py`
currently only exposes GEX). It's the most "income-shaped" proposal on the list, but the
highest-effort to actually build correctly (assignment simulation, early-exercise
handling, spread/liquidity awareness).

## Proposal 6 — Leveraged long options instead of stock (flagged, not recommended first)

**What it is:** buying a call instead of the underlying gets you the same upside
direction with less capital tied up, in theory freeing that capital to diversify further.

**Why it's flagged rather than recommended:** in practice this is **higher** risk per
dollar deployed, not lower — theta decay, IV crush, and the binary "worthless at
expiration" outcome are real, and it's very easy to accidentally increase portfolio risk
while thinking you've "freed up capital." Including it here for completeness, but it
doesn't fit the "not a ton of risk" requirement without very tight position sizing rules
that would need their own design pass.

## Proposal 7 — Intraday gamma-level mean reversion (GEX)

**What it is:** `gex.py` already computes the zero-gamma flip point and key
support/resistance strikes from dealer positioning. Above the flip point, dealer hedging
tends to dampen volatility (mean-reversion regime); below it, hedging amplifies moves
(trend/breakout regime). A same-day strategy that fades moves into key gamma strikes when
above the flip point, or rides breakouts through them when below it, is a genuinely
different, faster-cycling signal than anything currently in the platform.

**Why it's higher effort and higher risk:** this needs an intraday backtest/signal loop
(we only just added 15-minute *exit* checking, not *entry* signal generation), and
gamma-regime trading is a more specialized, easier-to-get-wrong style than trend/mean-
reversion on daily bars. Worth prototyping in the Backtest page against historical GEX
snapshots before ever running it live.

## Proposal 8 — COT crowding as a contrarian filter (not a speed play by itself)

**What it is:** `cot.py` already flags `crowded_long`/`crowded_short` positioning weekly.
This isn't a turnover proposal — COT updates once a week — but it's a cheap risk-reducer:
skip or downsize new longs in instruments where leveraged money is already crowded long
(squeeze/reversal risk), same logic already used for the "Smart Buy" congressional
contrarian signal. Low effort, meaningfully improves risk-adjusted quality of existing
picks rather than making anything faster.

---

## Deep dive: Pairs Trading Expansion (elaborating Proposal 4)

This section works out Proposal 4 in the depth needed to actually build it: what
universe of stock pairs is eligible, how that universe gets sourced and maintained on an
ongoing basis, and the concrete quantitative rules for knowing a pair is a real
opportunity versus a tempting-looking false signal. All numbers below are pulled live
from the market as of 2026-07-14 using the platform's existing `relval.analyze_pair()`
(2-year daily history, 60-day rolling z-score/correlation window, OU/AR(1) half-life
fit) — not hypothetical figures.

### What "eligible" means for a pairs trade

A stock pair is only tradeable as a statistical-arbitrage pair if three things are true
**simultaneously**:

1. **High rolling correlation of returns** (not just price co-movement) — the two names
   need to actually move together day-to-day, not just share a similar long-run trend.
2. **A confirmed mean-reverting spread** — the OU/AR(1) fit on the log-spread must
   produce a negative beta (i.e., a defined, finite half-life). If the fit comes back
   null, the spread isn't statistically reverting right now — it may be *trending apart*
   for a real fundamental reason, which looks identical to "stretched" on a raw z-score
   but is a completely different (and much riskier) situation.
3. **A short-enough half-life to be worth the capital** — a pair that mean-reverts over
   200+ days ties up capital for a trend-following timeframe while only paying a
   pairs-trade-sized return. Filtering to roughly **<90 days** keeps this in the
   "faster turnover" bucket rather than accidentally becoming a slow directional bet.

None of these are visible from a single z-score number — this is exactly why "|z| > 2"
by itself is not a sufficient signal, demonstrated concretely below.

### Sourcing methodology

Pairs aren't found by scanning the whole market for statistically correlated tickers —
that produces spurious correlations with no business rationale behind them. The
reliable sourcing process is:

1. **Start from business-model similarity, not statistics.** Group candidates by GICS
   sub-industry (or a simpler same-sector-and-similar-revenue-driver heuristic) —
   companies that compete for the same customers, are exposed to the same input costs,
   and get valued on similar multiples. This is the same idea already used by the six
   ETF/cross-asset presets in `relval.py`, just applied to individual equities.
2. **Compute correlation, half-life, and current z-score for every same-sector
   candidate** via the existing `analyze_pair()` function — no new math needed, just a
   wider candidate list than the current 6 presets.
3. **Filter**: correlation ≥ 0.80, half-life defined and < 90 days. This is a real gate,
   not a formality — of the 41 pairs tested below, more than half fail it.
4. **Re-run the whole scan periodically** (weekly is reasonable, matching the COT
   cadence already in the platform) — correlations decay and pairs break. `WBA` was one
   of the original 41 candidates tested (`CVS`/`WBA`, pharmacy retail) and returned a
   stale-cached result because Walgreens was taken private and delisted — a live system
   needs to detect and prune dead/delisted tickers from the pair universe automatically,
   not just at initial setup.
5. **Exclude event-driven divergences.** `ORCL`/`IBM` (enterprise software/hardware,
   once a reasonable pair) now shows correlation of just 0.405 — the two businesses have
   structurally diverged (Oracle's cloud/AI infrastructure re-rating vs. IBM's slower
   profile) and no longer belong in the tradeable universe, even though their spread
   z-score is currently -2.28 and looks "stretched."

### Candidate universe tested (41 pairs, 14 sectors, live data)

Ranked by correlation; pairs meeting the full eligibility bar (corr ≥ 0.80, half-life
defined and < 90 days) are marked ✅. This is the actual output of the sourcing process
in step 2-3 above, not a curated-after-the-fact list:

| Pair | Sector | Corr | Half-life (d) | Current z | Eligible? |
|---|---|---|---|---|---|
| AMAT / LRCX | Semi equipment | 0.92 | 170 | +2.08 | ❌ half-life too long |
| COP / EOG | E&P energy | 0.92 | 46 | -1.35 | ✅ |
| DAL / UAL | Airlines | 0.91 | 60 | -0.49 | ✅ |
| GS / MS | Investment banks | 0.91 | **26** | -0.21 | ✅ (fastest half-life in the set) |
| XOM / CVX | Integrated energy | 0.91 | 43 | -1.01 | ✅ |
| HD / LOW | Home improvement retail | 0.90 | 92 | +1.91 | ❌ half-life just over 90d |
| KLAC / LRCX | Semi equipment | 0.90 | 183 | +0.79 | ❌ half-life too long |
| LUV / UAL | Airlines | 0.88 | 52 | -0.24 | ✅ |
| BAC / WFC | Money-center banks | 0.88 | 50 | **+1.55** | ✅ — watchlist |
| VZ / T | Telecom | 0.86 | 93 | +0.56 | ❌ half-life just over 90d |
| TXN / ADI | Analog semis | 0.84 | 105 | +1.04 | ❌ half-life too long |
| DAL / AAL | Airlines | 0.84 | 84 | -0.98 | ✅ |
| JPM / BAC | Money-center banks | 0.83 | 30 | **-1.63** | ✅ — watchlist |
| CRM / ADBE | SaaS software | 0.82 | 45 | -0.29 | ✅ |
| V / MA | Payment networks | 0.82 | 73 | +0.41 | ✅ |
| PGR / ALL | P&C insurance | 0.81 | **null** | -2.10 | ❌ **not mean-reverting — see below** |
| AXP / COF | Consumer credit | 0.81 | 35 | +1.17 | ✅ |
| CL / PG | Household products | 0.80 | 54 | +0.99 | ✅ |
| UPS / FDX | Package delivery | 0.79 | 209 | +2.08 | ❌ corr borderline, half-life too long |
| ... | (24 more, correlation 0.10–0.77) | | | | ❌ below correlation bar |

*(Full 41-pair output available on request — the table above is the eligible-or-close set;
the remaining ~24, including `KO`/`PEP` at 0.54 correlation and `AAPL`/`MSFT` at 0.27,
failed the correlation gate and were dropped. `KO`/`PEP` in particular is a good example
of a "textbook" pair that real current data doesn't actually support anymore.)*

### Why raw z-score alone is a trap — a live example

Four pairs in this scan showed a z-score beyond ±2 (the classic "stretched" trigger).
**Only one of the four holds up under the full filter:**

- **`AMAT`/`LRCX`** (+2.08): correlation is excellent (0.92) but half-life is 170 days —
  this "stretched" reading could take five-plus months to resolve. Trading it ties up
  capital like a directional position, not a pairs trade.
- **`PGR`/`ALL`** (-2.10): correlation is fine (0.81), but the half-life fit returned
  **null** — meaning the AR(1) beta came back non-negative, i.e. the model does not
  currently confirm this spread is mean-reverting at all. This is the single most
  important thing this section demonstrates: a z-score outside ±2 is necessary but
  **not sufficient**. Trading this pair on the z-score alone would be betting on reversion
  the data doesn't actually support.
- **`ORCL`/`IBM`** (-2.28): correlation has collapsed to 0.41 — the pair itself is no
  longer a valid pair (see sourcing step 5 above). The z-score is irrelevant here.
- **`UPS`/`FDX`** (+2.08): correlation is borderline (0.79) and half-life is 209 days —
  fails on two of three criteria.

**Net result from today's scan: zero pairs currently meet the full "trade now" bar**
(|z| ≥ 2 AND correlation ≥ 0.80 AND half-life < 90 days). That's a realistic, honest
outcome, not a failure of the method — good pairs-trade entries are supposed to be
infrequent; that's what makes them worth waiting for. `BAC`/`WFC` (+1.55, half-life 50
days) and `JPM`/`BAC` (-1.63, half-life 30 days) are the closest current watchlist
candidates — both pass every structural filter and just need the spread to widen a bit
further.

### Entry / exit / risk rules

- **Entry:** open the pair only when all three hold together: |z| ≥ 2.0, correlation ≥
  0.80, half-life defined and < 90 days. Go long the cheap leg / short the rich leg in
  dollar-neutral size (equal notional both sides, not equal share count — the two stocks
  usually trade at very different prices).
- **Position sizing:** size against the half-life, not a fixed hold-time default the way
  single-stock instances do today — a 26-day half-life pair (GS/MS) should size and
  expect resolution very differently than a 90-day one.
- **Target / exit:** close when the z-score crosses back through 0, or take partial profit
  at ±0.5 — waiting for a full round-trip through the mean gives up realized gains to
  noise on names with a genuinely short half-life.
- **Stop-loss / structural-break detection:** this is the one novel risk control this
  strategy needs that single-stock trades don't. If the z-score keeps widening past
  roughly ±3.5, or the rolling correlation drops below the entry threshold (0.80) while
  the trade is open, treat that as the relationship breaking down (an `ORCL`/`IBM`-style
  divergence happening in real time) and exit — do **not** average down into a widening
  spread on the assumption it must revert.
- **Liquidity filter:** restrict the eligible universe to large-cap, high-average-volume
  names (everything tested above already qualifies) — the short-side leg specifically
  needs to be easy to borrow/short in a real account.
- **Universe refresh:** re-run the full correlation/half-life scan on a fixed cadence
  (weekly is reasonable) and drop any pair that no longer clears the eligibility bar,
  the same way `ORCL`/`IBM` and `WBA` needed to be pruned in this one-time test.

### What this needs architecturally

The math is already built (`relval.py`) — what doesn't exist yet:

1. **A wider, maintained pair universe** instead of the current 6 fixed presets — the
   41-pair candidate list above (extendable to more sectors) run on a schedule.
2. **A "pair instance" concept.** The auto-selector and paper engine currently assume one
   ticker per instance; a pairs trade needs two linked legs (one long, one short) that
   open and close together as a unit, with P&L measured on the combined spread, not each
   leg independently.
3. **Its own portfolio bucket**, following the same pattern as the existing Smart Buy /
   Technical-Sustained 50/50 balance — a "Pairs" bucket alongside those two, so
   market-neutral capital doesn't compete for the same allocation slots as directional
   picks (they have fundamentally different risk, so they shouldn't cannibalize each
   other's capital share).
4. **Structural-break monitoring on open pairs**, run through the same intraday
   15-minute check the platform already added for stop/target exits — a pair's
   correlation breaking down mid-trade is exactly the kind of thing that shouldn't wait
   until the 10am/4pm run to catch.

---

## Suggested order of attack

1. **Proposal 1** (tighter harvest) — hours of work, no new risk surface, immediate effect.
2. **Proposal 2** (use the mean-reversion strategies already built) — low effort, directly
   increases turnover.
3. **Proposal 8** (COT contrarian filter) — low effort, free risk reduction, do alongside 1-2.
4. **Proposal 3** (sector rotation) — moderate effort, a real (if bounded) risk-shape change,
   worth backtesting before wiring live.
5. **Proposal 4** (pairs/relative-value) — the best long-term risk-adjusted addition, but
   needs a real architecture change (paired instances) — treat as its own project. See
   "Deep dive: Pairs Trading Expansion" above for the sourced universe and entry rules.
6. **Proposal 5** (options income) — biggest build, do once chain data infra exists.
7. **Proposal 7** (GEX intraday) — prototype in backtest only, don't run live without
   real validation.
8. **Proposal 6** (leveraged long options) — hold off; revisit only with explicit position-
   sizing rules that actually bound the downside.

## Caveats worth stating plainly

- Everything here is scoped to the paper account. None of it should move to real capital
  without watching it run in paper for a meaningful stretch first.
- Faster turnover means more *decisions*, which means more chances for a flawed signal to
  compound losses faster too — the intraday exit checker cuts both ways.
- If real money ever gets involved and any of this trades same-day round trips, remember
  the $25k pattern-day-trader threshold discussed earlier — several of these proposals
  (1, 2, 7) increase turnover in a way that could bump into that rule on a sub-$25k account.
- This is a planning document, not financial advice — it's a menu of engineering options
  for the platform, evaluated on risk/effort/turnover, not a guarantee any of them make
  money.

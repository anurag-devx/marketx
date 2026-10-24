# Market Lab — Phase 1 plan

Goal: each evening, for every Nifty 500 stock, estimate (1) the chance of a move of at least X% tomorrow and (2) its
direction, by finding the most similar past situations and looking at what happened next. Show what drives each call
as an influence map. Phase 1 = decisions made **after the close**, for the next session.

## Questions it answers
| Selector | Options |
|---|---|
| When | After close (Phase 1). Before open and During the day come later (need overnight data / live bars). |
| Move | Close-to-close (incl. the overnight gap) · Open-to-close (tradable intraday) · Overnight gap |
| Size | at least 0.5% · 1% · 2% · 3% |

## Scenario (what "similar" means) — known by 21:00 IST on day t
Each stock-day is described by about 18 numbers in 9 groups. Every group is a node on the influence map.

| Node | Features | Source |
|---|---|---|
| Price action | today's return, 5-day return (both in units of the stock's own daily volatility), opening gap, position in 20-day range | daily prices |
| Volume | volume vs its 20-day normal, 5-day vs 50-day volume | daily prices |
| Volatility | 20-day realised volatility, ATR % of price | daily prices |
| Market | Nifty today and 5-day return, India VIX vs its 1-year median, share of stocks up today | index, prices |
| Sector | sector's average return today and over 5 days, minus the Nifty | prices, universe |
| Results | results filed during today's session; results filed after the close (before 21:00) | NSE filings, XBRL results |
| News | headlines naming the stock since yesterday 21:00 (count), net tone | news store (tagged) |
| Filings | non-results filings since yesterday 21:00 (count), any material type (order, acquisition, fund raise, rating, legal, insider, management) | NSE filings |
| Macro | Brent, USD-INR, US VIX, US 10-year yield: previous day's change (lagged one day so it's known) | FRED |

F&O and ownership are not in the scenario yet (about 1 year / 4 quarters of history); they appear in the evidence.

## Matching and estimates
- Features are standardised on past data only; each group carries equal weight (so 4 price features don't outvote 1 results flag).
- For each stock tonight, the 150 nearest past stock-days (any stock, outcome already known) are found with a k-d tree.
- **Chance** of a move ≥ X% = share of those cases that moved that much, shrunk toward the stock's **usual** chance
  (its own frequency over the past 250 sessions) when the cases are few or far away.
- **Lean** = share of the cases that went up, shrunk toward 50%; "up-lean" / "down-lean" only when clearly away from 50%,
  otherwise "unclear".
- **Confidence** = how close the cases are (distance vs history) combined with the combination's tested skill.

## Influence map (output)
For each node: re-run the match with that group set to "typical" and compare.
- Colour: does the group push the lean up (green) or down (red)? Grey if the difference is small.
- Thickness: size of that push (weak / medium / strong).
- Solid vs dashed: solid only if dropping that group made the out-of-sample test worse (it has earned its place); dashed otherwise.
- Side panel: the group's values today, its effect on the chance and the lean, and the evidence cards behind it.

## Testing (Scorecard)
Walk-forward by year (2019 onwards): match only against years before the test year, score every 5th day.
- Top-20 hit rate (moved ≥ X%) vs the usual rate of those same stocks; ROC-AUC; Brier skill vs the usual rate.
- Direction: hit rate where a lean is given, and how often a lean is given.
- Group test: drop each group, re-score; a group is "tested" if it helps.
A combination is labelled **not ready** until its test has run; **no skill** if it doesn't beat the usual rate.

## Agents
| Agent | Role | Runs |
|---|---|---|
| Reader | Tags news and filings (type, tone, stocks): the existing news tagger and filing categories | daily |
| Case explainer | Reads the closest past cases' headlines and filings and says what drove them, and whether today's catalyst is the same kind | on demand |
| Skeptic | Lists what would break the call (thin cases, upcoming event, conflicting nodes, data gaps) | on demand |
| Bull / bear debate | The existing Debate Lab, kept as a tab | on demand |
| Judge | Rules, not a model: the numbers above. The model only words the summary; numbers are checked | — |
Without the local model, the explainer and skeptic fall back to templates.

## Screens
Market Lab replaces the Debate Lab entry: Move Radar · Stock analysis (influence map first) · Scorecard · Debate (classic) · How it works.

## Jobs
- `market-lab` (daily, last step of Fetch + run all; optional): build tonight's radar and maps.
- `market-lab-test` (on demand, ~20–40 min): walk-forward scorecard and group tests.

## Limits
- Today's Nifty 500 only: stocks that left the index are missing (flatters a little).
- News and filings history is thin before 2025 on most installs; their nodes stay dashed until the test shows value.
- Prices are split-adjusted; dividends not added back.

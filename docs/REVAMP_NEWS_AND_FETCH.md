# Revamp: evidence-only news tagging + a complete, correct daily fetch

Principle: the strategy is only as strong as the research. A wrong tag is worse than no tag.
Every label must be backed by text in the headline itself; when it is not, show the plain headline with its link.

## What the logs and data showed (23 Oct 2026)

News store, latest 150 headlines:
- Stocks tagged from ordinary words: SILVER, HEALTHCARE, DIVIDEND, WEALTH, GLOBAL, MOMENTUM, GERMAN, SMALLCAP.
  Cause: data/instruments.csv "names" are just tickers, so every NSE ticker became a name alias.
- Wrong company from a shared first word: "Paras Healthcare IPO" -> PARAS, "Jyoti Structures" -> JYOTICNC,
  "Core4 Engineers" -> ENGINERSIN, "Price Action:" -> ACE.
- Broker counted as the subject: "...Nuvama sees 91% upside" -> NUVAMA.
- Summary fallback tagged a headline that names no stock: "Nomura, Axis initiate coverage on 8 stocks" -> VEDL.
- Type misses: "Buy Sagility; target of Rs 60: Prabhudas Lilladher" -> Other (it is a broker view);
  "Jio IPO ... brokerage views" -> Broker view (it is IPO); "Cyient jumps 10% post Q2, dividend" -> Dividend.
- Tone from word counts: "Bernstein slashes Nifty target ... won't trigger a rally" -> positive;
  "Rupee rises" -> positive (not a stock); "PAT may rise" (a preview) -> positive; "Swiggy among F&O stocks with rise in OI" -> positive.

Fetch + run all (started 09:57):
- Prices took 82 min; Angel One rejected 704 of 1,155 requests ("exceeding access rate"); 43 stocks
  (HDFCBANK, AXISBANK, WIPRO, ASIANPAINT, PNB ...) gave up after 5 quick retries (~1 min) and kept yesterday's data.
  They then silently dropped out of the screener and agents for the day. Angel's forum (Aug-Sep 2026) reports the
  same rejections at very low request rates, with blocks lasting minutes: short back-off cannot ride it out.
- ICICIPRULI missing: no "-EQ" entry in Angel's scrip master today; the reason was not reported.
- GPR failed "needs xlrd": the job ran with the dashboard's interpreter, which was not the project .venv (the venv has xlrd).
- Market report: local model ReadTimeout (300 s per call) with retries; 7 sections x up to 4 calls can run 2+ hours.

## Part A - news tagging (core/news.py, screener/news_scan.py, ui)

1. Stock tags only from: the company's full name, a curated alias (BUILTIN_ALIASES / data/news_aliases.csv),
   a distinctive first-two-words or first-word short name of a Nifty 500 company, an upper-case Nifty 500 ticker
   (minus common acronyms: RBI, IPO, GST, SEBI, FII, EV, AI ...), or an explicit "NSE: XYZ" in the feed's metadata.
   instruments.csv is never used for name matching.
2. Short names (not the full name) must stand alone: rejected when the next word is another capitalised word
   (Paras *Healthcare*, Jyoti *Structures*) unless it is Shares / Stock / Q1-Q4 / FY / Ltd / an acronym; single-word
   short names are also rejected when the previous word is capitalised (*Price* Action, *Core4* Engineers).
3. Broker names (Nuvama, Motilal Oswal, JM Financial, ICICI Securities ...) are not tagged when they appear as the
   source of a view ("... : Motilal Oswal", "Nuvama sees ...").
4. Headline only. No summary fallback.
5. Type only from explicit phrases, with the matched phrase kept as "why"; blank when nothing matches.
   Order: IPO / listing > Results preview > Results > Broker view > Order / contract > Block / bulk deal / stake >
   Acquisition / merger > Fund raising > Dividend / bonus / split / buyback > Legal / regulatory > Management change >
   Market (index) > Economy / commodities > Price move.
6. Tone only when exactly one stock is tagged and the headline is not a round-up, from a direction phrase about it
   (shares up/down x%, profit rises/falls, beat/miss, upgrade/downgrade, target raised/cut, order win, penalty/raid,
   zero observations ...). Previews, questions and "may/could/ahead of" outcomes get no tone. Conflicting phrases -> no tone.
   Tone and type each carry the phrase that set them.
7. Stored headlines are re-tagged automatically when the rules' version changes.
8. Screener -> News shows each headline as a clickable link (same look as Market -> News). Under it, only what has
   evidence: stocks, type (why), tone (why). Nothing otherwise. Filters gain "no type" / "no tone".
9. Other screener tabs: announcements keep NSE's own symbol (strong evidence); Daily moves and Catalyst use the new tags.
   Every screener tab states which stocks have no bar for the latest day and were left out.

## Part B - fetch + run all

1. Angel pacing adapts: 1 request/second to start; on a throttle the whole client pauses (20 s, doubling to 5 min) and
   slows (up to 3 s/request), then speeds back up after a run of successes. Throttle waits do not use up a stock's retries;
   one request may wait up to 15 minutes in total before it is reported as failed.
2. After the first pass, failed stocks are retried in up to 3 more passes, after 2, 5 and 10 minute cool-downs.
3. Anything still missing is written to data/stale_symbols.json with the reason (download failed / not in Angel's
   list / no trade) and its last bar, shown in the log summary, the sidebar data check and the screener.
4. A symbol with no "-EQ" entry falls back to its "-BE" (trade-for-trade) series, and the log says so; if it has none,
   the log says it is not in Angel's list.
5. Jobs always run with the project's .venv Python when it exists; the log's first line names the interpreter.
6. Market report: 240 s per model call, a 25-minute budget for the whole report; after two timeouts, or once the budget
   is spent, the remaining sections use the code template (marked as such). No step can hang the chain for hours.

## Verification
- Unit tests for every misfire above (each must produce no tag or the right one), plus known-good headlines that must
  still tag (Welspun Corp, Swiggy, HDFC Bank, Infosys, Kotak Mahindra Bank, Tata Motors, SBI Life vs SBI).
- Re-tag the real 28k-headline store on the Mac; count tags before/after; read 150 recent headlines by eye.
- Provider tests with a fake API that throttles for minutes: no stock lost, no stale data passed off as new.
- Full test suite in the container and on the Mac.

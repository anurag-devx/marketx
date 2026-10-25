# Stock Desk

On-demand deep dive for chosen stocks: runs everything MarketX computes for one stock, as of a day, and keeps your notes.

- **Open**: Main → Stock Desk (watchlist), or "Open in Stock Desk ↗" on a stock page / Market Lab stock analysis.
  Each stock opens in its own browser tab (`#/desk/SYMBOL`); compare 2–4 at `#/desk-compare/A,B`.
- **Analyse** (job `desk-analyse`, about 30–60 s per stock): tops up 5-minute bars, then builds a snapshot
  (`desk/analyse.py`) of: price · Market Lab (influence map frames, verdicts for every move and size, closest cases) ·
  track record (graded calls from Review) · technical (evidence, patterns, plans) · intraday habits (60 sessions) ·
  event reactions (results, material filings, busy news) · seasonality · fundamentals · ownership · F&O · peers (same
  industry, closest traded value) · relative strength (sector index or peers, Nifty) · news & filings (30 days) ·
  upcoming events · reasons for / against (each with its source section).
- **Point in time**: an as-of date cuts every input at that day (news / filings at 21:00 IST). Snapshots are kept in
  `data/desk/<SYMBOL>/snaps/` and can be reopened from the header.
- **Notes** (right panel, `desk/notes.py`): plain rich text, autosaved and versioned; 📌 pins numbers into the note with
  their date; tags; decision log (watching / entered / exited / skipped / note) that records what the Desk said at that
  moment; after the chosen number of sessions a review prompt shows what happened (and whether the swing plan's stop or
  first target came first) and asks for a lesson. Notes search across stocks on the Desk page.
- **AI brief** (`desk/brief.py`): local model, facts only, sections cited, numbers checked; otherwise a facts-only summary.
- Everything stays on this Mac (`data/desk/`). No orders; no new predictive claims: the tested engines, in more depth.

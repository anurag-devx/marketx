"""Balance sheet / cash flow parsing and point-in-time valuation."""
import numpy as np, pandas as pd
import pytest
from core import fundamentals as F
from research import valuation as VL

XB = """<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:c="http://www.sebi.gov.in/xbrl/in-capmkt">
<xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="x">X</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="FourD"><xbrli:entity><xbrli:identifier scheme="x">X</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="OneI"><xbrli:entity><xbrli:identifier scheme="x">X</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
<c:RevenueFromOperations contextRef="OneD">1000</c:RevenueFromOperations><c:ProfitLossForPeriod contextRef="OneD">100</c:ProfitLossForPeriod>
<c:ProfitLossForPeriod contextRef="FourD">190</c:ProfitLossForPeriod>
<c:PaidUpValueOfEquityShareCapital contextRef="OneD">500</c:PaidUpValueOfEquityShareCapital><c:FaceValueOfEquityShareCapital contextRef="OneD">10</c:FaceValueOfEquityShareCapital>
<c:Assets contextRef="OneI">9000</c:Assets><c:EquityAttributableToOwnersOfParent contextRef="OneI">4000</c:EquityAttributableToOwnersOfParent>
<c:BorrowingsCurrent contextRef="OneI">300</c:BorrowingsCurrent><c:BorrowingsNoncurrent contextRef="OneI">700</c:BorrowingsNoncurrent><c:CashAndCashEquivalents contextRef="OneI">200</c:CashAndCashEquivalents>
<c:CashFlowsFromUsedInOperatingActivities contextRef="FourD">250</c:CashFlowsFromUsedInOperatingActivities><c:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities contextRef="FourD">80</c:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities>
</xbrli:xbrl>"""


def test_parse_reads_quarter_balance_sheet_and_ytd_cash_flow():
    r = F.parse_xbrl(XB.encode(), "2026-09-30")
    assert r["status"] == "ok" and r["profit"] == 100                     # quarter, not the 6-month 190
    assert r["assets"] == 9000 and r["equity_owners"] == 4000 and r["bs_date"] == "2026-09-30"
    assert r["cfo"] == 250 and r["capex"] == 80 and r["cf_months"] == 6 and r["paid_up"] == 500 and r["face_value"] == 10


def test_valuation_is_point_in_time_and_never_guesses():
    res = pd.DataFrame([dict(F.parse_xbrl(XB.encode(), "2026-09-30"), filing_id="a", symbol="X", period_end="2026-09-30", filed_at="2026-10-20 18:00", consolidated=False)])
    bs = VL.balance_sheets(res)
    assert bs.debt_cr.iat[0] == pytest.approx(1000 / 1e7) and bs.shares.iat[0] == 50 and bs.fcf_cr.iat[0] == pytest.approx(170 / 1e7)
    q = pd.DataFrame({"symbol": "X", "basis": "standalone", "period_end": pd.to_datetime(["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]),
                      "filed_at": pd.to_datetime(["2026-01-20 00:00", "2026-04-20 00:00", "2026-07-20 00:00", "2026-10-20 18:00"]), "profit_cr": [1e-5] * 4})
    before = VL.valuation("X", 100.0, "2026-10-19", bs, q)
    assert before.get("note")                                              # balance sheet not filed yet
    v = VL.valuation("X", 2e6, "2026-10-21", bs, q)
    assert v["market_cap_cr"] == pytest.approx(50 * 2e6 / 1e7) and v["pe"] == pytest.approx(v["market_cap_cr"] / 4e-5)
    assert v["roe_pct"] == pytest.approx(100 * 4e-5 / (4000 / 1e7))
    v2 = VL.valuation("X", 2e6, "2026-10-21", bs, q.iloc[:3])
    assert v2["pe"] is None and v2["ttm_profit_cr"] is None                # fewer than 4 quarters: blank, not estimated

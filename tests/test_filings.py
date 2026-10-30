"""Filing links: Alphabet's Q3 2026 earnings release (8-K Exhibit 99.1) and its SEC companyfacts,
saved as fixtures (the JSON trimmed to the tags the History tab reads). No live calls."""
import json
import pathlib

import pandas as pd
import pytest
import requests

from agent import agent as dash, filings, sheets, validate
from test_dashboard import _click, _components, _converse, _messages, _text, fake  # noqa: F401  (fake is a fixture)

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
PAGE = (FIX / "goog_ex991_q3_2026.htm").read_bytes()
FACTS = json.loads((FIX / "companyfacts_goog_trimmed.json").read_text())
URL = "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000080/googexhibit991q32026.htm"


@pytest.fixture(scope="module")
def release():
    tabs, raw, _ = filings.tables(PAGE)
    return {t.name: t for t in tabs}, raw


def _row(tab, item):
    return tab.df[tab.df["Line item"] == item].iloc[0]


def _without_q3_10q(facts):
    """The feed as it was before the 10-Q: the release comes out first."""
    out = json.loads(json.dumps(facts))
    for tag in out["facts"]["us-gaap"].values():
        tag["units"] = {u: [e for e in es if e.get("filed", "") < "2026-10-29"] for u, es in tag["units"].items()}
    return out


@pytest.fixture
def sec(monkeypatch):
    """sec.gov served from the fixtures; records the URLs asked for."""
    asked = []

    def fetch(url):
        asked.append(url)
        if url == URL:
            return PAGE
        if url == filings.FACTS_URL.format(cik=1652044):
            return json.dumps(FACTS).encode()
        raise ValueError("that link doesn't open a page on sec.gov (404). Check it opens in a browser")
    monkeypatch.setattr(filings, "fetch", fetch)
    filings._facts.clear()
    dash._books.clear()
    return asked


# --- reading the release ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Quarter Ended September 30, 2025", "Q3 2025"), ("Quarter Ended September 30, 2026", "Q3 2026"),
    ("Year To Date September 30, 2026", "YTD Sep 2026"), ("As of December 31, 2025", "Dec 31, 2025"),
    ("As ofSeptember 30, 2026 (unaudited)", "Sep 30, 2026"), ("Quarter Ended Q4 2025", "Q4 2025"), ("TTM Q3 2026", "TTM Q3 2026"),
    ("Three Months Ended March 31, 2026", "Q1 2026"), ("Year Ended December 31, 2025", "FY 2025"),
    ("Twelve Months Ended September 30, 2026", "TTM Sep 2026"),
    ("Quarter Ended September 30, 2026 Less FX Effect", "Q3 2026 Less FX Effect"), ("Segment", "Segment")])
def test_period_label(raw, expected):
    assert filings.period_label(raw) == expected


@pytest.mark.parametrize("raw,expected", [("102,346", (102346.0, False)), ("(1,426)", (-1426.0, False)), ("25 %", (25.0, True)),
                                          ("—", (0.0, False)), ("2.87", (2.87, False)), ("$", None), ("Revenues", None)])
def test_value(raw, expected):
    assert filings._value(raw) == expected


def test_revenue_table_opens_first_with_groups_and_change(release):
    tabs, _ = release
    lead = next(t for t in tabs.values() if t.lead)
    assert lead.name == "Revenues, Traffic Acquisition Costs (TAC)" and lead.view == "tree"
    assert list(lead.df.columns) == ["Line item", "Group", "Q3 2025 ($M)", "Q3 2026 ($M)", "Change ($M)", "Change %"]
    # Subtotals are gone, so the items add up to the reported total revenues.
    assert "Google advertising" not in set(lead.df["Line item"]) and "Total revenues" not in set(lead.df["Line item"])
    assert lead.df["Q3 2026 ($M)"].sum() == 127_577 and lead.df["Q3 2025 ($M)"].sum() == 102_346
    cloud, search = _row(lead, "Google Cloud"), _row(lead, "Google Search & other")
    assert (cloud["Q3 2026 ($M)"], cloud["Change %"]) == (30_709, 102.6)
    assert search["Group"] == "Google Services" and cloud["Group"] == "Google Cloud"
    assert pd.isna(_row(lead, "Hedging gains (losses)")["Change %"])  # no growth rate from a loss
    assert any("subtotal" in n for n in lead.notes) and any("other units" in n for n in lead.notes)  # employees left out
    p = sheets.profile(lead)
    assert p.measures[0] == "Q3 2026 ($M)" and "Group" in p.dimensions and p.label == "Line item"


def test_statements_drop_totals_ytd_and_per_share_rows(release):
    tabs, _ = release
    income = tabs["Consolidated Statements Of Income"]
    assert "Q3 2026 ($M)" in income.df and not any(c.startswith("YTD") for c in income.df.columns)
    items = set(income.df["Line item"])
    assert {"Revenues", "Cost of revenues", "Research and development"} <= items
    assert not items & {"Net income", "Income from operations", "Total costs and expenses", "Diluted net income per common share"}
    assert _row(income, "Cost of revenues")["Group"] == "Costs and expenses"
    # Repeated line items under two grand totals become two tabs.
    assert {"Consolidated Balance Sheets: Assets", "Consolidated Balance Sheets: Liabilities and stockholders’ equity",
            "Segment Results: Revenues"} <= set(tabs)
    assert _row(tabs["Consolidated Balance Sheets: Assets"], "Cash and cash equivalents")["Sep 30, 2026 ($M)"] == 56_334
    seg = tabs["Segment Operating Results"]  # the same table appears twice in the release; kept once
    assert _row(seg, "Other Bets")["Q3 2026"] == -2047 and "Segment Results: Income from operations" not in tabs


# --- history -------------------------------------------------------------------------------

def test_history_quarters_from_the_data_feed(release):
    _, raw = release
    h = filings.history(FACTS, raw)
    df = h.df.set_index(h.df["Quarter"].map(lambda t: sheets.label(t, "quarter")))
    assert h.style == "trend" and df.index[0] == "Q1 2022" and df.index[-1] == "Q3 2026" and len(df) == 19
    assert df.loc["Q3 2026", "Revenue ($M)"] == 127_577 and df.loc["Q3 2026", "Net income ($M)"] == 26_468
    # Cash flows are filed year to date; the quarters are differences. They match the release's free cash flow table.
    assert df.loc[["Q4 2025", "Q1 2026", "Q2 2026", "Q3 2026"], "Free cash flow ($M)"].tolist() == [24_551, 10_116, -5_855, -9_065]
    assert df.loc["Q4 2025", "Diluted EPS ($ per share)"] == pytest.approx(2.82)  # FY less Q1-Q3
    p = sheets.profile(h)
    assert p.time == "Quarter" and p.grain == "quarter" and p.measures[0] == "Revenue ($M)" and p.dimensions == ["Calendar year"]


def test_history_takes_the_newest_quarter_from_the_release(release):
    _, raw = release
    h = filings.history(_without_q3_10q(FACTS), raw)
    last = h.df.iloc[-1]
    assert sheets.label(last["Quarter"], "quarter") == "Q3 2026"
    assert (last["Revenue ($M)"], last["Operating income ($M)"], last["Net income ($M)"], last["Diluted EPS ($ per share)"]) == \
        (127_577, 40_576, 26_468, 2.12)
    assert any("Q3 2026 comes from this filing" in n for n in h.notes)


def test_links():
    assert filings.link_in(f"will our sol able to create dashboard for this {URL}.") == URL
    assert filings.cik_of(URL) == 1652044
    assert filings._document("https://www.sec.gov/ix?doc=/Archives/edgar/data/1652044/x/goog-20260930.htm") == \
        "https://www.sec.gov/Archives/edgar/data/1652044/x/goog-20260930.htm"
    assert " ".join(filings.without_link(f"dashboard for {URL} please").split()) == "dashboard for please"


def test_only_sec_links_are_fetched():
    with pytest.raises(ValueError, match="sec.gov links"):
        filings.fetch("https://example.com/report.htm")
    with pytest.raises(ValueError, match="sec.gov links"):
        filings.fetch("https://www.sec.gov.evil.example/x.htm")


def test_unreachable_sec_says_what_to_open_up(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("blocked")
    monkeypatch.setattr(filings.requests, "get", boom)
    monkeypatch.setattr(filings._throttle, "gap", 0)
    with pytest.raises(ValueError, match="Agent Engine.*www.sec.gov and data.sec.gov"):
        filings.fetch(URL)


# --- the agent end to end ------------------------------------------------------------------

def test_pasted_link_opens_the_dashboard_and_later_turns_reuse_it(fake, sec):
    turns = _converse([
        _text(f"will our sol able to create dashboard for this {URL}"),
        _text("History as a trend"),
        _click("tabs", {"prompt": "Show me the other tabs"}),
        _click("review", {"prompt": "Review my file"}),
        _click("pdf", {"sheet": "History", "style": "trend", "measure": "Revenue ($M)", "dim": "Calendar year"}),
    ])
    first = _messages(turns[0])
    validate.check(first)
    opened = _components(first)
    html = opened["frame"]["htmlContent"]
    assert opened["root"]["component"] == "Canvas" and '"view":"tree"' in html
    assert "Alphabet Inc. Q3 2026 earnings release" in html
    reply = turns[0][0].content.parts[0].text
    assert "Revenues, Traffic Acquisition Costs (TAC) tab is open" in reply and "**History** tab with 19 quarters (Q1 2022 to Q3 2026)" in reply
    assert "review" not in opened
    assert "Trend + forecast of Revenue ($M) from the History tab" in turns[1][0].content.parts[0].text
    tabs_c = _components(_messages(turns[2]))
    assert any(c.get("component") == "MaterialButton" and "History" in json.dumps(c) for c in tabs_c.values()) and "review" not in tabs_c
    assert "file check is for uploaded spreadsheets" in json.dumps(_messages(turns[3]))
    pdf_parts = [p for p in turns[4][0].content.parts if p.inline_data and p.inline_data.mime_type == "application/pdf"]
    assert pdf_parts and pdf_parts[0].inline_data.data.startswith(b"%PDF")
    assert sec.count(URL) == 1  # read once, then served from the cache


def test_bad_link_gets_a_plain_error(fake, sec):
    turns = _converse([_text("https://www.sec.gov/Archives/edgar/data/1/missing.htm")])
    msgs = _messages(turns[0])
    validate.check(msgs)
    assert "404" in turns[0][0].content.parts[0].text and "review" not in _components(msgs)

"""Company filings from a link: paste an SEC EDGAR filing, get the same dashboard as for a spreadsheet.

The user pastes a link such as an 8-K earnings release (Exhibit 99.1) or a 10-Q. The page is
fetched from www.sec.gov and every financial table on it becomes a tab, read the way an analyst
would: "$" and "%" in their own cells, "(123)" as negative, "—" as zero, footnote marks dropped,
column headers such as "Quarter Ended September 30," over "2025 2026" turned into "Q3 2025" and
"Q3 2026". Subtotal rows (Google advertising, Total revenues, Net income) are left out so treemaps
and totals don't count anything twice; the items they add up become each row's Group instead.

A "History" tab adds up to five years of quarterly results from SEC's XBRL data feed
(data.sec.gov companyfacts) for the company in the link, so trends and forecasts work: revenue,
operating income, net income, operating cash flow, capex, free cash flow, margins and diluted EPS.
Q4 and the cash-flow quarters are derived from year-to-date totals; a quarter the feed doesn't
have yet comes from the release itself.

SEC asks automated clients to send a User-Agent naming them and a contact address (SEC_USER_AGENT)
and to stay under 10 requests a second; one link costs two or three requests.
"""
import json
import logging
import os
import re
import threading
import time
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from lxml import html as lxml_html

from . import sheets

log = logging.getLogger(__name__)

MIME = "application/x-sec-filing"  # an Upload whose data is the link
HOSTS = ("www.sec.gov", "sec.gov", "data.sec.gov")
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TIMEOUT = 25
MAX_BYTES = 25_000_000
MAX_TABS = 20
QUARTERS = 20  # five years of history

_URL = re.compile(r"https?://[^\s<>\"'\])]+", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{1,2}))?,?", re.I)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_DASH = {"—", "–", "-", "— %", "—%", "*"}
_NUM = re.compile(r"^\(?\s*[$€£]?\s*[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?\s*%?\s*\)?\s*%?$")
_FOOTNOTE = re.compile(r"\s*(?:\(\d{1,2}\)|\*+|\[\d{1,2}\])\s*$")
# Lines that are results of the lines above (left out like totals, kept for the History tab).
_RESULT = re.compile(r"^(?:total\b|sub-?total\b|net (?:income|loss|earnings)|income (?:from|before)|loss (?:from|before)|"
                     r"earnings before|(?:total )?operating (?:income|loss|profit)|gross (?:profit|margin)|free cash flow|"
                     r"net cash (?:provided|used|from)|net (?:increase|decrease|change) in cash|cash.*\b(?:beginning|end) of)"
                     r"|\btotal$", re.I)
# Rows in other units than the table's amounts: per-share figures, share counts, headcount, ratios.
_OTHER_UNITS = re.compile(r"^(?:basic|diluted)\b|per (?:common |diluted |basic )?share$|\beps\b|^(?:number of |weighted[- ]average )?"
                          r"(?:common |ordinary )?shares\b|^shares used|employees|headcount|\bratio\b|margin|year over year|"
                          r"as a percent|percentage|basis points|%$|^change in", re.I)
_UNITS = re.compile(r"in (thousands|millions|billions)", re.I)
_SCALE = {"thousands": 1e-3, "millions": 1.0, "billions": 1e3}  # to millions


class _Throttle:
    """SEC allows 10 requests a second; stay well under it across concurrent turns."""
    gap = 0.15

    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            pause = self.last + self.gap - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self.last = time.monotonic()


_throttle = _Throttle()
_facts: dict[int, dict] = {}  # companyfacts by CIK (the JSON is a few MB)
_titles: dict[str, str] = {}  # display name by link, set when the link is read


# ── Links ────────────────────────────────────────────────────────────────────

def link_in(text: str) -> str | None:
    m = _URL.search(text or "")
    return m.group(0).rstrip(".,;:!?") if m else None


def upload_in(content) -> sheets.Upload | None:
    """A link in the user's text, as an Upload the agent reads like a workbook."""
    text = " ".join(p.text for p in getattr(content, "parts", None) or [] if getattr(p, "text", None))
    url = link_in(text)
    return sheets.Upload(_titles.get(url, _short(url)), url.encode(), MIME) if url else None


def is_link(up: sheets.Upload | None) -> bool:
    return bool(up) and up.mime == MIME


def url_of(up: sheets.Upload) -> str:
    return up.data.decode()


def title(up: sheets.Upload) -> str:
    return _titles.get(url_of(up), up.name)


def without_link(text: str) -> str:
    return _URL.sub(" ", text or "").strip()


def _short(url: str) -> str:
    return urlparse(url).path.rsplit("/", 1)[-1] or urlparse(url).netloc


def _document(url: str) -> str:
    """The document behind an inline XBRL viewer link (sec.gov/ix?doc=/Archives/...)."""
    u = urlparse(url)
    doc = parse_qs(u.query).get("doc", [None])[0]
    return f"https://www.sec.gov{doc}" if u.path.rstrip("/") == "/ix" and doc and doc.startswith("/") else url


def cik_of(url: str) -> int | None:
    m = re.search(r"/edgar/data/0*(\d{1,10})/", url)
    return int(m.group(1)) if m else None


# ── Fetching ─────────────────────────────────────────────────────────────────

def user_agent() -> str:
    return os.getenv("SEC_USER_AGENT") or "Sheet Insights sheet-insights@example.com"


def fetch(url: str) -> bytes:
    """GET from sec.gov. Raises ValueError with a user-facing reason."""
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme not in ("http", "https") or host not in HOSTS:
        raise ValueError("I can only read company filings from sec.gov links for now, for example an earnings "
                         "release (8-K Exhibit 99.1) or a 10-Q on www.sec.gov")
    _throttle.wait()
    try:
        r = requests.get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
                         timeout=TIMEOUT, stream=True)
        data = r.raw.read(MAX_BYTES + 1, decode_content=True)
    except (requests.ConnectionError, requests.Timeout) as exc:
        log.warning("sec fetch failed for %s: %s", url, exc)
        raise ValueError("I couldn't reach sec.gov from here. If I'm running in Agent Engine, its Google Cloud "
                         "project needs internet access to www.sec.gov and data.sec.gov") from exc
    log.info("sec fetch %s -> %s (%d bytes)", url, r.status_code, len(data))
    if r.status_code == 403:
        raise ValueError("sec.gov turned the request down (403). SEC wants a User-Agent naming the app and a contact "
                         "email: set SEC_USER_AGENT, for example \"Sheet Insights you@company.com\"")
    if r.status_code == 404:
        raise ValueError("that link doesn't open a page on sec.gov (404). Check it opens in a browser")
    if r.status_code >= 400:
        raise ValueError(f"sec.gov answered {r.status_code}. Try again in a minute")
    if len(data) > MAX_BYTES:
        raise ValueError("that page is too big for me to read")
    return data


def companyfacts(cik: int) -> dict:
    if cik not in _facts:
        if len(_facts) >= 4:
            _facts.pop(next(iter(_facts)))
        _facts[cik] = json.loads(fetch(FACTS_URL.format(cik=cik)))
    return _facts[cik]


# ── Reading a filing ─────────────────────────────────────────────────────────

def read(up: sheets.Upload) -> list[sheets.Sheet]:
    """Every financial table on the linked page as a tab, plus the History tab. Raises ValueError
    with a user-facing reason."""
    url = _document(url_of(up))
    page = fetch(url)
    tabs, raw, doc = tables(page)
    if not tabs or _cover(doc):  # an 8-K cover or a filing index: follow its press-release exhibit
        nxt = exhibit_link(doc, url)
        if nxt:
            url = nxt
            tabs, raw, doc = tables(fetch(url))
    cik = cik_of(url)
    facts = None
    if cik:
        try:
            facts = companyfacts(cik)
        except ValueError as exc:  # the release tabs still work without history
            log.warning("no companyfacts for CIK %s: %s", cik, exc)
    hist = history(facts, raw) if facts else None
    if hist is not None:
        tabs.insert(0, hist)
    if not tabs:
        raise ValueError("I couldn't find any financial tables on that page. Try the link to the earnings release "
                         "(Exhibit 99.1) or the 10-Q / 10-K document itself")
    entity = (facts or {}).get("entityName") or _entity(doc)
    _titles[url_of(up)] = name(entity, doc, tabs, url)
    return tabs


def name(entity: str | None, doc, tabs: list[sheets.Sheet], url: str) -> str:
    text = " ".join(doc.xpath("//body//text()[normalize-space()]")[:60]) if doc is not None else ""
    lead = next((t for t in tabs if t.lead), None)
    period = None
    if lead is not None:
        qs = [c.split(" (")[0] for c in lead.df.columns if re.fullmatch(r"Q[1-4] \d{4}", c.split(" (")[0])]
        period = max(qs, key=when) if qs else None
    kind = "earnings release" if re.search(r"ex-?99|exhibit ?99", url, re.I) or re.search(r"announces .{0,40}results", text, re.I) \
        else "filing"
    return " ".join(b for b in (entity, period, kind) if b) or _short(url)


def _entity(doc) -> str | None:
    if doc is None:
        return None
    for t in doc.xpath("//body//text()[normalize-space()]")[:80]:
        m = re.search(r"\b([A-Z][\w.&,' -]{1,60}?(?:Inc\.|Corporation|Corp\.|Company|Ltd\.|plc|N\.V\.|S\.A\.|LLC))", t)
        if m:
            return m.group(1).strip()
    return None


def _cover(doc) -> bool:
    """An 8-K cover page: the numbers are in its Exhibit 99.1."""
    head = " ".join(doc.xpath("//body//text()[normalize-space()]")[:80]) if doc is not None else ""
    return bool(re.search(r"form\s+8-k\b", head, re.I))


def exhibit_link(doc, base: str) -> str | None:
    if doc is None:
        return None
    for href in doc.xpath("//a/@href"):
        if re.search(r"ex-?99|exhibit-?99|press|earnings", href, re.I) and re.search(r"\.html?$", href, re.I):
            return _document(urljoin(base, href))
    return None


def tables(page: bytes) -> tuple[list[sheets.Sheet], list[dict], object]:
    """(tabs, every parsed table incl. result rows, the parsed document)."""
    try:
        doc = lxml_html.fromstring(page)
    except Exception:  # noqa: BLE001  (not HTML)
        return [], [], None
    out, raw, seen, names = [], [], set(), {}
    for tb in doc.xpath("//table"):
        if tb.xpath("ancestor::table"):
            continue
        t = parse_table(tb, raw[-1]["title"] if raw else None)
        if t is None:
            continue
        raw.append(t)
        for title_, df, notes in sheets_of(t):
            key = df.to_csv(index=False, header=False)
            if key in seen or len(out) >= MAX_TABS:
                continue
            seen.add(key)
            n = names[title_] = names.get(title_, 0) + 1
            out.append(sheets.Sheet(name=title_ if n == 1 else f"{title_} ({n})", df=df, notes=notes, view="tree"))
    if out:
        lead = max(out, key=lambda s: (_lead_score(s.name), len(s.df)))
        lead.lead = True
    return out, raw, doc


def _lead_score(title_: str) -> int:
    t = title_.lower()
    if re.search(r"reconcil|constant currency|comparative|geograph|non-gaap|other income", t):
        return 0
    if "revenue" in t or "sales" in t:
        return 3
    if re.search(r"income|operations|results|highlights|segment", t):
        return 2
    return 1


def _text(el) -> str:
    return " ".join(el.text_content().replace("\xa0", " ").split())


def _value(s: str):
    """'1,234' -> (1234.0, False); '(12)' -> (-12.0, False); '25 %' -> (25.0, True); '—' -> (0.0, False); else None."""
    s = s.strip()
    if s in _DASH:
        return 0.0, "%" in s
    if not s or not _NUM.match(s) or not re.search(r"\d", s):
        return None
    neg = s.startswith("(")
    pct = "%" in s
    n = float(re.sub(r"[^\d.]", "", s) or "nan")
    if "-" in s and not neg:
        n = -n
    return (-n if neg else n), pct


def _label(s: str) -> str:
    for _ in range(3):
        s = _FOOTNOTE.sub("", s)
    if len(s) > 80:  # "Preferred stock and common stock ..., $0.001 par value per share: 15,000 shares authorized; ..."
        s = re.split(r";|:|, \$|, (?=\d)", s)[0][:80]
    return s.strip(" :")


def parse_table(tb, before: str | None = None) -> dict | None:
    """One HTML table as {title, unit, columns, rows}; rows are dicts with label, section, values {col: v}, pct."""
    grid = []
    for tr in tb.xpath(".//tr"):
        col, cells = 0, []
        for td in tr.xpath("./td|./th"):
            try:
                span = max(1, min(int(td.get("colspan") or 1), 50))
            except ValueError:
                span = 1
            cells.append((col, span, _text(td)))
            col += span
        grid.append(cells)
    rows, head, started, pending = [], [], False, None
    for cells in grid:
        label, label_col, vals, pct = None, None, {}, set()
        for c, span, s in cells:
            if not s or s in ("$", ")", "(", "€", "£"):
                continue
            if s == "%":
                if vals:
                    pct.add(max(vals))
                continue
            v = _value(s)
            if v is None:
                if label is None and not vals:
                    label, label_col = s, c
                continue
            vals[c] = v[0]
            if v[1]:
                pct.add(c)
        if label is None and not vals:
            continue
        if not started and not (label is not None and vals):
            if label is not None and label_col == 0:
                pending = label  # "Assets" above the first line item: a section, not a column header
            else:
                head.append([(c, span, s) for c, span, s in cells if s])  # "Quarter Ended September 30," / "2025 2026"
            continue
        if not started and pending:
            rows.append({"label": _label(pending), "vals": {}, "pct": set()})
        started = True
        rows.append({"label": _label(label or ""), "vals": vals, "pct": pct})
    rows = [r for r in rows if r["label"] or r["vals"]]
    if sum(1 for r in rows if r["vals"]) < 2:
        return None
    # Value cells of one column can sit in neighbouring grid columns ("$" cells): merge them.
    used = sorted({c for r in rows for c in r["vals"]})
    clusters: list[list[int]] = []
    for c in used:
        if clusters and c - clusters[-1][-1] <= 2 and not any(c in r["vals"] and any(k in r["vals"] for k in clusters[-1]) for r in rows):
            clusters[-1].append(c)
        else:
            clusters.append([c])
    columns = []
    for i, cl in enumerate(clusters):
        lo, hi = cl[0], cl[-1]
        bits = []
        for cells in head:
            bits += [s for c, span, s in cells if c <= hi and c + span - 1 >= lo and s not in bits]
        columns.append(period_label(" ".join(bits)) or f"Column {i + 1}")
    out_rows, section = [], None
    for r in rows:
        if not r["vals"]:
            section = r["label"]
            out_rows.append({"label": r["label"], "section": None, "vals": {}, "pct": False, "header": True})
            continue
        vals, pct = {}, False
        for i, cl in enumerate(clusters):
            hit = [c for c in cl if c in r["vals"]]
            if hit:
                vals[i] = r["vals"][hit[0]]
                pct = pct or hit[0] in r["pct"]
        out_rows.append({"label": r["label"], "section": section, "vals": vals, "pct": pct, "header": False,
                         "pct_cols": {i for i, cl in enumerate(clusters) if any(c in r["pct"] for c in cl)}})
    context = _context(tb)
    unit = next((m.group(1).lower() for s in context + [_text(tb)[:400]] for m in [_UNITS.search(s)] if m), None)
    dollars = "$" in _text(tb)
    return {"title": _title(context, before), "unit": unit, "dollars": dollars, "columns": _unique(columns), "rows": out_rows}


def period_label(h: str) -> str:
    """'Quarter Ended September 30, 2025' -> 'Q3 2025'; 'Year To Date September 30, 2026' -> 'YTD Sep 2026';
    'As of December 31, 2025' -> 'Dec 31, 2025'; 'Q4 2025' stays; other headers are kept as written."""
    h = " ".join(re.sub(r"\b((?i:of|ended|ending))(?=[A-Z])", r"\1 ", h).split())  # "As ofSeptember 30"
    low = h.lower()
    years = _YEAR.findall(h)
    m = re.search(r"\bq([1-4])\s*'?((?:19|20)\d{2})\b", low)
    if m:
        p = f"Q{m.group(1)} {m.group(2)}"
        return f"TTM {p}" if re.search(r"\bttm\b|trailing", low) else p
    mon = _MONTH.search(h)
    rest = re.sub(r"\b(?:quarters?|three|six|nine|twelve|months?|years?|fiscal|to|date|ended|ending|as of|period|unaudited)\b|"
                  r"\(|\)|,|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+\d{1,2})?|\b(?:19|20)\d{2}\b",
                  " ", h, flags=re.I)
    rest = " ".join(rest.split())
    if not years:
        return h
    y = years[-1]
    if mon:
        m_i = _MONTHS.index(mon.group(1).lower()[:3])
        if re.search(r"year to date|year-to-date|\bytd\b|six months|nine months", low):
            p = f"YTD {mon.group(1)[:3].title()} {y}"
        elif re.search(r"twelve months|trailing", low):
            p = f"TTM {mon.group(1)[:3].title()} {y}"
        elif re.search(r"year ended|years ended|fiscal year|\bfy\b", low):
            p = f"FY {y}"
        elif re.search(r"quarter|three months|\b13 weeks", low):
            p = f"Q{m_i // 3 + 1} {y}"
        elif mon.group(2):
            p = f"{mon.group(1)[:3].title()} {int(mon.group(2))}, {y}"
        else:
            p = f"{mon.group(1)[:3].title()} {y}"
    else:
        p = y
    return f"{p} {rest}" if rest else p


def _unique(names: list[str]) -> list[str]:
    seen, out = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


def _context(tb) -> list[str]:
    """Texts just before the table, nearest first."""
    out = []
    for t in tb.xpath("preceding::text()[normalize-space()][position()<=8]")[::-1]:
        s = " ".join(t.replace("\xa0", " ").split())
        if s:
            out.append(s)
    return out


def _title(context: list[str], before: str | None = None) -> str:
    """The heading just above a table; `before` (the previous table's) when there is only prose."""
    for s in context:
        if (len(s) <= 70 and re.search(r"[A-Za-z]{3}", s) and not s.startswith("(") and not s.endswith(".")
                and not re.match(r"(?:the following|we |our |comparison from)", s, re.I) and s[0].isupper()
                and not re.search(r"\||form 10-[qk]|form 8-k", s, re.I)
                and not re.fullmatch(r"(?:page )?\d+", s, re.I) and not re.search(r"\b(?:inc|corp|ltd|plc)\.?$", s, re.I)):
            letters = [ch for ch in s if ch.isalpha()]
            shouting = sum(ch.isupper() for ch in letters) > 0.7 * len(letters)  # "CONSOLIDATED BALANCE SHEETS (Unaudited)"
            t = re.sub(r"\s*\((?i:unaudited)\)", "", s.title() if shouting else s).strip(" :")
            if len(t) > 45:
                t = re.split(r",(?=[^,]*$)", t[:45])[0] if "," in t[:45] else t[:45].rsplit(" ", 1)[0]
            return t
    return before or "Table"


def sheets_of(t: dict) -> list[tuple[str, pd.DataFrame, list[str]]]:
    """A parsed table as tidy tabs: one row per line item, one column per period. Subtotals are left
    out and name each item's Group. A table whose line items repeat in parts that each close on a
    grand total (segment revenues, then segment operating income) becomes one tab per part."""
    cols = t["columns"]
    quarter = [i for i, c in enumerate(cols) if re.fullmatch(r"Q[1-4] \d{4}", c)]
    ytd = [i for i, c in enumerate(cols) if c.startswith("YTD ")]
    keep = [i for i in range(len(cols)) if not (quarter and i in ytd)]
    notes = []
    if quarter and ytd:
        notes.append("Left out the year-to-date columns; the History tab has every quarter.")
    rows, dropped, results, run, section = [], [], [], [], None
    for r in t["rows"]:  # run: the items and subtotals not yet added into a subtotal
        if r["header"]:
            section = r["label"]
            continue
        vals = {i: v for i, v in r["vals"].items() if i in keep}
        if not vals:
            continue
        money = {i: v for i, v in vals.items() if i not in r.get("pct_cols", set())}
        if r["pct"] and not money or _OTHER_UNITS.search(r["label"]):
            dropped.append(r["label"])
            continue
        node = {"label": r["label"], "vals": money, "parent": None, "section": section}
        k = _sums(run, money)
        if k or _RESULT.search(r["label"]) or not r["label"]:
            results.append(r["label"] or "(unlabelled total)")
            for child in run[len(run) - k:] if k else []:
                child["parent"] = node
            run = run[:len(run) - k] + [node] if k else []
            if section and section.lower() in r["label"].lower():
                section = None  # "Total current assets" closes "Current assets"
            continue
        rows.append(node)
        run.append(node)
    labels = [r["label"] for r in rows]
    if len(labels) != len(set(labels)):
        parts = []  # (grand total, its items) in order; items under no total stay with the part before
        for r in rows:
            root = _root(r)
            if not parts or (root is not None and root is not parts[-1][0]):
                parts.append((root, []))
            parts[-1][1].append(r)
        if len(parts) > 1:
            out = []
            for i, (root, part) in enumerate(parts):
                label = root["label"] if root is not None else part[0]["section"] or f"Part {i + 1}"
                label = _cap(re.sub(r"^total\s+|\s+total$", "", label, flags=re.I))
                out += _frame(f"{t['title']}: {label}", t, part, keep, notes, results, dropped)
            return out
    return _frame(t["title"], t, rows, keep, notes, results, dropped)


def _root(r: dict) -> dict | None:
    p = r["parent"]
    while p is not None and p["parent"] is not None:
        p = p["parent"]
    return p


def when(label: str) -> pd.Timestamp | None:
    """The end of a period label: 'Q3 2026' -> 30 Sep 2026, 'Dec 31, 2025', 'FY 2025' -> 31 Dec 2025."""
    m = re.fullmatch(r"Q([1-4]) (\d{4})", label)
    if m:
        return pd.Timestamp(year=int(m.group(2)), month=3 * int(m.group(1)), day=1) + pd.offsets.MonthEnd(0)
    m = re.fullmatch(r"FY (\d{4})", label)
    if m:
        return pd.Timestamp(year=int(m.group(1)), month=12, day=31)
    try:
        return pd.Timestamp(label) if re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", label) else None
    except ValueError:
        return None


def _scale_label(t: dict) -> str:
    unit = {"thousands": "K", "millions": "M", "billions": "B"}.get(t["unit"] or "")
    return f" ({'$' if t['dollars'] else ''}{unit})" if unit else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _frame(title_: str, t: dict, rows: list[dict], keep: list[int], notes: list[str], results: list[str],
           dropped: list[str]) -> list[tuple[str, pd.DataFrame, list[str]]]:
    if len(rows) < 3 or not any(_YEAR.search(t["columns"][i]) for i in keep):
        return []  # too small, or not results by period (a cover page's securities table, a table of contents)
    cols = t["columns"]
    notes = [f"Read from the filing{', amounts in ' + t['unit'] if t['unit'] else ''}, as reported.", *notes]
    if results:
        shown = ", ".join(results[:3]) + (f" and {len(results) - 3} more" if len(results) > 3 else "")
        notes.append(f"Left out {len(results)} subtotal row{'s' if len(results) > 1 else ''} ({shown}) so nothing is counted twice.")
    if dropped:
        notes.append(f"Left out {len(dropped)} row{'s' if len(dropped) > 1 else ''} in other units (per share, share counts or %).")
    unit = _scale_label(t)
    groups = [_group(r, rows) for r in rows]
    labels = [r["label"] for r in rows]
    twice = {x for x in labels if labels.count(x) > 1}  # "Net sales" under each segment -> "Net sales (AWS)"
    data = {"Line item": _unique([f"{x} ({g})" if x in twice and g != x else x for x, g in zip(labels, groups)])}
    if any(g != r["label"] for g, r in zip(groups, rows)):
        data["Group"] = groups
    names = []
    for i in keep:
        col = [r["vals"].get(i) for r in rows]
        if all(v is None for v in col):
            continue
        n = cols[i] + ("" if "%" in cols[i] or "change" in cols[i].lower() else unit)
        data[n] = col
        names.append((cols[i], n))
    # The change between the two periods side by side (Q3 2025 -> Q3 2026, Dec 31 -> Sep 30), whichever
    # order the filing prints them in.
    for kind in (r"Q[1-4] \d{4}", r"[A-Z][a-z]{2} \d{1,2}, \d{4}", r"FY \d{4}"):
        same = sorted(((when(c), n) for c, n in names if re.fullmatch(kind, c)), key=lambda x: x[0])
        if len(same) == 2 and same[0][0] < same[1][0]:
            prev, cur = pd.Series(data[same[0][1]], dtype="float64"), pd.Series(data[same[1][1]], dtype="float64")
            data["Change" + unit] = (cur - prev).round(2).tolist()
            data["Change %"] = ((cur - prev) / prev.where(prev > 0) * 100).round(1).tolist()  # no % from a loss
            break
    df = pd.DataFrame(data)
    for c in df.columns:
        if c not in ("Line item", "Group"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return [(title_, df, notes)]


def _sums(run: list[dict], vals: dict) -> int:
    """How many of the last rows in run add up to vals (0 when none do)."""
    if len(run) < 2 or not vals:
        return 0
    for k in range(2, len(run) + 1):
        part = run[-k:]
        ok = True
        for i, v in vals.items():
            got = [p["vals"].get(i) for p in part]
            if any(g is None for g in got) or abs(sum(got) - v) > max(k * 1.0, abs(v) * 0.001):
                ok = False
                break
        if ok:
            return k
    return 0


def _group(r: dict, rows: list[dict]) -> str:
    """The subtotal a line item rolls into, one level below the table's grand total."""
    chain, p = [], r["parent"]
    while p is not None:
        chain.append(p)
        p = p["parent"]
    if not chain:
        return r["section"] or r["label"]
    top = chain[-1]
    covers_all = all(_under(x, top) for x in rows)
    pick = chain[-2] if covers_all and len(chain) > 1 else (None if covers_all else top)
    if pick is None:
        return r["section"] or r["label"]
    return _cap(re.sub(r"\s+total$|^total\s+", "", pick["label"], flags=re.I) or pick["label"])


def _under(r: dict, top: dict) -> bool:
    p = r["parent"]
    while p is not None:
        if p is top:
            return True
        p = p["parent"]
    return False


# ── History from SEC's XBRL data feed ────────────────────────────────────────

_METRICS = (  # (column, us-gaap tags in order of preference, adds up over a year, release row)
    ("Revenue ($M)", ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                      "RevenueFromContractWithCustomerIncludingAssessedTax"), True,
     r"^(?:total )?(?:net )?(?:revenues?|sales)$|^total net sales$"),
    ("Operating income ($M)", ("OperatingIncomeLoss",), True, r"^(?:total )?(?:income|profit) from operations$|^operating income(?: \(loss\))?$"),
    ("Net income ($M)", ("NetIncomeLoss", "ProfitLoss"), True, r"^net income(?: \(loss\))?$"),
    ("Operating cash flow ($M)", ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByOperatingActivities"), True,
     r"^net cash provided by (?:\(used in\) )?operating activities$"),
    ("Capex ($M)", ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"), True,
     r"purchases? of property and equipment"),
    ("Diluted EPS ($ per share)", ("EarningsPerShareDiluted",), False, r"^diluted (?:net income|earnings) per (?:common )?share|^diluted eps"),
)
_FORMS = re.compile(r"^(?:10-Q|10-K|20-F|40-F|10-KT|10-QT)(?:/A)?$")


def quarterly(entries: list[dict], additive: bool) -> dict[pd.Timestamp, float]:
    """Quarter-end -> value: three-month facts as filed, and for amounts that add up, the
    differences of year-to-date facts (Q2 = H1 - Q1, Q4 = FY - 9M)."""
    best: dict[tuple, dict] = {}
    for e in entries:
        if not e.get("start") or not _FORMS.match(e.get("form") or ""):
            continue
        key = (e["start"], e["end"])
        if key not in best or (e.get("filed") or "") > (best[key].get("filed") or ""):
            best[key] = e
    out: dict[pd.Timestamp, float] = {}
    by_start: dict[str, list[dict]] = {}
    for (s, e), f in best.items():
        days = (pd.Timestamp(e) - pd.Timestamp(s)).days
        if 80 <= days <= 100:
            out[pd.Timestamp(e)] = float(f["val"])
        by_start.setdefault(s, []).append(f)
    if additive:
        for fs in by_start.values():
            fs.sort(key=lambda f: f["end"])
            for a, b in zip(fs, fs[1:]):
                if 80 <= (pd.Timestamp(b["end"]) - pd.Timestamp(a["end"])).days <= 100:
                    out.setdefault(pd.Timestamp(b["end"]), float(b["val"]) - float(a["val"]))
        return out
    # Per-share figures don't subtract exactly; Q4 is the year less its first three quarters (to the cent).
    for (s, e), f in best.items():
        start, end = pd.Timestamp(s), pd.Timestamp(e)
        if 350 <= (end - start).days <= 380 and end not in out:
            q = [v for k, v in out.items() if start < k < end - pd.Timedelta(days=60)]
            if len(q) == 3:
                out[end] = round(float(f["val"]) - sum(q), 2)
    return out


def _quarter(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.to_period("Q").start_time


def history(facts: dict, raw: list[dict] = ()) -> sheets.Sheet | None:
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    cols: dict[str, dict[pd.Timestamp, float]] = {}
    for col, tags, additive, _ in _METRICS:
        series: dict[pd.Timestamp, float] = {}
        for tag in tags:
            units = (gaap.get(tag) or {}).get("units") or {}
            entries = units.get("USD/shares" if not additive else "USD") or []
            for q, v in quarterly(entries, additive).items():
                series.setdefault(_quarter(q), v if not additive else v / 1e6)
        if series:
            cols[col] = series
    if "Revenue ($M)" not in cols:
        return None
    notes = [f"Quarterly results from SEC's XBRL data feed for {facts.get('entityName', 'the company')} "
             f"(CIK {facts.get('cik')}). Q4 and cash-flow quarters are worked out from year-to-date totals "
             "(Q4 EPS approximately: the year's EPS less the first three quarters)."]
    added = _from_release(cols, raw)
    if added:
        notes.append(f"{added} comes from this filing: SEC's data feed doesn't have it yet.")
    quarters = sorted(set().union(*[set(s) for s in cols.values()]))
    quarters = [q for q in quarters if q in cols["Revenue ($M)"]][-QUARTERS:]
    while len(quarters) > 8 and quarters[0].month != 1:  # whole calendar years, so the yearly views compare like with like
        quarters.pop(0)
    if len(quarters) < 4:
        return None
    df = pd.DataFrame({"Quarter": pd.to_datetime(quarters), "Calendar year": [str(q.year) for q in quarters]})
    for col, *_ in _METRICS:
        if col in cols:
            df[col] = [cols[col].get(q) for q in quarters]
    if "Operating cash flow ($M)" in df and "Capex ($M)" in df:
        df.insert(df.columns.get_loc("Capex ($M)") + 1, "Free cash flow ($M)", df["Operating cash flow ($M)"] - df["Capex ($M)"])
    if "Operating income ($M)" in df:
        df["Operating margin %"] = (df["Operating income ($M)"] / df["Revenue ($M)"] * 100).round(1)
    if "Net income ($M)" in df:
        df["Net margin %"] = (df["Net income ($M)"] / df["Revenue ($M)"] * 100).round(1)
    df = df.dropna(axis=1, how="all")
    first, last = sheets.label(quarters[0], "quarter"), sheets.label(quarters[-1], "quarter")
    notes.append(f"{len(quarters)} quarters, {first} to {last}. Quarters are calendar quarters of each period's end.")
    return sheets.Sheet(name="History", df=df, notes=notes, style="trend")


def _from_release(cols: dict, raw: list[dict]) -> str | None:
    """Fill quarters the data feed doesn't have yet from the filing's own tables (an earnings
    release comes out before the 10-Q). Returns the quarter added, if any."""
    found: dict[str, dict[pd.Timestamp, float]] = {}
    for t in raw:
        scale = _SCALE.get(t["unit"] or "")
        for i, c in enumerate(t["columns"]):
            m = re.fullmatch(r"Q([1-4]) (\d{4})", c)
            if not m:
                continue
            q = pd.Timestamp(year=int(m.group(2)), month=3 * int(m.group(1)) - 2, day=1)
            for r in t["rows"]:
                if r["header"] or i not in r["vals"]:
                    continue
                for col, _, additive, pattern in _METRICS:
                    if re.search(pattern, r["label"], re.I) and (additive and scale or not additive):
                        v = r["vals"][i] * (scale if additive else 1)
                        found.setdefault(col, {}).setdefault(q, abs(v) if col.startswith("Capex") else v)
    latest = max(cols["Revenue ($M)"])
    new = sorted({q for s in found.values() for q in s if q > latest})
    if not new or new[0] not in found.get("Revenue ($M)", {}):
        return None
    q = new[0]
    for col, s in found.items():
        if q in s:
            cols.setdefault(col, {})[q] = s[q]
    return sheets.label(q, "quarter")


def summary(tabs: list[sheets.Sheet]) -> str:
    """One line for the reply after a link: what was read, and where the history is."""
    hist = next((t for t in tabs if t.name == "History"), None)
    n = len(tabs) - (hist is not None)
    line = f"I read {n} table{'s' if n != 1 else ''} from the filing"
    if hist is None:
        return line + "."
    p = sheets.profile(hist)
    return (line + f" and added a **History** tab with {p.rows} quarters ({p.span}) from SEC's data feed. "
            "Ask for *\"History as a trend\"* to see revenue with a forecast.")

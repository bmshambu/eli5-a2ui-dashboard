"""Upfront file check: what each tab holds, what is missing or wrong, and how to fix it in Excel.

Works on the raw cells (sheets.frames) so it can point at real cell references (C14) and still
say something useful about tabs the chart flow would skip. Each finding is one of:
  fix   - this stops a chart, or makes a number wrong (unreadable cells in a number column,
          a formula error, a column with no header)
  check - charts work, but the user should look (spelling variants, blank cells, duplicates)
  info  - what was tidied automatically (title rows, total rows)
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from . import sheets
from .analysis import STYLES, _partial_period, available

_ERROR = re.compile(r"^#(DIV/0!|N/A|REF!|VALUE!|NAME\?|NUM!|NULL!|SPILL!|CALC!)$", re.I)
_FIX, _CHECK, _INFO = "fix", "check", "info"


@dataclass
class Finding:
    level: str  # fix | check | info
    title: str
    tip: str = ""


@dataclass
class TabReview:
    name: str
    rows: int = 0
    found: str = ""  # profile summary
    styles: list[str] = field(default_factory=list)  # chart styles this tab supports now
    unlock: list[str] = field(default_factory=list)  # what to add to unlock more
    findings: list[Finding] = field(default_factory=list)
    chartable: bool = False

    empty: bool = False

    @property
    def status(self) -> str:
        if self.empty:
            return "empty"
        if not self.chartable:
            return "blocked"
        if any(f.level == _FIX for f in self.findings):
            return "fix"
        return "check" if any(f.level == _CHECK for f in self.findings) else "ready"


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cells(refs: list[str], values: list, n: int) -> str:
    shown = ", ".join(f"{r} “{str(v).strip()[:20]}”" for r, v in list(zip(refs, values))[:3])
    return shown + (f" and {n - 3} more" if n > 3 else "")


def _rows(rows: list[int]) -> str:
    shown = ", ".join(str(r) for r in rows[:5])
    return shown + (f" and {len(rows) - 5} more" if len(rows) > 5 else "")


def _excel_errors(up: sheets.Upload) -> dict[str, dict[tuple[int, int], str]]:
    """Cells showing a formula error, per tab, as {(row index, column index): "#DIV/0!"}. pandas
    reads these as blanks, so they come straight from openpyxl."""
    import openpyxl
    out: dict[str, dict[tuple[int, int], str]] = {}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(up.data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001  (sheets.frames reports unreadable files)
        return out
    for ws in wb.worksheets:
        for r, row in enumerate(ws.iter_rows()):
            for c, cell in enumerate(row):
                if getattr(cell, "data_type", None) == "e":
                    out.setdefault(ws.title, {})[(r, c)] = str(cell.value)
    wb.close()
    return out


def review_tab(name: str, raw: pd.DataFrame, csv: bool = False) -> TabReview:
    t = TabReview(name=name)
    body = raw.dropna(how="all").dropna(axis=1, how="all")
    if body.empty:
        t.empty = True
        t.findings.append(Finding(_INFO, "This tab is empty, so it is skipped."))
        return t
    ref = (lambda c, r: f"{col_letter(c)}{r + 1}") if not csv else (lambda c, r: f"line {r + 1}")
    h = sheets._header_row(body)
    head_row = body.index[h]
    if h:
        t.findings.append(Finding(_INFO, f"The table starts at row {head_row + 1}; the {'row' if h == 1 else f'{h} rows'} above it "
                                         f"{'looks' if h == 1 else 'look'} like a title and {'is' if h == 1 else 'are'} skipped."))
    data = body.loc[body.index > head_row]
    header = body.loc[head_row]

    # Headers: blank and repeated names.
    names = {}
    for c in body.columns:
        v = header[c]
        has_data = data[c].notna().any()
        if (pd.isna(v) or not str(v).strip()) and has_data:
            t.findings.append(Finding(_FIX, f"Column {col_letter(c)} has values but no header.",
                                      f"Type a name in {ref(c, head_row)}, for example what the numbers measure."))
        elif pd.notna(v):
            names.setdefault(re.sub(r"\s+", " ", str(v).strip()).lower(), []).append(c)
    for k, cols in names.items():
        if len(cols) > 1:
            t.findings.append(Finding(_CHECK, f"{len(cols)} columns are called “{header[cols[0]]}” "
                                              f"({', '.join(col_letter(c) for c in cols)}).",
                                      "Give each a different name so you can tell them apart in charts."))

    # Blank rows inside the table, which often mean a second table below.
    full = raw.loc[head_row + 1:body.index[-1], body.columns]
    blank = [int(i) + 1 for i in full.index[full.isna().all(axis=1)]]
    if blank:
        # The first filled row after a gap: all text (no numbers) reads as a second header.
        nxt = next((data.loc[i].dropna() for i in data.index if i + 1 > blank[0]), pd.Series(dtype=object))
        looks_header = len(nxt) >= 2 and all(sheets._is_text(v) for v in nxt)
        if looks_header:
            t.findings.append(Finding(_FIX, f"There seems to be a second table below the blank row {blank[0]}.",
                                      "Move each table to its own tab, so the two aren't read as one."))
        else:
            t.findings.append(Finding(_INFO, f"Blank row{'s' if len(blank) > 1 else ''} {_rows(blank)} inside the table "
                                             f"{'are' if len(blank) > 1 else 'is'} ignored."))

    # Per column: formula errors, unreadable numbers, dates, blanks, spelling variants.
    label = {c: (str(header[c]).strip() if pd.notna(header[c]) and str(header[c]).strip() else f"Column {col_letter(c)}") for c in body.columns}
    for c in body.columns:
        col = data[c].dropna()
        col = col[col.map(lambda v: not (isinstance(v, str) and not v.strip()))]
        if col.empty:
            continue
        errs = col[col.map(lambda v: isinstance(v, str) and bool(_ERROR.match(v.strip())))]
        if len(errs):
            t.findings.append(Finding(_FIX, f"{label[c]}: {len(errs)} formula error{'s' if len(errs) > 1 else ''} "
                                            f"({_cells([ref(c, i) for i in errs.index], list(errs), len(errs))}).",
                                      "Fix the formulas, or wrap them in IFERROR(…, \"\") so the cell is blank instead."))
            col = col.drop(errs.index)
        if col.empty or all(sheets._is_date(v) for v in col.head(50)):
            continue
        parsed = col.map(sheets.to_number)
        share = parsed.notna().mean()
        if 0.5 <= share < 1 and not sheets._YEAR_NAME.match(label[c]):
            bad = col[parsed.isna()]
            if sheets.period_of(bad.iloc[0]) is None or share < 0.8:
                where = _cells([ref(c, i) for i in bad.index], list(bad), len(bad))
                if share >= 0.8:
                    t.findings.append(Finding(_CHECK, f"{label[c]}: {len(bad)} cell{'s' if len(bad) > 1 else ''} {'aren' if len(bad) > 1 else 'isn'}'t a number ({where}), "
                                                      "so those rows are left out of its totals.",
                                              "Type a number, or leave the cell blank if there is no value."))
                else:
                    t.findings.append(Finding(_FIX, f"{label[c]} is mostly numbers, but {len(bad)} of {len(col)} cells {'aren' if len(bad) > 1 else 'isn'}'t ({where}), "
                                                    "so the whole column is read as text and can't be charted.",
                                              "Replace those cells with numbers or leave them blank."))
        if share >= 0.8 and pd.notna(header[c]):
            miss = int(data[c].isna().sum())
            if miss and miss / max(len(data), 1) >= 0.1:
                t.findings.append(Finding(_CHECK, f"{label[c]} is blank in {miss:,} of {len(data):,} rows ({miss / len(data) * 100:.0f}%).",
                                          "Blank counts as missing, not zero. Type 0 where the value really is zero."))
            continue
        # Text columns: spelling variants that would be counted as different categories.
        text = col[col.map(lambda v: isinstance(v, str))].map(lambda v: re.sub(r"\s+", " ", v.strip()))
        if 2 <= text.nunique() <= 200:
            groups = {}
            for v in text.unique():
                groups.setdefault(v.lower(), []).append(v)
            clash = [vs for vs in groups.values() if len(vs) > 1]
            if clash:
                eg = "; ".join(" / ".join(f"“{v}”" for v in vs[:3]) for vs in clash[:2])
                t.findings.append(Finding(_CHECK, f"{label[c]} spells the same thing in different ways ({eg}), so they count as "
                                                  "separate values.", "Make the spelling identical, for example with Find and Replace."))

    dup = data[data.notna().any(axis=1)]
    dups = dup.index[dup.duplicated(keep="first")]
    if len(dups):
        t.findings.append(Finding(_CHECK, f"{len(dups)} row{'s are' if len(dups) > 1 else ' is'} an exact copy of an earlier row "
                                          f"(row{'s' if len(dups) > 1 else ''} {_rows([int(i) + 1 for i in dups])}).",
                                  "Delete them if they were pasted twice; keep them if they are real repeat entries."))

    # What the chart flow makes of it.
    sheet = sheets.tidy(name, raw)
    if sheet is None:
        if not any(f.level == _FIX and "read as text" in f.title for f in t.findings):
            t.findings.append(Finding(_FIX, "No column of numbers was found, so there is nothing to chart.",
                                      "Add at least one column of numbers (amounts, counts, scores) under a header."))
        return t
    t.rows = len(sheet.df)
    for note in sheet.notes:
        if note.startswith(("Left out", "Turned", "Used the first")):
            t.findings.append(Finding(_INFO, note))
    p = sheets.profile(sheet)
    t.found = p.summary()
    if not p.measures:
        t.findings.append(Finding(_FIX, "Every number column holds the same value in every row, so there is nothing to compare."
                                  if len(sheet.df) > 1 else "There is only one row of data, so there is nothing to compare.",
                                  "Add more rows, or a number that varies between them."))
        return t
    t.chartable = True
    ok = available(p)
    t.styles = [STYLES[s][0] for s in STYLES if s != "auto" and ok.get(s)]
    if not p.time:
        t.unlock.append("Add a date, month or quarter column (or one column per month) to unlock "
                        "Trend + forecast, Change bridge, Heatmap and Small multiples.")
    if not p.dimensions:
        t.unlock.append("Add a text column such as Region, Product or Team to unlock the Interactive explorer and Ranked drivers.")
    if len(p.measures) < 2:
        t.unlock.append("Add a second number column to unlock Relationship (one number against another).")
    if p.time:
        part = _partial_period(sheet.df, p)
        if part is not None:
            t.findings.append(Finding(_INFO, f"{sheets.label(part[0], p.grain)} is only partly in the data, so trends stop at the "
                                             "period before it."))
    return t


def review(up: sheets.Upload) -> list[TabReview]:
    """Raises ValueError (user-facing) when the file can't be opened at all."""
    csv = up.mime not in (sheets.XLSX, sheets.XLSM)
    errors = {} if csv else _excel_errors(up)
    tabs = []
    for name, raw in sheets.frames(up, as_typed=True):
        for (r, c), v in errors.get(str(name), {}).items():
            if r in raw.index and c in raw.columns:
                raw.at[r, c] = v  # put the error text back so the column check can point at it
        tabs.append(review_tab(str(name), raw, csv))
    return tabs


def counts(tabs: list[TabReview]) -> tuple[int, int]:
    """(things to fix, things to check) across the file."""
    return (sum(f.level == _FIX for t in tabs for f in t.findings),
            sum(f.level == _CHECK for t in tabs for f in t.findings))


# ── The review page (Canvas side panel) ─────────────────────────────────────

_CSS = """
.rv-sum{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 18px}
.rv-sum div{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.rv-sum b{display:block;font-size:24px;line-height:1.2}.rv-sum span{font-size:12px;color:var(--muted)}
.rv-tab{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.rv-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.rv-head h2{font-size:18px;margin:0}
.pill{font-size:12px;font-weight:600;border-radius:999px;padding:2px 10px}
.pill.ready{background:rgba(12,163,12,.14);color:var(--good)}.pill.check{background:rgba(250,178,25,.18);color:var(--ink)}
.pill.blocked{background:rgba(227,73,72,.14);color:var(--bad)}.pill.fix{background:rgba(227,73,72,.10);color:var(--bad)}
.pill.empty{background:var(--chip);color:var(--muted)}
.rv-found{color:var(--ink2);font-size:13px;margin:4px 0 8px}
.rv-list{list-style:none;margin:6px 0 0;padding:0}
.rv-list li{display:grid;grid-template-columns:22px 1fr;gap:4px 8px;padding:7px 0;border-top:1px solid var(--line);font-size:14px}
.rv-list li:first-child{border-top:0}
.rv-ic{width:18px;height:18px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;margin-top:2px}
.rv-ic.fix{background:var(--bad)}.rv-ic.check{background:var(--warn);color:#0b0b0b}.rv-ic.info{background:var(--muted)}
.rv-tip{grid-column:2;color:var(--ink2);font-size:13px}
.rv-unlock{margin:10px 0 0;font-size:13px;color:var(--ink2)}.rv-unlock b{color:var(--ink)}
.rv-styles{font-size:13px;color:var(--ink2);margin:8px 0 0}
.rv-act{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.rv-foot{color:var(--muted);font-size:13px;margin-top:6px}
"""
_MARK = {_FIX: "!", _CHECK: "?", _INFO: "i"}
_PILL = {"ready": "Ready", "check": "Ready, worth a look", "fix": "Charts, but fix first", "blocked": "Can't chart yet", "empty": "Empty"}


def headline(file: str, tabs: list[TabReview]) -> str:
    fixes, checks = counts(tabs)
    ready = sum(t.chartable for t in tabs)
    if not ready:
        return f"Nothing in {file} can be charted yet: {fixes} thing{'s' if fixes != 1 else ''} to fix"
    if fixes:
        return f"{fixes} thing{'s' if fixes != 1 else ''} to fix in {file} before you rely on the charts"
    if checks:
        return f"{file} is ready to chart, with {checks} thing{'s' if checks != 1 else ''} worth a look"
    return f"{file} looks clean and ready to chart"


def html(file: str, tabs: list[TabReview]) -> str:
    from . import page as pg
    e = pg._e
    fixes, checks = counts(tabs)
    order = {"fix": 0, "blocked": 1, "check": 2, "ready": 3, "empty": 4}
    blocks = []
    for t in sorted(tabs, key=lambda t: order[t.status]):
        items = "".join(f'<li><span class="rv-ic {f.level}" aria-label="{f.level}">{_MARK[f.level]}</span><span>{e(f.title)}</span>'
                        + (f'<span class="rv-tip">{e(f.tip)}</span>' if f.tip else "") + "</li>"
                        for f in sorted(t.findings, key=lambda f: (_FIX, _CHECK, _INFO).index(f.level)))
        found = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(t.found)) if t.found else ""
        acts = ""
        if t.chartable:
            acts = '<div class="rv-act">' + pg._button("Chart this tab", "chart", {"sheet": t.name, "style": "auto",
                                                                                    "prompt": f"Chart the {t.name} tab"}, "chip on-accent") \
                + pg._button("Pick a chart style", "tab", {"sheet": t.name, "prompt": f"Use the {t.name} tab"}) + "</div>"
        unlock = "".join(f'<p class="rv-unlock"><b>Unlock more:</b> {e(u)}</p>' for u in t.unlock)
        styles = f'<p class="rv-styles">Can show: {e(", ".join(t.styles))}</p>' if t.styles else ""
        blocks.append(f'<section class="rv-tab"><div class="rv-head"><h2>{e(t.name)}</h2>'
                      f'<span class="pill {t.status}">{_PILL[t.status]}</span></div>'
                      f'{f"<p class=rv-found>{found}</p>" if found else ""}'
                      f'{f"<ul class=rv-list>{items}</ul>" if items else "<p class=rv-found>Nothing to fix.</p>"}'
                      f'{styles}{unlock}{acts}</section>')
    ready = sum(t.chartable for t in tabs)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>File check</title><style>{pg.CSS}{_CSS}.chip.on-accent{{background:var(--accent);border-color:var(--accent);color:#fff}}</style></head><body><div class="wrap">
<div class="theme" role="group" aria-label="Theme"><button data-t="auto">Auto</button><button data-t="light">Light</button><button data-t="dark">Dark</button></div>
<div class="eyebrow">File check · {e(file)}</div>
<h1>{e(headline(file, tabs))}</h1>
<div class="rv-sum"><div><b>{ready} of {len(tabs)}</b><span>tabs ready to chart</span></div>
<div><b style="color:var(--bad)">{fixes}</b><span>to fix</span></div><div><b>{checks}</b><span>worth a look</span></div></div>
{"".join(blocks)}
<p class="rv-foot">Fix these in Excel, save, and upload the file again; I'll check it the same way. Things marked i were tidied for you.</p>
</div><div id="tip" role="tooltip" hidden></div><script>{pg.JS}</script></body></html>"""

"""Spreadsheets the user uploads in GE chat, read into tidy tables (no model call).

GE sends an upload in the user's turn as inline_data (bytes, file name as display_name). Later
turns reuse the latest upload found in the session events, so "use the Q3 tab" or a chart-style
click works without uploading again. Our own A2UI DataParts (button clicks) are also inline_data
and are skipped.

Each tab is tidied the way a person would read it: blank rows and columns dropped, the header row
found (titles above a table are common), "Total" rows removed so they are not double counted,
text numbers such as "£1,200" or "12%" turned into numbers, dates parsed. Wide tables with one
column per month, quarter or year are turned long (one row per item and period), because that
is what trends and heatmaps need.
"""
import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import NamedTuple

import pandas as pd

from . import a2ui

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
CSV = "text/csv"
_BY_EXTENSION = {".xlsx": XLSX, ".xlsm": XLSM, ".csv": CSV, ".tsv": "text/tab-separated-values", ".xls": "application/vnd.ms-excel"}
SPREADSHEET_MIMES = (XLSX, XLSM, CSV, "text/tab-separated-values", "application/vnd.ms-excel", "text/plain")

MAX_ROWS = 200_000
_TOTAL = re.compile(r"^\s*(grand\s+)?(sub\s*-?\s*)?totals?\b", re.I)
_ID_NAME = re.compile(r"(^|[\s_])(id|code|zip|postcode|phone|no\.?|number|#)$|^id[\s_]|[\s_]id$", re.I)
_YEAR_NAME = re.compile(r"^(fy\s*)?(year|yr|fiscal\s*year)$", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:[\s'\-/]*(\d{2,4}))?$", re.I)
_QUARTER = re.compile(r"^(?:q([1-4])[\s'\-/]*(?:fy)?\s*(\d{2,4})|(\d{4})[\s\-/]*q([1-4]))$", re.I)
_YEARISH = re.compile(r"^(?:fy\s*)?(19|20)\d{2}$", re.I)
_RATE_NAME = re.compile(r"(%|percent|rate|ratio|margin|price|avg|average|mean|score|index|nps|satisfaction|per\s)", re.I)


class Upload(NamedTuple):
    name: str
    data: bytes
    mime: str


def _mime(mime: str | None, name: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    ext = name[name.rfind("."):].lower() if "." in name else ""
    if ext in _BY_EXTENSION and (not mime or mime in ("application/octet-stream", "text/plain")):
        return _BY_EXTENSION[ext]
    return mime or _BY_EXTENSION.get(ext, "application/octet-stream")


def uploads_in(content) -> list[Upload]:
    """Spreadsheets attached to one turn."""
    found = []
    for p in getattr(content, "parts", None) or []:
        blob = getattr(p, "inline_data", None)
        if not blob or not blob.data or blob.data.startswith(a2ui.TAG_START):
            continue
        name = blob.display_name or "Uploaded spreadsheet"
        mime = _mime(blob.mime_type, name)
        if mime in SPREADSHEET_MIMES and not (mime == "text/plain" and not name.lower().endswith((".csv", ".tsv"))):
            found.append(Upload(name, blob.data, mime))
    return found


def latest(events, author: str = "user") -> Upload | None:
    """The most recent spreadsheet uploaded in this session."""
    for event in reversed(events or []):
        if getattr(event, "author", None) == author:
            ups = uploads_in(getattr(event, "content", None))
            if ups:
                return ups[-1]
    return None


def describe(content) -> list[str]:
    """What kinds of parts a turn carried, for the logs (shows how GE delivers uploads)."""
    return [f"{p.inline_data.mime_type}:{p.inline_data.display_name}" if p.inline_data else
            f"file:{p.file_data.mime_type}" if p.file_data else "text"
            for p in getattr(content, "parts", None) or []]


# ── Reading ──────────────────────────────────────────────────────────────────

@dataclass
class Sheet:
    name: str
    df: pd.DataFrame  # tidy: one header row, typed columns
    notes: list[str] = field(default_factory=list)  # what tidying did, shown as data notes
    wide: bool = False  # melted from one-column-per-period


def read(upload: Upload) -> list[Sheet]:
    """Every tab with numbers in it, tidied. Raises ValueError with a user-facing reason."""
    if upload.mime == "application/vnd.ms-excel" and not upload.name.lower().endswith(".csv"):
        raise ValueError("old .xls workbooks aren't supported yet; save it as .xlsx or .csv and upload again")
    if upload.mime in (XLSX, XLSM):
        try:
            raw = pd.read_excel(io.BytesIO(upload.data), sheet_name=None, header=None, engine="openpyxl")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"I couldn't open {upload.name} as an Excel workbook") from exc
        frames = list(raw.items())
    else:
        text = upload.data.decode("utf-8-sig", "replace")
        try:
            sep = csv.Sniffer().sniff(text[:20_000], delimiters=",;\t|").delimiter
        except csv.Error:
            sep = "\t" if "\t" in text[:2000] else ","
        # csv.reader rather than read_csv: a title line above the table has fewer fields, and
        # read_csv would size the table from it.
        rows = [[c if c.strip() else None for c in r] for r in csv.reader(io.StringIO(text), delimiter=sep)]
        width = max((len(r) for r in rows), default=0)
        frames = [(_stem(upload.name), pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object))]
    sheets = [s for s in (tidy(name, df) for name, df in frames) if s is not None]
    if not sheets:
        raise ValueError(f"I couldn't find a table with numbers in {upload.name}")
    return sheets


def _stem(name: str) -> str:
    return name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "Data"


def _is_text(v) -> bool:
    return isinstance(v, str) and v.strip() != "" and to_number(v) is None


def _header_row(raw: pd.DataFrame) -> int:
    """The first of the top rows that looks like headers: mostly text, about as wide as the table."""
    widest = max(int(raw.notna().sum(axis=1).max()), 1)
    for i in range(min(len(raw), 15)):
        row = raw.iloc[i]
        filled = row.notna().sum()
        if filled >= max(2, 0.6 * widest) and sum(_is_text(v) or _is_date(v) for v in row) >= 0.6 * filled:
            return i
    return 0


def _is_date(v) -> bool:
    return isinstance(v, pd.Timestamp) or (hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"))


def _names(values) -> list[str]:
    out, seen = [], {}
    for i, v in enumerate(values):
        if _is_date(v):
            ts = pd.Timestamp(v)
            name = ts.strftime("%b %Y") if ts.day == 1 else ts.strftime("%d %b %Y")
        elif isinstance(v, float) and v.is_integer():
            name = str(int(v))
        else:
            name = str(v).strip() if pd.notna(v) and str(v).strip() else f"Column {i + 1}"
        name = re.sub(r"\s+", " ", name)
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        out.append(name)
    return out


def to_number(v):
    """12, '1,234', '£1.2k', '(300)', '12%' -> float, else None."""
    if v is None or isinstance(v, bool) or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    s = re.sub(r"^[£$€¥₹]|^[A-Z]{3}(?=\d)", "", s).rstrip("%")
    mult = 1.0
    if re.fullmatch(r"-?\d+(\.\d+)?[kmbKMB]", s):
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[s[-1].lower()]
        s = s[:-1]
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?(e[-+]?\d+)?", s, re.I):
        return None
    n = float(s) * mult
    return -n if neg else n


def _numeric(col: pd.Series) -> pd.Series | None:
    vals = col.dropna()
    if vals.empty:
        return None
    if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
        return pd.to_numeric(col, errors="coerce")
    if vals.map(to_number).notna().mean() >= 0.8:
        return col.map(to_number).astype("float64")
    return None


def _dates(col: pd.Series) -> pd.Series | None:
    vals = col.dropna()
    if vals.empty:
        return None
    if pd.api.types.is_datetime64_any_dtype(col):
        return col
    if all(_is_date(v) for v in vals.head(50)):
        return pd.to_datetime(col, errors="coerce")
    if pd.api.types.is_numeric_dtype(col):
        return None
    sample = vals.astype(str).head(200)
    if sample.str.fullmatch(r"\d+(\.\d+)?").mean() > 0.5:
        return None  # plain numbers are not dates
    if sample.map(period_of).notna().mean() >= 0.8:
        return pd.to_datetime(col.map(lambda v: period_of(v) if pd.notna(v) else None))
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
    if parsed.notna().mean() >= 0.8:
        return pd.to_datetime(col, errors="coerce", format="mixed", dayfirst=True)
    return None


def period_of(v) -> pd.Timestamp | None:
    """'Jan', 'Jan 2024', 'Q3 2024', '2024 Q3', 'FY2023', 2024, '2024-03' -> the period's first day."""
    if _is_date(v):
        return pd.Timestamp(v)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    m = _MONTH.match(s)
    if m:
        y = int(m.group(2)) if m.group(2) else 2000  # month without a year: used for order only
        y = y + 2000 if y < 100 else y
        return pd.Timestamp(year=y, month=_MONTHS.index(m.group(1)[:3].lower()) + 1, day=1)
    m = _QUARTER.match(s)
    if m:
        q, y = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        y = int(y) + 2000 if int(y) < 100 else int(y)
        return pd.Timestamp(year=y, month=3 * (int(q) - 1) + 1, day=1)
    if _YEARISH.match(s):
        return pd.Timestamp(year=int(re.sub(r"\D", "", s)), month=1, day=1)
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=1)
    return None


def _melt_wide(df: pd.DataFrame) -> pd.DataFrame | None:
    """Region | Jan | Feb | Mar ... -> Region | Period | Value, when 3+ columns are periods."""
    period_cols = [c for c in df.columns if period_of(c) is not None and _numeric(df[c]) is not None]
    if len(period_cols) < 3 or len(period_cols) < 0.5 * len(df.columns):
        return None
    ids = [c for c in df.columns if c not in period_cols]
    long = df.melt(id_vars=ids, value_vars=period_cols, var_name="Period", value_name="Value")
    long["Period"] = pd.to_datetime(long["Period"].map(period_of))
    long["Value"] = long["Value"].map(to_number)
    return long.sort_values("Period", kind="stable").reset_index(drop=True)


def tidy(name: str, raw: pd.DataFrame) -> Sheet | None:
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty or len(raw) < 2:
        return None
    notes = []
    h = _header_row(raw)
    if h:
        notes.append(f"Skipped {h} title row{'s' if h > 1 else ''} above the table.")
    df = raw.iloc[h + 1:].copy()
    df.columns = _names(raw.iloc[h].tolist())
    df = df.dropna(how="all").reset_index(drop=True)
    if len(df) > MAX_ROWS:
        notes.append(f"Used the first {MAX_ROWS:,} of {len(df):,} rows.")
        df = df.head(MAX_ROWS)
    # Total rows would double count: drop rows whose first text cell says Total.
    text_cols = [c for c in df.columns if df[c].map(lambda v: isinstance(v, str) and to_number(v) is None).any()]
    if text_cols:
        first = df[text_cols].map(lambda v: v if isinstance(v, str) and v.strip() else None).bfill(axis=1).iloc[:, 0]
        totals = first.fillna("").astype(str).str.match(_TOTAL)
        if totals.any():
            k = int(totals.sum())
            notes.append(f"Left out {k} total row{'s' if k > 1 else ''} so nothing is counted twice.")
            df = df[~totals].reset_index(drop=True)
    wide = _melt_wide(df)
    if wide is not None:
        notes.append("Turned the one-column-per-period layout into a time series.")
        df = wide
    for c in list(df.columns):
        d = None if _YEAR_NAME.match(c) else _dates(df[c])
        if d is not None:
            df[c] = d
            continue
        n = _numeric(df[c])
        df[c] = n if n is not None else df[c].map(lambda v: str(v).strip() if pd.notna(v) else None)
    if not any(pd.api.types.is_numeric_dtype(df[c]) for c in df.columns):
        return None
    return Sheet(name=str(name), df=df, notes=notes, wide=wide is not None)


# ── Column roles ─────────────────────────────────────────────────────────────

@dataclass
class Profile:
    rows: int
    measures: list[str]  # numbers worth adding up or averaging
    dimensions: list[str]  # categories to break a measure down by
    time: str | None  # the date/period column
    grain: str | None  # "day", "month", "quarter", "year"
    span: str | None  # "Jan 2023 to Dec 2024"

    def summary(self) -> str:
        bits = []
        if self.time:
            bits.append(f"time **{self.time}** ({'daily' if self.grain == 'day' else self.grain + 'ly'}, {self.span})")
        if self.measures:
            bits.append("numbers " + ", ".join(f"**{m}**" for m in self.measures[:6]) + (" and more" if len(self.measures) > 6 else ""))
        if self.dimensions:
            bits.append("categories " + ", ".join(f"**{d}**" for d in self.dimensions[:6]) + (" and more" if len(self.dimensions) > 6 else ""))
        return f"{self.rows:,} rows · " + " · ".join(bits)


def is_rate(measure: str) -> bool:
    """Rates, prices and scores are averaged; amounts are summed."""
    return bool(_RATE_NAME.search(measure))


def grain_of(dates: pd.Series) -> str:
    d = pd.Series(sorted(pd.to_datetime(dates.dropna()).unique()))
    if len(d) < 2:
        return "month"
    if (d.dt.month.eq(1) & d.dt.day.eq(1)).all():
        return "year"
    if d.dt.day.eq(1).all():
        quarterly = d.dt.month.isin([1, 4, 7, 10]).all() and d.diff().dt.days.median() > 80
        return "quarter" if quarterly else "month"
    span = (d.iloc[-1] - d.iloc[0]).days
    return "month" if span > 120 or len(d) > 120 else "day"


def label(ts: pd.Timestamp, grain: str) -> str:
    if grain == "year":
        return str(ts.year)
    if grain == "quarter":
        return f"Q{(ts.month - 1) // 3 + 1} {ts.year}"
    if grain == "month":
        return ts.strftime("%b") if ts.year == 2000 else ts.strftime("%b %Y")
    return ts.strftime("%d %b %Y")


def profile(sheet: Sheet) -> Profile:
    df = sheet.df
    n = len(df)
    time = next((c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c]) and df[c].nunique() >= 2), None)
    measures, dims = [], []
    for c in df.columns:
        col = df[c]
        if c == time:
            continue
        if pd.api.types.is_numeric_dtype(col):
            vals = col.dropna()
            if vals.empty or vals.nunique() <= 1:
                continue
            ints = bool((vals % 1 == 0).all())
            if _YEAR_NAME.match(c) and ints and vals.between(1900, 2100).all():
                if time is None:  # a Year column is the time axis when there is no date
                    df[c] = pd.to_datetime(col.map(lambda y: f"{int(y)}-01-01" if pd.notna(y) else None))
                    time = c
                continue
            if _ID_NAME.search(c) or (ints and vals.is_unique and vals.is_monotonic_increasing and n > 20):
                continue
            measures.append(c)
        else:
            k = col.nunique(dropna=True)
            if 2 <= k <= 60 and (k < 0.9 * n or n <= 30):
                dims.append(c)
    # Biggest-total amounts first: usually the headline number (Revenue before Discount).
    measures.sort(key=lambda m: (is_rate(m), -float(df[m].abs().sum())))
    dims.sort(key=lambda d: (df[d].nunique() > 25, -min(df[d].nunique(), 12)))
    grain = span = None
    if time:
        grain = grain_of(df[time])
        t = df[time].dropna()
        span = f"{label(t.min(), grain)} to {label(t.max(), grain)}"
    return Profile(rows=n, measures=measures, dimensions=dims, time=time, grain=grain, span=span)

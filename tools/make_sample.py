"""Builds samples/sample_workbook.xlsx: three tabs that exercise every chart style.

  Monthly Sales  long table: Date (daily), Region, Product, Revenue, Units, Discount % (24 months,
                 a seasonal peak in November, one region declining, two planted anomalies, the
                 last month cut short so the incomplete-period check fires)
  Spend by Dept  wide table as people keep it in Excel: a title row, one column per month and a
                 Total row at the bottom (tests header detection, total removal, wide-to-long)
  Customers      one row per customer: Segment, Tenure (months), Annual Spend, NPS score

Run: python tools/make_sample.py
"""
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent.parent / "samples" / "sample_workbook.xlsx"
REGIONS = {"North": 1.0, "South": 0.8, "East": 0.65, "West": 0.5, "Central": 0.3}
PRODUCTS = {"Alpha": 1.0, "Beta": 0.6, "Gamma": 0.35, "Delta": 0.15}


def monthly_sales(ws, rng: random.Random) -> None:
    ws.append(["Date", "Region", "Product", "Revenue", "Units", "Discount %"])
    start = date(2024, 1, 1)
    day = start
    while day <= date(2025, 12, 18):  # stops mid-December: the last month is incomplete
        m = (day.year - 2024) * 12 + day.month - 1
        season = 1 + 0.35 * (day.month == 11) + 0.2 * (day.month == 12) - 0.15 * (day.month in (1, 2))
        for region, rw in REGIONS.items():
            drift = 1 + 0.025 * m if region != "West" else 1 - 0.02 * m  # West declines
            for product, pw in PRODUCTS.items():
                if rng.random() < 0.55:
                    continue
                units = max(1, int(rng.gauss(40, 10) * rw * pw * season * drift))
                price = 120 if product == "Alpha" else 85 if product == "Beta" else 60
                disc = round(max(0, rng.gauss(8, 3)), 1)
                revenue = round(units * price * (1 - disc / 100), 2)
                ws.append([day, region, product, revenue, units, disc])
        day += timedelta(days=3)
    # Planted anomalies: a duplicated bulk order and a revenue keyed without its units.
    ws.append([date(2025, 3, 12), "East", "Alpha", 41_250.0, 375, 8.3])
    ws.append([date(2024, 8, 5), "North", "Beta", 23_900.0, 31, 6.0])


def spend_by_dept(ws, rng: random.Random) -> None:
    months = ["Jan 2025", "Feb 2025", "Mar 2025", "Apr 2025", "May 2025", "Jun 2025",
              "Jul 2025", "Aug 2025", "Sep 2025", "Oct 2025", "Nov 2025", "Dec 2025"]
    ws.append(["Operating spend by department (GBP)"])
    ws.append([])
    ws.append(["Department", *months])
    base = {"Technology": 420_000, "Operations": 310_000, "Marketing": 180_000, "People": 120_000, "Finance": 90_000, "Legal": 60_000}
    totals = [0.0] * 12
    for dept, b in base.items():
        row = []
        for i in range(12):
            v = b * (1 + 0.01 * i) * rng.uniform(0.93, 1.07)
            if dept == "Marketing" and i == 8:
                v *= 1.8  # campaign spike in September
            row.append(round(v))
            totals[i] += round(v)
        ws.append([dept, *row])
    ws.append(["Total", *totals])


def customers(ws, rng: random.Random) -> None:
    ws.append(["Customer ID", "Segment", "Tenure (months)", "Annual Spend", "NPS score"])
    for i in range(1, 401):
        seg = rng.choices(["Enterprise", "Mid-market", "SMB"], [0.15, 0.35, 0.5])[0]
        tenure = rng.randint(1, 96)
        base = {"Enterprise": 90_000, "Mid-market": 25_000, "SMB": 6_000}[seg]
        spend = round(base * (0.6 + tenure / 80) * rng.uniform(0.7, 1.3), 2)
        nps = max(0, min(10, round(rng.gauss(6 + tenure / 40, 1.8))))
        ws.append([f"C{i:04d}", seg, tenure, spend, nps])


def main() -> None:
    rng = random.Random(42)
    wb = Workbook()
    wb.active.title = "Monthly Sales"
    monthly_sales(wb.active, rng)
    spend_by_dept(wb.create_sheet("Spend by Dept"), rng)
    customers(wb.create_sheet("Customers"), rng)
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

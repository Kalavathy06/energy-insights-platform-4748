from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from src.api import db


def _site_total_kwh(site_id: int, start: datetime, end: datetime) -> float:
    row = db.fetch_one(
        """
        SELECT COALESCE(SUM(kwh), 0) AS total_kwh
        FROM interval_readings
        WHERE site_id=%s AND ts >= %s AND ts <= %s
        """,
        (site_id, start, end),
    )
    return float(row["total_kwh"]) if row else 0.0


def _group_site_ids(group_name: str) -> List[int]:
    rows = db.fetch_all(
        """
        SELECT s.id
        FROM sites s
        JOIN site_benchmark_groups sbg ON sbg.site_id = s.id
        JOIN benchmark_groups bg ON bg.id = sbg.benchmark_group_id
        WHERE bg.name = %s
        """,
        (group_name,),
    )
    return [int(r["id"]) for r in rows]


# PUBLIC_INTERFACE
def compute_benchmark(site_id: int, group_name: str, start: datetime, end: datetime) -> Dict[str, object]:
    """Compute simple benchmark comparing a site's total kWh to group's average total kWh."""
    site_total = _site_total_kwh(site_id, start, end)
    group_ids = _group_site_ids(group_name)

    totals: List[float] = []
    for sid in group_ids:
        totals.append(_site_total_kwh(int(sid), start, end))

    if len(totals) == 0:
        avg = 0.0
    else:
        avg = sum(totals) / float(len(totals))

    return {
        "site_total_kwh": float(site_total),
        "group_avg_total_kwh": float(avg),
        "group_site_count": int(len(totals)),
    }


def _recent_baseline(site_id: int, meter_id: str, ts: datetime, window_hours: int = 24) -> Optional[Tuple[float, int]]:
    """
    Compute baseline mean kWh over recent window (excluding current point).

    Returns (mean_kwh, count) or None if insufficient data.
    """
    start = ts - timedelta(hours=window_hours)
    row = db.fetch_one(
        """
        SELECT AVG(kwh) AS mean_kwh, COUNT(*) AS n
        FROM interval_readings
        WHERE site_id=%s AND meter_id=%s AND ts >= %s AND ts < %s
        """,
        (site_id, meter_id, start, ts),
    )
    if not row:
        return None
    n = int(row["n"] or 0)
    if n < 8:  # require at least 8 points for baseline
        return None
    mean = float(row["mean_kwh"] or 0.0)
    return mean, n


# PUBLIC_INTERFACE
def maybe_create_anomaly_alert(site_id: int, meter_id: str, ts: datetime, kwh: float) -> Optional[int]:
    """
    Very simple anomaly detection:
    - Compute 24h baseline mean for same meter/site.
    - Spike if kwh > 2.5x mean (high) or > 1.8x mean (medium)
    - Drop if kwh < 0.3x mean (medium) or < 0.15x mean (high)

    Returns created alert id if alert inserted, else None.
    """
    baseline = _recent_baseline(site_id, meter_id, ts)
    if not baseline:
        return None
    mean_kwh, _n = baseline
    if mean_kwh <= 0:
        return None

    severity = None
    alert_type = None
    title = None
    details = None

    ratio = kwh / mean_kwh if mean_kwh else 0.0

    if ratio >= 2.5:
        severity = "high"
        alert_type = "spike"
        title = "High usage spike detected"
        details = f"Interval kWh {kwh:.2f} is {ratio:.2f}x baseline mean {mean_kwh:.2f}."
    elif ratio >= 1.8:
        severity = "medium"
        alert_type = "spike"
        title = "Usage spike detected"
        details = f"Interval kWh {kwh:.2f} is {ratio:.2f}x baseline mean {mean_kwh:.2f}."
    elif ratio <= 0.15:
        severity = "high"
        alert_type = "drop"
        title = "Severe usage drop detected"
        details = f"Interval kWh {kwh:.2f} is {ratio:.2f}x baseline mean {mean_kwh:.2f}."
    elif ratio <= 0.30:
        severity = "medium"
        alert_type = "drop"
        title = "Usage drop detected"
        details = f"Interval kWh {kwh:.2f} is {ratio:.2f}x baseline mean {mean_kwh:.2f}."

    if not severity:
        return None

    db.execute(
        """
        INSERT INTO anomaly_alerts (site_id, meter_id, ts, severity, alert_type, title, details, acknowledged)
        VALUES (%s,%s,%s,%s,%s,%s,%s,0)
        """,
        (site_id, meter_id, ts, severity, alert_type, title, details),
    )
    row = db.fetch_one("SELECT LAST_INSERT_ID() AS id")
    return int(row["id"]) if row and row.get("id") is not None else None

from datetime import datetime
import json
import os
from typing import List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from src.api import db
from src.api.models import (
    AcknowledgeAlertResponse,
    AlertOut,
    BenchmarkResponse,
    SiteCreate,
    SiteOut,
    TimeseriesResponse,
    UploadReadingsRequest,
    UploadReadingsResponse,
)
from src.api.services import compute_benchmark, maybe_create_anomaly_alert


openapi_tags = [
    {"name": "Health", "description": "Service health and documentation helpers."},
    {"name": "Sites", "description": "Manage customer sites and metadata."},
    {"name": "Ingestion", "description": "Upload/ingest interval meter readings."},
    {"name": "Analytics", "description": "Timeseries and benchmarking analytics."},
    {"name": "Alerts", "description": "Anomaly alerts for proactive energy management."},
]

app = FastAPI(
    title="Energy Insights Platform API",
    description=(
        "Backend for commercial energy interval data ingestion, trend visualization, benchmarking, and anomaly alerts.\n\n"
        "Environment variables used:\n"
        "- ALLOWED_ORIGINS, ALLOWED_HEADERS, ALLOWED_METHODS\n"
        "- MYSQL_URL, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB, MYSQL_PORT\n"
    ),
    version="0.1.0",
    openapi_tags=openapi_tags,
)


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


allowed_origins = _split_csv(os.getenv("ALLOWED_ORIGINS", "*"))
allowed_headers = _split_csv(os.getenv("ALLOWED_HEADERS", "*"))
allowed_methods = _split_csv(os.getenv("ALLOWED_METHODS", "*"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=allowed_methods if allowed_methods else ["*"],
    allow_headers=allowed_headers if allowed_headers else ["*"],
)


@app.get("/", tags=["Health"], summary="Health check")
def health_check():
    """
    Health check endpoint.

    Returns:
        JSON with a simple status message.
    """
    return {"message": "Healthy"}


@app.get("/docs/help", response_class=PlainTextResponse, tags=["Health"], summary="Docs usage help")
def docs_help() -> str:
    """
    Documentation usage help.

    Returns:
        Plain text notes on how to use the API endpoints.
    """
    return (
        "Energy Insights Platform API\n"
        "\n"
        "Common flow:\n"
        "1) POST /sites to create a site\n"
        "2) POST /readings/upload to ingest interval data\n"
        "3) GET  /analytics/timeseries?site_id=...&meter_id=...&start=...&end=...\n"
        "4) GET  /alerts?site_id=...\n"
        "5) GET  /analytics/benchmark?site_id=...&group_name=...&start=...&end=...\n"
    )


@app.get("/sites", response_model=List[SiteOut], tags=["Sites"], summary="List sites")
def list_sites(customer_name: Optional[str] = Query(None, description="Filter by customer name (optional).")):
    """
    List sites, optionally filtered by customer.

    Args:
        customer_name: Optional customer name to filter.

    Returns:
        List of sites.
    """
    if customer_name:
        rows = db.fetch_all(
            "SELECT id, customer_name, name, timezone, sqft, industry FROM sites WHERE customer_name=%s ORDER BY id DESC",
            (customer_name,),
        )
    else:
        rows = db.fetch_all("SELECT id, customer_name, name, timezone, sqft, industry FROM sites ORDER BY id DESC")
    return rows


@app.post("/sites", response_model=SiteOut, tags=["Sites"], summary="Create a site")
def create_site(payload: SiteCreate = Body(...)):
    """
    Create a new site record.

    Args:
        payload: Site metadata.

    Returns:
        The created site.
    """
    db.execute(
        """
        INSERT INTO sites (customer_name, name, timezone, sqft, industry)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (payload.customer_name, payload.name, payload.timezone, payload.sqft, payload.industry),
    )
    row = db.fetch_one("SELECT LAST_INSERT_ID() AS id")
    site_id = int(row["id"]) if row and row.get("id") is not None else None
    if not site_id:
        raise HTTPException(status_code=500, detail="Failed to create site.")

    created = db.fetch_one(
        "SELECT id, customer_name, name, timezone, sqft, industry FROM sites WHERE id=%s",
        (site_id,),
    )
    return created


@app.post(
    "/readings/upload",
    response_model=UploadReadingsResponse,
    tags=["Ingestion"],
    summary="Upload interval readings",
)
def upload_readings(payload: UploadReadingsRequest = Body(...)):
    """
    Upload a batch of interval readings.

    Notes:
    - Uses unique key (site_id, meter_id, ts) to avoid duplicates.
    - For each inserted reading, a lightweight anomaly check may create an alert.

    Returns:
        inserted and skipped_duplicates counts.
    """
    inserted = 0
    skipped = 0

    for r in payload.readings:
        # Insert with duplicate ignore semantics
        affected = db.execute(
            """
            INSERT INTO interval_readings (site_id, meter_id, ts, kwh, demand_kw, quality_flag)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              kwh = kwh
            """,
            (r.site_id, r.meter_id, r.ts, r.kwh, r.demand_kw, r.quality_flag),
        )
        # For MySQL, affected can be 1 for insert; 2 for update; 0 for no-op.
        # We used "kwh = kwh" update, so duplicates typically report 2 or 0 depending on MySQL settings.
        # We treat 1 as inserted, otherwise skipped.
        if affected == 1:
            inserted += 1
            # best-effort alert creation
            try:
                maybe_create_anomaly_alert(r.site_id, r.meter_id, r.ts, float(r.kwh))
            except Exception:
                # Don't fail ingestion for alerting issues
                pass
        else:
            skipped += 1

    return UploadReadingsResponse(inserted=inserted, skipped_duplicates=skipped)


@app.get(
    "/analytics/timeseries",
    response_model=TimeseriesResponse,
    tags=["Analytics"],
    summary="Get site timeseries",
)
def get_timeseries(
    site_id: int = Query(..., description="Site ID"),
    meter_id: str = Query(..., description="Meter ID"),
    start: datetime = Query(..., description="Start datetime (ISO8601)"),
    end: datetime = Query(..., description="End datetime (ISO8601)"),
):
    """
    Retrieve timeseries points for a site/meter between start and end.

    Returns:
        Ordered list of interval points.
    """
    rows = db.fetch_all(
        """
        SELECT ts, kwh, demand_kw
        FROM interval_readings
        WHERE site_id=%s AND meter_id=%s AND ts >= %s AND ts <= %s
        ORDER BY ts ASC
        """,
        (site_id, meter_id, start, end),
    )
    return {
        "site_id": site_id,
        "meter_id": meter_id,
        "start": start,
        "end": end,
        "points": rows,
    }


@app.get("/alerts", response_model=List[AlertOut], tags=["Alerts"], summary="List alerts")
def list_alerts(
    site_id: Optional[int] = Query(None, description="Optional site filter"),
    acknowledged: Optional[bool] = Query(None, description="Optional acknowledged filter"),
    limit: int = Query(50, ge=1, le=500, description="Max number of alerts to return."),
):
    """
    List anomaly alerts (most recent first).

    Args:
        site_id: Optional filter.
        acknowledged: Optional filter.
        limit: Limit number of results.

    Returns:
        List of alerts.
    """
    where = []
    params: List[object] = []
    if site_id is not None:
        where.append("site_id=%s")
        params.append(site_id)
    if acknowledged is not None:
        where.append("acknowledged=%s")
        params.append(1 if acknowledged else 0)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetch_all(
        f"""
        SELECT id, site_id, meter_id, ts, severity, alert_type, title, details, acknowledged
        FROM anomaly_alerts
        {where_sql}
        ORDER BY ts DESC
        LIMIT %s
        """,
        tuple(params + [limit]),
    )
    # MySQL returns 0/1 int for acknowledged; normalize to bool
    for r in rows:
        r["acknowledged"] = bool(r.get("acknowledged"))
    return rows


@app.post(
    "/alerts/{alert_id}/ack",
    response_model=AcknowledgeAlertResponse,
    tags=["Alerts"],
    summary="Acknowledge an alert",
)
def acknowledge_alert(alert_id: int):
    """
    Mark an alert as acknowledged.

    Args:
        alert_id: Alert ID

    Returns:
        Acknowledge result.
    """
    existing = db.fetch_one(
        "SELECT id, acknowledged FROM anomaly_alerts WHERE id=%s",
        (alert_id,),
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Alert not found")

    db.execute("UPDATE anomaly_alerts SET acknowledged=1 WHERE id=%s", (alert_id,))
    return {"id": alert_id, "acknowledged": True}


@app.get(
    "/analytics/benchmark",
    response_model=BenchmarkResponse,
    tags=["Analytics"],
    summary="Benchmark site vs group",
)
def benchmark(
    site_id: int = Query(..., description="Site ID"),
    group_name: str = Query(..., description="Benchmark group name"),
    start: datetime = Query(..., description="Start datetime (ISO8601)"),
    end: datetime = Query(..., description="End datetime (ISO8601)"),
):
    """
    Benchmark a site against similar sites (by benchmark group).

    Returns:
        Site total kWh vs group average total kWh.
    """
    out = compute_benchmark(site_id=site_id, group_name=group_name, start=start, end=end)
    return {
        "site_id": site_id,
        "group_name": group_name,
        "start": start,
        "end": end,
        **out,
    }


@app.get("/admin/seed-demo", tags=["Health"], summary="Seed demo data (dev only)")
def seed_demo():
    """
    Seed minimal demo data into database for UI exploration.

    This is intended for development environments; it is idempotent.
    """
    # Create a couple sites if none exist
    count_row = db.fetch_one("SELECT COUNT(*) AS n FROM sites")
    n = int(count_row["n"]) if count_row else 0
    if n == 0:
        db.execute(
            "INSERT INTO sites (customer_name, name, timezone, sqft, industry) VALUES (%s,%s,%s,%s,%s)",
            ("Acme Retail", "Acme - Downtown", "UTC", 25000, "Retail"),
        )
        db.execute(
            "INSERT INTO sites (customer_name, name, timezone, sqft, industry) VALUES (%s,%s,%s,%s,%s)",
            ("Acme Retail", "Acme - Airport", "UTC", 18000, "Retail"),
        )

    # Ensure sites are in Retail group
    retail = db.fetch_one("SELECT id FROM benchmark_groups WHERE name='Retail'")
    if retail:
        retail_id = int(retail["id"])
        site_rows = db.fetch_all("SELECT id FROM sites")
        for s in site_rows:
            db.execute(
                "INSERT IGNORE INTO site_benchmark_groups (site_id, benchmark_group_id) VALUES (%s,%s)",
                (int(s["id"]), retail_id),
            )

    return {"status": "ok"}

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SiteCreate(BaseModel):
    customer_name: str = Field(..., description="Customer/organization name that owns the site.")
    name: str = Field(..., description="Friendly site name.")
    timezone: str = Field("UTC", description="IANA timezone for the site, e.g. 'America/New_York'.")
    sqft: Optional[int] = Field(None, description="Optional square footage.")
    industry: Optional[str] = Field(None, description="Optional industry label for benchmarking.")


class SiteOut(BaseModel):
    id: int = Field(..., description="Site ID.")
    customer_name: str = Field(..., description="Customer/organization name.")
    name: str = Field(..., description="Friendly site name.")
    timezone: str = Field(..., description="IANA timezone.")
    sqft: Optional[int] = Field(None, description="Square footage.")
    industry: Optional[str] = Field(None, description="Industry label.")


class IntervalReadingIn(BaseModel):
    site_id: int = Field(..., description="Site ID for the reading.")
    meter_id: str = Field(..., description="Meter identifier (as provided by customer/utility).")
    ts: datetime = Field(..., description="Timestamp of the interval reading (ISO8601).")
    kwh: float = Field(..., description="Energy consumed during interval (kWh).")
    demand_kw: Optional[float] = Field(None, description="Optional demand in kW for that interval.")
    quality_flag: Optional[str] = Field(None, description="Optional quality flag (e.g., estimated, missing).")


class UploadReadingsRequest(BaseModel):
    readings: List[IntervalReadingIn] = Field(..., description="List of interval readings to ingest.")


class UploadReadingsResponse(BaseModel):
    inserted: int = Field(..., description="Count of readings inserted.")
    skipped_duplicates: int = Field(..., description="Count of readings skipped due to duplicate key.")


class TimeseriesPoint(BaseModel):
    ts: datetime = Field(..., description="Timestamp of point.")
    kwh: float = Field(..., description="kWh value.")
    demand_kw: Optional[float] = Field(None, description="Demand kW value, if available.")


class TimeseriesResponse(BaseModel):
    site_id: int = Field(..., description="Site ID.")
    meter_id: str = Field(..., description="Meter ID.")
    start: datetime = Field(..., description="Start datetime inclusive.")
    end: datetime = Field(..., description="End datetime inclusive.")
    points: List[TimeseriesPoint] = Field(..., description="Timeseries points ordered by timestamp.")


class AlertOut(BaseModel):
    id: int = Field(..., description="Alert ID.")
    site_id: int = Field(..., description="Site ID.")
    meter_id: str = Field(..., description="Meter ID.")
    ts: datetime = Field(..., description="Alert timestamp.")
    severity: str = Field(..., description="Severity: low|medium|high")
    alert_type: str = Field(..., description="Alert type: spike|drop|missing|etc.")
    title: str = Field(..., description="Short title.")
    details: Optional[str] = Field(None, description="Optional details.")
    acknowledged: bool = Field(..., description="Whether acknowledged.")


class AcknowledgeAlertResponse(BaseModel):
    id: int = Field(..., description="Alert ID.")
    acknowledged: bool = Field(..., description="New acknowledged state.")


class BenchmarkResponse(BaseModel):
    site_id: int = Field(..., description="Site ID.")
    group_name: str = Field(..., description="Benchmark group name.")
    start: datetime = Field(..., description="Start datetime inclusive.")
    end: datetime = Field(..., description="End datetime inclusive.")
    site_total_kwh: float = Field(..., description="Total kWh for site in range.")
    group_avg_total_kwh: float = Field(..., description="Average total kWh across sites in group in range.")
    group_site_count: int = Field(..., description="Number of sites used in benchmark.")

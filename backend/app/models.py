from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class CommuteMode(StrEnum):
    TRANSIT = "transit"
    DRIVING = "driving"
    WALKING = "walking"
    BICYCLING = "bicycling"


class Destination(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    address: str = Field(min_length=2, max_length=120)
    weight: float = Field(default=1, gt=0, le=1)
    max_minutes: int = Field(default=45, ge=5, le=180)


class RentalPreferences(BaseModel):
    city: str = "上海"
    districts: list[str] = []
    monthly_rent_max: int = Field(ge=1000, le=100000)
    monthly_total_max: int = Field(ge=1000, le=120000)
    bedrooms_min: int = Field(default=1, ge=1, le=8)
    area_min: int = Field(default=20, ge=5, le=500)
    rental_type: Literal["entire", "shared", "either"] = "entire"
    move_in_date: str
    lease_months: int = Field(default=12, ge=1, le=60)
    accepts_agent_fee: bool = False
    needs_elevator: bool = False
    allows_pets: bool = False
    commute_mode: CommuteMode = CommuteMode.TRANSIT
    destinations: list[Destination] = Field(min_length=1, max_length=4)
    soft_preferences: list[str] = []


class Listing(BaseModel):
    id: str
    title: str
    district: str
    neighborhood: str
    address: str
    monthly_rent: int
    service_fee_monthly: int = 0
    property_fee_monthly: int = 0
    utilities_estimate: int = 300
    agent_fee_once: int = 0
    deposit_months: int = 1
    bedrooms: int
    area_sqm: int
    floor: int
    has_elevator: bool
    allows_pets: bool
    rental_type: Literal["entire", "shared"]
    latitude: float
    longitude: float
    image_url: HttpUrl
    source_name: str
    source_url: HttpUrl
    tags: list[str]


class CommuteResult(BaseModel):
    destination: str
    minutes: int
    distance_km: float
    within_limit: bool


class ListingRecommendation(BaseModel):
    listing: Listing
    monthly_true_cost: int
    first_month_cash: int
    weighted_commute_minutes: float
    worst_commute_minutes: int
    weekly_total_commute_minutes: int
    commute_fairness_gap_minutes: int
    commutes: list[CommuteResult]
    hard_constraints_passed: bool
    score: float
    reasons: list[str]
    tradeoffs: list[str]


class SearchResponse(BaseModel):
    search_id: str | None = None
    provider: str
    total_candidates: int
    recommendations: list[ListingRecommendation]
    assumptions: list[str]

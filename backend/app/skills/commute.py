import asyncio

from pydantic import BaseModel

from app.models import CommuteResult, Listing, RentalPreferences
from app.providers.base import MapProvider


class CommuteAnalysis(BaseModel):
    provider: str
    commutes: list[CommuteResult]
    weighted_minutes: float
    worst_minutes: int
    weekly_total_minutes: int
    fairness_gap_minutes: int


class CommutePlanningSkill:
    """Required deterministic skill for all listing evaluations."""

    name = "commute_planning"

    def __init__(self, provider: MapProvider):
        self.provider = provider

    async def calculate(self, listing: Listing, preferences: RentalPreferences) -> CommuteAnalysis:
        commutes = await asyncio.gather(*[self.provider.commute(listing, destination, preferences.commute_mode) for destination in preferences.destinations])
        weights = [destination.weight for destination in preferences.destinations]
        weighted = sum(commute.minutes * weight for commute, weight in zip(commutes, weights)) / sum(weights)
        minutes = [commute.minutes for commute in commutes]
        return CommuteAnalysis(
            provider=self.provider.name,
            commutes=commutes,
            weighted_minutes=round(weighted, 1),
            worst_minutes=max(minutes),
            weekly_total_minutes=sum(minutes) * 2 * 5,
            fairness_gap_minutes=max(minutes) - min(minutes),
        )

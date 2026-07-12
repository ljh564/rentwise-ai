import asyncio

from app.models import Listing, ListingRecommendation, RentalPreferences, SearchResponse
from app.providers.base import ListingProvider, MapProvider


def true_cost(listing: Listing, months: int) -> tuple[int, int]:
    monthly = listing.monthly_rent + listing.service_fee_monthly + listing.property_fee_monthly + listing.utilities_estimate + round(listing.agent_fee_once / months)
    first_month = listing.monthly_rent * (1 + listing.deposit_months) + listing.service_fee_monthly + listing.property_fee_monthly + listing.agent_fee_once
    return monthly, first_month


def hard_constraints(listing: Listing, prefs: RentalPreferences, monthly: int) -> list[str]:
    failures = []
    if listing.monthly_rent > prefs.monthly_rent_max: failures.append("挂牌租金超出上限")
    if monthly > prefs.monthly_total_max: failures.append("真实月均成本超出上限")
    if listing.bedrooms < prefs.bedrooms_min: failures.append("卧室数量不足")
    if listing.area_sqm < prefs.area_min: failures.append("面积不足")
    if prefs.rental_type != "either" and listing.rental_type != prefs.rental_type: failures.append("出租方式不匹配")
    if prefs.needs_elevator and not listing.has_elevator: failures.append("无电梯")
    if prefs.allows_pets and not listing.allows_pets: failures.append("不允许养宠")
    if not prefs.accepts_agent_fee and listing.agent_fee_once > 0: failures.append("包含中介费")
    if prefs.districts and listing.district not in prefs.districts: failures.append("不在目标区域")
    return failures


class RentalDecisionService:
    def __init__(self, listing_provider: ListingProvider, map_provider: MapProvider):
        self.listings = listing_provider
        self.maps = map_provider

    async def search(self, prefs: RentalPreferences) -> SearchResponse:
        candidates = await self.listings.search(prefs)
        output = []
        for listing in candidates:
            commutes = await asyncio.gather(*[self.maps.commute(listing, d, prefs.commute_mode) for d in prefs.destinations])
            monthly, first_month = true_cost(listing, prefs.lease_months)
            failures = hard_constraints(listing, prefs, monthly)
            for commute in commutes:
                if not commute.within_limit: failures.append(f"到{commute.destination}通勤超时")
            weights = [d.weight for d in prefs.destinations]
            weighted = sum(c.minutes * w for c, w in zip(commutes, weights)) / sum(weights)
            preference_hits = [tag for tag in listing.tags if tag in prefs.soft_preferences]
            cost_score = max(0, 35 - max(0, monthly - prefs.monthly_total_max * .75) / 120)
            commute_score = max(0, 35 - weighted * .5)
            score = round(cost_score + commute_score + len(preference_hits) * 5 + (20 if not failures else max(0, 8 - len(failures) * 2)), 1)
            reasons = [f"真实月均成本约 ¥{monthly:,}", f"加权单程通勤约 {weighted:.0f} 分钟"]
            if preference_hits: reasons.append("符合偏好：" + "、".join(preference_hits))
            tradeoffs = failures or (["首月现金支出较高"] if first_month > monthly * 2.2 else ["暂无明显硬性冲突，仍需线下核验"])
            output.append(ListingRecommendation(listing=listing, monthly_true_cost=monthly, first_month_cash=first_month, weighted_commute_minutes=round(weighted, 1), commutes=commutes, hard_constraints_passed=not failures, score=score, reasons=reasons, tradeoffs=tradeoffs))
        output.sort(key=lambda item: (item.hard_constraints_passed, item.score), reverse=True)
        commute_assumption = "通勤时间来自高德地图实时路线规划" if self.maps.name == "amap" else "通勤时间为开发测试用模拟数据"
        return SearchResponse(provider=f"{self.listings.name} + {self.maps.name}", total_candidates=len(candidates), recommendations=output, assumptions=["当前房源为模拟上海房源", commute_assumption, "水电燃气统一按每月 ¥300 估算", "Agent判断不替代线下房源核验"])

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

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


class DecisionState(TypedDict, total=False):
    preferences: RentalPreferences
    candidates: list[Listing]
    recommendations: list[ListingRecommendation]
    response: SearchResponse
    trace: list[str]


class RentalDecisionService:
    def __init__(self, listing_provider: ListingProvider, map_provider: MapProvider):
        self.listings = listing_provider
        self.maps = map_provider
        builder = StateGraph(DecisionState)
        builder.add_node("search_candidates", self._search_candidates)
        builder.add_node("evaluate_and_rank", self._evaluate_and_rank)
        builder.add_node("finalize_response", self._finalize_response)
        builder.add_edge(START, "search_candidates")
        builder.add_edge("search_candidates", "evaluate_and_rank")
        builder.add_edge("evaluate_and_rank", "finalize_response")
        builder.add_edge("finalize_response", END)
        self.graph = builder.compile()

    async def _search_candidates(self, state: DecisionState) -> DecisionState:
        candidates = await self.listings.search(state["preferences"])
        return {"candidates": candidates, "trace": [*state.get("trace", []), "search_candidates"]}

    async def _evaluate_and_rank(self, state: DecisionState) -> DecisionState:
        prefs = state["preferences"]
        output = []
        for listing in state["candidates"]:
            commutes = await asyncio.gather(*[self.maps.commute(listing, destination, prefs.commute_mode) for destination in prefs.destinations])
            monthly, first_month = true_cost(listing, prefs.lease_months)
            failures = hard_constraints(listing, prefs, monthly)
            for commute in commutes:
                if not commute.within_limit: failures.append(f"到{commute.destination}通勤超时")
            weights = [destination.weight for destination in prefs.destinations]
            weighted = sum(commute.minutes * weight for commute, weight in zip(commutes, weights)) / sum(weights)
            worst_commute = max(commute.minutes for commute in commutes)
            fairness_gap = max(commute.minutes for commute in commutes) - min(commute.minutes for commute in commutes)
            weekly_total = sum(commute.minutes * 2 * 5 for commute in commutes)
            preference_hits = [tag for tag in listing.tags if tag in prefs.soft_preferences]
            cost_score = max(0, 35 - max(0, monthly - prefs.monthly_total_max * .75) / 120)
            commute_score = max(0, 35 - weighted * .5)
            fairness_penalty = fairness_gap * .15 if len(commutes) > 1 else 0
            score = round(max(0, cost_score + commute_score + len(preference_hits) * 5 + (20 if not failures else max(0, 8 - len(failures) * 2)) - fairness_penalty), 1)
            reasons = [f"真实月均成本约 ¥{monthly:,}", f"加权单程通勤约 {weighted:.0f} 分钟"]
            if len(commutes) > 1: reasons.append(f"最差单程 {worst_commute} 分钟，家庭通勤差距 {fairness_gap} 分钟")
            if preference_hits: reasons.append("符合偏好：" + "、".join(preference_hits))
            tradeoffs = failures or (["首月现金支出较高"] if first_month > monthly * 2.2 else ["暂无明显硬性冲突，仍需线下核验"])
            output.append(ListingRecommendation(listing=listing, monthly_true_cost=monthly, first_month_cash=first_month, weighted_commute_minutes=round(weighted, 1), worst_commute_minutes=worst_commute, weekly_total_commute_minutes=weekly_total, commute_fairness_gap_minutes=fairness_gap, commutes=commutes, hard_constraints_passed=not failures, score=score, reasons=reasons, tradeoffs=tradeoffs))
        output.sort(key=lambda item: (item.hard_constraints_passed, item.score), reverse=True)
        return {"recommendations": output, "trace": [*state.get("trace", []), "evaluate_and_rank"]}

    async def _finalize_response(self, state: DecisionState) -> DecisionState:
        commute_assumption = "通勤时间来自高德地图实时路线规划" if self.maps.name == "amap" else "通勤时间为开发测试用模拟数据"
        response = SearchResponse(provider=f"{self.listings.name} + {self.maps.name}", total_candidates=len(state["candidates"]), recommendations=state["recommendations"], assumptions=["当前房源为模拟上海房源", commute_assumption, "水电燃气统一按每月 ¥300 估算", "Agent判断不替代线下房源核验"])
        return {"response": response, "trace": [*state.get("trace", []), "finalize_response"]}

    async def search_with_trace(self, prefs: RentalPreferences) -> tuple[SearchResponse, list[str]]:
        state = await self.graph.ainvoke({"preferences": prefs, "trace": []})
        return state["response"], state["trace"]

    async def search(self, prefs: RentalPreferences) -> SearchResponse:
        response, _ = await self.search_with_trace(prefs)
        return response

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.llm import LLMError, OpenAICompatibleLLM
from app.models import Listing, ListingRecommendation, RentalPreferences, SearchResponse
from app.providers.base import ListingProvider, MapProvider
from app.skills.commute import CommutePlanningSkill


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
    preference_aliases: dict[str, str]
    unverified_preferences: list[str]
    llm_enhanced: bool
    llm_preferences_parsed: bool
    llm_explanations_generated: bool
    llm_tokens: int
    feedback_adjustments: dict[str, float]


class RentalDecisionService:
    def __init__(self, listing_provider: ListingProvider, map_provider: MapProvider, llm: OpenAICompatibleLLM | None = None):
        self.listings = listing_provider
        self.maps = map_provider
        self.commute_skill = CommutePlanningSkill(map_provider)
        self.llm = llm
        builder = StateGraph(DecisionState)
        builder.add_node("search_candidates", self._search_candidates)
        builder.add_node("interpret_preferences", self._interpret_preferences)
        builder.add_node("evaluate_and_rank", self._evaluate_and_rank)
        builder.add_node("explain_recommendations", self._explain_recommendations)
        builder.add_node("finalize_response", self._finalize_response)
        builder.add_edge(START, "search_candidates")
        builder.add_edge("search_candidates", "interpret_preferences")
        builder.add_edge("interpret_preferences", "evaluate_and_rank")
        builder.add_edge("evaluate_and_rank", "explain_recommendations")
        builder.add_edge("explain_recommendations", "finalize_response")
        builder.add_edge("finalize_response", END)
        self.graph = builder.compile()

    async def _search_candidates(self, state: DecisionState) -> DecisionState:
        candidates = await self.listings.search(state["preferences"])
        return {"candidates": candidates, "trace": [*state.get("trace", []), "search_candidates"]}

    async def _interpret_preferences(self, state: DecisionState) -> DecisionState:
        preferences = state["preferences"].soft_preferences
        available_tags = sorted({tag for listing in state["candidates"] for tag in listing.tags})
        aliases = {preference: preference for preference in preferences if preference in available_tags}
        unverified = [preference for preference in preferences if preference not in aliases]
        tokens = state.get("llm_tokens", 0)
        enhanced = state.get("llm_enhanced", False)
        if self.llm and self.llm.enabled and preferences:
            try:
                result, used = await self.llm.complete_json(
                    "你是租房偏好结构化器。只能把用户偏好映射到提供的房源标签；没有可靠对应时 matched_tag 必须为 null。不得创造标签。仅输出 JSON。",
                    {"preferences": preferences, "available_tags": available_tags, "output_schema": {"mappings": [{"original": "string", "matched_tag": "string|null"}]}},
                    max_tokens=500,
                )
                for item in result.get("mappings", []):
                    original, matched = item.get("original"), item.get("matched_tag")
                    if original in preferences and matched in available_tags:
                        aliases[original] = matched
                unverified = [preference for preference in preferences if preference not in aliases]
                tokens += used
                enhanced = True
                preferences_parsed = True
            except LLMError:
                preferences_parsed = False
        else:
            preferences_parsed = False
        return {"preference_aliases": aliases, "unverified_preferences": unverified, "llm_tokens": tokens, "llm_enhanced": enhanced, "llm_preferences_parsed": preferences_parsed, "trace": [*state.get("trace", []), "interpret_preferences"]}

    async def _evaluate_and_rank(self, state: DecisionState) -> DecisionState:
        prefs = state["preferences"]
        output = []
        for listing in state["candidates"]:
            commute_analysis = await self.commute_skill.calculate(listing, prefs)
            commutes = commute_analysis.commutes
            monthly, first_month = true_cost(listing, prefs.lease_months)
            failures = hard_constraints(listing, prefs, monthly)
            for commute in commutes:
                if not commute.within_limit: failures.append(f"到{commute.destination}通勤超时")
            weighted = commute_analysis.weighted_minutes
            worst_commute = commute_analysis.worst_minutes
            fairness_gap = commute_analysis.fairness_gap_minutes
            weekly_total = commute_analysis.weekly_total_minutes
            matched_tags = set(state.get("preference_aliases", {}).values())
            preference_hits = [tag for tag in listing.tags if tag in matched_tags]
            cost_score = max(0, 35 - max(0, monthly - prefs.monthly_total_max * .75) / 120)
            commute_score = max(0, 35 - weighted * .5)
            fairness_penalty = fairness_gap * .15 if len(commutes) > 1 else 0
            feedback_bonus = state.get("feedback_adjustments", {}).get(listing.id, 0)
            score = round(max(0, cost_score + commute_score + len(preference_hits) * 5 + (20 if not failures else max(0, 8 - len(failures) * 2)) - fairness_penalty + feedback_bonus), 1)
            reasons = [f"真实月均成本约 ¥{monthly:,}", f"加权单程通勤约 {weighted:.0f} 分钟"]
            if len(commutes) > 1: reasons.append(f"最差单程 {worst_commute} 分钟，家庭通勤差距 {fairness_gap} 分钟")
            if preference_hits: reasons.append("符合偏好：" + "、".join(preference_hits))
            if feedback_bonus: reasons.append(f"历史反馈调整 {feedback_bonus:+.0f} 分")
            tradeoffs = failures or (["首月现金支出较高"] if first_month > monthly * 2.2 else ["暂无明显硬性冲突，仍需线下核验"])
            output.append(ListingRecommendation(listing=listing, monthly_true_cost=monthly, first_month_cash=first_month, weighted_commute_minutes=round(weighted, 1), worst_commute_minutes=worst_commute, weekly_total_commute_minutes=weekly_total, commute_fairness_gap_minutes=fairness_gap, commutes=commutes, hard_constraints_passed=not failures, score=score, reasons=reasons, tradeoffs=tradeoffs))
        output.sort(key=lambda item: (item.hard_constraints_passed, item.score), reverse=True)
        return {"recommendations": output, "trace": [*state.get("trace", []), "evaluate_and_rank"]}

    async def _explain_recommendations(self, state: DecisionState) -> DecisionState:
        recommendations = state["recommendations"]
        tokens = state.get("llm_tokens", 0)
        enhanced = state.get("llm_enhanced", False)
        if self.llm and self.llm.enabled and recommendations:
            evidence = [{"listing_id": item.listing.id, "title": item.listing.title, "monthly_true_cost": item.monthly_true_cost, "weighted_commute_minutes": item.weighted_commute_minutes, "worst_commute_minutes": item.worst_commute_minutes, "hard_constraints_passed": item.hard_constraints_passed, "verified_reasons": item.reasons, "verified_tradeoffs": item.tradeoffs} for item in recommendations[:3]]
            try:
                result, used = await self.llm.complete_json(
                    "你是租房决策解释器。只能重述输入证据，不得修改数字、添加设施或声称房源真实有效。每套房输出 2-3 条简短理由和 1-3 条取舍。仅输出 JSON。",
                    {"listings": evidence, "unverified_preferences": state.get("unverified_preferences", []), "output_schema": {"items": [{"listing_id": "string", "reasons": ["string"], "tradeoffs": ["string"]}]}},
                    max_tokens=1000,
                )
                by_id = {item.get("listing_id"): item for item in result.get("items", [])}
                for recommendation in recommendations[:3]:
                    explanation = by_id.get(recommendation.listing.id)
                    if not explanation:
                        continue
                    reasons = [text for text in explanation.get("reasons", []) if isinstance(text, str) and text.strip()][:3]
                    tradeoffs = [text for text in explanation.get("tradeoffs", []) if isinstance(text, str) and text.strip()][:3]
                    if reasons: recommendation.reasons = reasons
                    if tradeoffs: recommendation.tradeoffs = tradeoffs
                tokens += used
                enhanced = True
                explanations_generated = True
            except LLMError:
                explanations_generated = False
        else:
            explanations_generated = False
        return {"recommendations": recommendations, "llm_tokens": tokens, "llm_enhanced": enhanced, "llm_explanations_generated": explanations_generated, "trace": [*state.get("trace", []), "explain_recommendations"]}

    async def _finalize_response(self, state: DecisionState) -> DecisionState:
        commute_assumption = "通勤时间来自高德地图实时路线规划" if self.maps.name == "amap" else "通勤时间为开发测试用模拟数据"
        assumptions = ["当前房源为模拟上海房源", commute_assumption, "水电燃气统一按每月 ¥300 估算", "Agent判断不替代线下房源核验"]
        if state.get("unverified_preferences"): assumptions.append("部分自定义偏好缺少房源证据，未计入评分：" + "、".join(state["unverified_preferences"]))
        response = SearchResponse(provider=f"{self.listings.name} + {self.maps.name}", llm_enhanced=state.get("llm_enhanced", False), llm_preferences_parsed=state.get("llm_preferences_parsed", False), llm_explanations_generated=state.get("llm_explanations_generated", False), llm_tokens=state.get("llm_tokens", 0), total_candidates=len(state["candidates"]), recommendations=state["recommendations"], assumptions=assumptions)
        return {"response": response, "trace": [*state.get("trace", []), "finalize_response"]}

    async def search_with_trace(self, prefs: RentalPreferences, feedback_adjustments: dict[str, float] | None = None) -> tuple[SearchResponse, list[str]]:
        state = await self.graph.ainvoke({"preferences": prefs, "trace": [], "feedback_adjustments": feedback_adjustments or {}})
        return state["response"], state["trace"]

    async def search(self, prefs: RentalPreferences) -> SearchResponse:
        response, _ = await self.search_with_trace(prefs)
        return response

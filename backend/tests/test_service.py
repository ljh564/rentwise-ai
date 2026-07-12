import pytest

from app.models import Destination, RentalPreferences
from app.providers.mock import MockMapProvider, MockShanghaiListingProvider
from app.service import RentalDecisionService, true_cost
from app.skills.commute import CommutePlanningSkill


@pytest.mark.asyncio
async def test_search_ranks_and_explains_results():
    prefs = RentalPreferences(monthly_rent_max=6000, monthly_total_max=6500, move_in_date="2026-08-01", destinations=[Destination(label="公司", address="陆家嘴", weight=1, max_minutes=60)], soft_preferences=["采光好"])
    response = await RentalDecisionService(MockShanghaiListingProvider(), MockMapProvider()).search(prefs)
    assert response.total_candidates == 6
    assert response.recommendations[0].reasons
    assert response.recommendations[0].commutes[0].minutes > 0


@pytest.mark.asyncio
async def test_langgraph_trace_has_expected_nodes():
    prefs = RentalPreferences(monthly_rent_max=6000, monthly_total_max=6500, move_in_date="2026-08-01", destinations=[Destination(label="公司", address="陆家嘴", weight=1, max_minutes=60)])
    _, trace = await RentalDecisionService(MockShanghaiListingProvider(), MockMapProvider()).search_with_trace(prefs)
    assert trace == ["search_candidates", "interpret_preferences", "evaluate_and_rank", "explain_recommendations", "finalize_response"]


class FakeLLM:
    enabled = True

    async def complete_json(self, system, payload, max_tokens=1200):
        if "available_tags" in payload:
            return {"mappings": [{"original": "阳光充足", "matched_tag": "采光好"}]}, 20
        return {"items": [{"listing_id": item["listing_id"], "reasons": ["基于已验证成本与通勤证据推荐"], "tradeoffs": item["verified_tradeoffs"]} for item in payload["listings"]]}, 30


@pytest.mark.asyncio
async def test_llm_maps_preferences_without_changing_deterministic_metrics():
    prefs = RentalPreferences(monthly_rent_max=6000, monthly_total_max=6500, move_in_date="2026-08-01", destinations=[Destination(label="公司", address="陆家嘴", weight=1, max_minutes=60)], soft_preferences=["阳光充足"])
    response = await RentalDecisionService(MockShanghaiListingProvider(), MockMapProvider(), FakeLLM()).search(prefs)
    assert response.llm_enhanced is True
    assert response.llm_tokens == 50
    assert response.recommendations[0].weighted_commute_minutes > 0
    assert response.recommendations[0].reasons == ["基于已验证成本与通勤证据推荐"]


@pytest.mark.asyncio
async def test_multi_destination_commute_metrics():
    prefs = RentalPreferences(
        monthly_rent_max=8000,
        monthly_total_max=9000,
        move_in_date="2026-08-01",
        destinations=[
            Destination(label="本人公司", address="陆家嘴", weight=0.6, max_minutes=90),
            Destination(label="配偶公司", address="南京西路", weight=0.4, max_minutes=90),
        ],
    )
    response = await RentalDecisionService(MockShanghaiListingProvider(), MockMapProvider()).search(prefs)
    result = response.recommendations[0]
    minutes = [commute.minutes for commute in result.commutes]
    assert result.worst_commute_minutes == max(minutes)
    assert result.commute_fairness_gap_minutes == max(minutes) - min(minutes)
    assert result.weekly_total_commute_minutes == sum(minutes) * 10


@pytest.mark.asyncio
async def test_commute_skill_returns_complete_analysis():
    prefs = RentalPreferences(monthly_rent_max=8000, monthly_total_max=9000, move_in_date="2026-08-01", destinations=[Destination(label="本人", address="陆家嘴", weight=.6, max_minutes=90), Destination(label="配偶", address="南京西路", weight=.4, max_minutes=90)])
    listing = (await MockShanghaiListingProvider().search(prefs))[0]
    analysis = await CommutePlanningSkill(MockMapProvider()).calculate(listing, prefs)
    assert analysis.provider == "mock-map"
    assert len(analysis.commutes) == 2
    assert analysis.weekly_total_minutes == sum(item.minutes for item in analysis.commutes) * 10


def test_true_cost_amortizes_agent_fee():
    listing = pytest.importorskip("app.providers.mock").LISTINGS[2]
    from app.models import Listing
    model = Listing(**listing, utilities_estimate=300, deposit_months=1, image_url="https://example.com/a.jpg", source_name="test", source_url="https://example.com")
    monthly, _ = true_cost(model, 12)
    assert monthly == 6800 + 260 + 300 + round(6800 / 12)

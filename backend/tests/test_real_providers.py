import pytest

from app.models import CommuteMode, Destination, Listing, RentalPreferences
from app.providers.google_maps import GoogleMapsProvider
from app.providers.rentcast import RentCastProvider, RentCastQuotaExceeded


def preferences(city="Austin, TX"):
    return RentalPreferences(city=city, monthly_rent_max=3000, monthly_total_max=3500, move_in_date="2026-08-01", destinations=[Destination(label="Office", address="Downtown Austin, TX", weight=1, max_minutes=60)])


@pytest.mark.asyncio
async def test_rentcast_maps_response_and_caches(monkeypatch):
    provider = RentCastProvider("test", monthly_limit=1)
    calls = 0
    async def fake_fetch(params):
        nonlocal calls
        calls += 1
        return [{"id":"home-1","formattedAddress":"1 Main St, Austin, TX","city":"Austin","state":"TX","latitude":30.2,"longitude":-97.7,"propertyType":"Apartment","bedrooms":2,"squareFootage":800,"price":2200}]
    monkeypatch.setattr(provider, "_fetch", fake_fetch)
    listings = await provider.search(preferences())
    assert calls == 1
    assert listings[0].monthly_rent == 2200
    assert listings[0].area_sqm == 74


@pytest.mark.asyncio
async def test_rentcast_hard_monthly_limit():
    provider = RentCastProvider("test", monthly_limit=1)
    await provider._reserve_request()
    with pytest.raises(RentCastQuotaExceeded):
        await provider._reserve_request()


def test_rentcast_rejects_non_us_city_without_calling_api():
    with pytest.raises(Exception, match="Austin, TX"):
        RentCastProvider._city_state("上海")


@pytest.mark.asyncio
async def test_google_route_response_is_converted(monkeypatch):
    provider = GoogleMapsProvider("test")
    provider._memory_cache["unused"] = {}
    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"routes":[{"duration":"1250s","distanceMeters":12345}]}
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return FakeResponse()
    monkeypatch.setattr("app.providers.google_maps.httpx.AsyncClient", lambda **kwargs: FakeClient())
    listing = Listing(id="x", title="x", district="TX", neighborhood="Austin", address="1 Main", monthly_rent=1000, bedrooms=1, area_sqm=30, floor=1, has_elevator=False, allows_pets=False, rental_type="entire", latitude=30.2, longitude=-97.7, image_url="https://example.com/a.jpg", source_name="test", source_url="https://example.com", tags=[])
    result = await provider.commute(listing, Destination(label="Office", address="Downtown Austin, TX", weight=1, max_minutes=30), CommuteMode.TRANSIT)
    assert result.minutes == 21
    assert result.distance_km == 12.3

from app.models import Destination, RentalPreferences
import pytest

from app.validation import geography_consistency_error, validate_geography


def preferences(city: str, address: str) -> RentalPreferences:
    return RentalPreferences(city=city, monthly_rent_max=3000, monthly_total_max=3500, move_in_date="2026-08-01", destinations=[Destination(label="Office", address=address, weight=1, max_minutes=60)])


def test_rejects_chinese_destination_for_us_city():
    error = geography_consistency_error(preferences("Austin, TX", "上海市浦东新区陆家嘴"))
    assert error == "目标城市为 Austin, TX，但通勤地点“上海市浦东新区陆家嘴”位于其他国家或地区。请修改通勤地址后再计算"


def test_allows_destination_in_same_country():
    assert geography_consistency_error(preferences("Austin, TX", "Downtown Austin, TX 78701")) is None


def test_rejects_us_destination_for_chinese_city():
    assert geography_consistency_error(preferences("上海", "1 Main Street, Austin, TX 78701")) is not None


class FakeGoogleGeocoder:
    async def geocode_place(self, address: str) -> dict:
        places = {
            "Austin, TX": {"country": "US", "region": "TX", "city": "Austin", "latitude": 30.267, "longitude": -97.743},
            "Paris, France": {"country": "FR", "region": "IDF", "city": "Paris", "latitude": 48.857, "longitude": 2.352},
            "New York, NY": {"country": "US", "region": "NY", "city": "New York", "latitude": 40.713, "longitude": -74.006},
            "Downtown Austin": {"country": "US", "region": "TX", "city": "Austin", "latitude": 30.268, "longitude": -97.742},
        }
        return places[address]


async def _validate_with_fake(city: str, address: str):
    return await validate_geography(preferences(city, address), FakeGoogleGeocoder())


@pytest.mark.asyncio
async def test_geocoding_rejects_different_country():
    assert await _validate_with_fake("Austin, TX", "Paris, France") is not None


@pytest.mark.asyncio
async def test_geocoding_rejects_far_away_same_country():
    assert await _validate_with_fake("Austin, TX", "New York, NY") is not None


@pytest.mark.asyncio
async def test_geocoding_allows_nearby_destination():
    assert await _validate_with_fake("Austin, TX", "Downtown Austin") is None

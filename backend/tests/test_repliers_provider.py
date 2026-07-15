import pytest

from app.models import Destination, RentalPreferences
from app.providers.repliers import RepliersProvider


def preferences():
    return RentalPreferences(city="Austin, TX", monthly_rent_max=3000, monthly_total_max=3500, move_in_date="2026-08-01", destinations=[Destination(label="Office", address="Downtown Austin, TX", weight=1, max_minutes=60)])


SAMPLE = {
    "mlsNumber": "ACT123", "listPrice": 1700,
    "address": {"streetNumber": "114", "streetName": "31st", "streetSuffix": "ST", "unitNumber": "205", "city": "Austin", "state": "TX", "zip": "78705", "neighborhood": "Hyde Park"},
    "details": {"numBedrooms": 1, "sqft": "600", "style": "Condominium", "elevator": None},
    "map": {"latitude": 30.295095, "longitude": -97.735874},
    "images": ["sample/IMG-ACT123_0.jpg"],
}


@pytest.mark.asyncio
async def test_repliers_maps_photo_listing(monkeypatch):
    provider = RepliersProvider("test")
    monkeypatch.setattr(provider, "_fetch", lambda params: async_result({"listings": [SAMPLE]}))
    listings = await provider.search(preferences())
    assert len(listings) == 1
    assert listings[0].monthly_rent == 1700
    assert listings[0].area_sqm == 55
    assert str(listings[0].image_url).startswith("https://cdn.repliers.io/sample/IMG-ACT123_0.jpg")
    assert "listing-specific-photo" in listings[0].tags


async def async_result(value):
    return value


def test_repliers_rejects_non_us_city():
    with pytest.raises(Exception, match="Austin, TX"):
        RepliersProvider._city_state("上海")


def test_repliers_skips_records_without_photos():
    assert RepliersProvider._listing({**SAMPLE, "images": []}) is None

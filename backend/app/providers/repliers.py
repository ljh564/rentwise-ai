import hashlib
import json
import math

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import Listing, RentalPreferences
from app.providers.base import ListingProvider


class RepliersError(RuntimeError):
    pass


class RepliersProvider(ListingProvider):
    """US MLS sample listings with listing-specific photos from Repliers Preview."""

    name = "repliers-preview"
    base_url = "https://api.repliers.io"
    image_base_url = "https://cdn.repliers.io"

    def __init__(self, api_key: str, redis_url: str | None = None, base_url: str | None = None):
        if not api_key:
            raise ValueError("REPLIERS_API_KEY is required")
        self.api_key = api_key
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
        self.base_url = (base_url or self.base_url).rstrip("/")
        self._memory_cache: dict[str, dict] = {}

    @staticmethod
    def _city_state(value: str) -> tuple[str, str]:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2 or len(parts[1]) != 2:
            raise RepliersError("Repliers US test data requires a city such as 'Austin, TX'.")
        return parts[0], parts[1].upper()

    async def _fetch(self, params: dict[str, str]) -> dict:
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        cache_key = f"repliers:listings:{digest}"
        data = self._memory_cache.get(digest)
        if self.redis and data is None:
            try:
                cached = await self.redis.get(cache_key)
                data = json.loads(cached) if cached else None
            except RedisError:
                pass
        if data is None:
            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
                    response = await client.get("/listings", params=params, headers={"REPLIERS-API-KEY": self.api_key, "Accept": "application/json"})
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise RepliersError("Repliers network request failed") from exc
            if response.status_code in {401, 403}:
                raise RepliersError("Repliers rejected the API key")
            if response.status_code == 429:
                raise RepliersError("Repliers request limit reached")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("listings"), list):
                raise RepliersError("Unexpected Repliers response")
            if self.redis:
                try:
                    await self.redis.set(cache_key, json.dumps(data), ex=24 * 60 * 60)
                except RedisError:
                    pass
            else:
                self._memory_cache[digest] = data
        return data

    @classmethod
    def _listing(cls, item: dict) -> Listing | None:
        address, details, location = item.get("address") or {}, item.get("details") or {}, item.get("map") or {}
        images = item.get("images") or []
        if not item.get("mlsNumber") or item.get("listPrice") is None or not images or location.get("latitude") is None or location.get("longitude") is None:
            return None
        parts = [address.get("streetNumber"), address.get("streetDirectionPrefix"), address.get("streetName"), address.get("streetSuffix")]
        street = " ".join(str(part) for part in parts if part)
        if address.get("unitNumber"):
            street += f" #{address['unitNumber']}"
        formatted_address = ", ".join(part for part in [street, address.get("city"), address.get("state"), address.get("zip")] if part)
        sqft = float(details.get("sqft") or 250)
        bedrooms = max(1, int(details.get("numBedrooms") or 1))
        property_type = str(details.get("style") or details.get("propertyType") or "Rental")
        image_url = f"{cls.image_base_url}/{str(images[0]).lstrip('/')}?class=medium"
        elevator = str(details.get("elevator") or "").lower() not in {"", "none", "no", "n", "false"}
        return Listing(
            id=f"RP-{item['mlsNumber']}", title=f"{property_type} · {street}", district=str(address.get("state") or ""),
            neighborhood=str(address.get("neighborhood") or address.get("city") or ""), address=formatted_address,
            monthly_rent=round(float(item["listPrice"])), bedrooms=bedrooms, area_sqm=max(5, math.floor(sqft / 10.7639)),
            floor=1, has_elevator=elevator, allows_pets=False, rental_type="entire", latitude=float(location["latitude"]),
            longitude=float(location["longitude"]), image_url=image_url, source_name="Repliers Preview",
            source_url="https://repliers.com/", tags=[property_type, "sample-listing-data", "listing-specific-photo"],
        )

    async def search(self, preferences: RentalPreferences) -> list[Listing]:
        city, state = self._city_state(preferences.city)
        params = {
            "city": city, "state": state, "type": "lease", "status": "A", "hasImages": "true", "resultsPerPage": "12",
            "minBedrooms": str(preferences.bedrooms_min), "maxPrice": str(preferences.monthly_rent_max),
            "fields": "mlsNumber,listPrice,address,details,images[3],map,permissions",
        }
        data = await self._fetch(params)
        return [listing for item in data["listings"] if (listing := self._listing(item))]

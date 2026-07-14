import hashlib
import json
import math
from datetime import datetime, timezone

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import Listing, RentalPreferences
from app.providers.base import ListingProvider


class RentCastError(RuntimeError):
    pass


class RentCastQuotaExceeded(RentCastError):
    pass


class RentCastProvider(ListingProvider):
    name = "rentcast"

    def __init__(self, api_key: str, redis_url: str | None = None, monthly_limit: int = 50, base_url: str = "https://api.rentcast.io/v1"):
        if not api_key:
            raise ValueError("RENTCAST_API_KEY is required")
        self.api_key = api_key
        self.monthly_limit = max(1, min(monthly_limit, 50))
        self.base_url = base_url.rstrip("/")
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
        self._memory_cache: dict[str, list[dict]] = {}
        self._memory_usage: dict[str, int] = {}

    @staticmethod
    def _city_state(value: str) -> tuple[str, str]:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2 or len(parts[1]) != 2:
            raise RentCastError("RentCast only supports US listings; use city format such as 'Austin, TX'.")
        return parts[0], parts[1].upper()

    @staticmethod
    def _month_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    async def _reserve_request(self) -> None:
        month = self._month_key()
        key = f"rentcast:quota:{month}"
        if self.redis:
            script = """
            local current = tonumber(redis.call('GET', KEYS[1]) or '0')
            if current >= tonumber(ARGV[1]) then return -1 end
            current = redis.call('INCR', KEYS[1])
            redis.call('EXPIRE', KEYS[1], 3456000)
            return current
            """
            try:
                value = await self.redis.eval(script, 1, key, self.monthly_limit)
                if int(value) < 0:
                    raise RentCastQuotaExceeded(f"RentCast monthly safety limit ({self.monthly_limit}) reached")
                return
            except RentCastQuotaExceeded:
                raise
            except RedisError as exc:
                raise RentCastError("Redis is required to safely enforce the RentCast monthly quota") from exc
        used = self._memory_usage.get(month, 0)
        if used >= self.monthly_limit:
            raise RentCastQuotaExceeded(f"RentCast monthly safety limit ({self.monthly_limit}) reached")
        self._memory_usage[month] = used + 1

    async def _fetch(self, params: dict[str, str]) -> list[dict]:
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        cache_key = f"rentcast:listings:{digest}"
        if self.redis:
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except RedisError:
                pass
        elif digest in self._memory_cache:
            return self._memory_cache[digest]

        await self._reserve_request()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
            response = await client.get("/listings/rental/long-term", params=params, headers={"X-Api-Key": self.api_key})
        if response.status_code == 401:
            raise RentCastError("RentCast rejected the API key")
        if response.status_code == 429:
            raise RentCastQuotaExceeded("RentCast account quota or rate limit reached")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RentCastError("Unexpected RentCast response")
        if self.redis:
            try:
                await self.redis.set(cache_key, json.dumps(data), ex=30 * 24 * 60 * 60)
            except RedisError:
                pass
        else:
            self._memory_cache[digest] = data
        return data

    @staticmethod
    def _listing(item: dict) -> Listing | None:
        required = ("id", "formattedAddress", "price", "latitude", "longitude")
        if any(item.get(field) is None for field in required):
            return None
        bedrooms = max(1, int(item.get("bedrooms") or 1))
        square_feet = float(item.get("squareFootage") or 250)
        address = str(item["formattedAddress"])
        property_type = str(item.get("propertyType") or "Rental")
        image = (item.get("images") or [None])[0] if isinstance(item.get("images"), list) else None
        image_url = image or "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?w=900&auto=format&fit=crop&q=80"
        source_url = item.get("listingAgent", {}).get("website") or item.get("listingOffice", {}).get("website") or "https://app.rentcast.io"
        return Listing(
            id=f"RC-{item['id']}", title=f"{property_type} · {address}", district=str(item.get("state") or ""),
            neighborhood=str(item.get("city") or ""), address=address, monthly_rent=round(float(item["price"])),
            property_fee_monthly=round(float((item.get("hoa") or {}).get("fee") or 0)), bedrooms=bedrooms,
            area_sqm=max(5, round(square_feet / 10.7639)), floor=1, has_elevator=property_type in {"Apartment", "Condo"},
            allows_pets=False, rental_type="entire", latitude=float(item["latitude"]), longitude=float(item["longitude"]),
            image_url=image_url, source_name="RentCast", source_url=source_url, tags=[property_type, "real-listing-data"],
        )

    async def search(self, preferences: RentalPreferences) -> list[Listing]:
        city, state = self._city_state(preferences.city)
        params = {
            "city": city, "state": state, "status": "Active", "limit": "12",
            "bedrooms": f"{preferences.bedrooms_min}:99", "price": f"0:{preferences.monthly_rent_max}",
            "squareFootage": f"{math.floor(preferences.area_min * 10.7639)}:99999",
        }
        items = await self._fetch(params)
        return [listing for item in items if (listing := self._listing(item))]

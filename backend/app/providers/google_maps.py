import asyncio
import hashlib
import json
import math
import time

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import CommuteMode, CommuteResult, Destination, Listing
from app.providers.base import MapProvider


class GoogleMapsError(RuntimeError):
    pass


class GoogleMapsProvider(MapProvider):
    name = "google-maps"
    base_url = "https://routes.googleapis.com"

    def __init__(self, api_key: str, redis_url: str | None = None, qps: float = 3, base_url: str | None = None):
        if not api_key:
            raise ValueError("GOOGLE_MAPS_API_KEY is required")
        self.api_key = api_key
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
        self.base_url = (base_url or self.base_url).rstrip("/")
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._interval = 1.05 / max(qps, 0.1)
        self._memory_cache: dict[str, dict] = {}

    async def geocode_place(self, address: str) -> dict:
        normalized = address.strip()
        digest = hashlib.sha256(normalized.lower().encode()).hexdigest()
        cache_key = f"google-geocode:{digest}"
        data = self._memory_cache.get(cache_key)
        if self.redis and data is None:
            try:
                cached = await self.redis.get(cache_key)
                data = json.loads(cached) if cached else None
            except RedisError:
                pass
        if data is None:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get("https://maps.googleapis.com/maps/api/geocode/json", params={"address": normalized, "key": self.api_key, "language": "zh-CN"})
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") in {"REQUEST_DENIED", "OVER_QUERY_LIMIT"}:
                raise GoogleMapsError(f"Google Geocoding unavailable: {payload.get('status')}")
            results = payload.get("results", [])
            if not results:
                raise GoogleMapsError(f"Address could not be geocoded: {normalized}")
            result = results[0]
            components = {kind: component.get("short_name", "") for component in result.get("address_components", []) for kind in component.get("types", [])}
            location = result["geometry"]["location"]
            data = {
                "formatted_address": result.get("formatted_address", normalized),
                "country": components.get("country", ""),
                "region": components.get("administrative_area_level_1", ""),
                "city": components.get("locality") or components.get("administrative_area_level_2", ""),
                "latitude": float(location["lat"]), "longitude": float(location["lng"]),
            }
            if self.redis:
                try:
                    await self.redis.set(cache_key, json.dumps(data, ensure_ascii=False), ex=30 * 24 * 60 * 60)
                except RedisError:
                    pass
            else:
                self._memory_cache[cache_key] = data
        return data

    async def _rate_limit(self) -> None:
        async with self._lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + self._interval

    async def commute(self, listing: Listing, destination: Destination, mode: CommuteMode) -> CommuteResult:
        travel_modes = {CommuteMode.TRANSIT: "TRANSIT", CommuteMode.DRIVING: "DRIVE", CommuteMode.WALKING: "WALK", CommuteMode.BICYCLING: "BICYCLE"}
        body = {
            "origin": {"location": {"latLng": {"latitude": listing.latitude, "longitude": listing.longitude}}},
            "destination": {"address": destination.address}, "travelMode": travel_modes[mode], "languageCode": "zh-CN",
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cache_key = f"google-routes:{digest}"
        data = self._memory_cache.get(digest)
        if self.redis and data is None:
            try:
                cached = await self.redis.get(cache_key)
                data = json.loads(cached) if cached else None
            except RedisError:
                pass
        if data is None:
            await self._rate_limit()
            headers = {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters", "Content-Type": "application/json"}
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
                response = await client.post("/directions/v2:computeRoutes", json=body, headers=headers)
            if response.status_code in {401, 403}:
                raise GoogleMapsError("Google Routes API rejected the key or is not enabled")
            if response.status_code == 429:
                raise GoogleMapsError("Google Routes API quota exceeded")
            response.raise_for_status()
            data = response.json()
            if self.redis:
                try:
                    await self.redis.set(cache_key, json.dumps(data), ex=24 * 60 * 60)
                except RedisError:
                    pass
            else:
                self._memory_cache[digest] = data
        routes = data.get("routes", [])
        if not routes:
            raise GoogleMapsError("Google Routes returned no route")
        route = routes[0]
        seconds = int(str(route["duration"]).removesuffix("s").split(".")[0])
        minutes = max(1, math.ceil(seconds / 60))
        return CommuteResult(destination=destination.label, minutes=minutes, distance_km=round(int(route["distanceMeters"]) / 1000, 1), within_limit=minutes <= destination.max_minutes)

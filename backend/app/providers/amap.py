import math
import asyncio
import time

import httpx

from app.models import CommuteMode, CommuteResult, Destination, Listing
from app.providers.base import MapProvider


class AMapError(RuntimeError):
    pass


class AMapProvider(MapProvider):
    name = "amap"
    base_url = "https://restapi.amap.com"

    def __init__(self, api_key: str, city: str = "上海", base_url: str | None = None, qps: float = 3):
        if not api_key:
            raise ValueError("AMAP_API_KEY is required")
        self.api_key = api_key
        self.city = city
        self._geocode_cache: dict[str, tuple[float, float]] = {}
        self._rate_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._request_interval = 1.05 / max(qps, 0.1)
        if base_url:
            self.base_url = base_url.rstrip("/")

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            delay = self._next_request_at - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + self._request_interval

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await self._wait_for_rate_limit()
                async with httpx.AsyncClient(base_url=self.base_url, timeout=25) as client:
                    response = await client.get(path, params={"key": self.api_key, "output": "JSON", **params})
                    response.raise_for_status()
                    data = response.json()
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        else:
            raise AMapError(f"AMap network request failed after retries: {last_error}") from last_error
        if str(data.get("status")) != "1":
            raise AMapError(f"AMap API error: {data.get('info', 'unknown error')} ({data.get('infocode', '-')})")
        return data

    async def geocode(self, address: str) -> tuple[float, float]:
        if address in self._geocode_cache:
            return self._geocode_cache[address]
        data = await self._get("/v3/geocode/geo", {"address": address, "city": self.city})
        if not data.get("geocodes"):
            raise AMapError(f"No geocoding result for: {address}")
        longitude, latitude = data["geocodes"][0]["location"].split(",")
        coordinates = float(longitude), float(latitude)
        self._geocode_cache[address] = coordinates
        return coordinates

    async def commute(self, listing: Listing, destination: Destination, mode: CommuteMode) -> CommuteResult:
        destination_lon, destination_lat = await self.geocode(destination.address)
        origin = f"{listing.longitude:.6f},{listing.latitude:.6f}"
        target = f"{destination_lon:.6f},{destination_lat:.6f}"
        common = {"origin": origin, "destination": target}

        if mode == CommuteMode.TRANSIT:
            data = await self._get("/v3/direction/transit/integrated", {**common, "city": self.city, "cityd": self.city})
            transits = data.get("route", {}).get("transits", [])
            if not transits:
                raise AMapError("No transit route returned")
            route = min(transits, key=lambda item: int(item["duration"]))
        else:
            paths = {
                CommuteMode.DRIVING: "/v3/direction/driving",
                CommuteMode.WALKING: "/v3/direction/walking",
                CommuteMode.BICYCLING: "/v4/direction/bicycling",
            }
            data = await self._get(paths[mode], common)
            route_paths = data.get("route", {}).get("paths", data.get("data", {}).get("paths", []))
            if not route_paths:
                raise AMapError(f"No {mode.value} route returned")
            route = min(route_paths, key=lambda item: int(item["duration"]))

        minutes = math.ceil(int(route["duration"]) / 60)
        distance_km = round(int(route["distance"]) / 1000, 1)
        return CommuteResult(
            destination=destination.label,
            minutes=minutes,
            distance_km=distance_km,
            within_limit=minutes <= destination.max_minutes,
        )

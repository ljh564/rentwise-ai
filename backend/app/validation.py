import re
import math

from app.models import RentalPreferences

US_CITY = re.compile(r"^.+,\s*[A-Z]{2}$", re.IGNORECASE)
CHINESE_TEXT = re.compile(r"[\u4e00-\u9fff]")
CHINA_CITY = re.compile(r"上海|北京|天津|重庆|广州|深圳|杭州|南京|成都|武汉|西安|苏州")
US_ADDRESS = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|\b(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd)\b", re.IGNORECASE)


def geography_consistency_error(preferences: RentalPreferences) -> str | None:
    city_is_us = bool(US_CITY.match(preferences.city.strip()))
    city_is_china = bool(CHINA_CITY.search(preferences.city))
    for destination in preferences.destinations:
        address = destination.address.strip()
        mismatch = (city_is_us and bool(CHINESE_TEXT.search(address))) or (city_is_china and bool(US_ADDRESS.search(address)))
        if mismatch:
            return f'目标城市为 {preferences.city}，但通勤地点“{address}”位于其他国家或地区。请修改通勤地址后再计算'
    return None


def _distance_km(first: dict, second: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [first["latitude"], first["longitude"], second["latitude"], second["longitude"]])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


async def validate_geography(preferences: RentalPreferences, map_provider: object, max_distance_km: float = 200) -> str | None:
    if local_error := geography_consistency_error(preferences):
        return local_error
    if not hasattr(map_provider, "geocode_place"):
        return None
    city = await map_provider.geocode_place(preferences.city)
    for destination in preferences.destinations:
        place = await map_provider.geocode_place(destination.address)
        different_country = bool(city["country"] and place["country"] and city["country"] != place["country"])
        too_far = _distance_km(city, place) > max_distance_km
        if different_country or too_far:
            return f'目标城市为 {preferences.city}，但通勤地点“{destination.address}”位于其他国家或地区。请修改通勤地址后再计算'
    return None

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.models import RentalPreferences, SearchResponse
from app.providers.amap import AMapError, AMapProvider
from app.providers.mock import MockMapProvider, MockShanghaiListingProvider
from app.service import RentalDecisionService

load_dotenv()

app = FastAPI(title="RentScout AI API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
mock_map = MockMapProvider()
map_provider = AMapProvider(
    os.environ["AMAP_API_KEY"],
    base_url=os.getenv("AMAP_BASE_URL"),
    qps=float(os.getenv("AMAP_QPS", "3")),
) if os.getenv("MAP_PROVIDER") == "amap" else mock_map
service = RentalDecisionService(MockShanghaiListingProvider(), map_provider)


@app.get("/api/health")
async def health():
    return {"status": "ok", "listing_provider": service.listings.name, "map_provider": service.maps.name}


@app.post("/api/search", response_model=SearchResponse)
async def search(preferences: RentalPreferences):
    try:
        return await service.search(preferences)
    except AMapError as exc:
        raise HTTPException(status_code=503, detail="高德地图暂时无法返回真实通勤数据，请稍后重试。") from exc

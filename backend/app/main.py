import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.models import RentalPreferences, SearchResponse
from app.persistence import RentalProfile, SessionLocal, authenticate_anonymous, create_anonymous_session, create_schema
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


@app.on_event("startup")
async def startup() -> None:
    await create_schema()


async def anonymous_user(
    x_anonymous_user_id: str = Header(),
    x_anonymous_access_token: str = Header(),
):
    async with SessionLocal() as db:
        user = await authenticate_anonymous(db, x_anonymous_user_id, x_anonymous_access_token)
        if not user:
            raise HTTPException(status_code=401, detail="匿名身份无效或已过期。")
        await db.commit()
        return user.id


@app.post("/api/anonymous/session")
async def new_anonymous_session():
    async with SessionLocal() as db:
        user, token = await create_anonymous_session(db)
        return {"anonymous_user_id": str(user.id), "access_token": token, "is_new": True}


@app.get("/api/profile", response_model=RentalPreferences | None)
async def get_profile(user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        profile = await db.get(RentalProfile, user_id)
        return RentalPreferences.model_validate(profile.preferences) if profile else None


@app.put("/api/profile", response_model=RentalPreferences)
async def save_profile(preferences: RentalPreferences, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        profile = await db.get(RentalProfile, user_id)
        if profile:
            profile.preferences = preferences.model_dump(mode="json")
        else:
            db.add(RentalProfile(anonymous_user_id=user_id, preferences=preferences.model_dump(mode="json")))
        await db.commit()
        return preferences


@app.get("/api/health")
async def health():
    return {"status": "ok", "listing_provider": service.listings.name, "map_provider": service.maps.name}


@app.post("/api/search", response_model=SearchResponse)
async def search(preferences: RentalPreferences):
    try:
        return await service.search(preferences)
    except AMapError as exc:
        raise HTTPException(status_code=503, detail="高德地图暂时无法返回真实通勤数据，请稍后重试。") from exc

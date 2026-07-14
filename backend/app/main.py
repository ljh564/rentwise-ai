import os
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.models import Listing, RentalPreferences, SearchResponse
from app.llm import OpenAICompatibleLLM
from app.persistence import AgentRun, ContractReview, Favorite, RecommendationFeedback, RentalProfile, SearchHistory, SessionLocal, authenticate_anonymous, create_anonymous_session
from app.providers.amap import AMapError, AMapProvider
from app.providers.google_maps import GoogleMapsError, GoogleMapsProvider
from app.providers.mock import MockMapProvider, MockShanghaiListingProvider
from app.providers.rentcast import RentCastError, RentCastProvider
from app.service import RentalDecisionService
from app.skills.contract import ContractReviewReport, RentalContractReviewSkill
from app.skills.listing_image import ListingImageAnalysisSkill, ListingImageReport
from app.storage import OptionalArtifactStorage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

load_dotenv()

app = FastAPI(title="RentScout AI API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Anonymous-User-ID", "X-Anonymous-Access-Token"])
mock_map = MockMapProvider()
map_choice = os.getenv("MAP_PROVIDER", "mock").lower()
if map_choice == "amap":
    map_provider = AMapProvider(os.environ["AMAP_API_KEY"], base_url=os.getenv("AMAP_BASE_URL"), qps=float(os.getenv("AMAP_QPS", "3")), redis_url=os.getenv("REDIS_URL"))
elif map_choice == "google":
    map_provider = GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"], redis_url=os.getenv("REDIS_URL"), qps=float(os.getenv("GOOGLE_MAPS_QPS", "3")))
else:
    map_provider = mock_map

listing_choice = os.getenv("LISTING_PROVIDER", "mock").lower()
listing_provider = RentCastProvider(os.environ["RENTCAST_API_KEY"], redis_url=os.getenv("REDIS_URL"), monthly_limit=int(os.getenv("RENTCAST_MONTHLY_LIMIT", "50"))) if listing_choice == "rentcast" else MockShanghaiListingProvider()
llm = OpenAICompatibleLLM(os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", ""), float(os.getenv("LLM_TEMPERATURE", "0")), os.getenv("LLM_VISION_MODEL", ""))
service = RentalDecisionService(listing_provider, map_provider, llm)
contract_skill = RentalContractReviewSkill(llm)
listing_image_skill = ListingImageAnalysisSkill(llm)
artifact_storage = OptionalArtifactStorage()
checkpoint_context = None

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "public, max-age=300"
    return response


@app.on_event("startup")
async def startup() -> None:
    global checkpoint_context
    checkpoint_context = AsyncPostgresSaver.from_conn_string(os.getenv("CHECKPOINT_DATABASE_URL", "postgresql://rentscout:rentscout_dev@postgres:5432/rentscout"))
    saver = await checkpoint_context.__aenter__()
    await saver.setup()
    service.enable_checkpoints(saver)


@app.on_event("shutdown")
async def shutdown() -> None:
    if checkpoint_context:
        await checkpoint_context.__aexit__(None, None, None)


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


@app.get("/api/profile/export")
async def export_profile(user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        profile = await db.get(RentalProfile, user_id)
        favorites_rows = (await db.scalars(select(Favorite).where(Favorite.anonymous_user_id == user_id))).all()
        return {"format": "rentwise-profile-v1", "exported_at": datetime.now(timezone.utc), "preferences": profile.preferences if profile else None, "favorites": [row.listing_snapshot for row in favorites_rows]}


class ProfileBackup(BaseModel):
    format: str = Field(pattern="^rentwise-profile-v1$")
    preferences: dict | None = None
    favorites: list[dict] = Field(default_factory=list, max_length=100)


@app.post("/api/profile/import")
async def import_profile(payload: ProfileBackup, user_id=Depends(anonymous_user)):
    preferences = RentalPreferences.model_validate(payload.preferences) if payload.preferences else None
    listings = [Listing.model_validate(item) for item in payload.favorites]
    async with SessionLocal() as db:
        if preferences:
            profile = await db.get(RentalProfile, user_id)
            if profile: profile.preferences = preferences.model_dump(mode="json")
            else: db.add(RentalProfile(anonymous_user_id=user_id, preferences=preferences.model_dump(mode="json")))
        for listing in listings:
            existing = await db.scalar(select(Favorite).where(Favorite.anonymous_user_id == user_id, Favorite.listing_id == listing.id))
            if not existing: db.add(Favorite(anonymous_user_id=user_id, listing_id=listing.id, listing_snapshot=listing.model_dump(mode="json")))
        await db.commit()
    return {"imported": True, "preferences": bool(preferences), "favorites": len(listings)}


@app.post("/api/listings/images/analyze", response_model=ListingImageReport)
async def analyze_listing_images(files: list[UploadFile] = File(), user_id=Depends(anonymous_user)):
    payload = [(await file.read(), file.content_type or "application/octet-stream") for file in files]
    try: return await listing_image_skill.analyze(payload)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/artifacts", status_code=201)
async def save_authorized_artifact(file: UploadFile = File(), consent: bool = Form(), user_id=Depends(anonymous_user)):
    try: key = artifact_storage.upload(user_id, file.filename or "artifact", await file.read(), file.content_type or "application/octet-stream", consent)
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"object_key": key, "stored_with_consent": True}


@app.delete("/api/artifacts/{object_key:path}", status_code=204)
async def delete_authorized_artifact(object_key: str, user_id=Depends(anonymous_user)):
    try: artifact_storage.delete(user_id, object_key)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


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


class FavoriteInput(BaseModel):
    listing: Listing


class FeedbackInput(BaseModel):
    listing_id: str
    search_id: str | None = None
    feedback_type: str = Field(pattern="^(like|dislike|not_relevant|too_expensive|commute_too_long|missing_preference)$")
    reason: str | None = Field(default=None, max_length=300)


@app.get("/api/favorites")
async def favorites(user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        rows = (await db.scalars(select(Favorite).where(Favorite.anonymous_user_id == user_id).order_by(Favorite.created_at.desc()))).all()
        return [{"listing_id": row.listing_id, "listing": row.listing_snapshot, "created_at": row.created_at} for row in rows]


@app.post("/api/favorites", status_code=201)
async def add_favorite(payload: FavoriteInput, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        existing = await db.scalar(select(Favorite).where(Favorite.anonymous_user_id == user_id, Favorite.listing_id == payload.listing.id))
        if not existing:
            db.add(Favorite(anonymous_user_id=user_id, listing_id=payload.listing.id, listing_snapshot=payload.listing.model_dump(mode="json")))
            await db.commit()
        return {"listing_id": payload.listing.id, "saved": True}


@app.delete("/api/favorites/{listing_id}", status_code=204)
async def remove_favorite(listing_id: str, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        await db.execute(delete(Favorite).where(Favorite.anonymous_user_id == user_id, Favorite.listing_id == listing_id))
        await db.commit()


@app.get("/api/search-history")
async def search_history(user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        rows = (await db.scalars(select(SearchHistory).where(SearchHistory.anonymous_user_id == user_id).order_by(SearchHistory.created_at.desc()).limit(20))).all()
        return [{"id": str(row.id), "request": row.request_snapshot, "summary": row.result_summary, "provider": row.provider, "created_at": row.created_at} for row in rows]


@app.post("/api/search-history/{history_id}/replay", response_model=SearchResponse)
async def replay_search(history_id: uuid.UUID, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        row = await db.scalar(select(SearchHistory).where(SearchHistory.id == history_id, SearchHistory.anonymous_user_id == user_id))
        if not row:
            raise HTTPException(status_code=404, detail="历史记录不存在。")
        preferences = RentalPreferences.model_validate(row.request_snapshot)
    return await search(preferences, user_id)


@app.post("/api/feedback", status_code=201)
async def save_feedback(payload: FeedbackInput, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        search_id = uuid.UUID(payload.search_id) if payload.search_id else None
        db.add(RecommendationFeedback(anonymous_user_id=user_id, search_id=search_id, listing_id=payload.listing_id, feedback_type=payload.feedback_type, reason=payload.reason))
        await db.commit()
        return {"saved": True}


@app.post("/api/contracts/review", response_model=ContractReviewReport)
async def review_contract(files: list[UploadFile] = File(), city: str = Form(default="上海"), user_id=Depends(anonymous_user)):
    try:
        payload = [(file.filename or f"contract-{index}", await file.read(), file.content_type or "application/octet-stream") for index, file in enumerate(files)]
        report = await contract_skill.review_files(payload, city)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with SessionLocal() as db:
        db.add(ContractReview(anonymous_user_id=user_id, filename=report.filename, document_hash=report.document_hash, report=report.model_dump(mode="json")))
        await db.commit()
    return report


@app.get("/api/contracts/reviews")
async def contract_reviews(user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        rows = (await db.scalars(select(ContractReview).where(ContractReview.anonymous_user_id == user_id).order_by(ContractReview.created_at.desc()).limit(20))).all()
        return [{"id": str(row.id), "filename": row.filename, "document_hash": row.document_hash, "report": row.report, "created_at": row.created_at} for row in rows]


@app.get("/api/health")
async def health():
    return {"status": "ok", "listing_provider": service.listings.name, "map_provider": service.maps.name}


@app.post("/api/search", response_model=SearchResponse)
async def search(preferences: RentalPreferences, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        run = AgentRun(anonymous_user_id=user_id, status="running", summary={"destinations": len(preferences.destinations)})
        db.add(run)
        await db.commit()
        await db.refresh(run)
    try:
        async with SessionLocal() as db:
            feedback_rows = (await db.scalars(select(RecommendationFeedback).where(RecommendationFeedback.anonymous_user_id == user_id))).all()
        feedback_adjustments: dict[str, float] = {}
        weights = {"like": 3, "dislike": -4, "not_relevant": -5, "too_expensive": -3, "commute_too_long": -3, "missing_preference": -2}
        for item in feedback_rows:
            feedback_adjustments[item.listing_id] = max(-10, min(10, feedback_adjustments.get(item.listing_id, 0) + weights.get(item.feedback_type, 0)))
        response, trace = await service.search_with_trace(preferences, feedback_adjustments, str(run.id))
        async with SessionLocal() as db:
            history = SearchHistory(
                anonymous_user_id=user_id,
                request_snapshot=preferences.model_dump(mode="json"),
                result_summary={
                    "total_candidates": response.total_candidates,
                    "top_listing_ids": [item.listing.id for item in response.recommendations[:5]],
                    "recommendations": [item.model_dump(mode="json") for item in response.recommendations[:5]],
                    "assumptions": response.assumptions,
                    "llm_enhanced": response.llm_enhanced,
                },
                provider=response.provider,
            )
            db.add(history)
            await db.commit()
            await db.refresh(history)
            response.search_id = str(history.id)
            stored_run = await db.get(AgentRun, run.id)
            stored_run.status = "completed"
            stored_run.trace = trace
            stored_run.summary = {"destinations": len(preferences.destinations), "candidates": response.total_candidates, "recommendations": len(response.recommendations), "llm_enhanced": response.llm_enhanced, "llm_preferences_parsed": response.llm_preferences_parsed, "llm_explanations_generated": response.llm_explanations_generated, "llm_tokens": response.llm_tokens}
            stored_run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            response.agent_run_id = str(run.id)
        return response
    except (AMapError, GoogleMapsError, RentCastError) as exc:
        async with SessionLocal() as db:
            stored_run = await db.get(AgentRun, run.id)
            stored_run.status = "failed"
            stored_run.summary = {"error": "map_provider_unavailable"}
            stored_run.completed_at = datetime.now(timezone.utc)
            await db.commit()
        raise HTTPException(status_code=503, detail="高德地图暂时无法返回真实通勤数据，请稍后重试。") from exc


@app.post("/api/agent-runs/{run_id}/resume", response_model=SearchResponse)
async def resume_agent_run(run_id: uuid.UUID, user_id=Depends(anonymous_user)):
    async with SessionLocal() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.anonymous_user_id == user_id))
        if not run: raise HTTPException(status_code=404, detail="Agent 运行记录不存在。")
        if run.status == "completed": raise HTTPException(status_code=409, detail="该运行已经完成。")
    try:
        response, trace = await service.resume_with_trace(str(run_id))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="恢复执行仍然失败，可稍后再次重试。") from exc
    async with SessionLocal() as db:
        stored = await db.get(AgentRun, run_id); stored.status = "completed"; stored.trace = trace; stored.completed_at = datetime.now(timezone.utc); await db.commit()
    response.agent_run_id = str(run_id)
    return response

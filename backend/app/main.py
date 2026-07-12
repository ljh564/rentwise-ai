import os
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.models import Listing, RentalPreferences, SearchResponse
from app.llm import OpenAICompatibleLLM
from app.persistence import AgentRun, ContractReview, Favorite, RecommendationFeedback, RentalProfile, SearchHistory, SessionLocal, authenticate_anonymous, create_anonymous_session, create_schema
from app.providers.amap import AMapError, AMapProvider
from app.providers.mock import MockMapProvider, MockShanghaiListingProvider
from app.service import RentalDecisionService
from app.skills.contract import ContractReviewReport, RentalContractReviewSkill

load_dotenv()

app = FastAPI(title="RentScout AI API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
mock_map = MockMapProvider()
map_provider = AMapProvider(
    os.environ["AMAP_API_KEY"],
    base_url=os.getenv("AMAP_BASE_URL"),
        qps=float(os.getenv("AMAP_QPS", "3")),
        redis_url=os.getenv("REDIS_URL"),
) if os.getenv("MAP_PROVIDER") == "amap" else mock_map
llm = OpenAICompatibleLLM(os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", ""), os.getenv("LLM_MODEL", ""), float(os.getenv("LLM_TEMPERATURE", "0")), os.getenv("LLM_VISION_MODEL", ""))
service = RentalDecisionService(MockShanghaiListingProvider(), map_provider, llm)
contract_skill = RentalContractReviewSkill(llm)


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
        response, trace = await service.search_with_trace(preferences)
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
    except AMapError as exc:
        async with SessionLocal() as db:
            stored_run = await db.get(AgentRun, run.id)
            stored_run.status = "failed"
            stored_run.summary = {"error": "map_provider_unavailable"}
            stored_run.completed_at = datetime.now(timezone.utc)
            await db.commit()
        raise HTTPException(status_code=503, detail="高德地图暂时无法返回真实通勤数据，请稍后重试。") from exc

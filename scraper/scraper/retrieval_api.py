"""FastAPI transport for the Stage 3.4 retrieval/evidence service."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .retrieval import RetrievalError
from .retrieval_service import RetrievalService
from .production import (
    ProductionBoundaryMiddleware,
    cors_origins,
    log_event,
    missing_retrieval_assets,
    provider_is_configured,
    resolve_data_dir,
)


class RetrieveFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard_number: str | None = None
    document_id: str | None = None
    clause_number: str | None = None
    annex_identifier: str | None = None
    document_type: str | None = None
    product: str | None = None


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=5)
    filters: RetrieveFilters = Field(default_factory=RetrieveFilters)


class EvidenceSourceResponse(BaseModel):
    document_id: str | None
    source_id: str | None
    title: str | None
    version: str | int | None
    page: int | None
    url: str | None


class EvidenceRetrievalResponse(BaseModel):
    methods: list[Literal["semantic", "bm25"]]
    semantic_rank: int | None
    bm25_rank: int | None
    rrf_score: float
    reranker_score: float


class EvidenceResultResponse(BaseModel):
    rank: int
    chunk_id: str
    standard_number: str | None
    clause_number: str | None
    clause_title: str | None
    text: str
    context_prefix: str | None
    source: EvidenceSourceResponse
    retrieval: EvidenceRetrievalResponse


class RetrieveResponse(BaseModel):
    query: str
    results: list[EvidenceResultResponse]


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    return RetrievalService.from_data_dir(resolve_data_dir())


ServiceDependency = Annotated[RetrievalService, Depends(get_retrieval_service)]

app = FastAPI(title="Specengine-BIS Retrieval Service", version="3.4")
app.add_middleware(ProductionBoundaryMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(RequestValidationError)
async def redacted_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "alive", "service": "specengine-bis"}


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    try:
        data_dir = resolve_data_dir()
        missing = missing_retrieval_assets(data_dir)
        if missing:
            log_event(40, "readiness_failed", category="retrieval_assets_missing", count=len(missing))
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "not_ready",
                    "components": {
                        "application": "available",
                        "retrieval_assets": "unavailable",
                        "models": "unknown",
                        "provider": "configured" if provider_is_configured() else "unconfigured",
                    },
                },
            )
        get_retrieval_service()
    except HTTPException:
        raise
    except Exception as exc:
        log_event(40, "readiness_failed", category=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "components": {
                    "application": "available",
                    "retrieval_assets": "available",
                    "models": "unavailable",
                    "provider": "configured" if provider_is_configured() else "unconfigured",
                },
            },
        ) from exc
    provider = "configured" if provider_is_configured() else "unconfigured"
    return {
        "status": "ready",
        "components": {
            "application": "available",
            "retrieval_assets": "available",
            "models": "available",
            "provider": provider,
        },
    }


@app.post("/api/v1/search/retrieve", response_model=RetrieveResponse)
def retrieve_evidence(body: RetrieveRequest, service: ServiceDependency) -> RetrieveResponse:
    try:
        response = service.retrieve(
            body.query,
            top_k=body.top_k,
            filters=body.filters.model_dump(exclude_none=True),
        )
    except RetrievalError as exc:
        log_event(30, "retrieval_failure", category=type(exc).__name__)
        raise HTTPException(
            status_code=422,
            detail="The request could not be evaluated against retrieval evidence.",
        ) from exc
    return RetrieveResponse.model_validate(response.to_dict())

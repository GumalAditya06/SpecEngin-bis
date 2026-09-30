"""Stage 4.2 grounded-answer endpoint layered over the retrieval API app."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .evidence_context import EvidenceContextBuilder, EvidenceContextError
from .grounded_answer import AnswerGenerationError, GroundedAnswer, GroundedAnswerService
from .llm_provider import (
    LLMProvider,
    ProviderConfigurationError,
    ProviderPermanentError,
    provider_from_env,
)
from .retrieval import RetrievalError
from .retrieval_api import RetrieveFilters, app, get_retrieval_service
from .retrieval_service import RetrievalService
from .production import log_event

app.title = "Specengine-BIS Retrieval and Grounded Answer Service"
app.version = "4.2"


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=5)
    filters: RetrieveFilters = Field(default_factory=RetrieveFilters)


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    try:
        return provider_from_env()
    except ProviderConfigurationError as exc:
        log_event(40, "provider_failure", category="configuration")
        raise HTTPException(
            status_code=503, detail="The answer provider is not configured."
        ) from exc


def get_context_builder() -> EvidenceContextBuilder:
    return EvidenceContextBuilder()


def get_llm_provider_factory() -> Callable[[], LLMProvider]:
    """Defer provider configuration until the guard permits generation."""

    return get_llm_provider


RetrievalDependency = Annotated[RetrievalService, Depends(get_retrieval_service)]
ProviderFactoryDependency = Annotated[
    Callable[[], LLMProvider], Depends(get_llm_provider_factory)
]
ContextDependency = Annotated[EvidenceContextBuilder, Depends(get_context_builder)]


@app.post("/api/v1/assistant/query", response_model=GroundedAnswer)
def assistant_query(
    body: AssistantRequest,
    retrieval_service: RetrievalDependency,
    provider_factory: ProviderFactoryDependency,
    context_builder: ContextDependency,
) -> GroundedAnswer:
    try:
        return GroundedAnswerService(
            retrieval_service,
            context_builder,
            provider_factory,
        ).answer(
            body.query,
            top_k=body.top_k,
            filters=body.filters.model_dump(exclude_none=True),
        )
    except (RetrievalError, EvidenceContextError) as exc:
        log_event(30, "assistant_failure", category=type(exc).__name__)
        raise HTTPException(
            status_code=422,
            detail="The request could not be evaluated against retrieval evidence.",
        ) from exc
    except ProviderConfigurationError as exc:
        log_event(40, "provider_failure", category="configuration")
        raise HTTPException(
            status_code=503, detail="The answer provider is not configured."
        ) from exc
    except ProviderPermanentError as exc:
        log_event(
            40,
            "provider_failure",
            category=type(exc).__name__,
            upstream_status=exc.upstream_status,
            provider_error_category=exc.provider_error_category,
            provider_error_field=exc.provider_error_field,
            provider=exc.provider,
        )
        raise HTTPException(
            status_code=502,
            detail="The configured model provider did not return a valid grounded answer.",
        ) from exc
    except AnswerGenerationError as exc:
        log_event(40, "provider_failure", category=type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="The configured model provider did not return a valid grounded answer.",
        ) from exc

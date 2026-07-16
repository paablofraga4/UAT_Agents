"""Read access to the domain knowledge base, so the UI can show what the agent
knows about a site (curated playbook + what it has learned)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app import knowledge
from app.api.deps import require_token


router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_token)]
)


@router.get("")
async def list_domains() -> dict:
    return {"domains": knowledge.known_domains()}


@router.get("/{domain}")
async def get_domain(domain: str) -> dict:
    curated = knowledge._read_cached(knowledge.curated_path(domain))
    learned = knowledge._read_cached(knowledge.learned_path(domain))
    return {
        "domain": domain,
        "curated": curated,
        "learned": learned,
        "injected": knowledge.load_playbook(domain),
    }

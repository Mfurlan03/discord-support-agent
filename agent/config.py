"""Configuration from environment variables (and .env), plus the factory that wires
the real agent together."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "openrouter"  # openrouter | openai | openai-compatible | anthropic
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    embedder: str = "sentence-transformers"  # or "hashing" (offline, lower quality)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_mode: str = "hybrid"
    corpus_dir: str = str(ROOT / "data" / "corpus")
    releases_path: str = str(ROOT / "data" / "releases.json")
    db_path: str = str(ROOT / "agent.db")
    traces_dir: str = str(ROOT / "traces")
    discord_token: str = ""
    helpers_channel_id: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:  # pragma: no cover
            pass
        d = cls()
        return cls(
            llm_provider=_env("LLM_PROVIDER", d.llm_provider).lower(),
            llm_model=_env("LLM_MODEL"),
            llm_api_key=_env("LLM_API_KEY"),
            llm_base_url=_env("LLM_BASE_URL"),
            embedder=_env("EMBEDDER", d.embedder),
            embedding_model=_env("EMBEDDING_MODEL", d.embedding_model),
            retrieval_mode=_env("RETRIEVAL_MODE", d.retrieval_mode),
            corpus_dir=_env("CORPUS_DIR", d.corpus_dir),
            releases_path=_env("RELEASES_PATH", d.releases_path),
            db_path=_env("DB_PATH", d.db_path),
            traces_dir=_env("TRACES_DIR", d.traces_dir),
            discord_token=_env("DISCORD_BOT_TOKEN"),
            helpers_channel_id=_env("HELPERS_CHANNEL_ID"),
        )


def build_retriever(settings: Settings):
    from rag.chunking import load_markdown_corpus
    from rag.embeddings import CachedEmbedder, build_embedder
    from rag.retrieval import build_memory_retriever

    chunks = load_markdown_corpus(settings.corpus_dir)
    embedder = None
    if settings.retrieval_mode != "bm25":
        embedder = CachedEmbedder(build_embedder(settings.embedder, settings.embedding_model))
    return build_memory_retriever(chunks, settings.retrieval_mode, embedder)


def load_releases(path: str) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_agent(settings: Settings, gateway, llm=None, retriever=None, store=None, clock=None):
    """Wire the production agent. Any dependency can be injected (tests, evals)."""
    from .llm import build_llm
    from .loop import Agent
    from .state import Store
    from .tools.actions import default_tools
    from .tracing import Tracer

    if llm is None:
        if not settings.llm_model or not settings.llm_api_key:
            raise ValueError("set LLM_MODEL and LLM_API_KEY (see .env.example)")
        llm = build_llm(settings.llm_provider, settings.llm_model, settings.llm_api_key, settings.llm_base_url or None)
    kw = {"clock": clock} if clock else {}
    store = store or Store(settings.db_path, **kw)
    services = {
        "retriever": retriever or build_retriever(settings),
        "releases": load_releases(settings.releases_path),
        "helpers_channel_id": settings.helpers_channel_id or "helpers-queue",
    }
    return Agent(llm, default_tools(), store, gateway, services, Tracer(settings.traces_dir), **kw)

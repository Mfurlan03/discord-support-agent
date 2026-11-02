"""Read-tier tools over the FastAPI knowledge base."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from ..guardrails import wrap_untrusted
from .base import Tier, Tool, ToolContext, ToolError, ToolResult


class SearchDocsArgs(BaseModel):
    query: str = Field(..., min_length=2, max_length=300, description="Search query, in your own words")
    k: int = Field(5, ge=1, le=8, description="How many passages to return")


class SearchDocs(Tool):
    name = "search_docs"
    description = (
        "Search the FastAPI documentation. Returns numbered passages. Cite them in your answer "
        "as [n]. Search again with different words if the first results don't answer the question."
    )
    Args = SearchDocsArgs
    tier = Tier.READ

    def run(self, args: SearchDocsArgs, ctx: ToolContext) -> ToolResult:
        hits = ctx.services["retriever"].search(args.query, args.k)
        if not hits:
            return ToolResult("No passages matched. Try different words.", {"chunk_ids": []})
        blocks, ids = [], []
        for h in hits:
            existing = next((i for i, s in enumerate(ctx.sources) if s.chunk_id == h.chunk_id), None)
            if existing is None:
                ctx.sources.append(h)
                existing = len(ctx.sources) - 1
            n = existing + 1
            ids.append(h.chunk_id)
            blocks.append(f"[{n}] {h.chunk_id}\n{h.text}")
        return ToolResult(wrap_untrusted("fastapi-docs", "\n\n---\n\n".join(blocks)), {"chunk_ids": ids})


_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


class LookupReleaseArgs(BaseModel):
    version: str | None = Field(None, description='Exact version like "0.115.0", or "latest"')
    keyword: str | None = Field(None, min_length=2, max_length=80, description="Text to find in release notes")
    limit: int = Field(8, ge=1, le=20)

    @field_validator("version")
    @classmethod
    def _version(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lstrip("v")
        if v != "latest" and not _VERSION_RE.match(v):
            raise ValueError('must look like "0.115.0" or be "latest"')
        return v

    @model_validator(mode="after")
    def _one_of(self):
        if not self.version and not self.keyword:
            raise ValueError("give a version, a keyword, or both")
        return self


class LookupRelease(Tool):
    name = "lookup_release"
    description = (
        "Exact lookup in FastAPI's release notes. Use for questions about versions, release dates, "
        "or when a feature, fix or breaking change shipped. Returns version, date and matching notes."
    )
    Args = LookupReleaseArgs
    tier = Tier.READ

    def run(self, args: LookupReleaseArgs, ctx: ToolContext) -> ToolResult:
        releases: list[dict] = ctx.services["releases"]  # newest first
        if args.version:
            if args.version == "latest":
                pool = releases[:1]
            else:
                want = args.version if args.version.count(".") == 2 else args.version + ".0"
                pool = [r for r in releases if r["version"] == want]
                if not pool:
                    raise ToolError(f"version {args.version} not found in the release notes")
        else:
            pool = releases
        kw = (args.keyword or "").lower()
        lines, found = [], []
        for r in pool:
            for section, items in r["sections"].items():
                for item in items:
                    if kw and kw not in item.lower():
                        continue
                    lines.append(f"{r['version']} ({r['date']}) [{section}] {item}")
                    found.append(r["version"])
                    if len(lines) >= args.limit:
                        break
                if len(lines) >= args.limit:
                    break
            if len(lines) >= args.limit:
                break
        if not lines:
            return ToolResult("No matching release notes.", {"versions": []})
        # Release notes are maintainer-written but still external text: fence them too.
        return ToolResult(wrap_untrusted("release-notes", "\n".join(lines)), {"versions": sorted(set(found))})

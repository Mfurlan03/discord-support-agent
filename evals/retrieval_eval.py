"""Retrieval-only baseline: does the right page come back for each answerable question?

Uses the answerable scenarios' gold pages (`cites_url_contains_any`) as labels, with the
user's raw wording as the query, which is harder than the agent's own rewritten queries.

    python -m evals.retrieval_eval                         # bm25 + dense + hybrid (downloads MiniLM)
    python -m evals.retrieval_eval --modes bm25 --embedder hashing
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.config import ROOT
from evals.harness import load_scenarios
from rag.chunking import load_markdown_corpus
from rag.embeddings import build_embedder
from rag.retrieval import build_memory_retriever


def evaluate(retriever, items, k_max=10):
    hits = {1: 0, 3: 0, 5: 0}
    rr = 0.0
    for query, frags in items:
        res = retriever.search(query, k_max)
        rank = next((i for i, h in enumerate(res, 1) if any(f in h.url for f in frags)), None)
        if rank:
            rr += 1 / rank
            for k in hits:
                hits[k] += rank <= k
    n = len(items)
    return {f"hit@{k}": v / n for k, v in hits.items()} | {"mrr@10": rr / n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["bm25", "dense", "hybrid"])
    ap.add_argument("--embedder", default="sentence-transformers", choices=["sentence-transformers", "hashing"])
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()

    items = [
        (s.turns[0]["user_says"], s.expect["cites_url_contains_any"])
        for s in load_scenarios(ROOT / "evals" / "scenarios")
        if "cites_url_contains_any" in s.expect
    ]
    chunks = load_markdown_corpus(str(ROOT / "data" / "corpus"))
    embedder = build_embedder(a.embedder, "sentence-transformers/all-MiniLM-L6-v2") if set(a.modes) - {"bm25"} else None
    rows = [
        f"Retrieval baseline: {len(items)} questions, {len(chunks)} chunks, raw user wording as the query",
        "",
        "| Retriever | Hit@1 | Hit@3 | Hit@5 | MRR@10 |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode in a.modes:
        r = evaluate(build_memory_retriever(chunks, mode, embedder), items)
        label = mode if mode == "bm25" else f"{mode} ({a.embedder})"
        rows.append(f"| {label} | {r['hit@1']:.2f} | {r['hit@3']:.2f} | {r['hit@5']:.2f} | {r['mrr@10']:.2f} |")
    md = "\n".join(rows) + "\n"
    print(md)
    if a.out:
        a.out.write_text(md)


if __name__ == "__main__":
    main()

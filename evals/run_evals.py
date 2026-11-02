"""Run the scenario suite against a real model.

    python -m evals.run_evals                      # all scenarios, k=3, model from .env
    python -m evals.run_evals --only injection -k 1
    python -m evals.run_evals --gate --min-pass 0.8 # CI: fail on any safety violation or low pass^1

Writes evals/results/<model>-<timestamp>.json and prints a markdown summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from agent.config import Settings, build_retriever, load_releases
from agent.llm import build_llm
from evals.harness import load_scenarios, run_trial, summarize

HERE = Path(__file__).parent


def to_markdown(summary: dict, results, model: str) -> str:
    lines = [
        f"### Agent eval: `{model}`",
        "",
        f"{summary['scenarios']} scenarios × k={summary['k']} trials = {summary['trials']} runs",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| pass^1 (mean success) | {summary['pass_1']:.1%} |",
        f"| pass^{summary['k']} (all trials pass) | {summary['pass_k']:.1%} |",
        f"| Safety violations | {summary['safety_violations']} |",
        f"| Median model calls per conversation | {summary['median_steps']} |",
        f"| Median tokens per conversation | {summary['median_tokens']:,} |",
        f"| Median / p95 seconds | {summary['median_seconds']:.1f} / {summary['p95_seconds']:.1f} |",
        "",
        "| Category | pass^1 | Trials |",
        "|---|---:|---:|",
    ]
    for cat, v in summary["by_category"].items():
        lines.append(f"| {cat} | {v['pass_1']:.0%} | {v['trials']} |")
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "<details><summary>Failures</summary>", ""]
        for r in failed:
            lines.append(f"- `{r.scenario}` (trial {r.trial}): " + "; ".join(r.safety_violations + r.failures))
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", type=int, default=3, help="trials per scenario")
    ap.add_argument("--only", nargs="*", help="scenario ids or categories")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gate", action="store_true", help="exit 1 on safety violations or pass^1 < --min-pass")
    ap.add_argument("--min-pass", type=float, default=0.0)
    ap.add_argument("--out", type=Path, default=HERE / "results")
    a = ap.parse_args()

    settings = Settings.from_env()
    if not settings.llm_model or not settings.llm_api_key:
        sys.exit("Set LLM_PROVIDER, LLM_MODEL and LLM_API_KEY to run evals.")
    llm = build_llm(settings.llm_provider, settings.llm_model, settings.llm_api_key, settings.llm_base_url or None)
    print("building retriever...", file=sys.stderr)
    retriever = build_retriever(settings)
    releases = load_releases(settings.releases_path)
    scenarios = load_scenarios(HERE / "scenarios", a.only)

    jobs = [(s, t) for s in scenarios for t in range(a.k)]
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(lambda j: run_trial(j[0], j[1], llm, retriever, releases), jobs))
    for r in results:
        mark = "PASS" if r.passed else ("UNSAFE" if r.safety_violations else "fail")
        print(f"{mark:6s} {r.scenario} #{r.trial}  " + "; ".join(r.safety_violations + r.failures), file=sys.stderr)

    summary = summarize(results, a.k)
    md = to_markdown(summary, results, settings.llm_model)
    print(md)
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = settings.llm_model.replace("/", "_")
    (a.out / f"{name}-{stamp}.json").write_text(
        json.dumps(
            {
                "model": settings.llm_model,
                "summary": summary,
                "results": [asdict(r) for r in results],
                "seconds": time.time() - t0,
            },
            indent=1,
        )
    )
    (a.out / "latest.md").write_text(md)

    if a.gate and (summary["safety_violations"] > 0 or summary["pass_1"] < a.min_pass):
        sys.exit(
            f"eval gate failed: {summary['safety_violations']} safety violation(s), pass^1 {summary['pass_1']:.1%}"
        )


if __name__ == "__main__":
    main()

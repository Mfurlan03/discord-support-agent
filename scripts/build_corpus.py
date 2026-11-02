"""Build the knowledge base from a FastAPI checkout (MIT licensed).

Produces, inside data/:
  corpus/<path>.md   cleaned docs pages, with referenced code examples inlined
  releases.json      structured release notes: version, date, sections, items
  SOURCE.md          provenance + license notice

Usage:
  git clone --depth 1 --filter=blob:none --sparse https://github.com/fastapi/fastapi fastapi-src
  (cd fastapi-src && git sparse-checkout set docs/en/docs docs_src)
  python scripts/build_corpus.py fastapi-src
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"

# Pages that are navigation, people lists or site plumbing rather than answers.
SKIP_DIRS = {"reference", "img", "css", "js", "resources"}
SKIP_FILES = {
    "release-notes.md", "_llm-test.md", "translation-banner.md", "translations.md",
    "fastapi-people.md", "external-links.md", "newsletter.md", "management.md",
    "management-tasks.md", "contributing.md",
}  # fmt: skip

INCLUDE_RE = re.compile(r"\{\*\s*\.\./\.\./(docs_src/[^\s*]+)[^*]*\*\}")
HTML_TAG_RE = re.compile(r"<(?:dfn|abbr)[^>]*>(.*?)</(?:dfn|abbr)>", re.S)
VERSION_RE = re.compile(r"^## (\d+\.\d+\.\d+)(?: \((\d{4}-\d{2}-\d{2})\))?\s*$")
SECTION_RE = re.compile(r"^### (.+?)\s*$")


def inline_code(text: str, src_root: Path) -> str:
    """Replace `{* ../../docs_src/x.py hl[9] *}` with the code itself."""

    def repl(m: re.Match) -> str:
        path = src_root / m.group(1)
        if not path.exists():
            return ""
        code = path.read_text(encoding="utf-8").strip()
        return f"```python\n{code}\n```"

    return INCLUDE_RE.sub(repl, text)


def clean(text: str) -> str:
    text = HTML_TAG_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def parse_releases(text: str) -> list[dict]:
    releases: list[dict] = []
    cur: dict | None = None
    section = "Changes"
    for line in text.splitlines():
        if m := VERSION_RE.match(line):
            cur = {"version": m.group(1), "date": m.group(2), "sections": {}}
            releases.append(cur)
            section = "Changes"
            continue
        if line.startswith("## "):  # "Latest Changes" = unreleased
            cur = None
            continue
        if cur is None:
            continue
        if m := SECTION_RE.match(line):
            section = m.group(1)
            continue
        if line.startswith("* "):
            item = re.sub(r"\s*PR \[#\d+\]\([^)]*\) by \[@[^\]]+\]\([^)]*\)\.?", "", line[2:]).strip()
            item = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", item)  # drop link targets, keep text
            cur["sections"].setdefault(section, []).append(item)
    return releases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fastapi_checkout", type=Path)
    a = ap.parse_args()
    docs = a.fastapi_checkout / "docs" / "en" / "docs"
    src_root = a.fastapi_checkout

    corpus = OUT / "corpus"
    shutil.rmtree(corpus, ignore_errors=True)
    n = 0
    for path in sorted(docs.rglob("*.md")):
        rel = path.relative_to(docs)
        if rel.parts[0] in SKIP_DIRS or rel.name in SKIP_FILES:
            continue
        text = clean(inline_code(path.read_text(encoding="utf-8"), src_root))
        if len(text) < 200:
            continue
        dest = corpus / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        n += 1

    releases = parse_releases((docs / "release-notes.md").read_text(encoding="utf-8"))
    (OUT / "releases.json").write_text(json.dumps(releases, indent=1), encoding="utf-8")

    try:
        commit = subprocess.check_output(["git", "-C", str(a.fastapi_checkout), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        commit = "unknown"
    license_text = (a.fastapi_checkout / "LICENSE").read_text(encoding="utf-8")
    (OUT / "SOURCE.md").write_text(
        "# Knowledge base source\n\n"
        "`corpus/` and `releases.json` are derived from the FastAPI documentation\n"
        f"(https://github.com/fastapi/fastapi, commit `{commit}`), used under the MIT License below.\n"
        "Code examples from `docs_src/` are inlined where the docs reference them.\n\n"
        "```\n" + license_text + "```\n",
        encoding="utf-8",
    )
    print(f"{n} pages -> {corpus}\n{len(releases)} releases -> {OUT / 'releases.json'}")


if __name__ == "__main__":
    main()

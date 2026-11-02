"""Document loading and chunking."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    doc_name: str
    chunk_id: str
    index: int
    text: str
    url: str = ""
    title: str = ""


def chunk_fixed(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """Original strategy: fixed-size character windows with overlap.

    Simple, but cuts through words and bullet points. Kept as a baseline for the eval.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")
    step = chunk_size - overlap
    out = []
    for i in range(0, len(text), step):
        piece = text[i : i + chunk_size]
        if piece.strip():
            out.append(piece)
    return out


def chunk_lines(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    """Structure-aware strategy: pack whole lines into chunks, never splitting a line
    unless it alone exceeds ``chunk_size``. Overlap is carried as whole trailing lines.

    FAQ / onboarding docs are mostly bullets and Q/A pairs, so keeping lines intact keeps
    a question next to its answer.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Hard-wrap pathological long lines so no chunk exceeds the budget.
        while len(line) > chunk_size:
            lines.append(line[:chunk_size])
            line = line[chunk_size - overlap :]
        lines.append(line)

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > chunk_size:
            chunks.append("\n".join(current))
            # carry trailing lines (up to `overlap` chars) into the next chunk
            carry: list[str] = []
            carried = 0
            for prev in reversed(current):
                if carried + len(prev) + 1 > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev) + 1
            current, size = carry, carried
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


CHUNKERS = {"fixed": chunk_fixed, "lines": chunk_lines}


def load_documents(folder: str) -> dict[str, str]:
    docs = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.txt"))):
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig strips the BOM some exports add
            docs[os.path.basename(path)] = f.read()
    return docs


def chunk_documents(
    docs: dict[str, str], strategy: str = "lines", chunk_size: int = 900, overlap: int = 150
) -> list[Chunk]:
    chunker = CHUNKERS[strategy]
    out = []
    for name, text in docs.items():
        for i, piece in enumerate(chunker(text, chunk_size, overlap)):
            out.append(Chunk(doc_name=name, chunk_id=f"{name}_chunk_{i}", index=i, text=piece))
    return out


# ---------------------------------------------------------------- markdown docs

_HEADING_RE = re.compile(r"^(#{1,3}) (.+?)(?:\s*\{\s*#([\w-]+)\s*\})?\s*$")
DOCS_BASE_URL = "https://fastapi.tiangolo.com/"


def page_url(rel_path: str, base: str = DOCS_BASE_URL) -> str:
    stem = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    if stem == "index":
        return base
    if stem.endswith("/index"):
        stem = stem[: -len("/index")]
    return f"{base}{stem}/"


def split_sections(text: str) -> list[tuple[list[str], str, str]]:
    """Split markdown into (heading_path, anchor, body) sections at #, ## and ### headings.

    Headings inside fenced code blocks are ignored.
    """
    sections: list[tuple[list[str], str, str]] = []
    path: list[str] = []
    anchor = ""
    body: list[str] = []
    in_code = False

    def flush():
        content = "\n".join(body).strip()
        if content:
            sections.append((list(path), anchor, content))

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path = path[: level - 1] + [m.group(2).strip()]
            anchor = m.group(3) or ""
            body = []
        else:
            body.append(line)
    flush()
    return sections


def chunk_markdown(rel_path: str, text: str, chunk_size: int = 1200, overlap: int = 150) -> list[Chunk]:
    """Heading-aware chunks: one per section, long sections split by lines.

    Each chunk starts with its heading path ("Query Parameters > Defaults") so it is
    self-describing for both retrieval and the LLM, and carries a deep link.
    """
    url = page_url(rel_path)
    out: list[Chunk] = []
    for headings, anchor, body in split_sections(text):
        title = " > ".join(headings) if headings else rel_path
        link = f"{url}#{anchor}" if anchor else url
        for piece in chunk_lines(body, chunk_size, overlap):
            i = len(out)
            out.append(
                Chunk(
                    doc_name=rel_path,
                    chunk_id=f"{rel_path}#{anchor or 'top'}-{i}",
                    index=i,
                    text=f"{title}\n\n{piece}",
                    url=link,
                    title=title,
                )
            )
    return out


def load_markdown_corpus(folder: str, chunk_size: int = 1200, overlap: int = 150) -> list[Chunk]:
    root = os.path.abspath(folder)
    chunks: list[Chunk] = []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        with open(path, encoding="utf-8") as f:
            chunks.extend(chunk_markdown(rel, f.read(), chunk_size, overlap))
    return chunks

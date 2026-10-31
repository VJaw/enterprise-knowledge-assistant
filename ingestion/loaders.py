"""Loaders for the raw corpus formats (§B5.2): PDF policies, Markdown with front-matter, FAQ JSON."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from pypdf import PdfReader

DOC_DIRS = {"policies": "policy", "manuals": "manual", "sops": "sop", "incidents": "incident"}


@dataclass
class Page:
    page: int  # 1-based
    text: str


@dataclass
class RawDoc:
    source_path: str
    fmt: str  # "pdf" | "md" | "faq"
    text: str
    pages: list[Page] | None = None
    front_matter: dict = field(default_factory=dict)
    type_hint: str | None = None  # from the folder name, used by the filename rules


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_pdf(path: Path, root: Path = Path(".")) -> RawDoc:
    reader = PdfReader(path)
    pages = [Page(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    return RawDoc(_rel(path, root), "pdf", "\n\n".join(p.text for p in pages), pages=pages,
                  type_hint=DOC_DIRS.get(path.parent.name))


def load_markdown(path: Path, root: Path = Path(".")) -> RawDoc:
    post = frontmatter.load(path)
    return RawDoc(_rel(path, root), "md", post.content, front_matter=dict(post.metadata),
                  type_hint=DOC_DIRS.get(path.parent.name))


def load_faq(path: Path, root: Path = Path(".")) -> list[RawDoc]:
    data = json.loads(path.read_text())
    defaults = {k: v for k, v in data.items() if k != "items"}
    docs = []
    for item in data["items"]:
        fm = {**defaults, "doc_id": item["faq_id"], "title": item["question"], "doc_type": "faq",
              "domain": "support", "category": item.get("category")}
        docs.append(RawDoc(_rel(path, root), "faq", f"Q: {item['question']}\nA: {item['answer']}",
                           front_matter=fm, type_hint="faq"))
    return docs


def load_raw_dir(raw_dir: Path, root: Path = Path(".")) -> list[RawDoc]:
    docs: list[RawDoc] = []
    for sub in DOC_DIRS:
        for path in sorted((raw_dir / sub).glob("*")):
            if path.suffix == ".pdf":
                docs.append(load_pdf(path, root))
            elif path.suffix == ".md":
                docs.append(load_markdown(path, root))
    if (raw_dir / "faq.json").exists():
        docs.extend(load_faq(raw_dir / "faq.json", root))
    return docs

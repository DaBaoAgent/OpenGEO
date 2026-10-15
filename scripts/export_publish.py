#!/usr/bin/env python3
"""Build the publish set for a delivered article.

A delivered article occupies two sibling folders that share one sequence number
and one slug:

    drafts/<NN>-<slug>/   the gated working package: the article, the numbered
                          images, the hero, the rendered HTML and PDF, the kb/
                          evidence bundle, preview/ and review.md
    out/<NN>-<slug>/      the publish set: the article .md plus the numbered
                          image files and nothing else

This script builds the second folder from the first, so the publish set is a
derived artifact rather than a folder someone remembers to update by hand. It
copies the article verbatim apart from one class of edit: a renderer
placeholder such as [TOC], which blog_render.py expands into a real contents
block in the HTML but which would show up as literal text on a publishing
surface where no renderer is involved.

The hero becomes the cover. Every image the article references is copied under
the name the article already uses, and the numbering convention is enforced:
the referenced names must carry a two-digit prefix in ascending reference
order, so the folder lists in the order the article reads.

Exit codes: 0 success, 1 the draft does not satisfy the layout contract,
2 usage error.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"^[ \t]*\[(?:TOC|toc)\][ \t]*$")
LABEL_RE = re.compile(r"^\*\*[^*]+\*\*$")
IMG_TAG_RE = re.compile(r"<img\b[^>]*?\bsrc=[\"\']([^\"\']+)[\"\']", re.IGNORECASE)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
HERO_NAMES = ("hero.png", "hero.jpg", "hero.jpeg", "hero.webp")
NUMBERED_RE = re.compile(r"^([0-9]{2})-[^/]+\.(?:jpg|jpeg|png)$", re.IGNORECASE)
DRAFT_NAME_RE = re.compile(r"^([0-9]{2})-([a-z0-9]+(?:-[a-z0-9]+)*)$")


class LayoutError(Exception):
    """The draft folder does not match the two-folder delivery contract."""


def _is_remote(src: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", src, re.IGNORECASE)) or src.startswith("//")


def strip_renderer_placeholders(text: str) -> tuple[str, list[str]]:
    """Drop [TOC]-style renderer placeholders and any orphaned label above them."""
    lines = text.replace("\r\n", "\n").split("\n")
    drop: set[int] = set()
    dropped: list[str] = []
    for index, line in enumerate(lines):
        if not PLACEHOLDER_RE.match(line):
            continue
        drop.add(index)
        dropped.append(line.strip())
        for back in range(index - 1, -1, -1):
            if not lines[back].strip():
                continue
            if LABEL_RE.match(lines[back].strip()):
                drop.add(back)
                dropped.append(lines[back].strip())
            break
    kept = [line for index, line in enumerate(lines) if index not in drop]
    collapsed: list[str] = []
    for line in kept:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed), dropped


def referenced_images(article: str) -> list[str]:
    """Return the local image references in the order the article uses them."""
    hits: list[tuple[int, str]] = []
    for pattern in (IMG_TAG_RE, MD_IMG_RE):
        for match in pattern.finditer(article):
            src = match.group(1).strip()
            if _is_remote(src):
                continue
            hits.append((match.start(), src))
    ordered: list[str] = []
    for _position, src in sorted(hits):
        if src not in ordered:
            ordered.append(src)
    return ordered


def plan_publish_set(draft: Path) -> dict:
    """Validate the draft layout and return the file list the publish set holds."""
    if not draft.is_dir():
        raise LayoutError(f"draft folder not found: {draft}")
    name_match = DRAFT_NAME_RE.match(draft.name)
    if not name_match:
        raise LayoutError(
            f"draft folder must be named <NN>-<slug>, got {draft.name!r}"
        )
    slug = draft.name[name_match.start(2):]

    article_path = draft / f"{slug}.md"
    if not article_path.is_file():
        raise LayoutError(f"article markdown not found: {article_path.name}")

    hero = next((draft / candidate for candidate in HERO_NAMES if (draft / candidate).is_file()), None)
    if hero is None:
        raise LayoutError(
            "hero image not found; expected one of " + ", ".join(HERO_NAMES)
        )

    article = article_path.read_text(encoding="utf-8")
    sources = [src for src in referenced_images(article) if Path(src).name not in HERO_NAMES]
    if not sources:
        raise LayoutError("the article references no local images")

    prefixes: list[str] = []
    for src in sources:
        if Path(src).parent != Path("."):
            raise LayoutError(
                f"image reference must be flat in the package, got {src!r}"
            )
        if not (draft / src).is_file():
            raise LayoutError(f"referenced image missing from the draft: {src}")
        numbered = NUMBERED_RE.match(src)
        if not numbered:
            raise LayoutError(
                f"image {src!r} must be named <NN>-<name>.jpg|jpeg|png so the publish set orders itself"
            )
        prefixes.append(numbered.group(1))
    if prefixes != sorted(prefixes):
        raise LayoutError(
            f"image numbering must ascend in reference order, got {prefixes}"
        )
    if len(set(prefixes)) != len(prefixes):
        raise LayoutError(f"duplicate image numbers: {prefixes}")

    return {
        "slug": slug,
        "article": article_path,
        "hero": hero,
        "images": sources,
        "publish_dir_name": draft.name,
    }


def export(draft: Path, out_root: Path, force: bool = False) -> dict:
    plan = plan_publish_set(draft)
    publish_dir = out_root / plan["publish_dir_name"]
    if publish_dir.exists():
        if not force:
            raise LayoutError(
                f"publish folder already exists: {publish_dir} (pass --force to rebuild)"
            )
        if any(child.is_dir() for child in publish_dir.iterdir()):
            raise LayoutError(
                f"refusing to rebuild {publish_dir}: it contains a subdirectory"
            )
        for child in publish_dir.iterdir():
            child.unlink()

    publish_dir.mkdir(parents=True, exist_ok=True)

    article = plan["article"].read_text(encoding="utf-8")
    article, dropped = strip_renderer_placeholders(article)
    (publish_dir / plan["article"].name).write_text(article, encoding="utf-8")

    cover = publish_dir / "01-cover.jpg"
    if plan["hero"].suffix.lower() in {".jpg", ".jpeg"}:
        shutil.copyfile(plan["hero"], cover)
    else:
        try:
            from PIL import Image
        except ImportError as exc:
            raise LayoutError(
                f"cannot convert {plan['hero'].name} to JPEG: Pillow is not installed"
            ) from exc
        with Image.open(plan["hero"]) as image:
            image.convert("RGB").save(cover, "JPEG", quality=90, optimize=True)

    for src in plan["images"]:
        shutil.copyfile(draft / src, publish_dir / src)

    return {
        "publish_dir": publish_dir,
        "article": plan["article"].name,
        "cover": cover.name,
        "images": plan["images"],
        "dropped_placeholders": dropped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--draft", required=True, help="Path to drafts/<NN>-<slug>")
    parser.add_argument("--out-root", default="out", help="Root of the publish sets (default: out)")
    parser.add_argument("--force", action="store_true", help="Rebuild an existing publish folder")
    args = parser.parse_args(argv)

    try:
        result = export(Path(args.draft), Path(args.out_root), force=args.force)
    except LayoutError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    publish_dir = result["publish_dir"]
    total = 1 + 1 + len(result["images"])
    print(f"OK: {publish_dir} ({total} files)")
    print(f"  {result['article']}")
    print(f"  {result['cover']}")
    for name in result["images"]:
        print(f"  {name}")
    for line in result["dropped_placeholders"]:
        print(f"  dropped renderer placeholder: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the publish set for a delivered article.

A delivered article occupies two sibling folders that share one sequence number
and one slug:

    drafts/<NN>-<slug>/   the gated working package: the article source, the
                          numbered images, the hero, the rendered HTML and PDF,
                          the kb/ evidence bundle, preview/ and review.md
    out/<NN>-<slug>/      the publish set: the article .md plus the numbered
                          image files and nothing else

This script builds the second folder from the first, and rewrites the article on
the way out. The draft is a build input: it carries YAML frontmatter, raw <img>
tags and markdown tables, all of which render well through blog_render.py and
none of which survive a paste into a publishing surface. WeChat and Toutiao take
text and images, so their copy has to be readable as plain text:

  * YAML frontmatter is dropped and the H1 carries the title.
  * <img src alt> becomes a markdown image, so the operator can see which file
    belongs at which point in the copy.
  * A table whose data cells are all bare numbers is a series, and is transposed
    into a wrapped line per column. Any other table becomes one bullet per row with
    its column labels attached, because a pasted markdown table arrives as
    literal pipes.
  * Blockquote markers and inline code spans are dropped. An internal document
    path is not something a reader of the published post can use.
  * Renderer placeholders such as [TOC] are removed, along with a bold label
    left stranded above them.

The hero becomes the cover. Every image the article references is copied under
the name the article already uses, and the numbering convention is enforced: the
referenced names must carry a two-digit prefix in ascending reference order, so
the folder lists in the order the article reads.

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
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
MD_IMG_FULL_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)]*)?\)")
BLOCKQUOTE_RE = re.compile(r"^[ \t]*>[ \t]?")
CODE_SPAN_RE = re.compile(r"`([^`]*)`")
EMPTY_PARENS_RE = re.compile(r"[（(][\s、,，;；·]*[）)]")
PAREN_RE = re.compile(r"[（(][^（）()]*[）)]")
PATHY_RE = re.compile(r"[/\\][^（）()]*\.[A-Za-z0-9]{2,5}")
NUMBER_RE = re.compile(r"^[\d,]+(?:\.\d+)?$")
SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")
# A 14-point series on one line is a wall on a phone screen.
SERIES_WRAP = 5
FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
HERO_NAMES = ("hero.png", "hero.jpg", "hero.jpeg", "hero.webp")
# WebP is accepted next to hero.webp above: the publish set copies the file
# verbatim, so the inline images keep whatever format the draft shipped.
NUMBERED_RE = re.compile(r"^([0-9]{2})-[^/]+\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)
DRAFT_NAME_RE = re.compile(r"^([0-9]{2})-([a-z0-9]+(?:-[a-z0-9]+)*)$")

# A bare number in a transposed series reads as a typo once the grid is gone, so
# the unit is restored on the way out. The pipeline's numeric series are prices.
NUMERIC_UNIT = "元"

# The publish set is posted by hand, so the copy has to say where each file
# goes. The marker carries the number the file already uses, and the caption
# line is the text to paste under the image once it is inserted.
BODY_MARKER = "【插图 {number}：{src}】"
COVER_MARKER = "【封面图：{src}（在平台的封面位置上传，正文里不重复插入）】"
CAPTION_PREFIX = "图注："


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


def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block; the H1 already carries the title."""
    match = FRONTMATTER_RE.match(text.replace("\r\n", "\n"))
    return text[match.end():] if match else text


def referenced_images(article: str) -> list[str]:
    """Return the local image references in the order the article uses them."""
    hits: list[tuple[int, str]] = []
    for match in IMG_TAG_RE.finditer(article):
        src = _attribute(match.group(0), "src")
        if src and not _is_remote(src):
            hits.append((match.start(), src))
    for match in MD_IMG_RE.finditer(article):
        src = match.group(1).strip()
        if src and not _is_remote(src):
            hits.append((match.start(), src))
    ordered: list[str] = []
    for _position, src in sorted(hits):
        if src not in ordered:
            ordered.append(src)
    return ordered


def _attribute(tag: str, name: str) -> str:
    match = re.search(name + r'\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def mark_image_insertions(text: str) -> str:
    """Turn each markdown image into a labelled insertion point."""
    def replacement(match: re.Match[str]) -> str:
        caption = match.group(1).strip()
        src = match.group(2).strip()
        numbered = NUMBERED_RE.match(src)
        number = int(numbered.group(1)) if numbered else 0
        return BODY_MARKER.format(number=number, src=src) + "\n" + CAPTION_PREFIX + caption

    return MD_IMG_FULL_RE.sub(replacement, text)


def mark_cover(text: str, cover_src: str) -> str:
    """Name the cover file directly under the headline."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines[index + 1:index + 1] = ["", COVER_MARKER.format(src=cover_src)]
            break
    return "\n".join(lines)


def strip_path_citations(line: str) -> str:
    """Drop parentheses that only cite an internal file path.

    The evidence trail is provenance for the drafting step; a reader of the
    published post cannot open `jks-product/佳康顺轮椅租赁流程.doc-fef73d.md`. A
    parenthesis that is a markdown link target, or that carries a scheme, is not
    a citation and must survive.
    """
    out: list[str] = []
    index = 0
    for match in PAREN_RE.finditer(line):
        content = match.group(0)[1:-1]
        target_of_link = match.start() > 0 and line[match.start() - 1] == "]"
        carries_scheme = "://" in content or "www." in content
        if target_of_link or carries_scheme or not PATHY_RE.search(content):
            continue
        out.append(line[index:match.start()])
        index = match.end()
    out.append(line[index:])
    return "".join(out)


def convert_images(text: str) -> str:
    """<img src alt width height> becomes a markdown image with its caption."""
    def replacement(match: re.Match[str]) -> str:
        tag = match.group(0)
        src = _attribute(tag, "src")
        caption = _attribute(tag, "alt") or Path(src).stem
        return f"![{caption}]({src})"

    return IMG_TAG_RE.sub(replacement, text)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render_table(rows: list[list[str]]) -> list[str]:
    """Render one markdown table as pasteable text.

    A table whose data cells are all bare numbers is a series over a shared key,
    so it transposes into one wrapped line per column. Anything else keeps its
    rows and gains its column labels, because the reader has no grid to line the
    values up against.
    """
    header = rows[0]
    width = len(header)
    body = [row + [""] * (width - len(row)) for row in rows[1:]]
    body = [row[:width] for row in body]
    if len(body) >= 3 and all(NUMBER_RE.match(row[col]) for row in body for col in range(1, width)):
        lines: list[str] = []
        for col in range(1, width):
            pairs = [f"{row[0]} {row[col]}{NUMERIC_UNIT}" for row in body if row[col]]
            if not pairs:
                continue
            lines.append(f"**{header[col]}**")
            for start in range(0, len(pairs), SERIES_WRAP):
                lines.append(" · ".join(pairs[start:start + SERIES_WRAP]))
            lines.append("")
        return lines

    lines = []
    labels = header[1:]
    for row in body:
        facts = [
            f"{label} {value}"
            for label, value in zip(labels, row[1:])
            if value and value != "/"
        ]
        suffix = "；".join(facts)
        lines.append(f"- **{row[0]}**" + (f"：{suffix}" if suffix else ""))
    lines.append("")
    return lines


def convert_tables(lines: list[str]) -> list[str]:
    """Replace every markdown table with its pasteable rendering."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            out.append(lines[index])
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            block.append(lines[index])
            index += 1
        rows = [_cells(line) for line in block]
        rows = [row for row in rows if not (row and all(SEPARATOR_RE.match(cell) for cell in row if cell))]
        if rows:
            out.extend(render_table(rows))
        else:
            out.extend(block)
    return out


def to_platform_markdown(text: str, cover_src: str = "01-cover.jpg") -> tuple[str, list[str]]:
    """Rewrite a draft into the copy a publishing surface accepts."""
    notes: list[str] = []
    text = strip_frontmatter(text)
    text, dropped = strip_renderer_placeholders(text)
    notes.extend(f"dropped renderer placeholder: {line}" for line in dropped)
    text = convert_images(text)
    text = mark_image_insertions(text)
    text = mark_cover(text, cover_src)

    lines: list[str] = []
    for line in text.split("\n"):
        if BLOCKQUOTE_RE.match(line):
            line = BLOCKQUOTE_RE.sub("", line)
        line = CODE_SPAN_RE.sub(lambda match: match.group(1), line)
        line = strip_path_citations(line)
        line = EMPTY_PARENS_RE.sub("", line)
        lines.append(line.rstrip())

    lines = convert_tables(lines)
    collapsed: list[str] = []
    for line in lines:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip("\n") + "\n", notes


def plan_publish_set(draft: Path, platform: bool = True) -> dict:
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
        raise LayoutError("hero image not found; expected one of " + ", ".join(HERO_NAMES))

    article = article_path.read_text(encoding="utf-8")
    sources = [src for src in referenced_images(article) if Path(src).name not in HERO_NAMES]
    if not sources:
        raise LayoutError("the article references no local images")

    prefixes: list[str] = []
    for src in sources:
        if Path(src).parent != Path("."):
            raise LayoutError(f"image reference must be flat in the package, got {src!r}")
        if not (draft / src).is_file():
            raise LayoutError(f"referenced image missing from the draft: {src}")
        numbered = NUMBERED_RE.match(src)
        if not numbered:
            raise LayoutError(
                f"image {src!r} must be named <NN>-<name>.jpg|jpeg|png so the publish set orders itself"
            )
        prefixes.append(numbered.group(1))
    if prefixes != sorted(prefixes):
        raise LayoutError(f"image numbering must ascend in reference order, got {prefixes}")
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
            raise LayoutError(f"refusing to rebuild {publish_dir}: it contains a subdirectory")
        for child in publish_dir.iterdir():
            child.unlink()

    publish_dir.mkdir(parents=True, exist_ok=True)
    cover_src = "01-cover.jpg"

    article = plan["article"].read_text(encoding="utf-8")
    article, notes = to_platform_markdown(article, cover_src=cover_src)
    (publish_dir / plan["article"].name).write_text(article, encoding="utf-8")

    cover = publish_dir / cover_src
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
        "notes": notes,
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
    total = 2 + len(result["images"])
    print(f"OK: {publish_dir} ({total} files)")
    print(f"  {result['article']}")
    print(f"  {result['cover']}")
    for name in result["images"]:
        print(f"  {name}")
    for note in result["notes"]:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

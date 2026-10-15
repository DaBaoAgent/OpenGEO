"""Unit regressions for scripts/export_publish.py.

The publish set is the folder someone uploads, so the failures that matter are
the quiet ones: a stale image left behind, an article that references a file the
folder does not hold, a renderer placeholder surviving into published text, or
the exporter overwriting a folder it did not build. These tests pin the layout
contract (flat, numbered, ascending) and the refusal paths.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_exporter():
    name = "export_publish_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "export_publish.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exporter = _load_exporter()

SLUG = "wheelchair-rental"
ARTICLE = """---
title: T
---

# Heading

**本文目录**

[TOC]

## Section

<img src="02-scene.jpg" alt="a" width="900" height="1200">

Text.

<img src="03-store.jpg" alt="b" width="900" height="1200">
"""


def make_draft(root: Path, name: str = f"01-{SLUG}", article: str = ARTICLE) -> Path:
    draft = root / name
    draft.mkdir(parents=True)
    (draft / f"{SLUG}.md").write_text(article, encoding="utf-8")
    (draft / "hero.jpg").write_bytes(b"hero-bytes")
    (draft / "02-scene.jpg").write_bytes(b"scene-bytes")
    (draft / "03-store.jpg").write_bytes(b"store-bytes")
    return draft


def test_strips_placeholder_and_its_label():
    text, dropped = exporter.strip_renderer_placeholders(ARTICLE)
    assert "[TOC]" not in text
    assert "本文目录" not in text
    assert dropped == ["[TOC]", "**本文目录**"]
    assert "\n\n\n" not in text


def test_strip_leaves_a_bare_placeholder_without_a_label():
    text, dropped = exporter.strip_renderer_placeholders("# H\n\n[TOC]\n\nBody\n")
    assert text == "# H\n\nBody\n"
    assert dropped == ["[TOC]"]


def test_referenced_images_are_in_document_order_and_skip_remote():
    article = "![a](04-b.jpg)\n<img src=\"02-x.jpg\">\n<img src=\"https://e.com/c.jpg\">\n![d](03-c.jpg)\n"
    assert exporter.referenced_images(article) == ["04-b.jpg", "02-x.jpg", "03-c.jpg"]


def test_plan_rejects_a_folder_without_a_sequence_number(tmp_path):
    draft = make_draft(tmp_path, name=SLUG)
    with pytest.raises(exporter.LayoutError, match="must be named"):
        exporter.plan_publish_set(draft)


def test_plan_rejects_an_unnumbered_image(tmp_path):
    article = ARTICLE.replace("02-scene.jpg", "scene.jpg")
    draft = make_draft(tmp_path, article=article)
    (draft / "scene.jpg").write_bytes(b"scene-bytes")
    with pytest.raises(exporter.LayoutError, match="must be named"):
        exporter.plan_publish_set(draft)


def test_plan_rejects_descending_numbers(tmp_path):
    article = ARTICLE.replace('src="02-scene.jpg"', 'src="09-scene.jpg"')
    draft = make_draft(tmp_path, article=article)
    (draft / "09-scene.jpg").write_bytes(b"scene-bytes")
    with pytest.raises(exporter.LayoutError, match="ascend"):
        exporter.plan_publish_set(draft)


def test_plan_rejects_a_missing_referenced_image(tmp_path):
    draft = make_draft(tmp_path)
    (draft / "03-store.jpg").unlink()
    with pytest.raises(exporter.LayoutError, match="missing from the draft"):
        exporter.plan_publish_set(draft)


def test_export_writes_only_the_article_cover_and_images(tmp_path):
    draft = make_draft(tmp_path)
    (draft / "review.md").write_text("stale", encoding="utf-8")
    (draft / "01-kb").mkdir()

    result = exporter.export(draft, tmp_path / "out")
    publish = result["publish_dir"]

    assert sorted(p.name for p in publish.iterdir()) == [
        "01-cover.jpg",
        "02-scene.jpg",
        "03-store.jpg",
        f"{SLUG}.md",
    ]
    assert result["cover"] == "01-cover.jpg"
    assert (publish / "01-cover.jpg").read_bytes() == b"hero-bytes"
    assert (publish / "02-scene.jpg").read_bytes() == b"scene-bytes"
    assert "[TOC]" not in (publish / f"{SLUG}.md").read_text(encoding="utf-8")


def test_export_refuses_to_overwrite_without_force(tmp_path):
    draft = make_draft(tmp_path)
    exporter.export(draft, tmp_path / "out")
    with pytest.raises(exporter.LayoutError, match="already exists"):
        exporter.export(draft, tmp_path / "out")


def test_export_force_removes_a_stale_image(tmp_path):
    draft = make_draft(tmp_path)
    out_root = tmp_path / "out"
    exporter.export(draft, out_root)
    stale = out_root / draft.name / "09-stale.jpg"
    stale.write_bytes(b"old")

    exporter.export(draft, out_root, force=True)

    assert not stale.exists()
    assert sorted(p.name for p in (out_root / draft.name).iterdir()) == [
        "01-cover.jpg",
        "02-scene.jpg",
        "03-store.jpg",
        f"{SLUG}.md",
    ]


def test_export_force_refuses_a_subdirectory(tmp_path):
    draft = make_draft(tmp_path)
    out_root = tmp_path / "out"
    exporter.export(draft, out_root)
    (out_root / draft.name / "nested").mkdir()
    with pytest.raises(exporter.LayoutError, match="subdirectory"):
        exporter.export(draft, out_root, force=True)


def test_main_reports_a_layout_error_as_exit_1(tmp_path, capsys):
    draft = make_draft(tmp_path, name=SLUG)
    assert exporter.main(["--draft", str(draft), "--out-root", str(tmp_path / "out")]) == 1
    assert "ERROR" in capsys.readouterr().err

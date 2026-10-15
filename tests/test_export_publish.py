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

NL = chr(10)
BT = chr(96)
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


def test_strip_frontmatter_drops_the_yaml_block():
    text = exporter.strip_frontmatter("---" + NL + "title: T" + NL + "tags:" + NL + "  - a" + NL + "---" + NL + NL + "# H" + NL)
    assert text.startswith(NL + "# H")
    assert "title:" not in text


def test_strip_frontmatter_leaves_a_body_without_frontmatter():
    assert exporter.strip_frontmatter("# H" + NL) == "# H" + NL


def test_convert_images_keeps_the_caption_and_the_filename():
    tag = '<img src="02-a.jpg" alt="一名使用者" width="900" height="1200">'
    assert exporter.convert_images(tag) == "![一名使用者](02-a.jpg)"


def test_render_table_transposes_a_numeric_series():
    rows = [["租期", "手动", "电动"], ["1 天", "15", "50"], ["2 天", "28", "98"], ["3 天", "39", "144"]]
    unit = exporter.NUMERIC_UNIT
    assert exporter.render_table(rows) == [
        "**手动**",
        f"1 天 15{unit} · 2 天 28{unit} · 3 天 39{unit}",
        "",
        "**电动**",
        f"1 天 50{unit} · 2 天 98{unit} · 3 天 144{unit}",
        "",
    ]


def test_render_table_keeps_rows_when_cells_are_not_numbers():
    rows = [["型号", "库存", "状态"], ["865-20", "40+ 台", "充足"], ["EY-05", "没货", "缺货"]]
    assert exporter.render_table(rows) == [
        "- **865-20**：库存 40+ 台；状态 充足",
        "- **EY-05**：库存 没货；状态 缺货",
        "",
    ]


def test_render_table_skips_absent_values_in_row_mode():
    rows = [["型号", "电池", "库存"], ["865-20", "/", "40+ 台"], ["P16", "10 锂电", "/"]]
    assert exporter.render_table(rows) == [
        "- **865-20**：库存 40+ 台",
        "- **P16**：电池 10 锂电",
        "",
    ]


def test_to_platform_markdown_drops_pipeline_markup():
    text = (
        "---" + NL + "title: T" + NL + "---" + NL + NL
        + "# H" + NL + NL
        + "> 说明：x" + NL + NL
        + BT + "internal/path.md" + BT + " 见下。" + NL + NL
        + "**本文目录**" + NL + NL + "[TOC]" + NL
    )
    out, notes = exporter.to_platform_markdown(text)
    assert "title:" not in out
    assert "[TOC]" not in out
    assert "本文目录" not in out
    assert "> 说明" not in out
    assert BT not in out
    assert "internal/path.md 见下。" in out
    assert any("placeholder" in note for note in notes)


def test_to_platform_markdown_removes_a_citation_only_parenthesis():
    text = "# H" + NL + NL + "- 《价格表》：日租价。（" + BT + "jks-product/x-08afbf.png" + BT + "）" + NL
    out, _notes = exporter.to_platform_markdown(text)
    assert "jks-product" not in out
    assert "- 《价格表》：日租价。" in out


def test_to_platform_markdown_keeps_the_article_title_as_the_h1():
    text = "---" + NL + "title: T" + NL + "---" + NL + NL + "# 真正的标题" + NL + NL + "正文。" + NL
    out, _notes = exporter.to_platform_markdown(text)
    assert out.startswith("# 真正的标题" + NL)


def test_export_writes_platform_markdown_not_the_draft_bytes(tmp_path):
    draft = make_draft(tmp_path)
    result = exporter.export(draft, tmp_path / "out")
    published = (result["publish_dir"] / f"{SLUG}.md").read_text(encoding="utf-8")
    assert "<img" not in published
    assert "![a](02-scene.jpg)" not in published
    assert "【插图 2：02-scene.jpg】" in published
    assert "图注：a" in published


def test_export_names_the_cover_under_the_headline(tmp_path):
    draft = make_draft(tmp_path)
    published = (
        exporter.export(draft, tmp_path / "out")["publish_dir"] / f"{SLUG}.md"
    ).read_text(encoding="utf-8")
    lines = published.split(NL)
    assert lines[0] == "# Heading"
    assert lines[2] == exporter.COVER_MARKER.format(src="01-cover.jpg")


def test_mark_image_insertions_numbers_each_file_by_its_prefix():
    text = "![甲](02-a.jpg)" + NL + NL + "正文" + NL + NL + "![乙](05-b.jpg)" + NL
    marked = exporter.mark_image_insertions(text)
    assert "【插图 2：02-a.jpg】" + NL + "图注：甲" in marked
    assert "【插图 5：05-b.jpg】" + NL + "图注：乙" in marked


def test_mark_cover_does_not_duplicate_an_existing_marker():
    text = "# 标题" + NL + NL + "正文。" + NL
    once = exporter.mark_cover(text, "01-cover.jpg")
    assert once.count(exporter.COVER_MARKER.format(src="01-cover.jpg")) == 1


def test_strip_path_citations_keeps_a_markdown_link_target():
    line = "见[电动轮椅产品页](https://www.unqcare.com/product/1/)。"
    assert exporter.strip_path_citations(line) == line


def test_strip_path_citations_keeps_a_parenthesised_url():
    line = "公告见（https://www.caac.gov.cn/XXGK/1.html）。"
    assert exporter.strip_path_citations(line) == line


def test_strip_path_citations_drops_an_internal_path():
    line = "- 《价格表》：日租价。（jks-product/x-08afbf.png）"
    assert exporter.strip_path_citations(line) == "- 《价格表》：日租价。"


def test_strip_path_citations_drops_two_internal_paths():
    line = "自提点名称。（jks-product/2-918418.png、marketing/a-dfb3bc.md）"
    assert exporter.strip_path_citations(line) == "自提点名称。"


def test_to_platform_markdown_keeps_links_and_drops_internal_paths():
    text = (
        "# H" + NL + NL
        + "见[产品页](https://www.unqcare.com/product/1/)，出处（" + BT + "jks-product/a-1.md" + BT + "）。" + NL
    )
    out, _notes = exporter.to_platform_markdown(text)
    assert "[产品页](https://www.unqcare.com/product/1/)" in out
    assert "jks-product" not in out

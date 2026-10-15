"""Unit regressions for scripts/kb_client.py.

Covers the parts of the company knowledge base client that decide what gets
downloaded: the extension filter (a suffix carries a leading dot, a user
argument does not), failure reporting instead of silent skipping, and the name
resolver that recovers an `原件:` reference living outside the file-service
tree without ever guessing between same-named files.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_kb_client():
    name = "kb_client_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "kb_client.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kb = _load_kb_client()


class FakeFiles:
    """Stand-in for KbFiles: an in-memory tree and a recording downloader."""

    def __init__(self, tree, fail=()):
        self.base = "http://kb.invalid/api"
        self.tree = tree
        self.fail = set(fail)
        self.downloaded = []

    def list_dir(self, root, path=""):
        return {"entries": self.tree.get(path.strip("/"), [])}

    def download(self, root, path, out):
        if path in self.fail:
            raise kb.KbError(f"download {path}: HTTP 404")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"payload")
        self.downloaded.append(path)
        return {"bytes": 7, "sha256": "0" * 64}


TREE = {
    "": [
        {"name": "a.jpg", "is_dir": False, "size": 10},
        {"name": "sub", "is_dir": True, "size": 0},
        {"name": "#recycle", "is_dir": True, "size": 0},
    ],
    "sub": [
        {"name": "b.png", "is_dir": False, "size": 20},
        {"name": "c.txt", "is_dir": False, "size": 30},
    ],
}


def test_ext_filter_adds_the_leading_dot():
    assert kb._ext_filter("jpg,png") == {".jpg", ".png"}
    assert kb._ext_filter(" .WEBP , JPEG ") == {".webp", ".jpeg"}
    assert kb._ext_filter("") is None
    assert kb._ext_filter(None) is None


def test_download_assets_honours_the_extension_filter(tmp_path):
    files = FakeFiles(TREE)
    records = [
        {"root": "files", "path": "a.jpg", "size": 10},
        {"root": "files", "path": "sub/b.png", "size": 20},
        {"root": "files", "path": "sub/c.txt", "size": 30},
    ]
    downloaded, failed = kb._download_assets(
        files, records, tmp_path, allowed_ext=kb._ext_filter("jpg")
    )
    assert [item["source_path"] for item in downloaded] == ["a.jpg"]
    assert failed == []
    assert (tmp_path / "a.jpg").is_file()


def test_download_assets_reports_failures_instead_of_dropping_them(tmp_path):
    files = FakeFiles(TREE, fail={"a.jpg"})
    records = [
        {"root": "files", "path": "a.jpg", "size": 10},
        {"root": "files", "path": "sub/b.png", "size": 20},
    ]
    downloaded, failed = kb._download_assets(files, records, tmp_path)
    assert [item["source_path"] for item in downloaded] == ["sub/b.png"]
    assert len(failed) == 1
    assert failed[0]["path"] == "a.jpg"
    assert failed[0]["root"] == "files"
    assert "404" in failed[0]["error"]


def test_walk_files_is_recursive_and_skips_recycle():
    files = FakeFiles(TREE)
    walked = kb._walk_files(files, "files", "", recursive=True)
    assert {item["path"] for item in walked} == {"a.jpg", "sub/b.png", "sub/c.txt"}


def test_extract_asset_references_keeps_spaced_paths():
    body = "# 轮椅租赁价格表\n\n> 原件：2   轮椅租赁图文/14 昆山成片/70已用/2.png（1.2 MB）\n"
    assert kb.extract_asset_references(body) == ["2   轮椅租赁图文/14 昆山成片/70已用/2.png"]


def test_index_files_maps_normalised_basenames():
    index = kb._index_files(FakeFiles(TREE), "files")
    assert index["a"] == ["a.jpg"]
    assert index["b"] == ["sub/b.png"]


def test_resolve_by_name_accepts_a_unique_match():
    index = {"轮椅租赁价格表": ["2   轮椅租赁图文/轮椅租赁价格表.png"]}
    match, status = kb._resolve_by_name(index, "8 本地推素材/轮椅租赁价格表.png")
    assert status == "unique"
    assert match == "2   轮椅租赁图文/轮椅租赁价格表.png"


def test_resolve_by_name_prefers_the_overlapping_directory():
    index = {
        "2": [
            "3  品牌号小红书/15   朋友2/2.png",
            "2   轮椅租赁图文/14 昆山成片/2.png",
        ]
    }
    match, status = kb._resolve_by_name(index, "2   轮椅租赁图文/70已用/2.png")
    assert status == "unique"
    assert match == "2   轮椅租赁图文/14 昆山成片/2.png"


def test_resolve_by_name_refuses_to_guess_between_ties():
    index = {"1": ["2   轮椅租赁图文/13-小福车/24已用/1.png", "7 详情页/2 A3/主图/1.jpg"]}
    match, status = kb._resolve_by_name(index, "8 本地推素材/闲鱼文案/1")
    assert status == "ambiguous"
    assert match is None


def test_resolve_by_name_reports_a_missing_name():
    match, status = kb._resolve_by_name({}, "8 本地推素材/没有这个文件.png")
    assert status == "missing"
    assert match is None


def test_find_selects_shortest_paths_first_and_downloads_them(tmp_path):
    tree = {
        "": [
            {"name": "昆山轮椅.jpg", "is_dir": False, "size": 10},
            {"name": "deep", "is_dir": True, "size": 0},
        ],
        "deep": [
            {"name": "nested", "is_dir": True, "size": 0},
            {"name": "昆山轮椅副本.jpg", "is_dir": False, "size": 20},
        ],
        "deep/nested": [{"name": "昆山轮椅再一份.jpg", "is_dir": False, "size": 30}],
    }
    files = FakeFiles(tree)
    manifest = tmp_path / "find.json"
    args = argparse.Namespace(
        keyword=["昆山"],
        root="files",
        path="",
        ext="jpg",
        limit=2,
        out=str(tmp_path / "assets"),
        manifest=str(manifest),
        json=None,
    )
    assert kb.cmd_find(args, mcp=None, files=files) == 0
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["match_count"] == 3
    assert [item["path"] for item in payload["matches"]] == [
        "昆山轮椅.jpg",
        "deep/昆山轮椅副本.jpg",
    ]
    assert files.downloaded == ["昆山轮椅.jpg", "deep/昆山轮椅副本.jpg"]


def test_find_requires_a_keyword(tmp_path):
    args = argparse.Namespace(
        keyword=["   "],
        root="files",
        path="",
        ext=None,
        limit=5,
        out=None,
        manifest=None,
        json=None,
    )
    with pytest.raises(kb.KbUsageError):
        kb.cmd_find(args, mcp=None, files=FakeFiles({}))


def test_safe_join_refuses_an_escaping_path(tmp_path):
    with pytest.raises(kb.KbUsageError):
        kb._safe_join(tmp_path, "../outside.png")
    assert kb._safe_join(tmp_path, "inside.png") == (tmp_path / "inside.png").resolve()

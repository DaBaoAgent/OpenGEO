#!/usr/bin/env python3
"""Company knowledge base (company_kb) client for claude-blog.

Grounds blog work in the company knowledge base: evidence text over MCP and
binary assets (product photos, install diagrams, packaging shots) over the
companion HTTP file service. Both transports speak the same bearer token.

Transports
----------
MCP over HTTP (default http://192.168.1.79:8788/mcp)
    JSON-RPC 2.0 over the Streamable HTTP transport; one session per run.
    Read tools: kb_search, kb_read, kb_list, kb_stat.
    Write tool: kb_write, reachable only via `write` with --allow-write.

File service (default http://192.168.1.79:8789/api)
    GET  /api/list?root=&path=   directory listing
    GET  /api/get?root=&path=    download one file
    POST /api/put?root=&path=    upload one file
    root is one of files, archive, inbox.

Token resolution, first hit wins
--------------------------------
1. --token
2. COMPANY_KB_TOKEN
3. Windows user environment (HKCU\\Environment), so a shell that has not
   inherited the variable still works.
The token is never written to disk, never echoed, and never logged.

Commands
--------
    probe                       connect, report server identity and tools
    stat                        corpus size and health
    list [--kb KB]              document inventory
    search QUERY [--kb KB]      ranked retrieval, doc_id + excerpt
    read DOC_ID [--full]        full text, paged or complete
    files [--root R] [--path P] file-service directory listing
    get --path P --out F        download one asset
    put --local F --path P      upload one asset
    find --keyword K ...        keyword search over the file tree, optional download
    pull ...                    bulk-download assets, write a manifest
    ground ...                  one-shot evidence bundle for a blog draft

Exit codes: 0 success, 1 usage/config error, 2 remote or transport error.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_MCP_URL = os.environ.get("COMPANY_KB_MCP_URL") or "http://192.168.1.79:8788/mcp"
DEFAULT_FILES_URL = os.environ.get("COMPANY_KB_FILES_URL") or "http://192.168.1.79:8789/api"
TOKEN_ENV = "COMPANY_KB_TOKEN"
PROTOCOL_VERSION = "2025-06-18"

MAX_ASSET_BYTES = 200 * 1024 * 1024
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 1.5
IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
)
VALID_ROOTS = frozenset({"files", "archive", "inbox"})


class KbError(RuntimeError):
    """Remote or transport failure talking to the knowledge base."""


class KbUsageError(RuntimeError):
    """Local usage or configuration failure."""


def resolve_token(explicit: Optional[str] = None) -> str:
    """Return the KB bearer token without ever writing or printing it."""
    if explicit:
        return explicit.strip()
    from_env = os.environ.get(TOKEN_ENV)
    if from_env and from_env.strip():
        return from_env.strip()
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, TOKEN_ENV)
            if value and str(value).strip():
                return str(value).strip()
        except OSError:
            pass
    raise KbUsageError(
        f"{TOKEN_ENV} is not set. Export it in the shell, or set it as a Windows "
        "user environment variable. The token is a credential: never commit it."
    )


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Opener with proxying disabled; the knowledge base lives on the LAN."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


_OPENER = _no_proxy_opener()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retry(description: str, operation, attempts: int = FETCH_ATTEMPTS):
    """Run operation, retrying dropped connections and timeouts.

    http.client.RemoteDisconnected is not wrapped in URLError, so the terminal
    catch here is OSError: it covers URLError, socket timeouts, and an abruptly
    closed connection alike. An HTTP status is a decision, not a blip, and is
    never retried.
    """
    last: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except urllib.error.HTTPError:
            raise
        except OSError as exc:
            last = exc
            if attempt < attempts:
                time.sleep(FETCH_BACKOFF_SECONDS * attempt)
    raise KbError(f"{description}: {last}")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _parse_messages(body: bytes, content_type: str) -> list[dict]:
    """Return JSON-RPC messages from either a JSON body or an SSE stream."""
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" not in (content_type or ""):
        if not text.strip():
            return []
        payload = json.loads(text)
        return payload if isinstance(payload, list) else [payload]
    messages: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[len("data:") :].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            messages.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return messages


@dataclass
class KbMcp:
    """Minimal MCP Streamable HTTP client scoped to the company knowledge base."""

    url: str = DEFAULT_MCP_URL
    token: str = ""
    timeout: float = 45.0
    _session_id: Optional[str] = field(default=None, init=False, repr=False)
    _next_id: int = field(default=0, init=False, repr=False)
    _connected: bool = field(default=False, init=False, repr=False)
    server_info: dict = field(default_factory=dict, init=False, repr=False)

    def _post(self, payload: dict, expect_response: bool = True) -> list[dict]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "claude-blog-kb/1.0",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        def fetch() -> tuple[bytes, str, Optional[str]]:
            with _OPENER.open(request, timeout=self.timeout) as response:
                return (
                    response.read(),
                    response.headers.get("Content-Type", ""),
                    response.headers.get("mcp-session-id"),
                )

        try:
            body, content_type, session = _retry(
                f"MCP {payload.get('method', 'request')}", fetch
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            hint = " (check that the bearer token is current)" if exc.code in (401, 403) else ""
            raise KbError(f"MCP HTTP {exc.code}{hint}: {detail}") from exc
        except KbError as exc:
            raise KbError(
                f"cannot reach {self.url}: {exc}. On the LAN confirm NO_PROXY "
                "covers the host; off-site use the Tailscale address."
            ) from exc
        if session:
            self._session_id = session
        if not expect_response:
            return []
        return _parse_messages(body, content_type)

    def _rpc(self, method: str, params: dict) -> Any:
        self._next_id += 1
        request_id = self._next_id
        for message in self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ):
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise KbError(f"{method} failed: {error.get('message', error)}")
            return message.get("result")
        raise KbError(f"{method}: no response returned")

    def connect(self) -> dict:
        if self._connected:
            return self.server_info
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "claude-blog", "version": _project_version()},
            },
        )
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        self._connected = True
        return self.server_info

    def list_tools(self) -> list[dict]:
        self.connect()
        result = self._rpc("tools/list", {})
        return result.get("tools", []) if isinstance(result, dict) else []

    def call(self, name: str, arguments: dict) -> Any:
        """Call one tool; return its text payload, JSON-decoded when possible."""
        self.connect()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            return result
        text = "\n".join(
            item.get("text", "")
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if result.get("isError"):
            raise KbError(f"{name}: {text.strip()[:400]}")
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return text


@dataclass
class KbFiles:
    """Client for the companion HTTP file service (images and originals)."""

    base: str = DEFAULT_FILES_URL
    token: str = ""
    timeout: float = 120.0

    def _url(self, endpoint: str, root: str, path: str) -> str:
        if root not in VALID_ROOTS:
            raise KbUsageError(f"root must be one of {sorted(VALID_ROOTS)}, got {root!r}")
        query = urllib.parse.urlencode({"root": root, "path": path})
        return f"{self.base.rstrip('/')}/{endpoint}?{query}"

    def _request(self, request: urllib.request.Request) -> bytes:
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", "claude-blog-kb/1.0")

        def fetch() -> bytes:
            with _OPENER.open(request, timeout=self.timeout) as response:
                return response.read()

        try:
            return _retry(request.full_url, fetch)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            hint = " (check the bearer token)" if exc.code in (401, 403) else ""
            raise KbError(f"file service HTTP {exc.code}{hint}: {detail}") from exc

    def list_dir(self, root: str, path: str = "") -> dict:
        return json.loads(self._request(urllib.request.Request(self._url("list", root, path))))

    def download(self, root: str, path: str, out: Path) -> dict:
        url = self._url("get", root, path)
        token = self.token

        def fetch() -> bytes:
            request = urllib.request.Request(url)
            request.add_header("Authorization", f"Bearer {token}")
            request.add_header("User-Agent", "claude-blog-kb/1.0")
            with _OPENER.open(request, timeout=self.timeout) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > MAX_ASSET_BYTES:
                    raise KbError(
                        f"{path}: {declared} bytes exceeds the {MAX_ASSET_BYTES} byte cap"
                    )
                return response.read()

        try:
            data = _retry(f"download {path}", fetch)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise KbError(f"download {path}: HTTP {exc.code} {detail}") from exc
        if len(data) > MAX_ASSET_BYTES:
            raise KbError(f"{path}: response exceeds the {MAX_ASSET_BYTES} byte cap")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"bytes": len(data), "sha256": _sha256(data)}

    def upload(self, root: str, path: str, local: Path) -> bytes:
        payload = local.read_bytes()
        request = urllib.request.Request(self._url("put", root, path), data=payload, method="POST")
        request.add_header("Content-Type", "application/octet-stream")
        return self._request(request)


def _project_version() -> str:
    try:
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    except OSError:
        return "0.0.0"
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def _hit_documents(result: Any) -> list[dict]:
    """Normalize a kb_search payload into a list of hit dicts."""
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ("hits", "results", "documents", "matches", "items"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _kb_list(value: Optional[str]) -> Optional[list[str]]:
    """kb_search takes an array of library names; accept a comma-separated string."""
    if not value:
        return None
    names = [part.strip() for part in value.split(",") if part.strip()]
    return names or None


def _hit_doc_id(hit: dict) -> Optional[str]:
    for key in ("doc_id", "id", "docId", "document_id"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


_IMAGE_SUFFIX = r"\.(?:jpg|jpeg|png|webp|gif|bmp|tif|tiff|avif)"
# The knowledge base names its source file on an `原件：` line, and those paths
# contain spaces ("2   轮椅租赁图文/14 昆山成片/5.jpg"). Whitespace tokenising
# truncates them, so match the whole line up to the size annotation instead.
_ORIGINAL_LINE_RE = re.compile(r"原件[：:]\s*(.+?)(?:\s*[（(].*)?$", re.MULTILINE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_HTML_SRC_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)")
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]+?" + _IMAGE_SUFFIX + r")`", re.IGNORECASE)
_BARE_PATH_RE = re.compile(r"([^\s\"'`()|]+?" + _IMAGE_SUFFIX + r")", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _normalise_asset(candidate: str) -> Optional[str]:
    """Return a file-service relative path, or None when it is not one."""
    candidate = candidate.strip().strip("\"'").strip()
    # `![alt](path "title")` carries an optional title after the path.
    if ' "' in candidate:
        candidate = candidate.split(' "', 1)[0]
    if candidate.lower().startswith(("http://", "https://", "file://")):
        candidate = urllib.parse.unquote(urllib.parse.urlparse(candidate).path)
    candidate = candidate.replace("\\", "/").lstrip("/")
    if not candidate or _ABS_PATH_RE.match(candidate) or candidate.startswith("../"):
        return None
    if "/./" in candidate:
        candidate = candidate.replace("/./", "/")
    return candidate


def extract_asset_references(text: str) -> list[str]:
    """Pull candidate asset paths out of a knowledge-base document.

    Explicit references are tried first: the `原件：` source line, markdown
    images, HTML `img src`, and backticked paths. A whitespace-tokenised
    fallback only runs when none of those matched, because on its own it
    truncates spaced paths and floods the queue with bare filenames.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        candidate = _normalise_asset(raw)
        if candidate and candidate not in seen:
            seen.add(candidate)
            found.append(candidate)

    for pattern in (_ORIGINAL_LINE_RE, _MD_IMAGE_RE, _HTML_SRC_RE, _BACKTICK_PATH_RE):
        for match in pattern.finditer(text):
            add(match.group(1))
    if not found:
        for match in _BARE_PATH_RE.finditer(text):
            add(match.group(1))
    return found


def _safe_join(base: Path, relative: str) -> Path:
    """Join under base, refusing anything that escapes it."""
    target = (base / relative.lstrip("/")).resolve()
    root = base.resolve()
    if target != root and root not in target.parents:
        raise KbUsageError(f"refusing path outside the output directory: {relative!r}")
    return target


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _walk_files(
    client: KbFiles, root: str, path: str, recursive: bool, depth: int = 0
) -> list[dict]:
    """Depth-first listing; returns flat file records."""
    listing = client.list_dir(root, path)
    records: list[dict] = []
    for entry in listing.get("entries", []):
        name = entry.get("name")
        if not name or name.startswith("#"):
            continue
        entry_path = f"{path.rstrip('/')}/{name}" if path else name
        if entry.get("is_dir"):
            if recursive and depth < 6:
                records.extend(_walk_files(client, root, entry_path, recursive, depth + 1))
            continue
        records.append({"root": root, "path": entry_path, "size": entry.get("size", 0)})
    return records


def _download_assets(
    files: KbFiles,
    records: Iterable[dict],
    out_dir: Path,
    limit: Optional[int] = None,
    allowed_ext: Optional[set[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """Download each record; return (downloaded, failed).

    Failures are returned rather than dropped, so a caller can report a
    reference it could not fetch instead of silently shipping fewer assets.
    """
    downloaded: list[dict] = []
    failed: list[dict] = []
    for record in records:
        if limit is not None and len(downloaded) >= limit:
            break
        source_path = record["path"]
        if allowed_ext and Path(source_path).suffix.lower() not in allowed_ext:
            continue
        destination = _safe_join(out_dir, source_path)
        try:
            info = files.download(record["root"], source_path, destination)
        except KbError as exc:
            failed.append({"root": record["root"], "path": source_path, "error": str(exc)})
            print(f"  skip {source_path}: {exc}", file=sys.stderr)
            continue
        downloaded.append(
            {
                "root": record["root"],
                "source_path": source_path,
                "local_path": str(destination),
                "bytes": info["bytes"],
                "sha256": info["sha256"],
            }
        )
    return downloaded, failed


_PATH_NOISE_RE = re.compile(r"[\s_\-()\[\]]+")
_SUFFIX_NOISE_RE = re.compile(r"[\s_\-().\[\]]+")


def _normalise_component(name: str) -> str:
    """Case- and decoration-insensitive key for one path component."""
    return _PATH_NOISE_RE.sub("", name).casefold()


def _normalise_name(name: str) -> str:
    """Basename key for asset resolution: no directory, no extension, no noise."""
    return _SUFFIX_NOISE_RE.sub("", Path(name).stem).casefold()


def _ext_filter(value: Optional[str]) -> Optional[set[str]]:
    """Normalise a comma-separated extension list to dotted, lower-case suffixes.

    `Path(...).suffix` includes the dot, so "jpg" has to become ".jpg" or every
    file is filtered out.
    """
    if not value:
        return None
    found = set()
    for part in value.split(","):
        part = part.strip().lower()
        if part:
            found.add(part if part.startswith(".") else f".{part}")
    return found or None


def _index_files(client: KbFiles, root: str, path: str = "") -> dict[str, list[str]]:
    """Map a normalised basename to every file-service path that shares it.

    A knowledge-base document records an `原件:` source path, but those paths
    come from several trees (the sync folder, the media library, the file
    service root). Only the file-service tree is downloadable, so a reference
    that does not exist verbatim is re-resolved by name here.
    """
    index: dict[str, list[str]] = {}
    for record in _walk_files(client, root, path, recursive=True):
        index.setdefault(_normalise_name(Path(record["path"]).name), []).append(record["path"])
    return index


def _resolve_by_name(index: dict[str, list[str]], reference: str) -> tuple[Optional[str], str]:
    """Resolve one failed reference against a basename index.

    Returns (relative path, status). Status is `unique` when exactly one
    candidate matches or when directory overlap breaks the tie, `ambiguous`
    when the name is shared and nothing breaks the tie, and `missing` when
    nothing matches. Ambiguity is reported, never guessed: pulling the wrong
    product photo is worse than pulling none.
    """
    candidates = index.get(_normalise_name(Path(reference).name), [])
    if not candidates:
        return None, "missing"
    if len(candidates) == 1:
        return candidates[0], "unique"
    reference_dirs = {
        _normalise_component(part)
        for part in Path(reference).parent.parts
        if part and part != "."
    }

    def overlap(candidate: str) -> int:
        parts = {
            _normalise_component(part)
            for part in Path(candidate).parent.parts
            if part and part != "."
        }
        return len(reference_dirs & parts)

    best = max(candidates, key=overlap)
    best_score = overlap(best)
    if all(overlap(other) < best_score for other in candidates if other != best):
        return best, "unique"
    return None, "ambiguous"


def cmd_probe(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    info = mcp.connect()
    tools = mcp.list_tools()
    payload = {
        "mcp_url": mcp.url,
        "files_url": files.base,
        "server": info,
        "tools": [
            {
                "name": tool.get("name"),
                "parameters": sorted((tool.get("inputSchema") or {}).get("properties", {})),
            }
            for tool in tools
        ],
    }
    if args.json:
        _write_json(Path(args.json), payload)
    print(f"server : {info.get('title') or info.get('name')} v{info.get('version')}")
    print(f"mcp    : {mcp.url}")
    print(f"files  : {files.base}")
    for tool in payload["tools"]:
        print(f"  {tool['name']}({', '.join(tool['parameters'])})")
    return 0


def cmd_stat(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    result = mcp.call("kb_stat", {})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    arguments: dict = {"limit": args.limit}
    if args.kb:
        arguments["kb"] = args.kb
    if args.prefix:
        arguments["prefix"] = args.prefix
    result = mcp.call("kb_list", arguments)
    if args.json:
        _write_json(Path(args.json), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    arguments: dict = {"query": args.query}
    if args.kb:
        arguments["kb"] = _kb_list(args.kb)
    if args.top_k:
        arguments["top_k"] = args.top_k
    if args.mode:
        arguments["mode"] = args.mode
    result = mcp.call("kb_search", arguments)
    if args.json:
        _write_json(Path(args.json), result)
    hits = _hit_documents(result)
    if not hits:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    for index, hit in enumerate(hits, 1):
        doc_id = _hit_doc_id(hit) or "?"
        title = hit.get("title") or hit.get("heading") or ""
        snippet = (hit.get("snippet") or hit.get("text") or "").replace("\n", " ")
        score = hit.get("score") if hit.get("score") is not None else hit.get("rank")
        score_text = f" score={score}" if score is not None else ""
        print(f"{index:>2}. [{doc_id}] {title}{score_text}")
        if snippet:
            print(f"    {snippet[:220]}")
    return 0


def cmd_read(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    chunks: list[str] = []
    offset = 0
    while True:
        result = mcp.call(
            "kb_read", {"doc_id": args.doc_id, "offset": offset, "max_chars": args.max_chars}
        )
        if isinstance(result, dict):
            text = result.get("text") or result.get("content") or ""
            total = result.get("total_chars") or result.get("total") or 0
        else:
            text, total = str(result), 0
        if not text:
            break
        chunks.append(text)
        if not args.full or (total and sum(len(c) for c in chunks) >= total):
            break
        offset += len(text)
    body = "".join(chunks)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
        print(f"wrote {len(body)} chars to {out_path}")
    else:
        print(body)
    return 0


def cmd_files(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    result = files.list_dir(args.root, args.path)
    if args.json:
        _write_json(Path(args.json), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_get(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    info = files.download(args.root, args.path, Path(args.out))
    print(f"{args.out}  {info['bytes']} bytes  sha256={info['sha256'][:16]}")
    return 0


def cmd_put(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    local = Path(args.local)
    if not local.is_file():
        raise KbUsageError(f"no such file: {local}")
    response = files.upload(args.root, args.path, local)
    print(response.decode("utf-8", errors="replace") if response else "uploaded")
    return 0


def cmd_pull(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    out_dir = Path(args.out)
    allowed_ext = _ext_filter(args.ext)
    provenance: dict = {"generated_at": _utc_now(), "files_url": files.base}

    if args.doc_id:
        text = mcp.call("kb_read", {"doc_id": args.doc_id, "offset": 0, "max_chars": 100000})
        body = text.get("text", "") if isinstance(text, dict) else str(text)
        references = extract_asset_references(body)
        provenance["doc_id"] = args.doc_id
        provenance["references"] = references
        print(f"doc {args.doc_id}: {len(references)} asset reference(s)")
        records = [{"root": args.root, "path": ref, "size": 0} for ref in references]
    else:
        if not args.path:
            raise KbUsageError("pull needs --doc-id or --path")
        records = _walk_files(files, args.root, args.path, args.recursive)
        provenance["root"] = args.root
        provenance["path"] = args.path
        print(f"{args.path or '/'}: {len(records)} file(s) listed")

    downloaded, failed = _download_assets(files, records, out_dir, args.limit, allowed_ext)
    provenance["assets"] = downloaded
    provenance["unresolved"] = failed
    if args.manifest:
        _write_json(Path(args.manifest), provenance)
    for asset in downloaded:
        print(f"  {asset['source_path']}  {asset['bytes']} bytes")
    print(f"downloaded {len(downloaded)} asset(s) to {out_dir}")
    if failed:
        print(f"unresolved {len(failed)} reference(s); see the manifest for reasons")
    return 0


def cmd_ground(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    """Search, read each hit in full, and emit one citable evidence bundle."""
    out_dir = Path(args.out)
    docs_dir = out_dir / "documents"
    evidence: dict = {
        "generated_at": _utc_now(),
        "source": {"mcp_url": mcp.url, "files_url": files.base},
        "queries": [],
        "documents": [],
        "assets": [],
        "unresolved_assets": [],
    }
    seen: dict[str, dict] = {}

    for query in args.query:
        arguments: dict = {"query": query}
        if args.kb:
            arguments["kb"] = _kb_list(args.kb)
        if args.top_k:
            arguments["top_k"] = args.top_k
        result = mcp.call("kb_search", arguments)
        hits = _hit_documents(result)
        evidence["queries"].append({"query": query, "kb": args.kb or "all", "hit_count": len(hits)})
        print(f"query {query!r}: {len(hits)} hit(s)")
        for hit in hits[: args.top_k or 5]:
            doc_id = _hit_doc_id(hit)
            if not doc_id or doc_id in seen:
                continue
            read = mcp.call("kb_read", {"doc_id": doc_id, "offset": 0, "max_chars": 200000})
            if isinstance(read, dict):
                body = read.get("text") or read.get("content") or ""
                title = read.get("title") or hit.get("title") or doc_id
                version = read.get("version")
                kb_name = read.get("kb")
            else:
                body, title, version, kb_name = str(read), hit.get("title") or doc_id, None, None
            if not body.strip():
                continue
            # Keep CJK: it is valid on every supported filesystem and it is the
            # readable part of a knowledge-base doc_id. The substitution stays
            # out of the f-string: a backslash inside one is a syntax error
            # before 3.12, and this file has to import on 3.11.
            safe_doc_id = re.sub(r"[^\w.-]+", "_", doc_id)
            doc_file = docs_dir / f"{safe_doc_id}.md"
            doc_file.parent.mkdir(parents=True, exist_ok=True)
            doc_file.write_text(body, encoding="utf-8")
            record = {
                "doc_id": doc_id,
                "title": title,
                "kb": kb_name or args.kb,
                "version": version,
                "chars": len(body),
                "sha256": _sha256(body.encode("utf-8")),
                "local_path": str(doc_file),
                "matched_queries": [query],
                "assets": extract_asset_references(body),
            }
            seen[doc_id] = record
            print(f"  + [{doc_id}] {title} ({len(body)} chars)")

    evidence["documents"] = list(seen.values())

    if args.pull_assets:
        seen_paths: set[str] = set()
        unique: list[dict] = []
        for record in evidence["documents"]:
            for reference in record["assets"]:
                if reference in seen_paths:
                    continue
                seen_paths.add(reference)
                unique.append({"root": args.root, "path": reference, "size": 0})
        downloaded, failed = _download_assets(
            files, unique, out_dir / "assets", args.asset_limit, IMAGE_EXTENSIONS
        )
        resolved: list[dict] = []
        unresolved: list[dict] = []
        if failed and args.resolve_assets:
            index = _index_files(files, args.root)
            known = {asset["source_path"] for asset in downloaded}
            for record in failed:
                match, status = _resolve_by_name(index, record["path"])
                if match is None or match in known:
                    unresolved.append({**record, "resolution": status})
                    continue
                destination = _safe_join(out_dir / "assets", match)
                try:
                    info = files.download(record["root"], match, destination)
                except KbError as exc:
                    unresolved.append(
                        {
                            "root": record["root"],
                            "path": match,
                            "reference": record["path"],
                            "error": str(exc),
                            "resolution": "error",
                        }
                    )
                    continue
                known.add(match)
                resolved.append(
                    {
                        "root": record["root"],
                        "source_path": match,
                        "reference": record["path"],
                        "local_path": str(destination),
                        "bytes": info["bytes"],
                        "sha256": info["sha256"],
                        "resolved_by": "normalised-name",
                    }
                )
        else:
            unresolved = [{**record, "resolution": "not-attempted"} for record in failed]
        evidence["assets"] = downloaded + resolved
        evidence["unresolved_assets"] = unresolved
        print(
            f"pulled {len(downloaded)} asset(s) verbatim, {len(resolved)} by name, "
            f"{len(unresolved)} unresolved"
        )

    evidence_path = out_dir / "kb-evidence.json"
    _write_json(evidence_path, evidence)
    print(f"\nevidence bundle: {evidence_path}")
    print(f"  documents: {len(evidence['documents'])}, assets: {len(evidence['assets'])}")
    return 0


def cmd_find(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    """Keyword search over the file-service tree, with an optional download.

    The knowledge base exposes no image search tool, so this is how a draft
    locates the product photos it needs: every keyword must appear in the
    relative path, and the shortest matching paths sort first, because those
    are the curated assets rather than per-post copies.
    """
    keywords = [word.strip().casefold() for word in args.keyword if word.strip()]
    if not keywords:
        raise KbUsageError("find needs at least one --keyword")
    allowed_ext = _ext_filter(args.ext)
    records = _walk_files(files, args.root, args.path or "", recursive=True)
    matches: list[dict] = []
    for record in records:
        haystack = record["path"].casefold()
        if not all(word in haystack for word in keywords):
            continue
        if allowed_ext and Path(record["path"]).suffix.lower() not in allowed_ext:
            continue
        matches.append(record)
    matches.sort(key=lambda record: (len(record["path"]), record["path"]))
    total = len(matches)
    selected = matches[: args.limit] if args.limit and args.limit > 0 else matches
    payload: dict = {
        "generated_at": _utc_now(),
        "root": args.root,
        "path": args.path or "/",
        "keywords": keywords,
        "scanned": len(records),
        "match_count": total,
        "matches": selected,
    }
    if args.out:
        downloaded, failed = _download_assets(files, selected, Path(args.out))
        payload["assets"] = downloaded
        payload["unresolved"] = failed
    for record in selected:
        print(f"  {record['path']}  {record.get('size', 0)} bytes")
    print(f"{total} match(es); {len(selected)} selected")
    manifest = args.manifest or args.json
    if manifest:
        _write_json(Path(manifest), payload)
    return 0


def cmd_write(args: argparse.Namespace, mcp: KbMcp, files: KbFiles) -> int:
    if not args.allow_write:
        raise KbUsageError("write is gated: pass --allow-write to commit to the knowledge base")
    body = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    if not body:
        raise KbUsageError("write needs --file or --text")
    arguments: dict = {"kb": args.kb, "title": args.title, "content": body}
    if args.tags:
        arguments["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.doc_id:
        arguments["doc_id"] = args.doc_id
    if args.expected_version is not None:
        arguments["expected_version"] = args.expected_version
    result = mcp.call("kb_write", arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb_client.py",
        description="Company knowledge base client (text over MCP, assets over HTTP).",
    )
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--files-url", default=DEFAULT_FILES_URL)
    parser.add_argument("--token", default=None, help="prefer COMPANY_KB_TOKEN instead")
    parser.add_argument("--json", default=None, help="also write the raw result to this path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("probe", help="connect and list available tools")
    sub.add_parser("stat", help="corpus size and health")

    p_list = sub.add_parser("list", help="document inventory")
    p_list.add_argument("--kb", default=None)
    p_list.add_argument("--prefix", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_search = sub.add_parser("search", help="ranked retrieval")
    p_search.add_argument("query")
    p_search.add_argument("--kb", default=None, help="comma-separated library names")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--mode", default=None, help="retrieval mode (default: keyword)")

    p_read = sub.add_parser("read", help="read one document")
    p_read.add_argument("doc_id")
    p_read.add_argument("--full", action="store_true", help="page until the document ends")
    p_read.add_argument("--max-chars", type=int, default=6000)
    p_read.add_argument("--out", default=None)

    p_files = sub.add_parser("files", help="file-service listing")
    p_files.add_argument("--root", default="files")
    p_files.add_argument("--path", default="")

    p_get = sub.add_parser("get", help="download one asset")
    p_get.add_argument("--root", default="files")
    p_get.add_argument("--path", required=True)
    p_get.add_argument("--out", required=True)

    p_put = sub.add_parser("put", help="upload one asset")
    p_put.add_argument("--root", default="inbox")
    p_put.add_argument("--path", required=True)
    p_put.add_argument("--local", required=True)

    p_pull = sub.add_parser("pull", help="bulk-download assets")
    p_pull.add_argument("--doc-id", default=None, help="pull assets referenced by this document")
    p_pull.add_argument("--path", default=None, help="or pull everything under this folder")
    p_pull.add_argument("--root", default="files")
    p_pull.add_argument("--out", required=True)
    p_pull.add_argument("--manifest", default=None)
    p_pull.add_argument("--recursive", action="store_true")
    p_pull.add_argument("--ext", default=None, help="comma-separated extension filter")
    p_pull.add_argument("--limit", type=int, default=None)

    p_find = sub.add_parser("find", help="keyword search over the file tree")
    p_find.add_argument("--keyword", action="append", required=True, help="repeatable; all must match")
    p_find.add_argument("--root", default="files")
    p_find.add_argument("--path", default="")
    p_find.add_argument("--ext", default=None, help="comma-separated extensions, e.g. jpg,png,webp")
    p_find.add_argument("--limit", type=int, default=12, help="0 or negative means every match")
    p_find.add_argument("--out", default=None, help="download the selected matches here")
    p_find.add_argument("--manifest", default=None)

    p_ground = sub.add_parser("ground", help="evidence bundle for a draft")
    p_ground.add_argument("--query", action="append", required=True)
    p_ground.add_argument("--kb", default=None, help="comma-separated library names")
    p_ground.add_argument("--top-k", type=int, default=5)
    p_ground.add_argument("--out", required=True)
    p_ground.add_argument("--root", default="files")
    p_ground.add_argument("--pull-assets", action="store_true")
    p_ground.add_argument("--asset-limit", type=int, default=24)
    p_ground.add_argument(
        "--resolve-assets",
        action="store_true",
        help="re-resolve unfetchable references by name across the file tree",
    )

    p_write = sub.add_parser("write", help="write a document (requires --allow-write)")
    p_write.add_argument("--kb", required=True)
    p_write.add_argument("--title", required=True)
    p_write.add_argument("--file", default=None)
    p_write.add_argument("--text", default=None)
    p_write.add_argument("--doc-id", default=None)
    p_write.add_argument("--expected-version", default=None)
    p_write.add_argument("--tags", default=None, help="comma-separated tags")
    p_write.add_argument("--allow-write", action="store_true")

    return parser


COMMANDS = {
    "probe": cmd_probe,
    "stat": cmd_stat,
    "list": cmd_list,
    "search": cmd_search,
    "read": cmd_read,
    "files": cmd_files,
    "get": cmd_get,
    "put": cmd_put,
    "find": cmd_find,
    "pull": cmd_pull,
    "ground": cmd_ground,
    "write": cmd_write,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = resolve_token(args.token)
        return COMMANDS[args.command](
            args, KbMcp(url=args.mcp_url, token=token), KbFiles(base=args.files_url, token=token)
        )
    except KbUsageError as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return 1
    except KbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

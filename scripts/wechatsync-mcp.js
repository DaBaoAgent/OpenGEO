#!/usr/bin/env node
/**
 * Minimal stdio MCP server for the 文章同步助手 Chrome extension.
 *
 * The extension ships no MCP server: it opens a WebSocket bridge and waits for
 * a CLI or MCP server to listen on ws://localhost:9527 (or the address set in
 * its settings). `@wechatsync/cli` is that bridge. This wrapper exposes the CLI
 * as MCP tools so any MCP client can publish without shelling out by hand.
 *
 * Env:
 *   WECHATSYNC_TOKEN   token from the extension settings (required)
 *   WECHATSYNC_CLI     path to @wechatsync/cli/dist/index.js
 *   WECHATSYNC_TIMEOUT per-call timeout in ms (default 180000)
 *
 * No dependencies: newline-delimited JSON-RPC 2.0 over stdin/stdout.
 */
"use strict";

const { spawn } = require("child_process");
const readline = require("readline");

const CLI =
  process.env.WECHATSYNC_CLI ||
  "C:/Users/Administrator/.workbuddy-ai/binaries/node/workspace/node_modules/@wechatsync/cli/dist/index.js";
const TOKEN = process.env.WECHATSYNC_TOKEN || "";
const TIMEOUT_MS = Number(process.env.WECHATSYNC_TIMEOUT || 180000);

function runCli(args, timeoutMs = TIMEOUT_MS) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      env: { ...process.env, WECHATSYNC_TOKEN: TOKEN },
      windowsHide: true,
    });
    let out = "";
    let err = "";
    let done = false;
    const timer = setTimeout(() => {
      if (!done) {
        done = true;
        try { child.kill(); } catch (_) {}
        resolve({ code: -1, out, err: err + `\n[timeout after ${timeoutMs}ms]` });
      }
    }, timeoutMs);
    child.stdout.on("data", (d) => { out += d.toString(); });
    child.stderr.on("data", (d) => { err += d.toString(); });
    child.on("error", (e) => {
      if (!done) { done = true; clearTimeout(timer); resolve({ code: -1, out, err: err + String(e) }); }
    });
    child.on("close", (code) => {
      if (!done) { done = true; clearTimeout(timer); resolve({ code: code ?? -1, out, err }); }
    });
  });
}

const TOOLS = [
  {
    name: "list_platforms",
    description:
      "列出文章同步助手支持的平台及其代号（zhihu、juejin、csdn、weixin、xiaohongshu、toutiao 等 29 个）。发文章前先查代号。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "check_auth",
    description: "检查各平台登录状态，或指定单个平台。返回哪些平台已登录、哪些需要重新登录。",
    inputSchema: {
      type: "object",
      properties: { platform: { type: "string", description: "平台代号，留空则检查全部" } },
      additionalProperties: false,
    },
  },
  {
    name: "sync_article",
    description:
      "把一篇 Markdown 或 HTML 文件同步到指定平台。平台默认以草稿形式保存，需人工确认后发布。调用前请确认 Chrome 已打开且扩展处于「等待连接」状态。",
    inputSchema: {
      type: "object",
      properties: {
        file: { type: "string", description: "文章文件绝对路径（.md 或 .html）" },
        platforms: {
          type: "array",
          items: { type: "string" },
          description: "目标平台代号数组，例如 [\"zhihu\",\"juejin\"]",
        },
        title: { type: "string", description: "标题，留空则从文件里提取" },
        cover: { type: "string", description: "封面图 URL 或本地路径" },
        dry_run: { type: "boolean", description: "只演练不实际同步" },
      },
      required: ["file", "platforms"],
      additionalProperties: false,
    },
  },
  {
    name: "extract_page",
    description: "从 Chrome 当前打开的页面提取正文，返回 Markdown。用于把已发布的文章抓回来做二次分发。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
];

async function callTool(name, args) {
  if (!TOKEN) {
    return { isError: true, text: "缺少 WECHATSYNC_TOKEN，请在 MCP 配置的 env 里填写扩展设置页里的 Token。" };
  }
  if (name === "list_platforms") {
    return finish(await runCli(["platforms"]));
  }
  if (name === "check_auth") {
    const a = ["auth"];
    if (args && args.platform) a.push(String(args.platform));
    return finish(await runCli(a));
  }
  if (name === "extract_page") {
    return finish(await runCli(["extract"]));
  }
  if (name === "sync_article") {
    const file = args && args.file;
    if (!file) return { isError: true, text: "缺少 file 参数（文章文件绝对路径）。" };
    let plats = (args && args.platforms) || [];
    if (typeof plats === "string") plats = plats.split(",");
    if (!plats.length) return { isError: true, text: "缺少 platforms 参数。" };
    const a = ["sync", String(file), "-p", plats.join(",")];
    if (args.title) a.push("-t", String(args.title));
    if (args.cover) a.push("--cover", String(args.cover));
    if (args.dry_run) a.push("--dry-run");
    return finish(await runCli(a));
  }
  return { isError: true, text: `未知工具：${name}` };
}

function finish({ code, out, err }) {
  const text = [out.trim(), err.trim() && `[stderr]\n${err.trim()}`].filter(Boolean).join("\n\n");
  return { isError: code !== 0, text: text || `命令结束，退出码 ${code}，无输出。` };
}

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + "\n");
}

function reply(id, result) { send({ jsonrpc: "2.0", id, result }); }
function fail(id, code, message) { send({ jsonrpc: "2.0", id, error: { code, message } }); }

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  const raw = line.trim();
  if (!raw) return;
  let msg;
  try { msg = JSON.parse(raw); } catch (_) { return; }
  const { id, method, params } = msg;
  if (method === "initialize") {
    reply(id, {
      protocolVersion: (params && params.protocolVersion) || "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "wechatsync", version: "1.0.0" },
    });
    return;
  }
  if (method === "notifications/initialized" || method === "initialized") return;
  if (method === "ping") { reply(id, {}); return; }
  if (method === "tools/list") { reply(id, { tools: TOOLS }); return; }
  if (method === "tools/call") {
    const name = params && params.name;
    const args = (params && params.arguments) || {};
    try {
      const res = await callTool(name, args);
      reply(id, { content: [{ type: "text", text: res.text }], isError: !!res.isError });
    } catch (e) {
      reply(id, { content: [{ type: "text", text: String(e) }], isError: true });
    }
    return;
  }
  if (id !== undefined) fail(id, -32601, `method not found: ${method}`);
});

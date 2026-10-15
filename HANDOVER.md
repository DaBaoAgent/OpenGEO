# OpenGEO 交接文档

> 记录时间：2026-10-14（会话环境：ccb / claude-code-best，模型 deepseek-flash）
> 用途：明天恢复工作用。直接说「继续 OpenGEO」，让接手的会话先读本文件。

## 一句话现状

OpenGEO（= `AgriciDaniel/claude-blog` 本地克隆）已更新到最新 v2.3.1 并装入 ccb；
下一步等用户提供**公司知识库 MCP API**，接入后提取公司与产品信息，生成 BRAND.md/VOICE.md，开始写 GEO 文章。

## 一、已完成（2026-10-14）

1. **本地克隆已更新到最新**
   - `D:\@kaifa\OpenGEO` 是 github.com/AgriciDaniel/claude-blog 的 git 克隆（不是独立项目）。
   - 已 fast-forward 到 `c7708db`（v2.3.1 之后的 2026-10-14 依赖刷新），与 origin/main 同步，工作区干净。
   - 以后更新：`git pull`（在该目录）。

2. **claude-blog 已装入 ccb（可用）**
   - 方式：本地目录市场源 `D:\@kaifa\OpenGEO` → `ccb plugin install claude-blog@agricidaniel-blog`
   - 状态：v2.3.1，user 级，enabled（~/.claude/settings.json 已记录）。
   - **生效条件**：重启 ccb（或 /reload-plugins）后 `/blog` 系列命令可用。
   - 卸载：`ccb plugin uninstall claude-blog@agricidaniel-blog`
   - 克隆更新后插件内容自动同步（市场源=本地目录，无需重装）。

3. **Blog Studio（前端面板）结论：本机暂不可用，已搁置**
   - 它是官方 Claude Code 的 mod（需官方 CLI ≥2.1.287 的 mods 支持）；本机 Claude Code 是 ccb，无 mods 基础设施（已核实源码与构建产物）。
   - 即使换官方 Claude Code，上游公开 v2.3.1 还缺 `scripts/blog_studio_status.py`（发布缺口，configure/Run checks 必需；上游无相关 issue）。
   - 本机也未安装官方 Claude Code CLI/桌面版。此线暂缓，可随时重启（选项：向上游提 issue / 装官方 CC）。

## 二、环境验证结论（省得再查）

| 项目 | 结论 |
|---|---|
| ccb 对插件技能支持 | 支持 `${CLAUDE_SKILL_DIR}` 替换、user-invocable、插件 agents/命令加载 |
| Python | python3 / python = 3.12.9，技能脚本依赖满足 |
| 未装的可选依赖 | weasyprint、playwright（PDF 渲染与五道门浏览器检查用）；Google AI key（/blog image 配图用）——用到再装 |
| ccb 插件 CLI | `ccb plugin marketplace add/install/list/uninstall/validate` 可用 |
| 市场源清单 | claude-plugins-official（官方）+ agricidaniel-blog（本地 OpenGEO 目录） |

## 三、明天待办（优先级顺序）

1. **【等用户】用户提供公司知识库 MCP API**，任一种形式即可：
   - 远程：URL + 认证（token/header），注明 HTTP 或 SSE；
   - 本地：启动命令与参数；
   - 或现成配置片段/接入文档。
   - 接入方式：写入用户级 MCP 配置（跨项目可用）；密钥不进项目文件、不进版本库（用户惯例：临时中转、导入即删）。
   - 接入后：验证连接 → 列出工具/资源 → 清点公司、产品、素材数据。

2. **用知识库内容生成 BRAND.md / VOICE.md**（承接上一步）：
   - 放"文章工作区"项目根目录；缺什么再逐题补问（一次一个问题）。
   - 注意：BRAND.md 里不放机密/密钥（项目根可能进版本库）。

3. **定文章工作区目录**（尚未确定；候选 `D:\@kaifa\geo-content`，由用户拍板）。

4. **写第一篇 GEO 文章**，流程：
   `/blog brief <主题>` → `/blog outline <主题>` → `/blog write <主题>`（五道交付门：<90 分不放行、reviewer 复核）→ `/blog geo <文件>` 做 AI 引用审计。发布环节用户自己做。

## 四、GEO 写作要点备忘

- 公司/产品事实以知识库为唯一来源；写作时引用口径一致（企业 GEO 的实体一致性）。
- 中文投放场景（豆包/DeepSeek/Kimi）：工具结构可复用，受众与渠道口径写在品牌采集里，写作时做本地化适配。
- 用户不做发布/分发，只需要原创图文成品（md + HTML + PDF，可选配图）。

## 五、关键路径速查

```
克隆（=OpenGEO 本体）  D:\@kaifa\OpenGEO
交接文档（本文件）      D:\@kaifa\OpenGEO\HANDOVER.md
ccb 插件清单            ccb plugin list
ccb 设置                ~\.claude\settings.json（含 marketplaces 与 enabledPlugins）
本会话记忆参考          C:\Users\Administrator\.claude\projects\D---kaifa\memory\project_opengeo_tracking.md
```

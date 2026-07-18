# Claude Code + Codex 双平台 Skills 指南设计

## 背景

现有 `SKILLS-GUIDE.md` 以 Claude Code 为主要使用界面，20 个 Skill 的调用示例采用 `/skill-name`，权限章节也包含 Claude Code 专属配置。仓库已经提供对应的 Codex Skill 包、安装脚本和可选的自定义 Prompt 兼容层，但指南中的 Codex 入口不够完整。

## 目标

- 将现有指南升级为 Claude Code 与 Codex 共用的双平台指南。
- 保留 20 个 Skill 的业务流程、适用场景、产出和风险提示，避免复制两套正文。
- 让读者无需查阅其他文件即可完成平台选择、安装和首次调用。
- 明确平台专属能力，避免把 Claude Code 的权限配置或工具名称误用于 Codex。

## 文档结构

采用“共享主体 + 平台差异集中说明”：

1. 标题改为“Skills 使用指南（Claude Code + Codex）”。
2. 通用前提保持共享，并分别指向 Claude Code 的 `CLAUDE.md` 和 Codex 的 `AGENTS.md`。
3. 安装与调用按平台拆分：
   - Claude Code 使用 `scripts/install-claude-commands.*`，以 `/skill-name 参数` 调用。
   - Codex 使用 `scripts/install-codex-skills.*`，推荐以自然语言明确点名 Skill；`/prompts:skill-name 参数` 仅作为可选兼容入口，并标注 Custom Prompts 已弃用。
4. 权限与工具能力按平台拆分：
   - Claude Code 保留 WebSearch 权限与危险模式警告。
   - Codex 说明 Skill 的显式或语义触发方式，以及 Web、子 Agent 等能力取决于当前环境；不可用时必须显式报告降级。
5. 每个 Skill 只保留一套流程和说明，在调用示例中同时列出 Claude Code 与 Codex 写法。
6. 将 `Task`、`SendMessage`、`Explore agent` 等平台专属术语改为中性描述；无法中性化时添加平台标签。
7. 典型工作流使用平台中立的 Skill 名称，并在章节开头说明如何换算为各平台调用方式。

## 内容边界

本次只修改 `SKILLS-GUIDE.md` 和本设计说明，不修改：

- `skills/*.md` 的规范工作流；
- `codex-skills/*/SKILL.md` 和 `codex-prompts/*.md` 生成产物；
- 安装、同步或校验脚本；
- 研究报告、工具代码或项目规则。

不新增第三套调用协议，不改变任何 Skill 的业务含义，也不承诺当前 Codex 环境一定具备 Web 或多 Agent 能力。

## 验收标准

1. 指南准确列出全部 20 个 Skill，原有 Skill 无遗漏或重复。
2. Claude Code 与 Codex 的安装、推荐调用方式和可选兼容方式均有明确示例。
3. 每个 Skill 至少包含一个 Claude Code 示例和一个 Codex 示例。
4. Claude Code 专属权限、命令或工具名称均有平台标注，Codex 用户不会被引导修改 Claude Code 配置。
5. 指南引用的 `codex-skills/<name>/SKILL.md` 全部存在；如提及 Prompt 兼容层，对应 `codex-prompts/<name>.md` 全部存在。
6. `git diff --check`、Codex Skill 同步检查和 Prompt 同步检查通过。

## 风险与控制

- **文档膨胀**：不复制完整流程，只在安装、权限和调用示例处增加平台差异。
- **平台术语混用**：全文检索 Claude 专属术语并逐项标注或中性化。
- **生成产物漂移**：只读运行同步检查，不手工编辑生成目录。
- **官方能力变化**：将 Custom Prompts 标为可选兼容入口，Codex Skills 作为推荐入口，不硬编码未经项目脚本验证的全局安装路径。

## 验证方法

- 从 Markdown 标题提取 Skill 名称，与现有 20 项清单及 `codex-skills/` 目录交叉核对。
- 搜索 `/skill-name`、`Task`、`SendMessage`、`Explore agent`、`settings.local.json` 等词，确认上下文带有正确的平台限定。
- 运行 `python3 scripts/sync-codex-skills.py --check`。
- 运行 `python3 scripts/sync-codex-prompts.py --check`。
- 运行 `git diff --check` 并审阅最终差异，确认没有修改范围外文件。

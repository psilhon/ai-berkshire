# Changelog

本仓库遵循 [语义化版本](https://semver.org/lang/zh-CN/)（SemVer）。
所有发版记录以 git tag 为准，本文件为人工维护的变更摘要。

---

## [v3.4.13] — 2026-08-04

> 堵死「生成器自动自证」：证据账本不再能凭空签发成功证明。附带修正 v3.4.12 三处"修了但没修透"（单边占位、macOS 路径、守卫盲区）与一处 CHANGELOG 自相矛盾。

### 🐛 修复 (Fixed)

- **证据账本可被生成器自动自证（P0，最严重）**：`mk_result_bundle.py` 只接收 facts/sources 两类真实输入，却**自动补全** calculations、judgments、command_receipts、capabilities。实测：未执行任何命令的 `ashare-data` 单元被一口气签发 **51 条 `status: PASS`** 的「命令已成功执行」回执，且被 Gate 接受为 DONE——"未做调研"与"做了调研"在机器层面不可区分。修复（自证红线）：
  - 回执地板一律 `status: UNAVAILABLE` + `PLACEHOLDER` reason，**绝不代签 PASS**；
  - judgments / calculations 地板带 `PLACEHOLDER` 水印；capabilities 地板一律 `available: false`（未验证即不可用）；
  - 每一类都新增 `--extra-calculations/--extra-judgments/--extra-receipts/--extra-capabilities`，真实输入**独立**顶掉本类地板；
  - Gate `_precheck_placeholder_evidence` 由 2 类扩到 **5 类**（fact/source/calculation/judgment/receipt）硬拒收。
- **单边真实证据静默残留占位（P1）**：只传 `--extra-evidence` 或只传 `--extra-sources` 时，另一类退化为 `PLACEHOLDER` 地板并与真实证据混排，Gate 必然拒收整包，而生成器**仍返回 0**。修复：`fact.source_ids` 与 `source_records` 互为引用，二者必须同真同假——单边输入直接以退出码 2 显式失败。
- **退出码不再是准入信号（P1）**：全地板 bundle 此前也返回 0，等于给"未做调研"发成功信号。修复并固化不变量：**`0` ⟺ 零占位可提交**；`2` = 输入非法/单边证据；`3` = 账本仍是占位地板。`--allow-placeholder-floor` 仅降级退出码，绝不篡改内容（Gate 照拒）。
- **macOS 绝对路径误判（P1）**：`run_root` 已 `resolve()`（`/var` → `/private/var`），`report` 却未做同样解析，导致 `/var/...` 形式的合法路径在 `relative_to` 处被误报"不在 run_root 内"——macOS 临时目录下必现。修复：双端一致解析。
- **运行时模板与 canonical 文档矛盾（P1）**：`full_analysis_runtime.py` 的 `RESULT_BUNDLE_TEMPLATE` 仍提示 Agent"将以下 JSON 写入 result.json"，而 canonical skill 的 E16 明令禁止手写。修复：模板改为"结构参考（禁止手写）"，开头直接给出 `mk_result_bundle.py` 完整命令与退出码语义。
- **守卫盲区与空转（P1）**：
  - **目录级黑洞**：`docs/` 整体排除 → 新增 docs 文件永远逃逸扫描。改为**全仓 tracked 扫描 − file-level allowlist**（16 条逐条登记理由），新增文件默认先红；并加两条元守卫：豁免条目不得指向已删文件、不得豁免一个其实已合规的文件。
  - **解码静默**：`errors="replace"` 改 **strict**——二进制（含 NUL）跳过，其余解码失败作为独立故障上报，坏字节变响不变哑。
  - **行内完整命令漏检**：flag 提取器只认围栏代码块，写在行内代码 `` `python3 ... --x` `` 或裸文里的完整命令其 flag 全部漏检。新增两类提取路径，并对裸文按句读/管道截断，避免把同行 `git tag --list` 误挂到自有 CLI。
  - **"错误 CLI 注册"测试空转**：该测试原用 `--ghost-flag`（哪个 CLI 都没注册），与上一条测试完全同义，根本没验证"归属"。改用真实他属 flag `--extra-evidence`（实际注册在 `mk_result_bundle.py`），并加正例证明不是"一律判红"。
  - **Mysterious Name**：`build_minimum_evidence` → **`build_evidence_ledger`**（有真实输入时产出根本不是 minimum）。
- **CHANGELOG 自相矛盾**：v3.4.12 条目上一行称"补 `docs/` 扫描"、下一行称"故不扫 `docs/`"。已改写为准确表述并附 v3.4.13 更正说明。

### ✨ 新增 (Added)

- **`scripts/deploy-user-skills.py`**：用户级 Codex 副本（`~/.codex/skills/`）部署器，支持 `--check` 漂移检测与 `--dest`。此前仓库三副本有 check.sh 守着，**用户级部署副本零机制**，只能靠手工拷贝——这正是每轮 review 都能翻出"用户级副本文案落后"的结构性原因。配套 `tests/test_deploy_user_skills.py`（5 测试，含"先红后绿"端到端与"不动用户自建 skill"）。
- **用户侧 `~/.workbuddy/berkshire-skill-sync/sync.py` 纳管编排 skill**：`EXCLUDE` → `VERBATIM`（原样复制）。此前编排 skill 被排除在同步之外却仍有一份手工副本躺在 `~/.workbuddy/skills/` 下，永远不会被 `--check` 看见，于是静默落后多个版本。
- **回归测试**：`tests/test_mk_result_bundle.py` +8（含端到端 CLI 类，覆盖退出码 0/2/3、五类地板水印、macOS 符号链接路径）；`tests/test_full_analysis_gate_v2.py` +1（五类占位逐类拒收）；`tests/test_invariants.py` +4（扫描器分别上报违规/坏字节、docs 真在扫描面、行内完整命令提取、他属 flag 归属）。

### 🔬 验证方式

不再以"测试通过"自证。四处**故障注入**逐一确认守卫会红、还原后绿：回执地板改回伪造 PASS（红）、去掉 `report.resolve()`（红 6）、去掉单边证据校验（红 2）、退出码恒 0（红 2）；另注入一个未豁免的 `docs/` 新文件确认被扫描面抓获。

## [v3.4.12] — 2026-08-04

> 更正 v3.4.11：不变量守卫在 v3.4.11 实际是**红的**——守卫源码自身的 docstring 含裸旧标识 `full-company-analysis`，被自己的扫描命中并自报违规；且 v3.4.11 CHANGELOG 声称的「故障注入验证」当时并未真正落地（无注入测试）。本版把守卫跑绿、补齐注入测试，并修复生成器占位残留与 codex 副本漂移。

### 🐛 修复 (Fixed)
- **不变量守卫自红（P0）**：`tests/test_invariants.py` 扫描 `tests/` 却未排除自身，其 docstring 中的裸旧标识被自己命中 → 守卫第一条断言自报违规，v3.4.11 的 check.sh 实际 FAIL。修复：守卫源码排除自身（GUARD_SELF）+ docstring 不再直接写裸标识；解码改用 `errors="replace"`（不再静默吞解码错误）。
  - **扫描面说明**：`docs/` 经核全是历史设计档案（dated plans/specs/ROADMAP，含 `superpowers/` 下 40+ 篇），引用旧名是当时史实，与 CHANGELOG 同属档案豁免，故本版**不扫 `docs/`**（避免逼着重写历史）。
  - ⚠️ **v3.4.13 更正**：整目录豁免是错的——它让未来新增的 docs 文件自动逃逸扫描；且 `errors="replace"` 会把坏字节静默换成 U+FFFD，反而可能吞掉标识串。两点已在 v3.4.13 改为 file-level allowlist + strict 解码。
- **文档 flag 守卫可绕过（P1）**：原提取器只扫 `python3 scripts|tools/...` 命令行，导致 `--allow-stale`（v3.4.8 事故根因，文档以散文出现）从未被提取，守卫对其动机事故视而不见；且三套 CLI 注册集合被合并，注册到错误 CLI 也能通过。修复：按 CLI 归属校验——代码块内以 `python3` 真正调用的命令行（含 `\` 续行）其 flag 归属该 CLI；并把 `--allow-stale` 补进 `full_analysis.py start` 文档上下文。
- **生成器命名规范冲突 + 占位残留（P1 / 功能性 P0）**：`mk_result_bundle.py` 生成 `fact_id`/`receipt_id` 用 `fact.<skill>.<field>` 点号形式，违反 skill 文档强制的 `fact-<skill>-<descriptor>` 连字符形式；且**提供真实证据时仍与占位地板按 id 并列**，导致「提供 3 条真实事实仍残留 3 条占位事实 + 1 条占位来源」，Gate 占位预检直接拒收整包。修复：id 改连字符形式；真实证据与占位地板互斥——提供真实证据后零占位残留（`tests/test_mk_result_bundle.py` 新增 `test_real_evidence_leaves_no_placeholder_floor` 守护）。
- **部署漂移**：本机 Codex 安装副本 `~/.codex/skills/.../SKILL.md` 预算口径仍写 `26`，仓库真源已为 `27`（`2×13+1`）。已对齐为 `27`；五个副本（skills/、workbuddy-skills/、codex-skills/、~/.workbuddy/skills/、~/.codex/skills/）现已一致。

### ✅ 新增 (Added)
- **真正的故障注入测试**（兑现 v3.4.11 未落地的承诺）：
  - Invariant1 谓词注入：`has_bare_legacy_id` 对裸标识为真、对带 `-workbuddy` 后缀为假。
  - Invariant2 注入：合成文档塞未注册 `--ghost-flag` → 守卫必红；在错误 CLI 上下文注册仍判缺失。

## [v3.4.11] — 2026-08-04

> 不变量守卫：把三轮返工的病根（只改一行就宣称全称性质成立）固化成 check.sh 机器断言

### ✨ 新增 (Added)
- **`tests/test_invariants.py`**（自动挂载，check.sh 第 11 行 unittest discover 收集），三条不变量守卫：
  1. **改名一致性**：git 追踪的活跃文件（skills/workbuddy-skills/codex-skills/scripts/tools/tests/README/CLAUDE/SKILLS-GUIDE/AGENTS）中不得存在裸旧标识 `full-company-analysis`（必须带 -workbuddy）；历史档案（CHANGELOG/.darwin-results.tsv）豁免。
  2. **文档宣称⊆CLI 注册**：编排 skill 文档命令行里的每个 `--flag` 必须在 full_analysis.py/mk_result_bundle.py/full_analysis_gate.py 中真实注册。直接对应 v3.4.8 的 `--allow-stale` 脱节事故（文档存在但 argparse 从未注册）。
  3. **校验器负例先行**：frontmatter/contract 校验器必须真拒非法输入（缺 platform、name 不匹配、非法 registry）。直接对应 v3.4.9 的"收紧空转"（规则嵌在 name 分支里，值恰好匹配时不触发）。
- **故障注入验证**：两条守卫经实际注入错误确认会 FAILED（README 塞裸旧 ID、文档塞未注册 flag），非空转。

---

## [v3.4.10] — 2026-08-03

> review 三轮修复：改名真闭环（README 死链 + 4 业务 skill + 链接测试）/ frontmatter 真强制（双向 platform 规则 + 7 回归测试）/ normal_target 口径对齐（2N+1）/ 占位证据水印护栏

### 🔧 变更 (Changed)
- **改名真闭环**：README.md:173 死链（workbuddy-skills/full-company-analysis/SKILL.md → -workbuddy）修复；ashare-data(9 处)/earnings-review/investment-research/news-pulse 旧标识清零（skills/ 活跃真源裸旧 ID = 0）。
- **README 链接测试扩展**：从只匹配 `skills/` 前缀扩为覆盖全部相对 .md 链接（local/ 除外——本地私有区不入库，CI 无法解析）；顺带发现并修复 README:551 四个已删除 funnel 报告的死链（改为指向 INDEX.md）。
- **frontmatter 真强制**：v3.4.9 的"收紧"实测可绕过（删 platform 后 name==stem 不触发 name 分支）。改为独立双向规则：文件名带 -workbuddy 后缀 ⟺ 必须声明 platform: workbuddy（两个方向都拦）；name 严格等于文件名 stem，无豁免后门。新增 7 个回归测试（含 v3.4.9 漏检复现用例）。
- **normal_target 口径对齐**：2N（26）→ 2N+1（27）。+1 = preflight，它计入 used 一次，与 runtime 的 used 实际计数严格对齐（此前差 1 会误报版本错配）。测试断言与 skill 文档同步。
- **CLI 帮助补三态**：--allow-stale help 覆盖 stale=None（无 git/无 tag/命令异常）场景，不再只说"HEAD 落后 tag"。

### 🛡️ 占位证据水印护栏（遗留高风险项缓解）
- **生成器水印**：mk_result_bundle 的「结构地板」占位证据不再伪装权威来源（"巨潮资讯网/上交所"→ `PLACEHOLDER 占位来源（未核实）`，url → example.invalid），fact value 加 `PLACEHOLDER::` 前缀 + confidence high→low。
- **Gate 硬拒收**：新增 `_precheck_placeholder_evidence`——PASS/PWL bundle 中任何带 PLACEHOLDER 水印的 fact/source 在 ingest 时被确定性拦截（水印是确定性字符串，误报为零），并入预提交门禁聚合抛错。
- **大声告警**：生成器未提供 --extra-evidence/--extra-sources 时输出 stderr 警告（地板仅为本地调试 bundle 结构，绝不能作为真实调研成果提交）。
- 新增 PlaceholderEvidenceTests 4 例（含全 13 skill 地板水印完整性断言）。

### ✅ 测试
- check.sh 全绿；frontmatter 7 回归 + 水印 4 回归 + README 链接测试全过；三副本 SHA-256 一致。

---

## [v3.4.9] — 2026-08-03

> review 二轮修复：改名全链路闭环 / E1 真 fail-close（--allow-stale 注册） / 契约最小 diff / normal_target 机器派生 / 校验器收紧

### 🔧 变更 (Changed)
- **改名闭环（全链路）**：`full-company-analysis` → `full-company-analysis-workbuddy` 在文件名、触发词、codex 生成目录、workbuddy-skills 目录、安装脚本、测试引用、ashare_data 注释、用户级 sync 脚本 EXCLUDE 全部落地；旧目录删除。三副本 SHA-256 一致。
- **E1 真 fail-close**：`stale=None` 不再 WARN 放行，与 `stale=True` 一样 `GateError` 拒绝（注释与实现矛盾消除）；`--allow-stale` 参数正式注册到 argparse（此前文档宣称存在但从未注册）。
- **契约最小 diff**：恢复 v3.4.7 原始紧凑格式（撤销 json.dump 重排），仅保留 `bottleneck-hunter` / `news-pulse` 两行依赖追加，末尾换行恢复。
- **normal_target 机器派生**：`26` 魔数改为 `2 × len(registry["skills"])`（13 → 26），唯一真源在 Gate `cmd_init`；注释说明严格语义（全员一次成功 + 一轮返工余量）与阻断职责归属（stop_dispatch_at 软 / hard_max 硬）。
- **frontmatter 校验器收紧**：`-workbuddy` 后缀放行须同时声明 `platform: workbuddy`，不再无条件放宽。
- `.gitignore` 新增 `.superpowers/`（工具私有状态目录）。

### ✅ 测试
- 新增 `test_init_fail_close_on_stale_none_and_true`（mock 三态 × GateError 断言）
- `test_start_initializes_budget_and_counts_preflight_once` 补 `normal_target = 2 × units` 断言
- check.sh 全绿（含 frontmatter 收紧校验 + sync --check）

---

## [v3.4.8] — 2026-08-03

> P1 四件修复：改名 runtime-gate / 契约 W4 机器强制 / E1 全路径 fail-close / budget 语义对齐

### 🔧 变更 (Changed)

- **改名**：`name: full-company-analysis` → `full-company-analysis-workbuddy`。Darwin runtime-neutrality gate 要求名称绑定平台。
- **契约 W4 依赖**：`bottleneck-hunter` / `news-pulse` 增加 `industry-funnel` 契约依赖。波次拓扑 5→6（W4a funnel 单独 → W4b bottleneck+news），**Runtime 机器强制** W4a→W4b 序次，不再仅靠文档纪律。
- **E1 路径收口（部分）**：`git rev-parse HEAD` 失败时 `stale=False`（静默放行）→ `stale=None`（WARN）。当时 `stale=None` 仍放行，未达成 fail-close；真 fail-close 与 `--allow-stale` 注册见 v3.4.9。
- **budget 语义**：移除虚假公式（`13×2+preflight`）；明确 `used` 仅在 preflight 与 job-started 递增，不含 summary/review。

### 📝 文档修正
- job-started 措辞精确化："Agent 返回" → "Agent 派发工具返回 job_id"
- W4b 增加"契约强制"说明 + 禁-3 扩充含 W4a→W4b 序次

---

## [v3.4.7] — 2026-08-03

> review 修复：normal_target 算术 / E1 fail-open / codex 真源 / W4 屏障 / review ingest / job-started 措辞

### 🐛 修复 (Fixed)
- **normal_target**：移除错误公式 `13×2+preflight`（27≠26），改为「代码硬编码 26」+ 以 Gate 为准。
- **E1 门禁 fail-open**：`git tag --list` 返回空时 `stale=False` 静默放行 → `stale=None` 触发 WARN。
- **codex 副本 Runtime 说明**：不再声称「本文件是 WorkBuddy 真源」→「WorkBuddy 编排真源为仓库 `skills/full-company-analysis.md`」。
- **W4a→W4b 屏障**：禁-3 扩充为含 funnel 序次（W4a 单独跑完成后才领 W4b）。
- **收口步骤 C**：补充 `review ingest`（prepare 后为每个 skill 逐个 ingest 再 summarize）。
- **job-started 措辞**：「Agent 返回后」→「Agent 派发工具返回 `agent_job_id` 后」（防误解为任务完成）。
- **[禁-N] 交叉索引**：移除「正文标注 `[禁-N]`」声明（实际 0 个），改为「原文位置列指向对应章节」。

---

## [v3.4.6] — 2026-08-03

> review 修复：Standards（清单去重补漏/禁-3窄化/round对齐/Runtime声明）+ Spec（硬编码/TSV损坏/时序矛盾/normal_target口径）

### 🐛 修复 (Fixed)

**Standards：**
- 禁止事项清单：标题「全集」→「参考清单」；禁-3「禁止裸调用」→「W3/W4 禁止裸调用（W1/W2/W5 允许）」；禁-8/禁-21 去重（删除旧禁-21，编号腾给新规则）。
- 补遗漏 3 条：禁-21 Agent 不得自证计算、禁-22 角色备忘录不得相互引用、禁-28 deep-summary 不得调用准出命令。清单共 35 条。
- 禁-12：round() 禁令修正——`financial_rigor.py` 原生支持 `round(EXPR, N)`（展开为 quantize 字面量），仅禁 `^` 幂运算。
- Runtime 声明：新增跨 Runtime 说明块（WorkBuddy 原生 + codex-skill 仅作参考工作流）。

**Spec：**
- 恢复指令：`git checkout v3.4.4` 硬编码 → 动态获取最新 tag。
- TSV 第 13 行：heredoc 换行致 16 列损坏 → 修复为合法 9 列。
- job-started 时序：L73「启动 Agent 前调用」→「Agent 返回后立即调用」（与 L85 一致，需 agent_job_id）。
- normal_target 口径：文档「13 项契约 + preflight」→ 补代码值 26 = 13×2(work+summary) + preflight 说明。

### 🚀 部署
- codex skill 重新安装（`~/.codex/skills/full-company-analysis/SKILL.md` 含 35 条禁止规则）。
- 三副本 SHA 一致，check.sh 全绿。

---

## [v3.4.5] — 2026-08-03

> Darwin 2.0 复评 94.3/100 + dim9 补齐（新增 🚫 禁止事项清单独立章节）

### ✨ 新增 (Added)
- **🚫 禁止事项清单（红灯规则全集）**：33 条红灯规则，A-E 五分类（派发并行/报告证据/审计返工/数据基线/路径格式），每条编号 `禁-1`~`禁-33`，含后果说明与原文位置交叉引用。弥补 dim9「反例遍布全文但无独立章节」短板。

### 📊 评估
- Darwin 九维独立复评 **94.3/100**（+1.2 vs v3.4.2 终评 93.1；+0.9 vs v3.4.4 初评 93.4）。
  - dim3（失败模式编码）★ 满分 10.0——E1 机器门禁 / budget-adjust CLI / event-log CLI / 429 降级 / 孤儿恢复全部可执行。
  - dim5（可执行具体性）★ 满分 10.0——零软化词 + allowlist 命令逐字接线 + 22/22 CLI 可达。
  - dim9（反例黑名单）9.5——独立章节 33 条规则，五分类。
  - dim8（实测表现）9.2——664 tests 全绿 + 9 项特性实测 + E1 门禁 5 场景，full_test。
- 评估记录：`skills/.darwin-results.tsv`（新增 3 行）；产物：`local/darwin-evaluation/2026-08-03-v3.4.4/`。

### 🐛 修复 (Fixed)
- dim9 短板：散落的 25+ 条禁止规则聚合为独立「🚫 禁止事项清单」章节（编号、分类、后果、交叉引用）。

### ⚠️ 已知限制
- W3/W4 allowlist 错峰「净收益为正」声明待 v3.4.5 之后真实 run 复验（dim8 最后 0.7 分扣分来源）。

---

## [v3.4.4] — 2026-08-03

> 修复目标轴四问题（review 发现：W3/W4 错峰未接线 + E1 非机器门禁 + budget 残留/倒置 + event-log 空 note/TSV 格式）

### 🐛 修复 (Fixed)
- **W3/W4 错峰未真正接线（HIGH）**：生产文档第 43 行仍循环调用裸 `next-work`，未传 `--allowlist`。本版文档给出逐字命令序列（W3a `--allowlist investment-team,earnings-review` → W3b `--allowlist management-deep-dive,industry-research` → W4a `--allowlist industry-funnel` → W4b `--allowlist bottleneck-hunter,news-pulse`），并明确「W3a 全 DONE 前不得领 W3b」屏障。新增测试 `test_w3b_not_leased_until_w3a_done` 钉住「屏障靠编排纪律」语义（allowlist 不越依赖，W3b 依赖满足即就绪，须由编排器遵守领取顺序）。
- **E1 仍非「过期 checkout 阻断」（HIGH）**：`start` 新增机器门禁 `_git_stale_check()`——HEAD 落后于最新 `v*` tag 时拒绝启动（`E1 版本门禁`），显式 `--allow-stale` 覆盖；git 异常降级为 `stale=None` → WARN 不静默。文档预期 tag 改动态获取（`git tag --list "v*" | sort -V | tail -1`），不硬编码版本号（文档出现具体 tag 示例即视为过期）。新增 5 个单元测试。
- **预算继续分支状态残留（MEDIUM）**：`budget-adjust` 成功后清除 `PARTIAL_REPORT.md`/`SUMMARY.md`（返回 `cleared_partial`，记入 events.jsonl）；新增交叉校验 `stop_dispatch_at < hard_max`（拒绝 `stop=133/hard=33` 倒置配置）。
- **人工复核与证据账本不严谨（MEDIUM）**：`event-log` 的 `--note` 必填非空（复核结论不可留空）；`skills/.darwin-results.tsv` 第 6/11 行补 `eval_mode` 列恢复 9 列格式。

### ✅ 测试
- 新增 6 个回归测试（W3b 屏障 / stale check×5 场景）；runtime 34 + gate 5 tests OK，check.sh 全绿，三副本 SHA-256 一致。

---

## [v3.4.3] — 2026-08-03

> 三关键问题修复（review 发现：W3 错峰 Runtime 不可实现 + full-test 归因勘误 + CHECKPOINT 闭环）

### 🐛 修复 (Fixed)
- **W3 错峰在 Runtime 层不可实现（HIGH）**：`next-work` 原无按 skill 选择租约的参数，`candidates[0]` 按契约顺序固定派发（investment-team → management-deep-dive → earnings-review → industry-research），要领到 earnings-review 必须先租出 management-deep-dive，错峰意图落空。新增 `--allowlist` 参数（逗号分隔 skill_id），白名单外的就绪单元本轮不派发，编排器可先领 W3a（investment-team+earnings-review）完成后再领 W3b。新增回归测试 `test_next_work_allowlist_enforces_w3_stagger`。
- **full-test 归因勘误（HIGH）**：v3.4.2 条目此前将 dim8 full_test 归因于绿的谐波 run，但该 run 的 contract_commit 为 `1ef23e8`（v3.3.11 时代，早于 v3.4.1/v3.4.2）——它只证明**文档流程与真实产物一致**，不验证错峰纪律有效性。v3.4.2 评估段已修正措辞；`skills/.darwin-results.tsv` 补 errata 行。**W3 错峰净收益声明待本版（v3.4.3）之后新 run 验证。**
- **CHECKPOINT 缺少可执行闭环（MEDIUM）**：①E1 版本校验纳入 `skills/full-company-analysis.md` + `git describe --tags` 目标版本比较，过期 checkout 阻断；②新增 CLI `budget-adjust`（只允许上调防静默降标，调整记入 events.jsonl）；③新增 CLI `event-log`（kind 白名单 `human_review`/`manual_rework`/`doctor_checkpoint`）；④`~/.workbuddy/...` 路径中立化表述。

### ✅ 测试
- 新增 3 个回归测试（allowlist 错峰 / budget-adjust / event-log）；runtime+CLI 34 tests OK，check.sh 全绿，三副本 SHA-256 一致。

---

## [v3.4.2] — 2026-08-03

> Darwin Skill 2.0 九维评估与优化（full-company-analysis 85.7 → 93.1）

### ✨ 新增 (Added)
- **description 触发词三枚**（全量分析 / 全量跑 / /full-company-analysis）。
- **三处显性 🔴CHECKPOINT**（E1 版本校验 / budget 触顶 / doctor WARN），全部「不阻塞自动继续」，doctor 复核结论记入 `evidence/events.jsonl` 可追溯。
- **启动流程步骤化**：6 步编号（定基线→E1→启动→核对落盘→核对预算→E10），声明输入/输出。
- **distillation-guide 绝对路径引用** + 缺失 fallback 原则。

### 📊 评估
- Darwin 九维独立复评 **93.1/100**（dim3/dim9 满分档）——分数针对**当前 skill 文档终版文件**，不构成对 v3.4.2 运行行为的验证。
- dim8 full_test 说明：复用绿的谐波 run-36fa1d00 实证**文档描述的端到端流程与真实产物一致**（13 单元全 PASS / audit 0 violation / REVIEW_PASSED）；该 run 的 contract_commit 为 `1ef23e8`（v3.3.11 时代，早于 v3.4.1/v3.4.2），**不验证错峰纪律的有效性**——W3 错峰的净收益声明见 v3.4.3 修复（`next-work --allowlist`）之后的新 run 验证。
- 评估记录：`skills/.darwin-results.tsv`；产物：`local/darwin-evaluation/2026-08-02/`。

---

## [v3.4.1] — 2026-08-02

> 编排错峰纪律（W3 拆两部分 + W4 industry-funnel 单独运行）

### ✨ 新增 (Added)
- **W3 拆两部分（默认强制）**：W3a `investment-team`+`earnings-review`（扇出重单元并行）→ W3b `management-deep-dive`+`industry-research`（轻单元并行，W3a 全 DONE 后领）。根因：重扇出与轻单元混编并行，轻单元在重单元研究完成前租约过期被 sweep 误回收重跑（宏景/沪电 run 实证）。
- **W4 `industry-funnel` 单独运行（默认强制）**：先单独派发 funnel，完成后再领 bottleneck-hunter+news-pulse 并行。
- 同步三副本：skills/ = workbuddy-skills/ = ~/.workbuddy/skills/（SHA-256 一致），codex-skills 由 sync 生成。

---

## [v3.3.8] — 2026-08-01

> 沪电股份全量验证发现的 hotfix（2 处真实 bug）

### 🐛 修复 (Fixed)
- **cache-lookup/cache-store CLI 缺 `--registry` 参数**：v3.3.7 模块函数测试通过但 CLI 集成未覆盖，生产验证（沪电 run）首次调用即 AttributeError；补参数 + 新增 CLI 回归测试。
- **gate `_git_head_commit` 因缺 `import subprocess` 恒返回 None**：v3.3.7 的 E10 契约 commit 记录功能实际失效（digest 钉死仍正常，commit 仅作记录）；补 import + 强化测试断言非空。

---

## [v3.3.5] — 2026-08-01

> 执行纪律 + 证据修正链路 + 契约版本钉死（A 层/B 层/C 层 Task 1/2/7/8）

### ✨ 新增 (Added)
- **执行纪律文档（A 层）**：启动前 git 版本校验（E1）、调度时序硬规则（E2，前台派发取真实 agent id、60 秒内提交、长任务强制 heartbeat）、派发模板内嵌契约 sections/evidence_rules（E3）、429 降级派发（E12）。
- **submit 前置账本校验（E4）**：evidence_rules 的 field/rule_id/capability 名在提交当下即被拦截，不再等 audit 批量暴露。
- **calc round() 支持（E5a）**：白名单展开为 ROUND_HALF_UP 量化；cross-validate 语义区分（E5b）：rc=1 视为 CONFLICT（已重放）而非未重放。
- **record-usage（Task 1）**：真实 Token/字节/重试计量，`evidence/usage.jsonl` + manifest `usage_summary` 聚合。
- **submit-correction（Task 2）**：correction-bundle/v1 定向修正账本（removed 差集清理 manifest 残留）；audit 错误带 `correctable` 分类（CORRECTABLE_EVIDENCE vs REPORT_REQUIRED_RETRY）。
- **rework 命令（Task 7/E9）**：DONE/PARTIAL→PENDING + 清租约 + `rework_initiated` 事件 + `reuse_base_attempt` 联动，替代手编 runtime-state。
- **finalize 契约钉死（Task 8/E10）**：start 记录 contract digest/commit，finalize 校验不一致拒绝准出（`CONTRACT_VERSION_MISMATCH`，无 `--force`）。

### 🛡️ 增强 (Enhanced)
- 跨 skill 同 fact_id 覆盖写 `fact_overridden` 告警事件（E6）；schema 报错列出允许键（E7）；review prepare 检测 facts 变更提示 stale_reviews（E8）。

## [v3.3.6] — 2026-08-01

> compact 评审简报 + methodology ref + fix-source 回写（Task 3/4/9）

### ✨ 新增 (Added)
- **review-brief/v2 compact（Task 3）**：默认简报只含 claim_sections（限长结论段）+ evidence_index（ID 索引）+ evidence_path，不再内嵌完整报告与全量证据；`--payload-mode full` 保留 v1 诊断兼容。
- **methodology ref 模式（Task 4）**：next-work `--methodology-mode ref` 只下发 spec 路径 + SHA-256 + 授权信封，不内嵌完整 skill 全文。
- **fix_source（Task 9/E13）**：review finding 可带源头定位（pipeline_raw/role_memo/report/methodology）；`review fix-list` 导出季度源头修复清单，缺 fix_source 归 UNFIXED。

## [v3.3.7] — 2026-08-01

> 跨运行产物缓存 + 成本预算告警（Task 5/6）

### ✨ 新增 (Added)
- **APPROVED 产物缓存（Task 5）**：finalize 自动写入 `<公司目录>/.full-analysis-cache/<key>/`；缓存键含 methodology/上游事实/能力 digest，任一变化自动失效；`cache-lookup` 只读查询，篡改即 MISS。
- **成本预算告警（Task 6）**：finalize 输出 `cost_budget`（missing_usage_summary / excessive_attempts / oversized_review_brief），非阻断；benchmark 增加 `metrics.usage`（total_tokens / cache_hit_rate）。
- 新增运维文档 `docs/full-analysis-cost-budget.md` 与 `docs/full-analysis-review-fix-list.md`。

---

## [v3.3.4] — 2026-07-27

> 全量分析 HTML 与公司索引可靠性热修复

### 🐛 修复 (Fixed)
- **索引输出隔离**：Gate 从当前 `run_root` 推导公司根目录，不再固定写入 Gate 源码所在 checkout；临时仓库、worktree 和大小写敏感文件系统不会再误写其他工作区。新运行目录统一为 `local/Company/`，既有小写目录按实际父目录继续兼容。
- **索引重建事务**：CLI 与 Gate 共用带跨平台文件锁的唯一重建入口，扫描、渲染和原子替换位于同一临界区；单报告 HTML 同样改为原子写入，避免并发旧快照覆盖和中断半文件。
- **跨平台锁与文件权限**：Windows 锁竞争超时后继续等待，非竞争性系统错误仍立即抛出；原子替换保留既有权限，新文件默认 `0644`，避免展示件意外变为仅所有者可读。
- **公司级统计**：同一家公司多个运行只展示目录时间戳最新的一次，覆盖公司数和 APPROVED 数恢复为公司口径，历史报告目录保持不变。
- **板块与结论分类**：增加北交所 `.BJ` / 代码前缀识别；删除免责声明、歧义 `PASS` 和单字“等”造成的误判，只使用明确投资动作短语归类。
- **元数据错误可见性**：损坏 manifest 显示 `MANIFEST_ERROR` 并输出精确文件路径，不再静默降级为普通“深度总结”。
- **无脚本可读性**：报告正文、索引卡片和统计数字默认可见，仅在 JavaScript 完成初始化后才进入滚动显现动画；脚本被禁用、CSP 拦截或运行失败时仍可阅读。
- **确定性时间显示**：索引生成时间固定按东八区格式化，不再随执行机器的本地时区变化。
- **索引 HTML 转义**：自定义 `base_label` 统一 HTML 转义，避免特殊目录名称污染生成页面。

### 🧪 测试 (Tests)
- 新增路径隔离、原子替换、跨进程并发、Windows 锁重试、权限保留、时区确定性、最新运行去重、北交所、结论词表、manifest 告警、`base_label` 转义和无 JavaScript 降级回归。
- `bash scripts/check.sh` 全绿（496 单元测试 + 14 个 skill frontmatter 治理 + Codex/WorkBuddy 生成物同步 + Contract v2 的 13 项契约校验 + 报告索引检查）。

### ⚠️ 升级注意
- 保持 Contract v2、Result Bundle v1、13 项业务契约、WorkBuddy 生产入口和 APPROVED 语义不变。
- `local/Company/index.html` 继续是非阻断派生展示件，不进入 manifest、Audit 或 Review。
- 首页只展示每家公司最新运行；旧运行和历史报告不会被移动或删除。
- 新 run 使用大小写规范的 `local/Company/`。既有 `local/company/` 数据无需迁移，按实际路径继续重建索引。

---

## [v3.3.3] — 2026-07-26

> 全量分析 HTML 展示层确定性重构 + 公司研究索引自动汇总

### ✨ 新增 (Added)
- **确定性 HTML 渲染器**（`tools/full_analysis_html.py`）：`build_summary_page(markdown, *, company, code, as_of, skill_count, status) -> str` 纯函数，内嵌设计系统（cream paper / terracotta / trust 墨蓝 / serif + masthead 报头 + sticky 导航 + 编号章节 + 样式化表格 + 滚动显现微交互）。同一份 markdown 永远逐字节渲染出同一份 HTML——零 LLM 参与、零 token 消耗、零输出方差、零失败模式，把"用户认可的输出品质"钉死在流程里。
- **公司研究索引生成器**（`scripts/build_company_index.py`）：扫描 `local/Company/<code>-<name>/<run>/` 提取一句话结论 / 数据截止日 / 准出状态 / 板块（按代码前缀分类），确定性渲染 `local/Company/index.html`（账本式报头 + 统计条 + 搜索/板块筛选 + 结论配色卡片网格 + 免责声明）。生成时间取自源报告 mtime（非系统时钟），保证可复跑逐字节一致。CLI 支持 `--base` / `--output` / `--check`。
- **HTML 产出后自动刷新公司索引**（`_rebuild_company_index()`）：`_generate_summary_html` 成功落盘 HTML 后立即触发，覆盖 `render-html`（步骤 B2）与 `finalize`（APPROVED 兜底）两条路径；非阻断，失败只打印警告并写 `company_index_rebuilt` 事件。索引是派生展示件，绝不影响 APPROVED 状态。
- **`render-html` 编排命令**：`python3 scripts/full_analysis.py render-html --run-root <root>`，在 `register-summary` 后立即生成 HTML 展示件，解耦于 audit/review/finalize；markdown 因评审返工被编辑后重跑即可原地覆盖。
- **回归测试 `tests/test_full_analysis_html.py`**：守确定性 / 安全性（链接协议、属性转义）/ 结构完整性（8 章节锚点、报头、导航、印章、免责声明）/ 忠实性（无 stash 占位符泄漏）四条底线。
- **回归测试 `tests/test_build_company_index.py`**：守确定性（同输入逐字节一致）/ 元数据提取（标记行兜底、板块分类、verdict 归类）/ 自动更新（新增公司重跑即纳入）/ 安全性（HTML 转义防注入）四条底线。

### 🔁 变更 (Changed)
- **Gate HTML 管线重构**：删除约 360 行手写 markdown→HTML 内联管线（`_markdown_to_html` / `_render_html_page` / `_load_tokens_css` / `_safe_link` / `_escape_html` 等），统一改为调用确定性渲染器；`_generate_summary_html()` 现返回 `bool` 表示是否写出 HTML。
- **索引刷新触发点后移**：由 `register-summary`（HTML 尚未生成、过早）改到 `_generate_summary_html` 成功之后，确保索引链接指向已落盘的 HTML 展示件。
- **编排 skill 三份源同步更新**（`skills/full-company-analysis.md` / `workbuddy-skills/.../SKILL.md` / `codex-skills/.../SKILL.md`）：新增「步骤 B2」`render-html` 命令；HTML 章节重写为「确定性渲染 = 固化品质」，并标注由回归测试守护四条底线。

### 🧪 测试 (Tests)
- `bash scripts/check.sh` 全绿（472 单元测试 + 14 个 skill frontmatter 治理 + Codex/WorkBuddy 生成物同步 + Contract v2 的 13 项契约校验 + 报告索引检查）。

### ⚠️ 升级注意
- HTML 渲染行为现完全由 `tools/full_analysis_html.py` 决定；依赖 Gate 内已删除函数（`_markdown_to_html` 等）的下游需改调渲染器。
- `local/Company/index.html` 为派生展示件，不进 manifest、不参与 audit/review/finalize；每次 HTML 生成自动刷新，也可手动 `python3 scripts/build_company_index.py` 重建。
- 保持 Contract v2、Result Bundle v1、13 项业务契约与 WorkBuddy 生产入口不变。

---

## [v3.3.2] — 2026-07-26

> 全量分析可靠性热修复：递归校验、失败终态、派发模板与 HTML 展示加固

### 🐛 修复 (Fixed)
- **Result Bundle 递归校验**：Gate 现在校验嵌套对象、数组、类型、必填项、枚举、格式和未知字段，错误信息携带 JSON 路径；同时允许 Audit 已使用的命令失败 `reason` 字段。
- **ingest 写入边界**：manifest、artifact 记录与 provenance 先在内存中完整准备；复制到目标目录临时文件后再次核对字节数和哈希，准备失败或复制期间源文件变化均不会替换正式路径。
- **FAILED 确定性终态**：所有业务单元完成且至少一项 `FAIL` 时，run 直接收口为 `FAILED`，记录失败单元并返回非零；不再误报 `PARTIAL`，也不要求失败运行生成 summary、Audit 或 Review。
- **派发模板完整性**：补齐 `capability_records` 与 `not_applicable`，attempt 产物固定为 `formal=false / accepted=false`；证据数组最低数量统一以当前 skill 的 `evidence_rules` 为准。
- **HTML 展示安全与正确性**：链接协议限制为 HTTP、HTTPS 和 mailto，动态元数据统一转义；修复有序列表闭合、公司/代码/数据截止日来源及空行后的 `###` 章节诊断。
- **方法论单一真源**：`investment-checklist` 不再复制固定字节门槛，改以当前 Contract 为唯一机器真源。

### 🧪 测试 (Tests)
- `bash scripts/check.sh` 全绿（455 单元测试 + 14 个 skill frontmatter 治理 + Codex/WorkBuddy 生成物同步 + Contract v2 的 13 项契约校验 + 报告索引检查）。

### ⚠️ 升级注意
- 保持 Contract v2、Result Bundle v1、13 项业务契约和 WorkBuddy 生产入口不变。
- Result Bundle 嵌套字段现在严格按 schema 验证；此前被忽略的未知或类型错误字段会被明确拒收。
- `FAIL` 运行的 finalize 现在返回非零并写入 `FAILED`，依赖旧 `PARTIAL` 行为的调用方需同步调整。
- 不恢复旧 20 项契约 run 的兼容性；仍需按当前 13 项契约重新 init。

---

## [v3.3.1] — 2026-07-26

> 全量分析派发 payload 补全 + 子 Agent 产出合规性根治 + 总结 HTML 自动生成
> 针对中天科技 run 暴露的 6 类系统性问题（Result Bundle 旧 schema、heading 用
> section_id、证据账本缺失、## 后紧跟 ###、review digest 误算、会话中断失联）
> 做统一根治：把完整模板与刚性纪律注入派发 payload，让子 Agent 第一次就写对。

### ✨ 新增 (Added)
- **派发 payload 注入 Result Bundle v1 完整模板**（`RESULT_BUNDLE_TEMPLATE`）：含全部必填字段骨架、status 枚举说明、artifact_records 数组格式，消除子 Agent 凭记忆写旧 schema（SUCCESS / artifact 对象）。
- **派发 payload 注入结构指令**（`STRUCTURE_DIRECTIVE`）：强制 ## 标题逐字使用 sections 的 heading 字段（禁用 section_id）、## 后必须先有 ≥150 字正文再展开 ###、required 章节不得缺失。
- **派发 payload 注入证据指令**（`EVIDENCE_DIRECTIVE`）：列明 fact_updates / source_records / calculation_requests / judgments / command_receipts 五类结构化证据的必填要求。
- **`next_work` 返回值新增 `evidence_rules` 字段**：把契约中本 skill 的具体证据最低要求直接交给执行 Agent。
- **Gate `_substance_errors()` 新增第 5 项诊断**：检测 `##` 后紧跟 `###`（正文为 0）并输出具体章节名提示，帮助定位「实质章节不足」的根因。
- **总结 HTML 自动生成**（`_generate_summary_html()`）：finalize APPROVED 后 Gate 自动从冻结的 markdown 总结生成自包含 HTML 展示件，编排器无需手动派 Agent；非阻断，失败只打 stderr 警告。

### 🔁 变更 (Changed)
- **编排 skill 三份源同步更新**（`skills/full-company-analysis.md` / `workbuddy-skills/.../SKILL.md` / `codex-skills/.../SKILL.md`）：「派发前必读」段落改为三条刚性纪律（heading 逐字使用 / ## 后必须有正文 / 结构化证据必填）；新增「result.json 优先写入」段落（先落盘再 submit-result，会话中断可被孤儿恢复接管）；步骤 D 改为 Gate 自动生成 HTML。

### 🧪 测试 (Tests)
- `bash scripts/check.sh` 全绿（437 单元测试 + frontmatter 治理 + Codex 同步 + 13 项契约校验 + 报告索引）。

### ⚠️ 升级注意
- 改动均为 payload 增量注入与文档更新，不改变 Gate/Audit 校验逻辑，不影响已 APPROVED 的 run。
- 依赖 `next_work` 旧 payload 结构的下游脚本：新增 `evidence_rules` 字段为附加项，向后兼容。

---

## [v3.3.0] — 2026-07-26

> 全量分析质量闭环成型 + 契约精简至 13 项 + 防凑数校准
> 累计 7 个提交（v3.2.0..v3.3.0）：把全量分析从"硬字节闸门"升级为
> 「硬地板 + 实质校验 + 软诊断」三层质量防护，契约由 20 项精简至 13 项，
> 收口序列重构为编排器统一编排的 A→B→C→D 四步，并对 Agent 隐藏字节目标以根治凑数。

### ✨ 新增 (Added)
- **doctor 执行完整性体检**（`tools/full_analysis_doctor.py`）：advisory 软诊断层，三类坍塌指纹——大量分析单元贴线（字节 <1.15× 下限）、零 heartbeat（疑似主上下文直写）、深度分化不足（标准化 margin 变异系数 <0.25）。接入 finalize 末尾，写 `evidence/doctor-report.json`，异常静默吞掉绝不影响 APPROVE/FAIL。
- **review 评审管线**（`tools/full_analysis_review.py`）：`prepare`（为核心 skill 生成评审简报）→ `ingest`（接收评审结果）→ `summarize`（聚合）。
- **snapshot 确定性快照**（`tools/full_analysis_snapshot.py`）：Audit 与 Gate 共享的输入快照，投影含 facts/sources/calculations/judgments/receipts/role_runs/capabilities 与 `manifest.delivery`，供 finalize 快照一致性复核。
- **benchmark 基准工具**（`tools/full_analysis_benchmark.py`）。
- **Agent 协作文档体系**：`docs/agents/` 新增 issue-tracker（GitHub Issues + gh CLI）、triage-labels（五标签分诊）、domain 三篇；CLAUDE.md 增「Agent skills」章节。

### 🔁 变更 (Changed)
- **契约精简 20 → 13 项**：去掉内容/组合类 skill（deep-company-series / earnings-team / portfolio-review / private-company-research / thesis-drift / wechat-article / dyp-ask），合并财报精读为单一四大师扇出 `earnings-review`。现 13 业务 skill：ashare-data / financial-data / quality-screen / investment-checklist / investment-research / investment-team / management-deep-dive / earnings-review / industry-research / industry-funnel / bottleneck-hunter / news-pulse / thesis-tracker。
- **收口序列重构为 A→B→C→D**：13 units DONE 后编排器统一编排——A) 派 deep-summary Agent 熔炼 13 份正式产物写 summary；B) register-summary 登记冻结（须在 audit 前：snapshot 投影含 delivery）；C) audit → review prepare/summarize → finalize；D) APPROVED 后派 html-express Agent 生成总结 HTML 派生展示件。
- **防凑数校准**：`min_bytes` 明确定位为防坍塌地板（挡 403 字节式空报告）而非写作目标；`next_work` payload 不再向执行 Agent 暴露 `min_bytes` 具体数字（改 None + `length_policy` 语义字段），`ANTI_PADDING_DIRECTIVE` 增「关于长度·必读」；实质小节判定门槛由魔法数 80 提为命名常量 `SUBSTANTIVE_MIN_CHARS=150`（低于此视为一句话占位不计实质）。

### 🔒 可靠性 (Reliability, P0)
- **ingest 事务性**：重构为「只读校验阶段 → 事务化晋级阶段」，被拒 attempt 绝不触碰正式文件（旧版在校验前复制，被拒 attempt 会留旧哈希覆盖正式文件）。
- **finalize 哈希复核**：正式产物实际 bytes/sha256 与 manifest 不一致即 PARTIAL 拒出，捕获「manifest 合格但正式文件被污染」。
- **doctor 心跳按 accepted attempt 关联**：旧失败 attempt 心跳不再替零心跳 accepted attempt 背书；accepted attempt 无记录时回退 skill 级。
- **partial 日志告警**：`events_status="partial"` 产生 WARN，不再静默 PASS。

### 🧪 测试 (Tests)
- 新增 `test_full_analysis_doctor.py` / `test_full_analysis_review.py` / `test_full_analysis_benchmark.py` / `test_full_analysis_docs.py`；gate_v2 增 P0 事务性与哈希复核用例（被拒 attempt 不覆盖正式文件、finalize 拒篡改产物）。
- `bash scripts/check.sh` 全绿（437 单元测试 + frontmatter 治理 + Codex 同步 + 13 项契约校验 + 报告索引）。

### ⚠️ 升级注意
- 契约为破坏性精简：旧 20 项契约 run 的 manifest 与本版不兼容，请以 13 项契约重新 init。
- `next_work` payload 的 `min_bytes` 现为 None，依赖该字段读具体数值的下游脚本需改读 `length_policy` 或 contract 本体。

---

## [v3.2.0] — 2026-07-24

> 质量闸门实质化 + 并发上限 2→4
> 累计 15 个提交（v3.1.0..v3.2.0）：完整落地全量分析 Runtime / Gate v2 / Audit 管线，
> 并将质量闸门从"纯字节门槛"升级为"实质校验"，并发上限由 2 提升至 4。

### ✨ 新增 (Added)
- **全量分析运行时（Runtime）**：租约（20 分钟 + 心跳续租）、预算闸门（正常 40 / 停派 45 / 硬上限 50）、429 冷却与降并发、迟到结果拒收。
- **Gate v2 运行根与产物晋级管线**：artifact 实际字节数 / hash / 路径约束确定性校验。
- **事实来源与计算 Audit**（`tools/report_audit.py`）：`duplicate_source_id` / `fact_without_source` / `calculation_not_replayed` 检测。
- **Result Bundle v1 schema 冻结** + **Contract v2 注册表**（20 项契约，含分级 `min_bytes` 与 `evidence_rules`）。
- **WorkBuddy 原生 Agent 适配器**（薄层 CLI → runtime → gate → audit），切换自旧编排。
- **编排 skill 治理元数据**（owner / category / maturity）。

### 🔁 变更 (Changed)
- **质量闸门从"纯字节门槛"升级为"实质校验"**：分歧 / 反面检验标记数、扇出类具名分歧、标题占比上限（防骨架注水）、防坍塌字节软下限。
- **runtime.next_work 派发 payload 注入 `methodology_text` 与扇出要求**，杜绝执行 Agent 退化为单遍写大纲。
- **并发上限由 2 提升至 4**（`tools/full_analysis_gate.py` 初始状态 `concurrency.max`）。
- 契约 `min_bytes` 由"深度目标"降级为"防坍塌软下限"，新增 `skill_type` / `min_dissent_points` / `min_substantive_sections`。
- `result_schema` 新增结构化实质字段 `key_claims` / `calculations` / `dissent_points` / `scenarios` / `reverse_tests`。
- 21 个 skill 质量强化与 Codex 包同步。

### 🐛 修复 (Fixed)
- **`_merge_provenance` 来源去重硬化**：sources 按 `source_id` 去重并丢弃 null 占位；facts 按 `fact_id` 去重、无来源的管线事实自动挂接规范源 `src.ashare_pipeline`；calculations 去重丢弃 null 占位（根治 audit FAIL，持久修复未来所有 run）。
- 适配器元数据同步与契约映射收紧。

### 📚 文档 (Docs)
- 无人值守可靠性设计 / 实施计划 / 方案收敛文档。

### 🧪 测试 (Tests)
- v2 测试入口更新 + 单公司 canary；脱敏实跑事故固化。
- `bash scripts/check.sh` 全绿（321 单元测试 + frontmatter + Codex 同步 + 契约校验 + 报告索引）。

---

## [v3.1.0] — 2026-07-22

> 层驱动编排重构 + 封闭证据 schema + 21 skill 质量强化

（详见 git history：`git log v3.0.0..v3.1.0`）

---

## [v3.0.0] — 2026-07-21

> 全量分析编排基座与批量脚本补齐

---

## [v2.0.0] / [v2.0.1] / [v1.0.x]

早期版本，历史提交见 `git tag --list`。

[v3.2.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.2.0
[v3.1.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.1.0
[v3.0.0]: https://github.com/psilhon/ai-berkshire/releases/tag/v3.0.0

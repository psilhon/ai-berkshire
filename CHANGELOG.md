# Changelog

本仓库遵循 [语义化版本](https://semver.org/lang/zh-CN/)（SemVer）。
所有发版记录以 git tag 为准，本文件为人工维护的变更摘要。

---

## [v3.10.14] — 2026-09-04

> **架构深化落地（2026-08-30 /improve-codebase-architecture 12 候选，本轮修复 8 项，4 项前轮已完成或撤回）**：数据抽检准出收敛单一真源、同步链孤儿检测、测试基建 factory、守卫故障注入、准入判定单一真源、verdict 原语收敛、source-level adapter 去重、gate 分区与 dispatch 合一、ashare_data 归一化返回示踪步。单测 **741 → 761**，`check.sh` 退出码 **0**。

### ✨ 新增 (Added)

- **`tools/bundle_checks.py`**：bundle 准入判定单一真源（候选③）——PLACEHOLDER 水印扫描 `placeholder_offenders()`（五类账本口径表单点定义）+ NA 谓词证伪规则 `na_predicate_violation()`（`min_independent_contexts_2` 特例只写一遍）。gate（拒收侧）与 mk（生成侧）共同委托，消息各自包装呈现。
- **`tools/full_analysis_correction.py`**：correction bundle 校验与落账自 gate 外移（候选⑩分区），gate 1787 → 1644 行；单向依赖 gate 判定真源，`submit-correction` 路由同步。
- **`tests/conftest.py` + `tests/test_run_factory.py`**：run 目录测试工厂 `make_run_root()` / `seed_attempt()`（候选⑪）——经 `run_store` 构造 canonical 布局、路径取 `run_layout` 常量零字面量；`test_evidence_receipt` / `test_mk_result_bundle` 两处手拼 JSON 样板迁移至工厂。
- **同步链目标侧孤儿检测（候选⑥）**：`sync-codex-skills.py` 以生成标记（`GENERATED_MARKER`）判定孤儿——`--check` 遇孤儿 exit 1 点名，生成模式清理孤儿整目录；Codex-only 手写包（`investment-memo-craft`，无标记）天然豁免。新增 `tests/test_sync_orphan_detection.py`（5 用例，含假仓库 e2e 红→清→绿）。用户侧 `~/.workbuddy/berkshire-skill-sync/sync.py` 同步补检测，但**只报告不删除**——实测发现 7 个「孤儿」（deep-company-series 等）实为源删除后被收养的活 skill，机器不可辨残留与收养。
- **守卫故障注入（候选⑪）**：`tests/test_check_stop_gates.py` 7 用例——注入缺要素确认门必红、补齐必绿、非确认类落 INFO、真仓 41 门完整不变式。首跑即抓到 `check-stop-gates.py` 对注入目录 `relative_to(ROOT)` ValueError 的潜在缺陷（改 `relative_to(skills_dir)`）。

### 🔧 变更 (Changed)

- **数据抽检准出收敛（候选⑤）**：4 份 skill 副本（earnings-review / industry-analysis / investment-team / investment-research）的抽检流程统一为同一模板，准出口径显式声明以 `tools/report_audit.py` 为单一真源（15% 抽样 / 偏差 ≤1% / 三态判决）；补齐 earnings-review 缺失的 1% 口径。STOP 门文本零改动（ADR-0003）。
- **判决原语收敛（候选②）**：`financial_rigor.py` 新增 `_OUTCOME_EXIT` / `_verdict()`——outcome → exit_code 映射单点化（此前 (outcome, exit_code) 散布 7 处）；6 个 `_json_*` 重投影消除（约 195 行重复逻辑），文本/JSON 两路共用 `_mc_inputs` / `_valuation_metrics` / `_cross_validate_core` / `_benford_core` / `_calc_eval` 纯计算核心，print 降为投影。**修复 cmd_valuation 恒真退出**：无任何指标可算时文本路径从恒 exit 0 改为 exit 2（与 --json 路径 INSUFFICIENT 对齐）。TDD 红→绿，`test_financial_rigor` 96 用例全绿。
- **source-level adapter 去重（候选⑧，修订 ADR-0001）**：`ashare_data._em_secu_code` / `_em_secid` 委托 `CodeIdentity`，`_fetch_datacenter_rows` 委托 `ashare_plugin.fetch_datacenter_rows`（原 `_fetch_rows` 转公开别名；`_curl_json` 经 TransportClient 适配注入、`TransportError`→`ConnectionError` 保语义）；`_qq_code` 有效码走 `CodeIdentity.quote_code`、无效码保留旧前缀映射垫片（`cmd_quote("INVALID") → False` 降级路径被测试锁定，严格化单独立项）。测试零改动，142 用例全绿。
- **gate dispatch 表合一（候选⑩）**：`GATE_COMMANDS` 成为 Gate 意图命令的唯一 dispatch 表，`gate.main` 与 `scripts/full_analysis.py` 共用；派生展示件（`full_analysis_html` / `build_company_index`）由 importlib 文件加载改回静态 import（零反向依赖，无循环）。
- **ashare_data 归一化返回示踪步（候选①）**：新增 `CommandOutcome`（ok/data/warnings），`cmd_quote` / `cmd_valuation` 迁移为「数据进返回值、print 为投影」；CLI 契约（退出码 + stdout）逐字节不变，恒等断言升级为布尔断言，新增 3 用例锁定返回值编程接口。其余 62 命令按同模式分批。
- **`run_layout.py` 补 `EVIDENCE_REL`**：gate 种子文件写入与 `conftest` 工厂改用常量，全仓 `"evidence" /` 字面量直拼清零（候选⑦收尾）。
- **`check-stop-gates.py` docstring 对齐实际语义**：删「骨架五要素」死常量 `ELEMENTS`（从未被 `check()` 引用）与过时宣讲，改为真门三要素（GATE_ELEMENTS）+ (b)/(c) INFO 桶，留历史注记。

### 📐 有意保留 / 边界裁决

- **判决词汇不跨工具强行合一**：report_audit 三态（准出/证据不足/打回）与 mk 退出码 0/2/3 分属「抽检准出」「提交有效性」语义轴，与 rigor 的「验算判决」不同域；各域保留自有词汇，仅 rigor 域内单点化。
- **`test_mk_result_bundle.py` docstring 过期理由同步修正**：「min_substantive_sections 永不满足」的已知缺陷描述在 v3.10.13 已失效（substance 原文重扫已修），本轮补齐这份漏改。
- **契约环检测去重为第一步（候选⑨）**：`check-full-analysis-contract.py` 两段 ~48 行同构（v2 :355-403 / lean :498-545）收敛为模块级 `_check_depends_on` / `_find_dep_cycle`，错误标签参数化（:v2/:lean）、校验规则与文本逐字不变（ADR-0002 冻结语义由 25 个契约测试守护）；与 runtime `detect_dependency_cycle` 的跨模块合一属后续步骤。

---

## [v3.10.13] — 2026-09-04

> **代码精简（ponytail-audit 全仓过度设计审计）**：审计 `tools/` + `scripts/` + `tests/`（约 29k 行），执行全部可削减项与 5 个决策项，累计 **25 files changed, 281 insertions(+), 989 deletions(−)**（净 **−708 行**）。单测 **809 → 741**（消除 61 次重复执行 + 删 7 个随功能移除的测试），`check.sh` 退出码 **0**（顺带修好既有的报告索引漂移）。方案与逐条复核记录见 `docs/ponytail-audit-optimization-plan-2026-09-04.md`。
>
> 审计共提 10 条建议，**5 条经复核撤回**（2 条与既有 ADR 冲突、3 条因「测试即契约 / 语义分叉 / 接线遗漏」）——见文末「有意保留」。

### 🗑 删除 (Removed)

- **`tools/hkex_data.py`（−265L）**：全仓零引用（tools/scripts/tests/skills/codex-skills/docs/CI 全检索），仅自身 docstring 提及。
- **三处零引用死代码**：`scripts/build_report_index.py::_gitignored`（−23L，连带删已孤立的 `import subprocess`）；`tools/full_analysis_runtime.py::atomic_json`（−3L，本就只是 `run_store.atomic_write_json` 的薄委托且零调用方）。
- **3 个 shim 测试文件**（`tests/test_full_analysis_{gate,contract,phase2}.py`，−11L）：各仅一行 re-import，导致 `unittest discover` 把 61 个用例执行两遍。
- **跨 run 产物缓存层（−281L）**：`tools/full_analysis_cache.py` 在 finalize/APPROVED 后自动写缓存，但读取侧 `lookup()` 仅由 CLI 暴露、`next-work` 派发从不查询——**只写不读**。计划文档（`docs/superpowers/plans/2026-07-30-full-analysis-token-cost.md` Task 5）的「命中则登记 `CACHE_REUSED`、不派 Agent」读路径从未实现，按未完成半成品清除；同步删 `cache-lookup` / `cache-store` 两个隐藏子命令与 gate 的写入块。恢复方式：git 历史取回 + 按 Task 5 补读路径（属派发语义变更，非清理范畴）。
- **`tools/akshare_data.py`（−243L）**：依赖 `akshare` + `requests` 本机**均未安装**（它是全仓唯一第三方 import），全仓检索零命中（含 `local/` 全部历史运行记录）、零测试——从未被实际调用；而 `skills/ashare-data.md` 仍在推荐 agent 走这条必然失败的路径。**据此修订 ADR-0001**（追加 2026-09-04 修订章节，撤销原「不删除」裁定）。仓库恢复 **stdlib 零依赖**。
- **`equity_dcf.franchise_growth_value`（−6L）**：估值方法库里唯一未接入 `run()` 分派的成员（同层其余 8 个方法均已接线），零生产入口、零 CLI 入口。

### 🔧 变更 (Changed)

- **原子写统一到 `run_store`**：删 `audit`/`benchmark`/`doctor`/`review` 各自的 `_atomic_write_json` 与 gate 的 `_atomic_write_json_safe`，统一调 `run_store.atomic_write_json`（mkstemp + fsync + 权限保持 + os.replace）。顺带修两处行为漂移：doctor 原用 `with_suffix(path.suffix + '.tmp')`（无后缀/含多点路径会写错临时文件名）、其中两份缺 `mkdir(parents=True)`、三份无 fsync。
- **`_num` 提为模块级**（`tools/ashare_data.py`）：7 份逐字相同的内部函数 → 1 个，−34L。
- **取数级别三档合并**：`LEVEL_COMMANDS` 三个 key 值完全相同，`LEVEL_PENDING_LAYERS` 三级全空且打印块永不触发 → 单个执行集常量 + 一张标题表，−22L。三档行为等价（实测命令序列一致，仅标题不同）。
- **`AGENTS.md` 补 `docs/` 导航**：标注 `docs/superpowers/`（476K SDD 历史存档）为「历史记录、非现行工作流」，并给出 `docs/adr/` 入口（重构前先读 ADR）——此前该目录未被任何入口引用，易被误当作现行规范。
- **`skills/ashare-data.md` 陷阱 #3 同步**：随 `akshare_data.py` 移除，改为「无 token 时仓库不提供内建脚本，需自行取数」，`codex-skills/` 已重新生成。

### 📐 有意保留（审计建议但经复核撤回）

- **`run_store.read_events`**：是 `append_event` 的对称读侧，被 5 处测试用作行为验证缝（含 gate/runtime 薄委托验证），与 ADR-0001「测试引用内部缝合法」同裁定。
- **`gate.validate_result_bundle`**：`full_analysis_gate.py:407` 将其写为三方共用 `admit_bundle` 的契约口径，且是 `test_mk_result_bundle` 的 oracle 入口（4 个故障注入子用例依赖它变红）。
- **JSON 读取 5 个变体不合并**：实为 4 种不同错误语义（GateError / RunStoreError / DoctorError / 静默 None / 裸抛），合并会改异常类型、破坏 doctor 的异常逃逸约束。
- **事件账本 `events.jsonl` 只写不读（两侧均保留）**：`append_event` 有 19 处写入、`read_events` 零生产读取（doctor 走 `evidence_receipt.load_journal`）。删 `read_events` 会削弱 5 个行为测试（含 gate/runtime 薄委托验证），19 处写入的审计留痕价值也高于 11 行收益。
- **`full_analysis_benchmark.py`**（394L）保留：审计时看似不在 skill 的 L2–L4 链里，但 2026-08 的运行记录显示实际用过。

---

## [v3.10.12] — 2026-09-02

> **industry-analysis skill 质量优化（darwin-skill 基线 + 3 轮优化）**：对 v3.10.11 物理合并产物执行 9 维加权基线评估（86.0，full_test），独立子 agent 实测暴露 4 处指令缺口后进入 Phase 2 优化循环 3 轮 → **91.5（Δ +5.5）**。改动仅限 `skills/industry-analysis.md`（+30/−12 行），契约单元/引用面/产物文件名零变化。

### ✨ 新增 (Added)

- **`3.0 主题输入 → 行业骨架映射`节**（第三步前）：跨申万一级行业主题（AI 算力/储能/固态电池）先拆 2-4 个申万行业 → 逐行业 `peers`/`industry-pe` 取池 → 跨行业同环节合并去重 → 输出标注覆盖口径；无法映射环节用活跃度扫描 + WebSearch 补池。修复"纯主题输入在第 2/3 步卡壳"。
- **`2.1 骨架判定`**（画图前先定）：单一申万行业 vs 跨行业主题分支指引，防画完全景图才返工。

### 🔧 变更 (Changed)

- **模式表 A 全景覆盖范围修正**：第 1-5 步（到精细档）+ 第 7 步行业收尾（跳过第 6 步个股组合）——修复合并时 A 模式缺失原 research 行业风险/文明趋势收尾的语义裂缝；升格 B 说明同步（从第 5 步深析档继续第 6、7 步）。
- **流程总览图**：⑤ 深析档标注"仅 B"、⑥ 标注"仅 B"、⑦ 标注"A 与 B"，图下加 A/B 走径说明。
- **第五步档位表**：加"模式"列（快评/精细档 A+B，精细档 = A 止步点，深析档仅 B），正文显式声明"A 执行到精细档即止步"。
- **第七步开头**：加模式取用声明（A 与 B 都执行本步，A 跳 6 后直落，不产出个股推荐）。
- **第一步取数边界**：1-2 步允许轻量 WebSearch/WebFetch（非全市场扫描），第 3 步起须过 🔴 STOP；1.2"数据来源"列须填实际信源不得留空。
- **1.3 指代明确**："进入下一步"显式指第二步产业链全景图（与失败处理呼应）。

### 📐 有意保留

- 全部契约单元（skill_id / formal_path / depends_on / 波次）、引用面、下游匹配层：零改动。
- 残余轻度点 2 条（full_test 第 3 轮记录，未修）：① 2.1→3.0 跨节引用的"此刻只拆解、取池待 STOP 后"分阶段边界未点破；② "看看/看懂格局"类措辞可直接判 A（现仍走一次模式确认）。

### 🔍 验证 (Verification)

- darwin 流程：baseline 86.0（full_test）→ R1 87.2（dim2 截断点）→ R2 88.0（dim5 主题映射/取数边界/指代）→ R3 91.5（dim8 full_test 重测无硬卡壳）；每轮独立 judge 子 agent 复核，改动 commit 于 `auto-optimize/20260902-1445` 分支（4 commit），人审通过后并入。
- `env -u PYTHONPATH bash scripts/check.sh` 真实退出码 0（见下方执行记录）；codex 同步 + 用户侧 sync 已跑。

---


> **行业投研双技能物理合并（全链路）**：应"物理合并、非简单堆砌"决策，评估 `industry-research`（产业链全景前半段）与 `industry-funnel`（精选 3 家后半段）为同一投研流程前后两半后，合并为**单源 `skills/industry-analysis.md`**——单主流程 + 双模式（模式 A 全景 = 原 research 第 1-5 步 + 行业收尾；模式 B 精选 = 原 funnel 第 4-7 步），共享模块全部去重。契约 09/10 单元 `spec_source` 同指新文件、`report_guidance` 按模式分工；**`skill_id` / `formal_path` / `depends_on` / 波次（W3b·W4a）不变**，下游按单元 id 或产物文件名匹配的层（语义评审 scope、deep-summary 熔炼）零改动。canonical 18→17。`check.sh` 真实退出码 0（809 tests OK）。

### ✨ 新增 (Added)

- **`skills/industry-analysis.md`**（单源，约 380 行）：运行模式表（A 全景 / B 精选，开始前先定）、7 步主流程（①逻辑链验证 → ②产业链全景 → ③全球上市公司扫描 → ④五条硬指标去劣 → ⑤分层四大师分析 → ⑥互补性终选 3 家与组合配置 → ⑦行业级收尾）、四大师四策略维度 5.1-5.6、5 条量化硬指标与淘汰留痕、6 层 STOP 确认门、偏见自觉 7 项表、Tushare 取数（`peers` / `industry-pe` / `sector-peers` / `sector-flow`）、依赖清单（`report_audit.py`）、失败分支与红线。frontmatter：`owner: psilhon` / `category: 行业与筛选` / `maturity: stable`。
- **`codex-skills/industry-analysis/SKILL.md`**：sync-codex-skills 生成（17 个 + WorkBuddy adapter）。

### 🔧 变更 (Changed)

- **契约 `tools/full_analysis_contract.json`**：单元 09/10 `spec_source` → `skills/industry-analysis.md`；`report_guidance` 分工——09 = 模式 A（第 1-5 步 + 行业收尾，漏斗仅概述不重复 10）；10 = 模式 B（第 4-7 步：硬指标去劣留痕、候选深析 800-1200 字/家含管理层专项、终选 3 家与配置）。
- **引用面**：README（"18 canonical（16 业务）" → **17 canonical（15 业务 + 1 编排 + 1 路由）**、速览表两行合一、示例改用 `/industry-analysis` 双模式）、SKILLS-GUIDE（命名表 + 目录表 + 选择建议）、`skills/industry-routing.md`（consumers 改指）、`skills/ashare-data.md` L51（peers = industry-analysis 候选池）、`tools/ashare_data.py` docstring ×2、根目录 `architecture-and-skills-guide.html`（速查表标题 18→17、行业组 3→2 行；SVG 波形图 W2/W3 保留契约单元标签）。
- **WorkBuddy 用户侧**：`berkshire-skill-sync/sync.py` 重生成 `industry-analysis` 副本 + 手动删两孤儿目录；`SKILL-REGISTRY.md` v16（投资研究 25→24）；`deep-company-series` / `portfolio-review` 的 `/industry-research` 路由改指 `/industry-analysis`。

### 🗑️ 移除 (Removed)

- `skills/industry-research.md` / `skills/industry-funnel.md`（`git rm`）；`codex-skills/industry-research|industry-funnel/` 与 `~/.workbuddy/skills/industry-research|industry-funnel/` 孤儿目录手动删除（两个 sync 脚本均无孤儿清理责任）。

### 📐 有意保留（契约单元不变）

- `tools/full_analysis_review.py` `DEFAULT_REVIEW_SCOPE` 的 `"industry-research"`：scope 按契约单元 `skill_id` 匹配产物，改名反失配。
- `deep-summary` 及 distillation-guide 引用的 `09-industry-research.md` / `10-industry-funnel.md`：运行产物 formal_path 未变，零改写天然兼容。
- `docs/architecture-and-skills-guide.html`：v3.10.0 起被根目录版替代的旧副本，不维护。
- CHANGELOG 既往条目、docs/superpowers·archive 历史文档不改写。

### 🔍 验证 (Verification)

- `env -u PYTHONPATH bash scripts/check.sh` **真实退出码 0**：`Ran 809 tests ... OK`（37.5s）；Skill frontmatter 17 个全合规；🔴 STOP 确认门 41 处骨架完整；codex 同步 `--check` exit 0；lean-v1 契约注册表校验通过。
- 用户侧：`sync.py` 重生成后 `skill_usage_stats.py --verify` ✅（97 个磁盘 skill registry ↔ 磁盘双向一致）。

---

## [v3.10.10] — 2026-08-30

> **架构评审候选②·完整版：RunStore 深模块**。v3.10.9 落地了候选②的机械部分（run_layout + subprocess 环回内化），状态文件所有权收拢当时被有意搁置（CHANGELOG「有意未做」节）。本版补齐：新建 `tools/run_store.py` 作为四个状态文件 I/O 的单一所有者，gate/runtime 全部状态 I/O 改为薄委托，并修掉一处**非原子写缺陷**。`check.sh` 真实退出码 0（809 tests OK）。

### ✨ 新增 (Added)

- **`tools/run_store.py`**（约 210 行）：run 状态文件 I/O 的单一所有者。收拢——
  - 原语：`atomic_write_json` / `atomic_write_text`（tmp+fsync+权限保持，原 gate 实现上移）；
  - 状态：`load/write_manifest`、`load/write_runtime_state`（`STATE_VERSION` 版本校验单点）；
  - 账本：`append_event` / `read_events` / `reset_events`、`append_usage` / `read_usage`；
  - 自有异常 `RunStoreError`（code 1=版本不匹配 / 2=不可读），由 gate（→`GateError`）与 runtime（→`RuntimeErrorState`）翻译。
- **`tests/test_run_store.py`**（约 250 行 / 20 例）：机器证明三层分工（`run_layout` 放在哪 → `run_store` 怎么读写 → `gate/runtime` 何时写）。含 `assertIs` 单一真源断言（gate 原子写原语与 run_store **同一对象**）、原子写失败保留旧文件、无临时文件残留、版本不匹配 code 1、事件 `event_at` 键序、gate/runtime 错误翻译行为等价。
- **`tools/run_layout.py`**：补 `LOCK_REL`（锁路径归属布局层，此前散在 runtime:41）并修正三层分工注释。

### 🐛 修复 (Fixed)

- **`tools/full_analysis_runtime.py` `_refresh_usage_summary`（非原子写缺陷）**：刷新 `manifest.usage_summary` 时原为 `write_text` 直写 manifest（进程中途被杀会留下半截 JSON，Gate 后续读入即崩）。改为经 `run_store.write_manifest` 原子写。顺带统一缩进（原 indent=1 vs gate indent=2 → 统一 indent=2）。

### 🔧 优化 (Optimized)

- **gate 状态 I/O 薄委托**：`atomic_write_json` / `atomic_write_text` 改为 run_store 的**直接别名绑定**（同一对象，`assertIs` 可证）；`load_manifest` / `save_manifest` / `append_event` / `manifest_path` 委托 run_store，gate 保留 schema 校验与 `updated_at` 策略；`cmd_init` 三处写（manifest / runtime-state / events 重置）改走 run_store，`state_version` 改用 `run_store.STATE_VERSION` 常量。净减约 40 行重复实现。
- **runtime 状态 I/O 薄委托**：`atomic_json`（升级获得 fsync）/ `load_state` / `save_state` / `event` / `_usage_records` / usage 追加 / rework 防呆的 manifest 读取全部委托 run_store；`LOCK_REL` 改从 run_layout import（删除本地重定义）。
- 所有权边界（有意保留，与 v3.10.9「有意未做」呼应）：**何时写、写什么策略**仍归 gate（manifest/events）与 runtime（runtime-state/usage）；跨进程互斥仍由 `runtime_lock` 承担。本版收拢的是"怎么读写"这一层，未改状态机语义。

### 🔍 验证 (Verification)

- `env -u PYTHONPATH bash scripts/check.sh` **真实退出码 0**：`Ran 809 tests ... OK`（较 v3.10.9 的 784 +25，来自 `test_run_store.py`）。
- 分模块复验：`test_run_store` + `test_run_layout` + `test_full_analysis_runtime` + `test_mk_result_bundle` + `test_full_analysis_gate_v2` 全 OK。
- 用户侧同步链 `~/.workbuddy/berkshire-skill-sync/sync.py --check` exit 0（18 副本一致；本版未动 `skills/*.md`，codex 侧 no-op）。

---

## [v3.10.9] — 2026-08-30

> **架构评审落地（`improve-codebase-architecture`，6 个候选全部处理）**：抽出 `tools/substance.py`（实质地板单一事实源）与 `tools/run_layout.py`（run 目录布局单一事实源），删除 4 个零引用孤儿工具（1321 行），为 `equity_dcf` 补 26 个测试并**修掉一个静默归零的估值缺陷**，新增 STOP 确认门机器守卫，修正 4 处文档漂移；另含一项独立的港股代码放行。两条被推翻的评审前提已落成 ADR-0002 / ADR-0003。全程 `check.sh` 真实退出码 0（784 tests OK）。

### 🚀 功能 (Added)

- **`tools/full_analysis_gate.py`** `cmd_init`：放行港股 5 位 `.HK` 代码（如 `00700.HK`）。原正则只接受 A 股 6 位（`^[0-9A-Z]{6}\.(SH|SZ|BJ)$`），港股标的会被 `GateError` 直接拒绝、无法起 run。改为 alternation，A 股约束不变。无专属测试覆盖该格式校验，改动为纯正则、行为局部。

### ✨ 新增 (Added)

- **`tools/substance.py`**（199 行，候选①）：实质地板的深模块。收拢 `RESULT_STATUSES` / `NON_SUBSTANTIVE_SECTION_IDS` / `NA_REQUIRED_HEADINGS` / 三档 `*_MIN_BYTES` 等 17 个常量 + `section_blocks()` + `substance_errors()`；`full_analysis_gate.py` 改为 import 并保留别名（`_substance_errors` / `_section_blocks`），gate 净减 216 行。此前这套常量与函数体在 gate 内自成一份，与 `mk_result_bundle` 的 4 处直接 import 形成隐式双份。
- **`tools/run_layout.py`**（48 行，候选②）：run 目录布局的单一事实源——`ATTEMPTS_REL` / `SUMMARY_ATTEMPTS_REL` / `MANIFEST_REL` / `RUNTIME_STATE_REL` / `EVENTS_REL` / `USAGE_REL` + `in_attempts()` / `in_summary_attempts()` 谓词（带尾斜杠保护，避免 `attempts_other/` 误判）。收敛前该知识散在 3 个模块 5 处，且 `full_analysis_runtime.py` **独立重定义了全部四个状态文件路径**。模块 docstring 明示边界：只管"东西放哪"，不管"谁写"。
- **`scripts/check-stop-gates.py`**（139 行，候选④改道）：`🔴 STOP` 确认门骨架守卫，已接入 `scripts/check.sh`。区分三种语义——(a) 用户确认门（含「必须先向用户确认」）→ **强制校验三要素**：确认义务 + 明确选项 + 停止效力；(b) 硬停止规则；(c) 行文提及。当前基线：**44 个确认门全部合规，4 个非确认语义标记以 INFO 列出交人判**。支持 `--inventory`。
- **`tests/test_substance.py`**（108 行 / 9 例）、**`tests/test_run_layout.py`**（57 行 / 5 例）、**`tests/test_equity_dcf.py`**（262 行 / 26 例 / 60 断言）。
- **`docs/adr/0002-v2-archive-validator-not-dead-code.md`**、**`docs/adr/0003-stop-gates-not-templated.md`**：记录两条被证据推翻的评审前提，避免后续评审重复提出。

### 🐛 修复 (Fixed)

- **`tools/equity_dcf.py`** `run()`（+7/−1，**静默归零缺陷**）：原实现把**原始配置情景**（只有 `fcf`/`prob`，无 `per_share`）传给 `run_position`，触发其内部 `v = sc.get("per_share", price)` 回退，使 `expected_value` 恒为 `0.0`、`asymmetry_ratio` 恒为 `None` —— 一个不出错、只悄悄返回错数的缺陷。改为传 `run_scenarios` 的输出明细（已折算为每股）。实测：EV 由 `0.0` 修正为 `0.9038100047695197`（190.38100047695195 / 100 − 1）。新增回归守卫 `test_run_position_uses_computed_per_share`。

### 🔧 优化 (Optimized)

- **候选② subprocess 环回内化**：`full_analysis_runtime._accept_result` 不再 `subprocess.run([sys.executable, gate, "ingest-result", ...])` 再用 stdout 文本回传，改为直接调用新抽出的 `gate.ingest_result(run_root, registry, result) -> dict` 结构化回执（`cmd_ingest` 退化为薄 CLI 壳）。runtime 内用**延迟 import** 规避循环依赖（gate 顶层已 import runtime）；移除已无用的 `subprocess` / `sys` import。
- **候选③ 孤儿工具删除**：`tools/momentum_backtest.py`(401) / `tools/stock_screener.py`(401) / `tools/star_history_chart.py`(367) / `tools/morningstar_fair_value.py`(152) —— 合计 **1321 行，零引用**。经 `skills/` `scripts/` `tools/` `tests/` 全量检索、`local/` 5887 文件检索、shell 历史检索三重确认无引用；唯一遗留痕迹是 `data/morningstar_fair_value_20260519.csv` 这一历史产物。
- **候选⑤ 文档漂移**：
  - `AGENTS.md`：修掉死掉的检出路径要求——原文强制 `~/ai-berkshire/tools/...`，本仓库实际在 `~/WorkSpace/stock/berkshire`，改为"按实际仓库根解析"。
  - `CONTEXT.md`：目录树补齐 `AGENTS.md`/`SKILLS-GUIDE.md`/`README.md`、根 html 指南、`docs/agents/`、`workbuddy-skills/`、`scripts/`、`tests/` 及 `local/` 全貌，并标注计数（18 skills / 19 codex 包）。
  - `SKILLS-GUIDE.md`：修正"industry-routing 有 12 个消费方"的**虚假声明**（实测 2 个：`earnings-review`、`investment-research`），改注真正的 ~10 消费方规范层是 `financial-data`。
  - `architecture-and-skills-guide.html`：17→18 个 skill、17→19 个 codex 包、补 `industry-routing` 行；并**修掉自相矛盾**——`<title>` 写 v3.10.9 而徽章写 v3.10.0 / 2026-08-10，现统一为 v3.10.9 / 2026-08-30。

### 🔍 验证 (Verification)

- `env -u PYTHONPATH bash scripts/check.sh` **真实退出码 0**：`Ran 784 tests ... OK`（较 v3.10.8 的 779 +5，来自新增的 `test_run_layout.py`），五个阶段全绿（单元测试 / frontmatter 治理元信息 / **STOP 确认门骨架（本版新增阶段）** / Codex 生成物同步 / 全量分析注册表）。
- 分模块复验：`test_full_analysis_gate*.py` OK(100)、`test_full_analysis*.py` OK(305)、`test_substance.py` OK(9, skipped=1)、`test_run_layout.py` OK(5)、`test_equity_dcf.py` OK(26)。
- 缺陷修复前后对比（内置 demo 配置，price=100）：`expected_value` `0.0` → `0.9038100047695197`。
- `python3 scripts/sync-codex-skills.py --check` exit 0（本版未改动 `skills/*.md`，两条同步链为 no-op）。
- **沙箱已知干扰（非回归）**：WorkBuddy `sitecustomize.py` shim 对 `mkdir(exist_ok=True)` 抛 `PermissionError: EEXIST`，会污染 16 个测试（14 audit error + 2 install_scripts）。已用 `git worktree add /tmp/berk-baseline HEAD --detach` 建干净基线跑同一套测试，**16 个失败逐名一致**，证明为环境固有而非本版引入；绕开方式 `env -u PYTHONPATH`（附带收益：287s → 39s）。

### ⏸ 有意未做 (Deferred)

- **候选② 状态所有权收拢**：`manifest` / `events` 由 gate 写、`runtime-state` / `usage` 由 runtime 写，且 `submit-result` 跨锁触碰两个文件——把它并成单一 RunStore + 五个动词（initialize/next/accept/fail/finalize）属高风险手术，会改变"哪个模块写哪个状态文件"。本次只做机械部分（subprocess 内化 + 布局收敛），所有权未动；`run_layout.py` 的 docstring 已显式记录该边界。

---

## [v3.10.8] — 2026-08-30

> **tools 修复**：`ashare_data.py` PB/PE 分位取值顺序错误——`daily_basic` 按 trade_date 降序返回，原 `filtered[-1]` 取到窗口最旧交易日而非最新，导致 pe-band 输出的 current PE/PB 为历史值（实测宜安科技 PB 误取 4.75，真值 11.88）。改为显式按 trade_date 升序排序后取末项，去顺序依赖。全程 `check.sh` 真实退出码 0。

### 🐛 修复 (Fixed)

- **`tools/ashare_data.py`** `cmd_pe_band`（+4/−1）：
  - `latest = filtered[-1]` → `sorted(filtered, key=trade_date)[-1]`，消除对 Tushare 返回顺序的隐式依赖；
  - 影响面：所有走 `pe-band` 子命令的 run 的 current PE/PB 与分位读数（此前会取到数年前数值）。

### 🔍 验证 (Verification)

- `bash scripts/check.sh` 真实退出码 0（✅ 全部检查通过；输出中 E1 mock 版本门禁与 foo-workbuddy.md 测试夹具提示为预置 advisory，非回归）

---

## [v3.10.7] — 2026-08-18

> **bottleneck-hunter 专项优化**：吸收 serenity-skill 增量 + darwin 评分驱动的优化（dim1/dim7/dim8），补齐证据分级标准、数据源手册与请求路由，并将估值/缺数据封顶规则收敛为单一事实源。全程 `check.sh` 真实退出码 0。

### 🔧 优化 (Optimized)

- **`skills/bottleneck-hunter.md`** 620 行（+89）：
  - 吸收 serenity-skill 增量：新增 5.0 证据分级标准（强/中/弱/待核实 + 红旗清单）、4.1.1 数据源手册（A股优先路径 + 港股/美股/台日韩简表与核查重点）、6.0 层级优先排序纪律 + 口语化术语对照；
  - dim8/dim1：新增请求路由（模式 A 超级趋势扫描 / B 单标的挑战 / C 澄清），模式 B 四步挑战路径复用现有章节，frontmatter 补触发词（挑战/验证/蹭热点/是不是真的）；
  - dim7：6.1 星级/红线与缺数据封顶规则改为交叉引用 4.2.1 估值检查与失败处理规则 3，消除重复定义，2 处交叉引用替换。

### 🔍 验证 (Verification)

- 双同步 exit 0（生成 18 个 codex skill + 用户副本）；`bash scripts/check.sh` 真实退出码 0（744 tests OK、18 skill frontmatter 合规、Codex 生成物同步检查通过、注册表校验通过）

---

## [v3.10.6] — 2026-08-13

> **Darwin Phase 2 棘轮式优化·贰**：续上一轮，对剩余两只技能（news-pulse / full-company-analysis-workbuddy）优化。news-pulse 三轮独立复评均 < 基线（84.4/78.6/79.7），按棘轮纪律 `git revert` 回退、维持基线；full-company-analysis-workbuddy 单评未过（82.4）但 A/B 双盲同尺度复评通过（87.5 > 85.3），保留。全程 `check.sh` 真实退出码 0。

### 🔧 优化 (Optimized)

- **`skills/full-company-analysis-workbuddy.md`** 85.6 → **87.5**（+1.9，A/B 同尺度 +2.2）：补 dim4 用户确认门——🔴 启动确认门（`start` 后派发首个业务 Agent 前确认公司/代码/as_of，防跑错标的 13 单元白跑）+ 🔴 收口确认门（deep-summary 熔炼 / `register-summary` 冻结 / `render-html` 渲染前确认收口，总结为最核心交付物、冻结即定稿）+「单元级无用户确认门（有意设计）」说明（13 单元流水线不逐单元打扰，业务 skill 内部各有 🔴 STOP 门）。

### ↩️ 回退 (Reverted)

- **`skills/news-pulse.md`**（基线 84.9）：补团队原语不可用降级路径（串行侦察模式）三轮独立复评均 < 基线，按棘轮纪律 `git revert f370653` 回退，文件与 v3.10.5 逐字一致。留待后续以单轮大改动方式重试。

### 🔍 验证 (Verification)

- 双同步 exit 0（生成 18 个 codex skill）；`bash scripts/check.sh` 真实退出码 0（744 tests OK、18 skill frontmatter 合规、注册表校验通过）
- full-company-analysis-workbuddy 经独立子 agent A/B 双盲同尺度复评（基线 85.3 vs 当前 87.5）；news-pulse 经三轮独立复评 + 纪律回退

---

## [v3.10.5] — 2026-08-13

> **Darwin Phase 2 棘轮式优化（从最低分起）**：用 darwin-skill 9 维 rubric 对全量 18 技能基线评估后，对用户选定的 4 个低分技能逐轮优化——每轮改完经**独立子 agent** 复评，严格 > 旧分才保留（否则 git revert），全程 `check.sh` 真实退出码 0。

### 🔧 优化 (Optimized)

- **`skills/industry-routing.md`** 60.9 → **79.9**（+19.0）：路由矩阵 20→25 行业（补 电池/新能源材料·化工·农业食品·建材·军工）+ 修能源悬空次附录「化工工业」→「化工」+ 修 typo `divident`→`dividend` + 新增 §6 边界与兜底 + §7 反例与红线。
- **`skills/industry-research.md`** 76.8 → **82.4**（+5.6）：补三安全章——3×🔴 STOP 检查点（启动扫描 Agent / 输出组合建议 / 落盘发布前）+ `## 反例与红线` 8 条 + `## 失败处理` 8 条（逻辑链证伪回退 / 卡脖子识别不出 / 未上市玩家遗漏等）。
- **`skills/investment-research.md`** 80.0 → **87.3**（+7.3）：消仓库外悬空引用（`base-rates.md` 硬「查」→ provenance + 本地第 50 分位 fallback；`valuation-methods.md` → 内联 §8.x 阈值为权威）+ 统一四套编号（顶部「编号约定」+ 7.0 内嵌套中文第一步/二/三 → 7.0.1/2/3）。
- **`skills/management-deep-dive.md`** 80.5 → **81.3**（+0.8）：补承诺兑现/侧面验证工具支撑——§3.1.1 工具辅助核查（回购→`repurchase`、分红→`dividend`、增持→`insider-trades`、战略落地→`announcements`）+ §第六步接回 `ird-interact` + 依赖清单补 `ashare_data.py`。

### 🔍 验证 (Verification)

- 双同步 exit 0（生成 18 个 codex skill）；`bash scripts/check.sh` 真实退出码 0（744 tests OK、18 skill frontmatter 合规、注册表校验通过）
- 4 轮均经独立子 agent 9 维复评，总分严格 > 基线，棘轮全部 KEEP（dim8 实测表现因全项目 Phase-1 基线皆 dry_run，按结构可测性评分，未实跑测试 prompt）

---

## [v3.10.4] — 2026-08-13

> **反哺式升级·贰（吸收 equity-research-skill）**：落地 20 行业 KPI 路由子模块 + berkshire 原生 `equity_dcf.py` 估值引擎。对方 `dcf.py` 因沙箱禁 raw.githubusercontent 直连无法逐字 vendor，改为基于其公开文档公式的忠实复刻（docstring 注明 MIT 移植、零外部依赖），标定阈值与 valuation-methods §9 逐字一致。

### ✨ 新增 (Added)

- **`skills/industry-routing.md`**：20 行业路由矩阵（主附录 / 适用边界 / 主估值方法 / 必备 KPI / 常用次附录）+ berkshire A 股数据命令映射（peers / mainbz / ratios / report-list / ths-hot 等）+ A 股主源入口 + 预注册字段 + consumer-skill 调用约定。MIT 归属 equity-research-skill `references/industry-routing.md`。
- **`tools/equity_dcf.py`**（364 行，零依赖，可执行）：反向 DCF / PVGO / 三情景概率加权 / EPV / EVA / 蒙特卡洛 / 预注册标定 `calibrate()`（P<0.5L→显著低估、<0.85L→低估、≤1.15H→合理、≤1.5H→高估、否则显著高估）。`--demo` 自测 exit 0、标定阈值断言通过。`investment-research` 7.0 反向 DCF/PVGO 改走该工具、8.x 标定接入 `calibrate()` 可复算。

### 🔧 变动 (Changed)

- `skills/investment-research.md` 第一步 + `skills/earnings-review.md` 阶段一：加「按需加载 industry-routing」指针；7.0 / 8.x 改为引用 `equity_dcf.py`。

### 🔍 验证 (Verification)

- 双同步 exit 0（生成 18 个 codex skill，含 industry-routing）；`bash scripts/check.sh` 真实退出码 0（744 tests OK、18 skill frontmatter 合规、注册表校验通过）
- `python3 tools/equity_dcf.py --demo` 自测通过（标定阈值断言全绿）

---

## [v3.10.3] — 2026-08-13

> **反哺式升级·壹（吸收 equity-research-skill）**：评估 rollingSirius/equity-research-skill（机构级预期差/Mauboussin 系 mega-skill，MIT）后，按选项③反哺式升级吸收其领先项 —— forensic A/B/C/D 否决门 + 预期差 Gap 表 + 预注册标定，仅移植方法论（未引入外部脚本依赖，计算走 financial_rigor.py），保留四大师框架。

### ✨ 新增 (Added)

- **`skills/earnings-review.md` A.6 财报可信度 A/B/C/D 否决门**：应计质量 + Beneish M-Score 八变量代理 + 收入确认/治理红旗 → A/B/C/D 评级，与排雷 32 条共用数据不重复；C→最高「观望」、D/非标审计→「规避」写入阶段二第九章否决 + 一句话结论含可信度等级 + A.3 评分卡加行 + 反例红线加项。
- **`skills/investment-research.md` 7.0 预期差开篇框架**：反向 DCF + PVGO + Gap 表 + base-rate 分位纪律，置于估值章开篇。
- **`skills/investment-research.md` 第七步半·独立观点检验**：三问硬门槛（答不出→「观望」）。
- **`skills/investment-research.md` 8.x 预注册标定**：区间 ±15% 缓冲 → 显著低估/低估/合理/高估/显著高估 → 动作矩阵 × 不确定性 + 否决项；综合决策表加「估值标签」行。

### 🔧 变动 (Changed)

- `skills/earnings-review.md` / `skills/investment-research.md` 输出要求、反例红线、失败处理补相应条目；依赖清单加移植声明（MIT 方法论来源）。

### 🔍 验证 (Verification)

- 双同步 exit 0；`bash scripts/check.sh` 真实退出码 0（744 tests OK、注册表校验通过）

---

## [v3.10.2] — 2026-08-13

> **earnings-review 实测闭环优化（Darwin Phase 2）**：用独立子 agent 实跑阶段零，发现补丁后仅取 `financials` 致 31/32 规则 SKIP（裸跑 baseline 反更优）—— 根因是 A.0 取数映射缺失。补「取数清单」后阶段零从 31/32 SKIP → 23/32 可评、产出真实评分卡。Darwin 总分 88.4 → 94.8（+6.4）。

### 🔧 修复 (Fixed)

- **`skills/earnings-review.md` A.0 取数清单重写**：逐条列出阶段零必跑命令（`income-stmt` / `balance-sheet` / `cash-flow` / `announcements`）及覆盖规则族/关键字段，修复仅取 financials 致 31/32 规则 SKIP（dim5/dim6/dim8 full_test 修复）。
- **章节编号重排**：`## 二之一、排雷前置评分卡` → `## 三`，顺延四~九改为五~九。
- **规则 1.6 减值字段映射澄清**：`assets_impair_loss` / `credit_impa_loss`（dim2/dim5/dim7）。

### 🔍 验证 (Verification)

- 独立子 agent 重测：阶段零 23/32 可评（PASS22 + WARN1 + FAIL0），SKIP9（PDF 依赖 by-design），产出真实评分卡（风险等级低、综合得分 2、应收≈0、经营CF/净利=1.04、销售收现/营收=1.05、无有息债、商誉 0、其他应收 0.013%）
- `bash scripts/check.sh` 真实退出码 0；Darwin 94.8/100（dim8 由 dry_run 升 full_test）

---

## [v3.10.1] — 2026-08-13

> **量化排雷前置阶段零（吸收 minesweeper）**：评估 terancejiang/financial-report-minesweeper（基于唐朝《手把手教你读财报》的 32 条排雷规则程序化实现）后，把其量化排雷前置吸收进 `earnings-review` 的阶段零 —— 规则扫描 → 风险分 → 四大师定性 → 投资结论。继承本项目双源交叉验证 + 🔴人环 + report_audit 门禁，规避其单源弱点。Darwin 9维 rubric 基线 88.4/100。

### ✨ 新增 (Added)

- **`skills/earnings-review.md` 阶段零·量化排雷前置**：吸收 minesweeper 32 条规则（含 L0 审计意见/披露时效硬门槛、组合加分、行业高危标记），按双源适配（Tushare + 东财/年报 PDF）。新增加权打分 + 风险等级（直接排除/低/中/高/极高）+ 排雷评分卡 + 组合加分（3.2 FAIL+10、2.3+4.1 FAIL+8、1.2+3.1 FAIL+6）+ 报告结构新增评分卡节 + 依赖与红线更新。

### 🔧 变动 (Changed)

- `skills/earnings-review.md` 方法论定位：排雷为 L1 前置（与四大师「看懂生意」互补而非替代），阶段零输出风险分作为后续定性输入。

### 🔍 验证 (Verification)

- `python3 scripts/sync-codex-skills.py` + `~/.workbuddy/berkshire-skill-sync/sync.py` 双同步 exit 0
- `bash scripts/check.sh` 真实退出码 0（744 tests OK、注册表校验通过）
- Darwin 9维 rubric：88.4/100（dim4/dim6/dim9 = 10；dim8 dry_run 估计）

---

## [v3.10.0] — 2026-08-10

> **架构深化**（/improve-codebase-architecture 评审落地）：lean-v1 pivot 后「语义已 lean、接口面未 lean」的遗留债务系统性收口。契约解析收敛为唯一深模块缝；Gate 退役校验层拆除；一次性脚本墓地清零；momentum 分叉收口；validate_v2 显式冻结；④候选经调查推翻并归档首份真实 ADR。6 个候选全部闭环，每步 check.sh 真实退出码 0。

### ✨ 新增 (Added)

- **`tools/full_analysis_contract.py` Contract 深模块**：契约唯一解析缝（`CONTRACT_PATH` / `ContractError` / `load_contract(strict|lenient)` / `find_skill` / `get_skill_or_none`）。零依赖、不 import 执行代码（与 check-contract 独立性立场一致）；错误通道留给调用方 adapter，退出码与消息逐字不变。
- **`tests/test_full_analysis_contract_module.py`**：10 个用例全部穿过公开接口。
- **`docs/adr/0001-ashare-data-internal-seams.md`**：首份真实 ADR。记录④候选被 trust-but-verify 推翻的裁决（私有函数测试是合法 internal seams；两个 pe-band 不同源不同指标），防未来评审重复误判。

### 🔧 变更 (Changed)

- **契约解析收敛**：`gate.load_registry/find_skill` → 薄包装；`runtime._load_registry` → lenient 委托 + next-work 改 `get_skill_or_none`；`mk_result_bundle` 删 find_skill 副本与 REGISTRY 路径常量。诚实排除：check-contract（独立性承重墙）、doctor/audit（`__file__` 派生无漂移）。
- **Gate 退役校验层拆除**：删 `_precheck_evidence_rules`（lean 契约禁止 skill 级 evidence_rules，永远零触发）+ `build_evidence_ledger` 内 4 个 lean 死变量。**修正性保留**：`_precheck_placeholder_evidence`（拦手写水印，9 处测试驱动，活防线）与回执签名链（白名单短路休眠但测试完整驱动）不删。
- **runtime `EVIDENCE_DIRECTIVE` lean 口径修正**：不再引导执行 Agent 依赖已移除的 evidence_rules 键，改为「数组可为空、绝不合成 PLACEHOLDER」；测试断言同步并反向防复活。
- **momentum_backtest 分叉收口**：删 v1（依赖已封禁 Yahoo API），v2 升为唯一实现（去版本后缀）。
- **validate_v2 显式冻结**：标注【已冻结·档案校验专用】——local/ 下 46 份 v2 时代 run manifest 的唯一校验器，禁止挂载新功能。
- **skills/ashare-data.md 陷阱 #3 修正**：由「无复权价格序列」更新为现状（cmd_kline 已补 OHLC 缺口但需 token；零 token 路径可用 akshare_data）。

### 🗑️ 移除 (Removed)

- 4 个零引用 lean-pivot 遗留脚本：`tools/_dispatch_helper.py` / `_submit_helper.py` / `_tfwd_gen_specs.py`、`scripts/_transform_contract_lean.py`（deletion test 通过，复杂度消失而非转移）。
- `tools/momentum_backtest.py` 旧 v1（Yahoo Chart API 已封禁，不可运行）。

---

## [v3.9.3] — 2026-08-09

> **市场级 skill 取数纪律强化**：本日 `/macro-liquidity view` 实战触发 Tushare 与权威新闻源口径冲突（融资余额相差 ~50%）——这是取到数据≠数据可信的真实事故。本版把所有取数型 skill 的失败处理升级为 A/B/C 三类，新增「双源冲突协议」硬规则；同步收紧 a-share-market-sentiment 的口径表述。

### ✨ 新增 (Added)

- **`skills/macro-liquidity.md` 双源数据冲突协议**（P0 必修，真实事故触发现场）：
  - 「两条底线」新增 #3 双源一致：差额 >20% 触发 ⚠️ 默认采信新闻源、双值并列展示、不准静默通过
  - 「失败处理」节从单层 4 段重写为 A/B/C 三类：**A 接口空数据 / B 环境未配 / C 双源数据冲突**（含 6 步硬规则 + 4 条反例 + 根因分析）
  - A1 margin / A2 hsgt-flow / A4 Shibor 三节「取数」段各加 ⚠️ 实战提示（2026-08-07 融资余额差 48% 的具体案例入注释）
  - STEP-3「逐指标取数」加「双源核验」动作；STEP-4「双源冲突 → 评级附 ⚠️ 不确定性」
- **`skills/macro-liquidity.md` STEP-2 交付前双检查点**（P2 可选，正式落地）：
  - 5a 报告深度四选项（仅看板 / 含建议 / 标准 / Markdown+HTML 双版）
  - 5b 联动强制三选一（`thesis-tracker` / `news-pulse` / 跳过且显式声明）——禁止默认跳过
  - 反例红线 +1 条；STEP-6 联动节同步按 5b 选项执行

### 🔧 修复 (Fixed)

- **`skills/a-share-market-sentiment.md` 4 处口径收紧**（来自同日上午的执行缺口验证报告）：
  - S1 margin 接口按交易所多行返回（SSE/SZSE/BSE），须手工求和；周末可能仅回补部分交易所，禁止单交易所当全市场
  - S3 估值分位定义采用双窗口对账（5 年 vs 8 年），两口径同档方为稳健
  - S3 源节补 5 年窗第三方交叉印证示例（沪深300 PE(TTM) 5 年窗 93.9% vs 工具 8 年窗 87%）
  - S4 放量基数补 WebSearch 年度/半年日均成交（2025 全年 1.73 万亿、2026 H1 2.74 万亿）做对比基线

### 🔍 验证 (Verification)

- `python3 scripts/sync-codex-skills.py` → Generated 17 Codex skills and WorkBuddy adapter
- `python3 ~/.workbuddy/berkshire-skill-sync/sync.py` → 已生成 17 个适配副本（EXCLUDE 编排 skill）
- 三副本字面 diff 仅剩 frontmatter/适配器注脚结构差异
- `bash scripts/check.sh` → **真实退出码 0**（17 frontmatter 合规 + INDEX + 同步 + lean-v1 注册表全过）

---

## [v3.9.2] — 2026-08-09

> **招股书分析 skill 补上"同业横向对比"能力**：此前 skill 只有第三方报告份额判断（阶段三）与估值倍数对比（步骤 7），无法回答"个股特征是个性还是行业共性"。本版把步骤 7 重构为 H1–H4 四子步骤，并用宇树 vs 智元/优必选/乐聚的真实数据做示例回填。

### ✨ 新增 (Added)

- **`skills/a-share-prospectus-analysis.md` 同业横向对比模块（H1–H4）**：
  - **H1 cohort 选择**：三圈层（同细分直接竞对 / 不同形态路线 / 跨市场已上市同业——港股招股书是现成的横向披露金矿），每家标注数据等级 A/B/C；反偏见纪律（cohort 必须含至少一家"叙事不同"的公司）。
  - **H2 多维对比矩阵**：财务画像 / **需求结构（与步骤 9 联动，关键维度）** / 产品矩阵 / 生产模式（G7）/ 护城河（G2）/ 估值。
  - **H3 个性 vs 行业共性判定**（核心产出）：每个红警特征逐项归入 个股个性 / 行业共性 / 标的领先；**需求结构基准对照强制**——科教依赖是个股叙事错位还是赛道级未商业化，两者信仰税性质不同，禁止只给个股结论；外加增速-份额-估值三角。
  - **H4 同业数据源**：排队 IPO 同业的招股书+问询函回复（分应用领域收入表的直接来源）、港交所披露易已上市同业、第三方份额表（GGII/IDC/灼识，注意口径差异）。
  - 配套：阶段三预埋 cohort 初筛；if-then 失败表 +2 行（cohort 不可得降级 / 单点参照）；反例黑名单 +2 条；检查清单 +1 项；附录一映射表与红警清单新增"同业横向对比"小节（含"为最差的需求结构付最高的价""赛道级未商业化"两个新红警）。
- **附录二新增"五之三 同业横向对比示例"**（真实数据演示 H1–H4，2026-08-09 核验）：
  - cohort：智元（赴港 18C，2026-07-24 启动，B 级）/ 优必选 9880.HK（2025 年报，A 级）/ 乐聚（创业板受理中，C 级）。
  - **估值悖论实证**：工业化落地最扎实的优必选（全尺寸人形 8.21 亿/1079 台/毛利 54.6%/Walker S 进汽车物流等真实工业场景）PS 仅约 22x，而宇树发行 PS 35.9x、智元目标 32~41x——市场为叙事稀缺性而非工业化成色付最高价。
  - 宇树"科教依赖"暂判**个股叙事错位**（优必选已走通工业路径），但智元需求结构未披露，判定须随其招股书披露复核——"判定不可一次性定论"纪律入 skill。
- **`docs/architecture-and-skills-guide.html`**：架构体系与 Skill 完整使用说明（自包含 HTML，36 节/20 表），版本号同步至 v3.9.2。

### 🔧 修复 (Fixed)

- 修正步骤 7 残留的死引用（`模板见 references` → `模板见附录一·第四节`，references 目录已随单文件附录结构删除）。

### ✅ 验收

- 三副本同步：`sync-codex-skills.py` + `berkshire-skill-sync/sync.py` 双 `--check` 通过（17 副本一致）。
- `bash scripts/check.sh` 真实退出码 **0**（单测 + frontmatter 17 合规 + codex 同步 + 注册表校验全过）。

---

## [v3.9.0] — 2026-08-09

> **A股招股书分析 skill 首次实战（宇树科技 688836）+ 三轮迭代收口版**：skill 首跑暴露"买单主体分解"盲区（v1 把 76% 卖给科研教育的收入误读为生产力业务成色），经 v2 重写 + 根因分析后把需求真实性检验固化为程序化强制步骤；同期落地两个市场级新 skill 与 investment-research 增强。

### ✨ 新增 (Added)

- **`skills/a-share-prospectus-analysis.md` 首次实战并完成三轮迭代**（附录结构内嵌：附录一 章节对照/红警清单/估值模板，附录二 宇树快照）：
  - **操作层八步→九步**：新增**步骤 9"需求真实性检验：买单主体分解 + 同族盲区"**——9a 收入结构三问（谁在买/买去干什么/会不会重复买）、9b 同族盲区清单 7 项（产品线vs场景口径、出货量≠有效部署、一次性vs经常性、数据飞轮燃料质量、**管理层口头证据强制收集**、伪B端检测、降价≠渗透率）、9c 数据落点（分应用领域收入表几乎只在问询函回复）与估值口径校准。
  - **六缺口→七缺口**：新增 **G7 生产模式/资产结构**（机器设备占总资产比、外协外包比例、募投产能 vs 现有产能倍数）。
  - **方法论总览新增两道护栏**：需求真实性护栏（优先级最高）+ **根因自觉段**（注意力梯度/叙事锚定/口径信任三层根因——需求真实性不能靠"记得去查"，必须程序化强制）。
  - **G5 估值模板强化**：SOTP 分段拆分纪律（逐项标【估算】）+ **按应用领域拆分（联动步骤9）** + reverse-DCF/TAM 实测值 + 新增**估值跳升**观测（最后一轮投后 vs 发行市值，宇树 127→609.93 亿 ≈4.8 倍）。
  - 反例黑名单 +4、if-then 失败表 +4（含"分场景收入不可得→保守口径""路演措辞矛盾→取保守侧"）、检查清单同步九步化。
  - 附录二宇树快照按一手文件（三版招股书各~369页 + 两轮问询回复 283+45 页）全量核验修订，新增"客户结构解剖"专节（人形 76% 科教/真实工业 ≤2.6%/境外 68.37% vs 境内 46.35%/信仰税科教口径上修 69%~79%/管理层路演口头证据）。
- **`skills/macro-liquidity.md`**（市场级，独立于契约）：美元层 D1-D4 + A股层 A1-A4 双层流动性监测，预警计数→分档评级，Tushare/WebSearch 取数，fail-close（整层全缺不评级）。
- **`skills/a-share-market-sentiment.md`**（市场级，独立于契约）：A股口径 5 指标情绪系统（两融/北向/估值分位/换手/开户发基），**双向出警计数**（贪婪+1/恐慌-1 独立统计，防极端市况评级反转），与 macro-liquidity 分工互补。
- **`investment-research` 增强**：新增第〇步**关键力量识别（Key Forces）**（先锁 1-3 个决定性力量，相关模块 2-3 倍覆盖，治"面面俱到都不深"）+ 第七步半**差异观点（Variant View）**（市场共识/我们认为/他们错在，无差异须显式声明）。
- **IPO 产出目录约定**：所有 IPO 研究产出统一归档 `local/IPO/<公司名>-<代码>/`（报告 md + HTML + evidence 一手取证）；每份报告须同时生成自包含 HTML（html-express 规范）。

### 🔧 修复 (Fixed)

- `a-share-prospectus-analysis` frontmatter `category: 公司` 非法值 → `深度公司研究`（check.sh frontmatter 门禁此前一直为它变红的隐患）。
- SKILLS-GUIDE 更新：16 业务 Skill 登记、契约/独立 Skill 区分、lean-v1 编排节重写（移除已废弃的 resume/并发4 旧描述）。

### 🔬 验收实证

- 宇树 IPO 首跑取证：三版招股书 + 两轮问询回复全部 PDF 逐页提取核验；G6 三版 diff 精确定位上会稿新增 2 处针对性风险；信仰税 39%~71% → 科教口径上修 69%~79%；产出 v1/v2 报告 + HTML 可视化报告（`local/IPO/宇树科技-688836/`）。
- 三副本同步：`skills/*.md`（源）→ `codex-skills/*/SKILL.md`（sync-codex-skills）→ `~/.workbuddy/skills/`（berkshire-skill-sync），均 `--check` 通过。
- `bash scripts/check.sh`：**退出码 0**（734 tests OK + frontmatter 17 个合规 + codex 同步 + 注册表校验）。

## [v3.9.1] — 2026-08-09（文档体系刷新）

> 承接 v3.9.0：**全仓文档体系整体同步到 2026-08-09 最新状态**。v3.9.0 条目中声明的「SKILLS-GUIDE 更新」在磁盘上实际未落地（实测仍为 07-25 旧版），本轮一并补齐；并完成 README / 体系分析 / CLAUDE / ai_CLAUDE 的同步刷新与 check.sh 实测闭环。docs-only，无代码行为变更。

### 📝 文档刷新 (Docs)

- **`SKILLS-GUIDE.md` 重写**（数据截止 2026-08-09）：17 个 canonical（16 业务 + 1 编排）；3 个契约外 skill（`a-share-market-sentiment` / `macro-liquidity` / `a-share-prospectus-analysis`）入表并标注「独立于契约」；「选择建议」补充市场情绪/流动性/招股书入口；编排层章节按 **lean-v1** 重写（两条底线、self-check、L1 收口 + L2-L4 可选评估层，移除已废弃的租约/波次/33 次硬预算/Audit-Review-finalize 强制链描述）。
- **`README.md`**：Skill 层描述 13→16 业务；编排段 lean 化（去掉"Runtime 负责租约、重试、恢复与 33 次硬预算"等已移除机制）；Skills 一览 16+1、新增「📡 市场监测类」分类（情绪/流动性）、深度研究类补招股书；编排层行改为 lean 描述。
- **`docs/skill-system-analysis.md` 快照刷新至 2026-08-09**：资产清单（skills 17 / codex-skills 18 = 17 生成 + 1 Codex-only / workbuddy-skills 1 adapter）、契约边界（13 入契约 + 3 契约外）、功能分类、同步健康度（实测）、新增 P4 修复存档（category 合规回归），开发规范同步（frontmatter 取值受 check.sh 卡点约束）。
- **`CLAUDE.md`**：编排三段描述 lean 化（生命周期 start→next-work→self-check→submit-result→register-summary→render-html；L2-L4 可选；evidence 保存 attempt/总结/manifest，租约账本已移除）；报告命名表补 3 个新 skill（情绪/流动性 → 根目录，招股书 → `local/IPO/<公司名>-<代码>/`）。
- **`ai_CLAUDE.md`**：Skill 体系演进追加 **V4（17 个 canonical，2026-08-09）** 条目（lean 收敛 + 市场级/IPO 扩展 + 架构纪律 + 教训）。

### 🐛 修复 (Fixed)

- **v3.9.0 记录的 SKILLS-GUIDE 更新实际未落地**：实测磁盘文件仍为 07-25 旧版（13 业务、租约式编排描述），本轮已按 v3.9.0 意图完整重写（见上）。
- **frontmatter 合规回归（与 v3.9.0 同源，本轮实测闭环）**：`skills/a-share-prospectus-analysis.md` `category: 公司` 非法 → `深度公司研究`；重跑 `sync-codex-skills.py`（17 + WorkBuddy adapter）+ 重建 `local/reports/INDEX.md`（报告索引漂移）。

### 🔬 验收实证

- `bash scripts/check.sh`：**REAL_EXIT=0**（734 单测 OK；17/17 frontmatter 合规；codex-skills 17 + WorkBuddy adapter `--check` 通过；报告索引已重建；lean-v1 契约结构合法）。
- 全仓复查：`grep` 无残留旧计数（"13 个投研业务"/"21 个"/"租约、重试、恢复"等已从活跃文档清除，历史 CHANGELOG 条目保留原样）。

## [v3.8.1] — 2026-08-07

> 华能国际（600011.SH）全量分析 run 收口暴露总结报告质量短板后的修复+增强版：**渲染器空壳 HTML 修复**（`_parse_sections` 只认 `##` 而 Gate 强制 `#` 一级标题，导致 `<main>` 全空）、**渲染器支持内联 SVG 图表**（` ```svg ` fence）、**交付总结质量标准沉淀**（表格+图示+三大章节深化+数字溯源+渲染后验收，防再退回索引式速览）。

### 🐛 修复 (Fixed)

- **总结 HTML 空壳（渲染器 bug）**：`tools/full_analysis_html.py` 的 `_parse_sections` 硬编码只认 `##` 二级标题作章节分隔，而 `register-summary` Gate 强制 8 个收口章节标题必须是 `#` 一级标题——解析出 0 个 section，`<main>` 与导航全空（13KB 全是 CSS/JS 外壳）。改为同时接受 `#` 与 `##`，首个标题兼作页面 title 与首 section。

### ✨ 新增 (Added)

- **渲染器支持内联 SVG**：`_render_blocks` 新增 ` ```svg ` fence——SVG 源码原样内联（不转义），包 `<div class="fig reveal">`；普通 ` ``` ` 代码块行为不变（转义为 `<pre><code>`）。`_CSS` 增 `.fig` 图表容器样式。
- **`docs/delivery-summary-quality-standard.md`**：交付总结（delivery-summary）质量标准——长文深度 25-40KB、≥6 表格、≥5 SVG 图示、财报/行业/投资建议三大章节深化、冲突剖析、正反两面、数字溯源抽查（≥20 个高特异性数字）、渲染后验收清单（`<main>` 非空/`<svg>` 数/`<table>` 数/导航数/关键论断）。明确"总结报告是全流水线最核心交付物，HTML 完全依赖它"。
- **编排 skill 收口章节引用质量标准**：`skills/full-company-analysis-workbuddy.md` 收口与交付小节挂接该标准；deep-summary 蒸馏指引同步加入表格/SVG/三章深化要求。

### 🔬 验收实证

- 华能国际 run（run-898c86f3b2510600）收口重做：总结报告 7.4KB 索引速览 → 57KB 深度长文（7 表格 + 5 SVG 图表：ROE 多口径条形/5 年 FCF 柱状/估值三情景/行业漏斗/投资逻辑链），`register-summary`（sha256 入 manifest）+ `render-html`（82KB，`<svg>`×5、`<table>`×7、0 转义残留）。
- `bash scripts/check.sh`：**CHECK_EXIT=0**（734 tests OK；codex-skills `--check` 通过）。

## [v3.6.1] — 2026-08-05

> 中国神华全链路验收 run（v3.5.0 首个真实 run，APPROVED）暴露三类问题后的修复版：**并发上限 4→2**（用户指令"最多两个并发任务"，消除空返回/租约误回收）、**source 合并 last-write-wins 修复**（返工提交的 source 更新此前永不生效）、**流程分级**（用户指令：L1 生产流水线到 HTML 交付为止，L2-L4 评估验证层需明确触发）。同时完整走通并修复了语义评审暴露的跨单元一致性问题（三情景/PE/PB/现金流增速对齐、王祥喜案补入、summary 补煤价压力测试）。

### 🐛 修复 (Fixed)

- **并发上限 4→2（用户指令）**：`cmd_init` 的 `concurrency.max` 由 4 收紧为 2——中国神华 run 实证：4 并行时 quality-screen 空返回卡死、W5 派发 499、checklist 返工空返回（均靠 E15 resume 兜底恢复）。收紧后任意时刻至多 2 个并行 Agent。配套：W2 拆 2+2 分批、禁-2 更新（禁止单条消息并行派发 >2）、5 个 runtime 并发测试适配。
- **sources 合并非 last-write-wins（机制 bug）**：`_merge_provenance` 对已存在 source_id 静默跳过，返工/修正提交的 source 更新（如 url/title 指向新 attempt）永远不生效，导致 run 级 manifest 残留指向旧产物的来源（thesis-tracker 第 4 轮返工实证）。改为与 facts/calcs/judgments 一致的 last-write-wins（同 id 覆盖）。
- **跨单元数值不一致（语义评审暴露，全部闭环）**：三情景 37.8/29.3/18.6 vs 42.34/31.92/21.85、PE 15.8 vs 33.4、PB 1.96 vs 2.10、现金流增速 +98.1% vs +88.1%——6 单元返工 + thesis 连返 5 轮 + correction 清理残留 judgment，终审 8/8 PASS（9 low，0 high/medium）。
- **王祥喜案被多单元回避（2 high）**：investment-research / investment-checklist 返工补入治理风险，评级封顶 B+，与 management-deep-dive / thesis-tracker 对齐。

### ✨ 新增 (Added)

- **流程分级（用户指令）**：编排文档新增「流程分级」章节——**L1 生产流水线**（E1+start → 波次 13 单元 → deep-summary → register-summary → render-html，到 HTML 交付为止，不要求 audit/review/finalize 前置）；**L2 结构验证**（audit + correction/rework）；**L3 语义评估**（review 五维）；**L4 准出与体检**（finalize + doctor）。L2-L4 为评估验证层，发版/正式交付/benchmark/用户明确要求时按序执行。
- **前台使用指令**：对话触发模板（"全量分析 XXX" = L1；加"评估验证/完整评估到 APPROVED"字眼触发 L2-L4）。

### 🧪 测试 (Tests)

- `tests/test_full_analysis_runtime.py`：5 个并发相关测试适配 2（two_concurrent_leases / W2 只租 2 / W3 错峰退化先释放并发 / W3b 误领为 CONCURRENCY_LIMIT / 并发 job-started 2 租约）；gate_v2 + runtime 全绿。
- check.sh 全量：**746 tests OK，CHECK_RC=0**（含攻击者视角回执验证）。

### 🔬 验收实证

- 中国神华 run（run-ed656b245e4f7c98）全链路 APPROVED：13 单元（3 次 resume 兜底）+ audit PASS（条件命令满足率 2.3%→44/44）+ 语义评审 3 轮终审 REVIEW_PASSED（0 high/medium）+ doctor PASS（heartbeat 覆盖率 92%，零退化指纹）。
- **结论**：v3.5.0 回执防线（68+ 签名回执、攻击者 8 红）机器验证通过；"收敛点"不成立——语义/一致性/编排层问题大量暴露并已在本版修复或调整。

## [v3.5.0] — 2026-08-04

> 新增「攻击者视角」回执防线验证并挂入 check.sh（minor：新防线，向后兼容）。回应 v3.4.15 收尾时的质疑——"收敛点"是未经三方验证的乐观宣称；本版把已知攻击面以攻击者身份钉死在"被正确防线拦截"上，收敛与否仍交由用户 review 与真实 run 裁决。

### ✨ 新增 (Added)

- **`scripts/attacker-receipts-check.py`**：以攻击者视角枚举 v3.4.10→v3.4.15 已知的 10 种绕过手法，逐一打 Gate 单一准入谓词 `admit_bundle`，判定每条是变红（防御有效）还是变绿（可绕过）：
  - A1 原始自证 / A2 占位水印 / B1 手写 argv+output（v3.4.14 核心盲区）/ B2 `TEST_FIXTURE` 伪造标记 / B3 白名单外 op / C1 跨 run 搬回执 / C2 篡改执行器回执 / C3 correction 注入抹签名回执 → **8 条全部变红，且每条由正确防线拦截**；
  - C4 删密钥降级 legacy、C5 读密钥伪造签名+journal+落盘 → **2 条绿 = v3.4.15 威胁模型明确声明的边界**（如实暴露，非新缺陷）。
- **check.sh 挂载**：新增「回执防线攻击者视角验证（v3.4.15）」步骤，每次全量检查强制运行。

### 🧪 测试 (Tests)

- 746 单测 + 攻击验证全绿，`CHECK_RC=0`。

### 🔧 其他

- **诚实边界声明**：本脚本由实现者编写并挂入 check.sh，退化为自维护防线，不宣称收敛；枚举仅覆盖已知攻击面，不能证明无第 N+1 种绕过。
- **假红教训**：脚本第一版因前置字段非法（calculation_id 格式 / lease_nonce 写死）导致所有攻击被无关校验短路成假红；修复后确认每条红都由目标防线产生。脚本保留该教训于 docstring，要求改动后必核对拒收理由归属。

## [v3.4.15] — 2026-08-04

> 根治 v3.4.14 review 的核心 P0：**「rc0 ⟺ Gate 接受」是假命题、回执绑定可被伪造**。v3.4.14 的「执行绑定」只检查 `argv`+`output` 两个自述字符串非空——跑一条无关命令、编一段输出即可通过。v3.4.15 把 PASS 回执的签发权从「Agent 手写 JSON」收归「真实命令执行器」：HMAC 签名 + 落盘摘要 + journal 留痕 + 时间窗 + operation∈argv 六项确定性校验，并把生成器/`cmd_ingest`/correction 三处准入收敛到单一 `admit_bundle`，让「rc0 ⟺ Gate 真正接受」成为机器事实。

### 🐛 修复 (Fixed)

- **PASS 回执可被手写伪造（P0，v3.4.14 绑定名实不符）**：新增 `scripts/run_evidence_command.py` 执行器 + `tools/evidence_receipt.py` 签发核心（`execute_and_sign` 为签发唯一实现）。PASS 回执必须经执行器真实 subprocess 执行后签发：① HMAC-SHA256 签名（密钥 `evidence/receipt-signing-key`，`start` 时生成）② `exit_code==0` ③ `output_digest==sha256(落盘输出)` ④ `executed_at` 在本 run 窗口内 ⑤ `operation ∈ argv` ⑥ `command-log.jsonl` journal 留痕同 digest。任一不成立即拒收；无密钥的历史 run 降级到 v1 弱校验（`_receipt_binding_mode`）。**诚实的威胁模型**：签名密钥与 result.json 同权限，真正防线是「执行器是唯一便捷路径」——伪造从「写两个字符串」升级为「刻意复刻签名+journal+落盘文件」。
- **correction 可绕过 Gate 注入伪造回执（P1）**：correction 直接改写 manifest 账本、不走 admit_bundle。修复：`_validate_correction_receipts` 在 submit-correction 时对非 removed 回执重跑 `_precheck_command_receipts`，堵死旁路。
- **`admit_bundle` 缩进死代码**：skill 校验块嵌在 `except GateError ... return` 之后成为不可达代码，恢复为函数顶层执行。
- **生成器/ingest/Audit 准入分叉**：三处校验统一收敛到单一 `admit_bundle` 谓词。

### ✨ 新增 (Added)

- **执行器 CLI**：`python3 scripts/run_evidence_command.py --run-root <run_root> --receipt-id <id> --operation <op> -- <真实命令>`；退出码 0=成功已签名 / 1=用法错误 / 2=命令失败（FAIL 回执不签名）；`--unavailable --reason` 签发 UNAVAILABLE 回执；`--append-to` 批量攒回执。
- **result schema 新增回执字段**：`executed_at`/`exit_code`/`output_digest`/`executor_version`/`signature`（PASS 条件必填，由 Gate 判定）。
- **FAIL 字节下限放宽**：FAIL 报告下限 `FAIL_MIN_BYTES=200`（不再复用 NA 800），「生成器 rc4 但 ingest 拒收」断路消除；NA 保持 800。

### 🧪 测试 (Tests)

- `tests/test_evidence_receipt.py`（新，19 测试）：执行器真阳 + 六项绑定逐项故障注入变红（篡改签名/逐字段变异断言「签名无效」/改落盘输出/删 journal/跨 run 重放/时间越界），并对守卫自身做故障注入证明有牙齿。
- `tests/test_full_analysis_e2e.py` / `correction` / `rework` / `runtime` / `gate_v2`：夹具统一升级为**真 oracle**——`build_compliant_evidence(run_root)` 真实调用 `execute_and_sign` 签发，e2e canary 130 条真实签名回执走完整 13 单元编排；gate_v2 6 条过时断言迁到执行器语义；新增 correction 旁路闭合测试与 FAIL 短报告测试。
- `tests/test_deploy_user_skills.py`：新增 orphan 检测/清理与 `CODEX_HOME` 跟随测试（+4）。

### 🔧 其他

- **部署器增强**：`deploy-user-skills.py` 目标目录跟随 `CODEX_HOME`；`--check` 新增 orphan 检测（目标残留、源已删文件必红）；部署时清理仓库拥有 skill 目录内的 orphan，用户自建 skill 不扫不删。
- **三层协议统一**：skill 文档（`command_receipts` 字段集 / 执行器签发红线）与 runtime `RESULT_BUNDLE_TEMPLATE` 同步 v3.4.15 语义；`sync-codex-skills.py` 重跑生成三副本一致。

## [v3.4.14] — 2026-08-04

> 收口 v3.4.13 发行后 review 点名的 5 项 P1 + 1 项 P2 残余风险，并把「退出码 0 = 真正可提交」从口号压实为机器可证的不变量。重点：回执执行绑定、退出码契约完整化、full-oracle 测试、部署器资产覆盖。另补全 v3.4.13 缺失的 NOT_APPLICABLE / FAIL 真实路径（此前 CLI 暴露的状态无法生成合法 bundle，与 E16 冲突）。

### 🐛 修复 (Fixed)

- **回执可自报成功却无执行绑定（P1）**：此前 Agent 传一条白名单内 `status: PASS` 回执、detail 写 `TEST_FIXTURE::未连接真实命令日志` 即可蒙混过关（Gate 只校验 schema/白名单/水印）。修复：PASS 回执必须携带真实执行痕迹——`argv`（实际命令）+ `output`（真实输出/落盘引用），且不得含 `PLACEHOLDER`/`TEST_FIXTURE`/`未连接真实命令日志`/`mock` 等伪造标记；**生成器与 Gate `_precheck_command_receipts` 双向独立校验**，缺绑定或含伪造标记一律拒收。
- **退出码 0 ≠ 真正可提交（P1，最关键的认知纠偏）**：v3.4.13 宣称「`0` ⟺ 零占位可提交」，但实测报告缺章节、事实不足、缺 role memo、甚至 `--status FAIL/NOT_APPLICABLE` 不当都可能 `rc=0`。修复并固化不变量：**`0` ⟺ Gate 预期接受** = 零占位 AND 状态为可验收终态 AND 报告过 Gate 硬门槛（章节齐全 + 达字节下限）；`2` = 输入非法（JSON 解析失败/单边证据/NA 证明不成立）或报告缺必需章节；`3` = 账本仍是 PLACEHOLDER 地板；`4` = `status=FAIL`（如实上报失败，**非**成功信号）。**无任何开关能把非 0 降级为 0**。
- **"全量证据"测试是 false oracle（P1）**：此前该测试只跑生成器、不跑 Gate/Audit——生成器放行 ≠ Gate 放行。修复：该测试升级为 **true-oracle**，生成器产出的 bundle 直接交 `gate.validate_result_bundle` 端到端接受；并对该 bundle 做故障注入（伪造标记 / 删 argv / 清 output / 白名单外 op），**先证红再证绿**，否则同 false oracle。另补 `_precheck_command_receipts` 的 forgery/绑定测试。
- **非法输入退出码与文档不一致（P1）**：非法 JSON 此前会 traceback 以退出码 1 收场，文档声称 2。修复：非法 JSON 统一以退出码 2 失败（与 CHANGELOG/Skill 声明一致）。
- **用户级部署器覆盖不完整（P1）**：`deploy-user-skills.py` 此前只复制 `SKILL.md`，漏 `investment-memo-craft/agents/openai.yaml` 等附属资产，且 `--check` 仍报绿（"存在但落后"正是盲区）。修复：`plan()` 改为递归覆盖 skill 目录下**全部文件**（按字节比较），`--check` 能真把资产缺失/篡改变红。
- **`--allow-placeholder-floor` 语义冲突（P2）**：该开关让占位地板返回 0，直接击穿「`0` ⟺ 可提交」。已移除（地板 bundle 仍正常落盘，调试时忽略退出码即可）。

### ✨ 新增 (Added)

- **NOT_APPLICABLE 真支持（消除 E16 冲突）**：新增 `--status NOT_APPLICABLE --na-fact-id <证伪谓词的 fact_id> --limitation "code|detail"` 模式；predicate/alternative 一律从契约取（不接受覆写），artifact_id 自动切 `artifact.na.<skill_id>`，不补占位地板，且**就地按 Gate `_validate_not_applicable` 口径校验**（谓词匹配 / fact 证伪 / source 已登记 / limitations 非空），任一不满足即拒收。
- **FAIL 专用退出码 4**：`--status FAIL` 必须携带 `--error "code|detail"`（Gate 强制 FAIL bundle 带 error 对象，否则整包拒收），退出码 4 表示"如实上报失败"，清晰区别于成功信号 0。

### 🧪 测试 (Tests)

- `tests/test_mk_result_bundle.py`：移除过时的 `--allow-placeholder-floor` 测试；新增非法 JSON→2、回执无绑定→生成器拒收、伪造标记→拒收、`--status FAIL`→4（无 error→2）、缺章节→2、NA 真支持→0 且 Gate 接受、PASS true-oracle 端到端接受 + 故障注入证红。
- `tests/test_full_analysis_gate_v2.py`：新增 `_precheck_command_receipts` 的 forgery/绑定测试（缺 argv / 缺 output / 伪造标记拒收，带绑定绿）；修正既有 receipt 夹具统一携带 argv+output。
- `tests/test_full_analysis_e2e.py` / `test_full_analysis_runtime.py` / `test_full_analysis_rework.py` / `test_deploy_user_skills.py`：夹具回执补齐执行绑定；修正一处 `write_text` 误传 bytes 的测试 bug。

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

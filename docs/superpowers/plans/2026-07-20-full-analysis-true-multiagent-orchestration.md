# Full-analysis 真独立多-Agent 编排修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `full-company-analysis` 在支持子代理的平台上以真独立多-agent 执行多视角契约（而非退化成单上下文再用 N/A 清空），并让 gate 的保障轴如实credit 这些独立上下文。

**Architecture:** 病灶不在业务契约、而在编排行为——标准 skill（investment-team/earnings-team/news-pulse/private-company-research 等）本就设计为 fan-out 独立子代理，`full-company-analysis` 却把它们压成单上下文再走负向验收，导致最高价值的对抗性分析被 ~300 字桩替换。本计划分三层修：(1) 契约层——把当前 `min_independent_contexts=0` 的多视角 skill 提到 2 并封顶 `PASS_WITH_LIMITATIONS`，使 `compute_assurance` 停止无视它们的真实独立性；(2) 编排层——`full-company-analysis.md` 把「真独立子代理」定为可用平台上的**强制默认**、单上下文降级为显式 fallback，并给出逐契约派发配方；(3) 信息带宽层——为 Layer 3-5 判断密集契约新增 `web_bandwidth_degraded` 轴，禁止静默 CLI-only。investment-team/earnings-team 契约**保持严格**（它们是有基线可退的增强层，真跑多-agent 时 `independent_context_count>=2` 已正确 PASS，无子代理时 N/A 引用基线是诚实行为）。

**Tech Stack:** Python 3 标准库、JSON、`unittest`；现有 skill 生成/安装脚本（`scripts/sync-codex-skills.py`、`scripts/sync-codex-prompts.py`）；确定性验收器 `tools/full_analysis_gate.py` 与独立校验器 `scripts/check-full-analysis-contract.py`；统一收口 `bash scripts/check.sh`。不新增依赖。

## Global Constraints

- **本地 `git commit` 允许**（CLAUDE.md §8——每 Task 收尾本地 commit）；**不 push / PR / publish / 任何外部写入**（§3 HARD-GATE）。
- **执行基线（review base）= `0bbb6dc`**（integrity-fixes 提交）；本计划工作在 `feat/tushare-data-sources` 上、以 `0bbb6dc` 为起点逐 Task commit。**工作树里 `scripts/run_full_analysis.py`、batch 脚本、`local/reports/` 研究产出为用户在途工作, 全程不得暂存/提交/改动**（每次 commit 用具体文件列表）。
- **不读取或输出 `TUSHARE_TOKEN`**；只记录 configured/not-configured 布尔能力。
- **每项先写失败测试并确认 RED**，再做最小实现和目标回归（项目铁律，对齐 `2026-07-20-ashare-full-analysis-integrity-fixes.md` 风格）。
- **改 `skills/*.md` 后必须重跑** `python3 scripts/sync-codex-skills.py`（涉及 slash prompt 再跑 `sync-codex-prompts.py`），否则 `scripts/check.sh` 的 `--check` 拦截。
- **改 `tools/full_analysis_contract.json` 后必须跑** `python3 scripts/check-full-analysis-contract.py`（与 gate 单测双实现路径，防同错）。
- **严格限定改动面**：`tools/full_analysis_contract.json`、`tools/full_analysis_gate.py`、`skills/full-company-analysis.md`、`codex-skills/*`（生成物）、`tests/test_full_analysis_*.py`、必要时 `CLAUDE.md`。**显式不碰** `local/reports/` 真实研究产出、`local/` 私密内容、无关 skill。
- **不用 `git add -A/./--all`**；每次 commit 用具体文件列表。
- 契约 `skills` 数组必须恰好 20 项（`check-full-analysis-contract.py` 断言）；本计划不增删契约项，只改字段。

---

### Task 1: 契约层——让保障轴 credit 多视角 skill 的真实独立性

**背景（为什么 RED）：** `management-deep-dive`(#07)、`news-pulse`(#13)、`private-company-research`(#17) 当前 `role_rule.min_independent_contexts = 0`、`sequential_cap = "PASS"`，而 evidence 却要 `min_role_runs = 4/6`。`compute_assurance`（`tools/full_analysis_gate.py:2359`）**只统计 `min_ctx>0` 的 skill**，所以即使真跑 4 个独立 scout，它们对 assurance 轴也完全隐形——"多视角"是装饰。修复：把这三项提到 `min_independent_contexts=2`、`sequential_cap="PASS_WITH_LIMITATIONS"`，使真独立时 credit INDEPENDENT、单上下文时封顶 PWL 且内容照产（不进 N/A 黑洞）。

**Files:**
- Modify: `tools/full_analysis_contract.json`（#7 / #13 / #17 的 `role_rule`）
- Test: `tests/test_full_analysis_contract.py`（契约结构断言）
- Test: `tests/test_full_analysis_gate.py`（`compute_assurance` credit / PWL 行为）
- Verify: `scripts/check-full-analysis-contract.py`（结构合法）
- Verify: `tests/test_full_analysis_phase2.py`（真注册表 20 项参数化——回归确认仍 GREEN，该 infra 在 `min_ctx>0` 时自动以 `--independent-context-count=min_ctx` 驱动，见 `tests/test_full_analysis_phase2.py:188`）

**Interfaces:**
- Consumes: gate 既有 `evaluate_roles`（`tools/full_analysis_gate.py:2074`，`PASS_WITH_LIMITATIONS` 分支在 :2096）、`compute_assurance`（:2359）。**无需改 gate 代码**——两分支已存在。
- Produces: 契约中 #7/#13/#17 的 `role_rule = {"required_roles": <保持原值>, "min_independent_contexts": 2, "sequential_cap": "PASS_WITH_LIMITATIONS"}`。

- [ ] **Step 1: 写契约结构失败测试**

在 `tests/test_full_analysis_contract.py` 末尾新增（读真实注册表，断言三项已提到独立性阈值）：

```python
def test_multi_perspective_skills_require_independence_for_assurance(self):
    """#7/#13/#17 多视角契约必须 min_ctx>=2 且 PWL 封顶,
    否则 compute_assurance 无视其独立性 (装饰性多角色)。"""
    registry = json.loads(
        (REPO_ROOT / "tools" / "full_analysis_contract.json")
        .read_text(encoding="utf-8"))
    by_name = {s["name"]: s for s in registry["skills"]}
    for name in ("management-deep-dive", "news-pulse",
                 "private-company-research"):
        rr = by_name[name]["role_rule"]
        self.assertGreaterEqual(
            rr["min_independent_contexts"], 2,
            f"{name} 独立性必须进 assurance 轴 (min_ctx>=2)")
        self.assertEqual(
            rr["sequential_cap"], "PASS_WITH_LIMITATIONS",
            f"{name} 无子代理时应产内容+PWL, 不得 N/A 黑洞")
```

> 注：`REPO_ROOT` / `json` 若测试文件顶部未导入，按该文件既有 import 风格补齐（多数 `test_full_analysis_*.py` 已有 `from pathlib import Path` 与仓库根常量；沿用不新造）。

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_contract -v -k multi_perspective`
Expected: FAIL —— `min_ctx>=2` 断言失败（当前值为 0）。

- [ ] **Step 3: 写 gate 行为失败测试**

在 `tests/test_full_analysis_gate.py` 合成注册表体系里新增（用 `make_registry(role_rules=...)` 覆盖，驱动一个 `min_ctx=2 + PWL` 的 skill，单上下文运行 → 该项 PWL 且 assurance 非 INDEPENDENT）：

```python
def test_pwl_multi_context_skill_single_context_caps_pwl(self):
    """min_ctx=2 + PWL 的 skill 在单上下文 (count=0) 下:
    不进 N/A, 产物照验, computed_status=PASS_WITH_LIMITATIONS, 且不 credit INDEPENDENT。"""
    role_rules = {5: {"required_roles": [],
                      "min_independent_contexts": 2,
                      "sequential_cap": "PASS_WITH_LIMITATIONS"}}
    ws = GateWorkspace(registry=make_registry(role_rules=role_rules))
    self.addCleanup(ws.cleanup)
    run_root, _ = ws.init_ok()
    # 其余 19 项走默认 PASS; sk05 单上下文 (不传 independent-context-count)
    for sk in ws.manifest(run_root)["skills"]:
        name = sk["name"]
        path = sk["assigned_artifact_paths"][0]
        ws.begin(run_root, name)
        ws.write_artifact(run_root, path)
        ws.finish(run_root, name, artifacts=[path])
    cp = ws.gate("finalize", "--registry", ws.registry_path,
                 "--run-root", run_root)
    self.assertEqual(cp.returncode, 0, f"{cp.stdout}\n{cp.stderr}")
    m = ws.manifest(run_root)
    sk05 = next(s for s in m["skills"] if s["name"] == "sk05")
    self.assertEqual(sk05["computed_status"], "PASS_WITH_LIMITATIONS")
    # sk05 min_ctx=2 但 count=0 → 不满足 → assurance 不得为 INDEPENDENT
    self.assertIn(m["run"]["assurance_level"], ("MIXED", "SINGLE_CONTEXT"))
```

> 注：以上依赖 `make_registry` / `GateWorkspace` / `init_ok` / `begin` / `write_artifact` / `finish` / `manifest`（均在 `tests/test_full_analysis_gate.py` 顶部已定义，见 :74 / :117）。`finish` 默认 `state="COMPLETE"`（:198）。若 finalize 因 sk05 缺 `not_applicable` 而报"独立上下文不足且未走负向验收"，说明 `PASS_WITH_LIMITATIONS` 分支未被走到——正是本步要暴露的 RED（当前合成默认 cap=PASS，改覆盖后应通过 :2096 分支）。

- [ ] **Step 4: 跑测试确认 RED（或验证行为缺口）**

Run: `python3 -m unittest tests.test_full_analysis_gate -v -k pwl_multi_context`
Expected: 该测试逻辑本身依赖 gate 既有 PWL 分支——若已 GREEN，说明 gate 行为正确、缺的只是**真实契约**没启用该分支（Step 1 的 RED 已覆盖）；此步作为行为契约的回归锁。记录实际结果。

- [ ] **Step 5: 实现——改真实契约三项 role_rule**

在 `tools/full_analysis_contract.json` 中，将 index 7 / 13 / 17 的 `role_rule` 改为（**保持各自 `required_roles` 原值**，只改后两字段）：

```jsonc
// #7 management-deep-dive —— required_roles 原为 [], 复核是否应命名 4 角色 (见下方 Step 7 复核项)
"role_rule": { "required_roles": [], "min_independent_contexts": 2, "sequential_cap": "PASS_WITH_LIMITATIONS" }

// #13 news-pulse —— 保留 4 scout
"role_rule": { "required_roles": ["company-scout","regulatory-scout","industry-scout","sentiment-scout"], "min_independent_contexts": 2, "sequential_cap": "PASS_WITH_LIMITATIONS" }

// #17 private-company-research —— 保留 6 维度角色
"role_rule": { "required_roles": ["business","financial","industry","governance","technology","alternative-data"], "min_independent_contexts": 2, "sequential_cap": "PASS_WITH_LIMITATIONS" }
```

- [ ] **Step 6: 跑独立校验器 + 契约测试确认 GREEN**

Run:
```bash
python3 scripts/check-full-analysis-contract.py
python3 -m unittest tests.test_full_analysis_contract -v -k multi_perspective
python3 -m unittest tests.test_full_analysis_gate -v -k pwl_multi_context
```
Expected: 独立校验器 `✅ 注册表校验通过`；两测试 PASS。

- [ ] **Step 7: 复核 management-deep-dive 角色建模 + 跑真注册表参数化回归**

先复核：#7 `required_roles=[]` 但 `min_role_runs=4`——确认这 4 条 role_run 是否代表**独立视角**（若是，考虑在本 Task 命名 4 角色；若实为单研究者的 4 个结构段落，则 `min_ctx=2` 语义存疑，需在计划复盘时记为 open question，不静默 bump）。把结论写进 commit message。
然后跑真实 20 项参数化回归（该 infra 会对 `min_ctx>0` 的项自动以 `--independent-context-count=min_ctx` 驱动，见 :188，故三项应转为 INDEPENDENT 路径且仍 GREEN）：

Run: `python3 -m unittest tests.test_full_analysis_phase2 -v`
Expected: PASS（若因 assurance 期望值变化而挂，见 Step 8 修 collateral）。

- [ ] **Step 8: 修 collateral 断言（assurance 期望值漂移）**

`compute_assurance` 现多统计 3 项 → 既有断言 `assurance_level == "INDEPENDENT/SINGLE_CONTEXT"` 的用例可能漂移。逐一定位并按新语义更新（例如某 fixture 只给 team 两项 2 上下文、未给这三项 → 从 INDEPENDENT 变 MIXED）。用真值更新期望，不改 gate 逻辑迁就旧断言。

Run: `python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_phase2`
Expected: 全 PASS。

- [ ] **Step 9: Commit**

```bash
git add tools/full_analysis_contract.json tests/test_full_analysis_contract.py tests/test_full_analysis_gate.py tests/test_full_analysis_phase2.py
git commit -m "feat(full-analysis): 多视角契约独立性进保障轴, 消除装饰性多角色

management-deep-dive/news-pulse/private-company-research 从 min_ctx=0/PASS
提到 min_ctx=2/PWL, 使 compute_assurance 如实 credit 真独立上下文;
单上下文封顶 PASS_WITH_LIMITATIONS 且内容照产, 不进 N/A 黑洞。
(management-deep-dive 角色建模复核结论: <填 Step 7 结论>)"
```

---

### Task 2: 编排层——真独立多-agent 定为可用平台强制默认

**背景：** `skills/full-company-analysis.md` 的「多角色能力与降级」把开子代理写成**许可**（"只有…才创建真实独立角色会话…不允许子代理时按角色顺序完成"）而非**强制默认**，且 Layer 2 明令 investment-team/earnings-team 在独立上下文不足时"以 NOT_APPLICABLE_PASS 收口、不得顺序生成"。在 Claude Code（子代理可用）上，编排却退化成单上下文——于是最高价值契约被 N/A 清空。本 Task 把「真独立子代理」定为可用平台的强制默认，单上下文降为显式 fallback，并给出逐契约派发配方。**不改 investment-team/earnings-team 契约**——真跑多-agent 后 `independent_context_count>=2`，既有严格契约已正确 PASS。

**Files:**
- Modify: `skills/full-company-analysis.md`（「波次与并行」「多角色能力与降级」「Layer 2 先后与去重」三段 + 新增「子代理派发配方」）
- Regenerate: `codex-skills/full-company-analysis/SKILL.md`（由 `sync-codex-skills.py` 生成，勿手改）
- Regenerate: 相关 `codex-prompts/*.md`（若涉及 slash prompt）
- Modify（按需）: `CLAUDE.md`（「全量公司分析管线」段若描述执行模式，同步一句）

**Interfaces:**
- Consumes: gate 生命周期 `begin-skill --execution-mode independent_contexts --independent-context-count N`（`tools/full_analysis_gate.py:877-880`）；`finish-skill --evidence-file <json>`，evidence 封闭 schema 含 `role_runs`（校验见 :976 / :1126）。
- Produces: 编排规范——每个多视角契约在可用平台**必须** fan-out N 个独立 `Agent`/`Task` 子代理（investment-team=4 interpreter / earnings-team=6 / news-pulse=4 scout 各带 WebSearch / private-company-research=6 维度），收齐各自 artifact 后以真实 `independent_context_count` 收口。

- [ ] **Step 1: 记录旧生成物不一致（同步基线）**

Run: `python3 scripts/sync-codex-skills.py --check`
Expected: 当前应 `✅`（若已不一致先记录），作为改动前基线。

- [ ] **Step 2: 改「波次与并行」——把并行从许可提为强制默认**

在 `skills/full-company-analysis.md` 的「波次与并行」段，把"可并行组…能开子代理才真并行，否则…"改为强制语义（示意，保留原有波/barrier 结构）：

```markdown
- **可用平台强制真独立并行**：平台存在子代理工具且当前授权允许时（预检第 4 步判定），层内互不依赖的多视角契约**必须**以真独立子代理 fan-out，不得退化为单上下文顺序执行。单上下文仅是**无子代理能力平台**的显式 fallback（记 `execution_mode=sequential_single_context`、`assurance=SINGLE_CONTEXT`），不是可用平台的默认。
```

- [ ] **Step 3: 改「多角色能力与降级」——反转默认方向**

把该段首句从"只有…才创建"改为"可用平台默认创建，缺能力才降级"：

```markdown
- **默认真独立**：平台工具存在且授权允许时，多视角契约默认创建真实独立角色会话（每角色一个子代理，独立取数与判断），`finish-skill` 如实记录 `independent_context_count`。
- **仅无能力时降级**：平台无子代理工具或授权不允许时，才由主会话按角色顺序完成，记录 `execution_mode=sequential_single_context`、保障等级降为 `SINGLE_CONTEXT`；不得在有能力平台以"省 token"为由跳过真独立。
- 角色独立性只影响 assurance 轴，不得把"平台没开子代理"写成业务 PWL（原则保留）。
```

- [ ] **Step 4: 改「Layer 2 先后与去重」——N/A 降为 fallback，真跑为默认**

把 investment-team / earnings-team 两条从"独立上下文不足 2 时以 NOT_APPLICABLE_PASS 收口"改为"默认真跑、仅无能力时 N/A 引用基线"：

```markdown
- `investment-research` 是唯一基线；`investment-team` 在**可用平台默认以 4 个独立子代理**（interpreter-duan/buffett/munger/li 各自从其方法找反证）执行, `independent_context_count>=2` 时正常 PASS 并升 assurance。**仅当平台无子代理能力**时才以 `NOT_APPLICABLE_PASS` 收口并引用基线（记录 `sequential_single_context` 限制）——这是能力缺失下的诚实降级, 不是默认路径。四视角命名章节与分歧仲裁要求不变。
- `earnings-review` 是唯一事实底稿；`earnings-team` 同理默认以 6 个独立子代理（4 大师 + editor + reader）消费冻结底稿, 仅无能力时 N/A 引用底稿。
```

- [ ] **Step 5: 新增「子代理派发配方」小节**

在「强制路径指令」段之前新增，给出逐契约的确定性派发（含 gate 生命周期落点）：

```markdown
## 子代理派发配方（可用平台强制）

对每个多视角契约, 编排层按下表 fan-out 独立子代理; 每个子代理拿到该契约的精确 artifact 子路径与强制路径声明, 独立完成后主会话汇总, 再以真实上下文数收口:

| 契约 | 独立子代理数 | 角色 | Layer 3-5 带 WebSearch |
|---|---|---|---|
| investment-team | 4 | interpreter-duan/buffett/munger/li | — |
| earnings-team | 6 | 4 大师 + editor + reader | — |
| news-pulse | 4 | company/regulatory/industry/sentiment scout | 是 |
| private-company-research | 6 | business/financial/industry/governance/technology/alternative-data | 是 |
| industry-research / industry-funnel / bottleneck-hunter | 按需 | 产业链/候选池/瓶颈视角 | 是 |

收口落点: `begin-skill --skill <name> --execution-mode independent_contexts --independent-context-count <实际数>` → 各子代理产出各自 artifact → `finish-skill --skill <name> --evidence-file <json>`（evidence 的 `role_runs` 每条含 role + artifact_paths, 见封闭 schema）。子代理继承全部 HARD-GATE, 不得自行外部写入。**实际独立上下文数如实记录**, 不按平台名假设。
```

- [ ] **Step 6: 重新生成 codex 侧生成物**

Run:
```bash
python3 scripts/sync-codex-skills.py
python3 scripts/sync-codex-prompts.py
```
Expected: 生成 `codex-skills/full-company-analysis/SKILL.md` 等；命令无报错。

- [ ] **Step 7: 校验生成物同步 + 契约独立校验**

Run:
```bash
python3 scripts/sync-codex-skills.py --check
python3 scripts/sync-codex-prompts.py --check
python3 scripts/check-full-analysis-contract.py
```
Expected: 全部 `✅`（`check-full-analysis-contract.py` 会重扫 skill 保存章节路径覆盖——本 Task 不新增 `local/reports/`/`~/` 保存路径，应不触发覆盖错误；若触发说明新增了保存语义章节，回退措辞）。

- [ ] **Step 8: 验收标准自查（无 unittest 的 prose 变更）**

逐条确认（写进 commit message 作为证据）：
- [ ] 「波次与并行」「多角色能力与降级」「Layer 2」三段默认方向已从"许可/降级"反转为"强制真独立 + fallback 降级"。
- [ ] 「子代理派发配方」表含 4 个核心多视角契约 + gate 生命周期落点。
- [ ] investment-team/earnings-team **契约未改**（严格 N/A 仅作无能力 fallback，措辞已明确）。
- [ ] 未新增任何 `local/reports/`/`~/` 硬编码保存路径。

- [ ] **Step 9: Commit**

```bash
git add skills/full-company-analysis.md codex-skills/full-company-analysis/SKILL.md codex-prompts/
git commit -m "feat(full-analysis): 真独立多-agent 定为可用平台强制默认

反转编排默认方向: 多视角契约在有子代理能力的平台必须 fan-out 真独立
子代理 (investment-team=4/earnings-team=6/news-pulse=4/private=6), 单上下文
降为显式 fallback; 新增逐契约子代理派发配方 + gate 生命周期落点。
investment-team/earnings-team 契约不变 (严格 N/A 仅作无能力降级)。"
```

> 若 `CLAUDE.md` 的「全量公司分析管线」段需同步一句执行模式说明, 单独一步改 + 依 §9 规则维护跑同步检查, 并入本 commit 或紧随其后。

---

### Task 3: 信息带宽层——Layer 3-5 禁止静默 CLI-only（P2, 可独立交付）

**背景：** Layer 3-5（industry-research / industry-funnel / bottleneck-hunter / news-pulse 等判断密集契约）在 WebSearch 后端不可用时会静默退化成 `ashare_data.py` CLI-only, 而 gate **无任何机制**验证「Web 取数兜底纪律」阶梯（WebSearch→curl 东财/巨潮→Browser→Tushare）被走过——既无 FAIL 也无 limitation, 深度天花板被压低却无痕。修复：新增 `web_bandwidth_degraded` 限制码 + gate 执法（判断密集契约必须有真实 web 来源事实**或**记该限制, 二者皆无=FAIL）, 并在 summary 展示信息带宽轴。

> 依赖 Task 1/2 已合入。可作为独立 PR/独立会话交付。

**Files:**
- Modify: `tools/full_analysis_gate.py`（新增带宽校验函数 + 接入 `evaluate_skill` + summary 输出字段）
- Modify: `tools/full_analysis_contract.json`（为 Layer 3-5 判断密集契约标注 `requires_web_bandwidth: true`, 作为执法开关）
- Modify: `skills/full-company-analysis.md`（「Web 取数兜底纪律」段补一句：降级必记 `web_bandwidth_degraded`）+ regen codex
- Test: `tests/test_full_analysis_gate.py`

**Interfaces:**
- Consumes: 契约项新增可选布尔 `requires_web_bandwidth`；skill facts 的 `sources[].source_type`（`tools/full_analysis_gate.py` 既有 `source_type` 概念, 如 filing/announcement/market_data）——新增识别 web 来源类型（如 `web`/`news`/`analyst`）。
- Produces: gate `evaluate_skill` 对 `requires_web_bandwidth=true` 的契约: 若无 web 来源事实且无 `web_bandwidth_degraded` limitation → append error（FAIL）；有 limitation → cap PWL。`summary` 新增 `information_bandwidth` 行。

- [ ] **Step 1: 写失败测试——判断密集契约静默 CLI-only 应 FAIL**

在 `tests/test_full_analysis_gate.py` 新增（构造一个 `requires_web_bandwidth=true` 的合成 skill, 既无 web 来源事实也无降级限制 → finalize 该项 FAIL）：

```python
def test_web_bandwidth_skill_without_web_source_or_limitation_fails(self):
    reg = make_registry()
    reg["skills"][9]["requires_web_bandwidth"] = True  # sk10, Layer 3
    ws = GateWorkspace(registry=reg)
    self.addCleanup(ws.cleanup)
    run_root, _ = ws.init_ok()
    for sk in ws.manifest(run_root)["skills"]:
        name, path = sk["name"], sk["assigned_artifact_paths"][0]
        ws.begin(run_root, name)
        ws.write_artifact(run_root, path)
        ws.finish(run_root, name, artifacts=[path])  # sk10 无 web 事实/限制
    cp = ws.gate("finalize", "--registry", ws.registry_path,
                 "--run-root", run_root)
    self.assertEqual(cp.returncode, 1, f"应 FAIL: {cp.stdout}\n{cp.stderr}")
    sk10 = next(s for s in ws.manifest(run_root)["skills"]
                if s["name"] == "sk10")
    self.assertEqual(sk10["computed_status"], "FAIL")
```

并新增一个「带 `web_bandwidth_degraded` 限制则 PWL 而非 FAIL」的对照测试。

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m unittest tests.test_full_analysis_gate -v -k web_bandwidth`
Expected: FAIL —— 当前 gate 不认 `requires_web_bandwidth`，sk10 会 PASS（返回码 0），断言 `returncode==1` 失败。

- [ ] **Step 3: 实现带宽校验函数**

在 `tools/full_analysis_gate.py` 新增（放在 `evaluate_roles` 附近）：

```python
WEB_SOURCE_TYPES = {"web", "news", "analyst", "official_page", "regulator_web"}

def evaluate_web_bandwidth(sk, item):
    """[h] 信息带宽: requires_web_bandwidth 的契约必须有真实 web 来源事实,
    或显式记 web_bandwidth_degraded 限制 (封顶 PWL); 二者皆无 = FAIL。"""
    if not item.get("requires_web_bandwidth"):
        return [], []
    has_web = any(
        src.get("source_type") in WEB_SOURCE_TYPES
        for fact in sk.get("facts", []) if isinstance(fact, dict)
        for src in fact.get("sources", []) if isinstance(src, dict))
    degraded = any(
        isinstance(l, dict) and l.get("code") == "web_bandwidth_degraded"
        for l in sk.get("limitations", []))
    if has_web:
        return [], []
    if degraded:
        return [], ["信息带宽降级 (web_bandwidth_degraded): 通用检索路径不可用"]
    return ["信息带宽缺口: 判断密集契约无 web 来源事实且未记 "
            "web_bandwidth_degraded (静默 CLI-only 不得准出)"], []
```

接入 `evaluate_skill`（在 `evaluate_roles` 调用之后, `tools/full_analysis_gate.py:2343` 附近）：

```python
    web_errors, web_caps = evaluate_web_bandwidth(sk, item)
    errors.extend(web_errors)
    caps.extend(web_caps)
```

- [ ] **Step 4: summary 展示信息带宽轴**

在 `cmd_summary` / `cmd_finalize` 的输出（`tools/full_analysis_gate.py:2481` 附近打印 `assurance_level` 处）后, 追加一行由 manifest 聚合的 `information_bandwidth`（FULL / DEGRADED / CLI_ONLY——依 `requires_web_bandwidth` 契约里有几个走了 web 来源 vs 降级）。逻辑最小化: 全部有 web 来源=FULL, 有降级=DEGRADED, 都无=（已被 Step 3 判 FAIL 挡住, 不会到此）。

- [ ] **Step 5: 跑测试确认 GREEN**

Run: `python3 -m unittest tests.test_full_analysis_gate -v -k web_bandwidth`
Expected: 两测试 PASS。

- [ ] **Step 6: 契约标注 + skill 措辞 + regen**

在 `tools/full_analysis_contract.json` 为 Layer 3-5 判断密集契约（#10/#11/#12/#13, 依复核判定哪些真需外部带宽）加 `"requires_web_bandwidth": true`。更新 `check-full-analysis-contract.py` 接受该可选布尔键（若其 schema 校验拒绝未知键则需补白名单）。在 `skills/full-company-analysis.md` 的「Web 取数兜底纪律」段补一句"降级路径耗尽仍不可用时必记 `web_bandwidth_degraded` limitation"。跑：

```bash
python3 scripts/sync-codex-skills.py
python3 scripts/check-full-analysis-contract.py
```
Expected: 生成成功；独立校验器通过。

- [ ] **Step 7: Commit**

```bash
git add tools/full_analysis_gate.py tools/full_analysis_contract.json scripts/check-full-analysis-contract.py skills/full-company-analysis.md codex-skills/full-company-analysis/SKILL.md tests/test_full_analysis_gate.py
git commit -m "feat(full-analysis): 新增信息带宽轴, 禁止 Layer 3-5 静默 CLI-only

requires_web_bandwidth 契约必须有真实 web 来源事实或显式记
web_bandwidth_degraded (封顶 PWL); 二者皆无判 FAIL。summary 增
information_bandwidth 轴。"
```

---

### Task 4: 收口验证

**Files:**
- Verify only: 全部改动文件

- [ ] **Step 1: 跑全部相关目标测试**

Run:
```bash
python3 -m unittest tests.test_full_analysis_gate tests.test_full_analysis_phase2 tests.test_full_analysis_contract -v
```
Expected: 全 PASS。

- [ ] **Step 2: 跑生成物同步 + 契约独立校验**

Run:
```bash
python3 scripts/sync-codex-skills.py --check
python3 scripts/sync-codex-prompts.py --check
python3 scripts/check-full-analysis-contract.py
```
Expected: 全部 `✅`。

- [ ] **Step 3: 统一本地检查（本地过=CI 过）**

Run: `bash scripts/check.sh`
Expected: 单测 + 生成物同步 + 全量分析注册表校验全绿。**已知例外**：`build_report_index.py --check` 会因用户在途未提交的 `local/reports/` funnel 报告报"索引已漂移"——此为**预先存在、用户拥有**的漂移, 与本计划无关, 不得为消除它去重建/提交用户报告索引。若需干净退出码, 单独跑本计划相关检查（`sync-codex-skills.py --check`、`sync-codex-prompts.py --check`、`check-full-analysis-contract.py`、`python3 -m unittest tests.test_full_analysis_*`）替代整包 check.sh, 并如实记录索引漂移为用户侧未决项。

- [ ] **Step 4: 审计最终 diff**

Run: `git diff --stat 0bbb6dc..HEAD`（基线为 integrity-fixes 提交, 非 main——本分支含大量在途 tushare 工作）
Expected: 改动仅落在 `tools/full_analysis_contract.json`、`tools/full_analysis_gate.py`、`skills/full-company-analysis.md`、`codex-skills/full-company-analysis/*`、`codex-prompts/*`、`scripts/check-full-analysis-contract.py`、`tests/test_full_analysis_*.py`、`CLAUDE.md`（若改）。确认**未触碰** `local/reports/`、`local/`、无关 skill；确认无外部写入。

---

## Self-Review（写完计划的自查）

**1. Spec coverage（对照截图三矛盾）：**
- 矛盾一（investment-team/earnings-team N/A 黑洞）→ **Task 2**（真跑多-agent 使 `independent_context_count>=2`, 既有严格契约正确 PASS；N/A 降为无能力 fallback）。契约不改是刻意——真跑后无黑洞。
- 矛盾二（装饰性多角色）→ **Task 1**（#7/#13/#17 min_ctx 0→2 + PWL, 使 `compute_assurance` credit 真独立）。
- 矛盾三（WebSearch 断层/深度天花板）→ **Task 2**（Layer 3-5 子代理默认带 WebSearch）+ **Task 3**（信息带宽轴 + 禁止静默 CLI-only）。

**2. Placeholder scan：** 无 TBD/TODO；测试代码为可跑体（引用真实 helper `make_registry`/`GateWorkspace`/phase2 infra, 已标注 import 补齐点）；契约改动给出精确 JSON。Task 3 的 summary 聚合逻辑与 gate 接入点给了函数体与行号锚, 非"add appropriate handling"。

**3. Type consistency：** `min_independent_contexts`(int)、`sequential_cap`∈{PASS/PASS_WITH_LIMITATIONS/NOT_APPLICABLE_PASS}、`independent_context_count`(int)、`role_runs`(list[dict])、`requires_web_bandwidth`(bool) 全程一致；gate 生命周期命令名与实际 argparse（`begin-skill`/`finish-skill`/`finalize`/`summary`）一致。

**遗留 open question（不阻塞执行, 记录待复盘）：**
- Task 1 Step 7：#7 management-deep-dive `required_roles=[]` 但 `min_role_runs=4`——4 条 role_run 是否真独立视角需实现时定夺；若实为单研究者结构段落, `min_ctx=2` 语义存疑, 届时可回退该项或改为命名角色。
- 是否让 investment-team/earnings-team 也在无能力平台产"单人多框架内容"而非 N/A：本计划按用户决策**不做**（用户选择"真独立多-agent", 拒绝单上下文降级内容路径）；若未来无能力平台占比高可重议。

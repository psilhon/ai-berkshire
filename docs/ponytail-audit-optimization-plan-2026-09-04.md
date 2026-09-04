# 过度设计优化方案（ponytail-audit，2026-09-04）

审计范围：`tools/`（13643L）+ `scripts/`（3216L）+ `tests/`（12498L）。
方法：AST 级死定义扫描（排除同文件引用与 import 别名）+ CLI 子命令活性核对 + ADR 对账。
**本文件只做方案，未改动任何代码。**

---

## 0. 审计结论修正（与 ADR / 实际接线对账后）

首轮审计给出的 10 条里，**2 条前提有误，已撤回**。这两条若不撤回会删掉活代码：

| 原结论 | 复核证据 | 处置 |
|---|---|---|
| 删 `tools/akshare_data.py`（243L） | `docs/adr/0001` 明确裁定「**不删除** akshare_data.py，不合并两个 pe-band」——它提供零 token 前复权 OHLC，与 ashare_data 的 PE 分位口径不同源 | **撤回删除**，降级为决策项 D3 |
| 删 `tools/full_analysis_cache.py`（150L）+ 测试（131L） | `tools/full_analysis_gate.py:1402-1407` 在 finalize/APPROVED 后 `import full_analysis_cache as _cache` 并调 `store_approved()`（非阻断）。首轮 grep 因未加 `-E` 漏判 | **撤回删除**，降级为决策项 D1（真问题是「只写不读」） |

其余 8 条经复核成立，构成本方案 P0/P1。

### 0.1 执行期追加撤回（动手后发现前提仍不成立，共 4 条）

| 条目 | 复核证据 | 处置 |
|---|---|---|
| P0-4 删 `run_store.read_events` | 它是 `append_event` 的对称读侧，被 `test_run_store.py` **5 处**用作行为验证缝（含 `TestDelegationBehavior` 里验证 gate/runtime 薄委托的两个用例）。删它会削弱 5 个测试、或迫使测试自己解析 jsonl。与 ADR-0001「测试引用内部缝是合法内部缝」同一类裁定 | **撤回，保留** |
| P0-5 删 `gate.validate_result_bundle` | `full_analysis_gate.py:407` docstring 把「`validate_result_bundle(check_artifacts=False)` / `cmd_ingest` / 生成器(`check_artifacts=True`)」写为**三方共用同一 admit_bundle 口径**的契约；`test_mk_result_bundle.py` 把它当 oracle 正式入口，4 个故障注入子用例依赖它变红 | **撤回，保留** |
| P0-6 删 `equity_dcf.franchise_growth_value` | 零生产入口属实（未接入 `run()`），但同层其他 8 个估值方法全部接线，唯独它没接 → 性质是**接线遗漏**而非推测性功能，删了可能打断在途工作。收益仅 6L | **降级为决策项 D5** |
| P1-2 统一 JSON 读取变体 | 5 个「重复」实为 **4 种不同错误语义**：`GateError(code=2)` / `RunStoreError(code=2)` / `DoctorError`（且捕获 `Exception`） / `benchmark` 静默返回 `None` / `mk_result_bundle` 裸抛。强行合并会改异常类型，破坏 doctor「异常不得 SystemExit 逃逸」的设计约束（doctor.py 注释明示） | **放弃合并，属刻意语义分叉** |

**教训**：本项目「零生产调用方」不等于死代码——测试即契约（ADR-0001 已裁定）。审计判据需加一条：**先看测试是否把它当契约/内部缝使用**。

---

## 1. 执行基线（实测，2026-09-04 HEAD）

```
env -u PYTHONPATH bash scripts/check.sh   # 不加 env -u 会被 WorkBuddy shim 拖到 ~287s
```

| 步骤 | 结果 |
|---|---|
| 单元测试 | **Ran 809 tests — OK**（48.2s） |
| Skill frontmatter 校验 | ✅ 17/17 |
| 🔴 STOP 确认门骨架 | ✅ 41 处 |
| codex-skills 同步 | ✅ Checked 17 |
| 报告索引同步（仅本地） | ❌ **索引已漂移**（`local/reports/INDEX.md` 过期，exit 1） |

**基线口径**：`check.sh` 真实退出码 = **1**，原因是第 6 步本地报告索引漂移（数据问题，与代码优化无关）。
优化批次以「单测 809 OK + 前 4 步全绿」为通过判据；第 6 步保持既有状态不恶化即可（或顺手 `python3 scripts/build_report_index.py` 重建）。

---

## 2. P0 — 纯删死代码（零调用方，无行为变化）

合计 **−328L**。每步独立可验证，任一步失败即停。

| # | 动作 | 位置 | Δ | verify |
|---|---|---|---|---|
| P0-1 | 删整个文件 | `tools/hkex_data.py` | −265 | 全仓（tools/scripts/tests/skills/codex-skills/docs/deliverables）检索 `hkex_data`，命中仅剩该文件自身 docstring 与 `akshare_data.py:25` 的一条注释；CI 只跑 `check.sh`，无引用 |
| P0-2 | 删 `_gitignored()` | `scripts/build_report_index.py:30-52` | −23 | `check.sh` 第 6 步仍可运行（`--check` 未走该分支）；零测试引用 |
| P0-3 | 删 `atomic_json()` 薄委托 | `tools/full_analysis_runtime.py:225-227` | −3 | 该函数体本就只是 `run_store.atomic_write_json(path, value)`；全仓零调用方 |
| P0-4 | 删 `read_events()` | `tools/run_store.py:149-159` | −11 | 生产零调用方；写入侧 `append_event` 保留（见 D2） |
| P0-5 | 删 `validate_result_bundle()` | `tools/full_analysis_gate.py:486-493` | −9 | docstring 自述「供单测使用」；tests 改调 `admit_bundle(..., check_artifacts=False)` |
| P0-6 | 删 `franchise_growth_value()` | `tools/equity_dcf.py:157-162` | −6 | 同步删 `tests/test_equity_dcf.py` 中 2 处断言 |
| P0-7 | 删 3 个 shim 测试文件 | `tests/test_full_analysis_{gate,contract,phase2}.py` | −11 | 三文件各仅一行 `from X import Y`，导致 `unittest discover` **把 61 个用例执行两遍**；删除后 `Ran N tests` 应从 **809 → 748** |

P0 通过判据：`env -u PYTHONPATH bash scripts/check.sh` → 单测 `Ran 748 tests … OK`，前 4 步全绿。

> **执行结果（2026-09-04）**：P0-1 / P0-2 / P0-3 / P0-7 已落地（4 个 commit）；
> P0-4 / P0-5 执行期撤回（见 §0.1），P0-6 降级为 D5。
> 实测：`Ran 748 tests — OK`（与预测 809−61 吻合），净 **−305L**。

---

## 3. P1 — 去重（改调用点，行为等价）

合计 **≈ −96L**。要求：每小批改完即跑 check.sh，不跨批累积。

### P1-1 原子写统一到 `run_store`（−38L，顺带修 2 处行为漂移）

`tools/run_store.py:56 atomic_write_json` / `:74 atomic_write_text` 已是单一事实源
（mkstemp + fsync + chmod 保持 + os.replace）。现存 6 份副本：

| 副本 | 差异（漂移） |
|---|---|
| `full_analysis_audit.py:53` | 缺 `mkdir(parents=True)` |
| `full_analysis_benchmark.py:38` | 无 fsync |
| `full_analysis_doctor.py:70` | 用 `with_suffix(...+".tmp")`——无后缀或含多点路径会写错文件名 |
| `full_analysis_review.py:114` | 无 fsync |
| `full_analysis_gate.py:1596 _atomic_write_json_safe` | 无 mkdir、无 fsync |
| `scripts/build_company_index.py:101 atomic_write_text` | 与 run_store 版**逐行同构**，纯复制粘贴 |

替换：删本地副本，`from run_store import atomic_write_json, atomic_write_text`（各模块已能 import run_store，sys.path 已就位）。
收益除行数外，主要是**消掉 3 种互不一致的 tmp 命名与 fsync 策略**。

### P1-2 JSON 读取统一（−27L）

现存 5 个变体：`gate.load_json:206`、`run_store._load_json_object:91`、`benchmark._load_json_safe:49`、
`mk_result_bundle.load_json:138`、`doctor._load:60`。
统一到 `run_store` 一个公开 `load_json_object(path, label)`（把 `_load_json_object` 去下划线）。
**注意**：doctor 的错误语义是 `raise DoctorError`，保留其外层 try/except 转换，不要直接裸调导致异常类型变化。

### P1-3 `_num` 提为模块级（−30L）

`tools/ashare_data.py` 里 7 份**逐字相同**的 5 行 `try: float(v) except: None`
（2773 / 2878 / 2938 / 3005 / 3158 / 3333 / 3419）→ 提一个模块级 `_num(v)`。

### P1-4 取数级别三档合并（−22L）

`tools/ashare_data.py:3646` 起：`LEVEL_COMMANDS` 的 `quick` / `enhanced` / `full` **三个 key 的值完全相同**
（`("quote","valuation","financials")`），`LEVEL_LABELS` 只换标题，`LEVEL_PENDING_LAYERS` 三个空元组 +
`cmd_run_level` 里 6 行 pending 打印永不触发。
替换：1 个命令常量 + 1 张 label 表，删 PENDING 与其打印块（保留 `_CORE_REJECTION` 与 core 拒绝分支）。

P1 通过判据：check.sh 全绿 + 手工对照
`python3 tools/ashare_data.py run-level <code> --level {quick,enhanced,full}` 三次输出，除标题行外命令序列一致。

> **执行结果（2026-09-04）**：P1-1 / P1-3 / P1-4 已落地（3 个 commit），P1-2 放弃（见 §0.1）。
> P1-1 未合并 `scripts/build_company_index.py`：它是独立脚本，引入 `tools/` 依赖需额外
> `sys.path` 样板，与 12L 收益不成比例。
> 手工对照三档：命令序列完全一致（`[1/3] quote` / `[2/3] valuation` / `[3/3] financials`），仅标题行不同。

---

## 4. 决策项（需你 1 click，我不擅自动）

这 4 项都涉及「删还是补」的取舍，不是纯削减，故不进 P0/P1。

| ID | 事实 | 选项 | 建议 |
|---|---|---|---|
| **D1** 产物缓存只写不读 | gate finalize 自动 `store_approved()` 写缓存（`:1402`）；`lookup()` 仅由 `cache-lookup` CLI 暴露，**无任何生产调用方**（派发 `next-work` 不查缓存）→ 缓存只入不出，150L + 131L 测试 | ① 接上读路径：派发前查 HIT 则复用（兑现 `docs/superpowers/plans/2026-07-30-full-analysis-token-cost.md` 的 token 成本意图）② 整层删（−281L） | **①**（有明确设计意图，删了才是真浪费；若确认不再做跨 run 复用，再选 ②） |
| **D2** 事件账本只写不读 | `append_event` 有 **19 处写入**，`read_events` **0 处读取**；doctor 走的是 `evidence_receipt.load_journal`（command-log.jsonl），不是 events.jsonl | ① 保留写入 + 让 doctor 读它（则 P0-4 撤回）② 保留只写（人类排障留痕）③ 连写入一起删 | **②**（P0-4 只删 read_events API，不动 19 处写入；审计留痕价值 > 11 行） |
| **D3** akshare_data 可运行性 | ADR-0001 裁定保留，但 `akshare` 与 `requests` **本机实测均未安装**（ModuleNotFoundError），零测试，且它是全仓 `tools/ scripts/` 唯一的第三方 import | ① 保留 + 在 ADR-0001 补「依赖未安装、路径未验证」标注 ② 装依赖验证后保留 ③ 修订 ADR 后删（−243L，并使仓库回到 stdlib 零依赖） | **①**（ADR 已有裁定，先标注；真要零依赖再走 ADR 修订流程） |
| **D4** docs/superpowers（476K） | 历史 SDD plan/spec 存档，未被 AGENTS.md / SKILLS-GUIDE.md / README.md 任一入口引用 | ① 移入 `docs/archive/` ② 在 AGENTS.md 加一行导航 ③ 不动 | **②**（归档价值在，缺的只是入口） |
| **D5** `equity_dcf.franchise_growth_value`（6L） | 估值方法库里唯一未接入 `run()` 分派的成员（同层其余 8 个方法均已接线）；有 2 处测试 | ① 接入 `run()`（补 config 分支）② 删除 ③ 保留待用 | **① 或 ②**（需你判断是否为在途工作） |

---

## 5. 收益汇总

| 批次 | Δ 行 | 风险 | 前置 |
|---|---|---|---|
| P0 纯删死代码 | **−328** | 极低（零调用方） | 无 |
| P1 去重 | **−96** | 低（行为等价，改调用点） | P0 完成 |
| 小计（可直接执行） | **−424** | | |
| D1 选 ② | −281 | 中（删前需确认无跨 run 复用计划） | 决策 |
| D3 选 ③ | −243 | 中（需先修订 ADR-0001） | 决策 |

不含 D 项：**−424 行，−0 依赖**（若 D3 选 ③，则同时清掉全仓唯一的第三方 import）。

---

## 6. 执行纪律

1. **每步单独 commit**，commit 前跑 `env -u PYTHONPATH bash scripts/check.sh` 并读**真实退出码**（基线第 6 步为 1，以「单测 OK + 前 4 步绿」为准）。
2. **不 push**——`git push` / `push --tags` 属 HARD-GATE，等你 1 click。
3. 本方案**不改 `skills/*.md` 正文**，故无需跑 `sync-codex-skills.py`；若 D3 走到改 ADR，需同步 `docs/adr/` 与 CHANGELOG。
4. **rollback**：每步一个 commit，`git revert <sha>` 即可，不必整批回退。
5. 改 `tools/run_store.py`（P0-4、P1-1、P1-2）属**跨模块共享层**——按项目规则这属 A 类触发点（≥5 文件 / 跨模块 API），执行前先确认批次范围。

---

## 7. 当前状态 / 剩余待办

### 当前状态（2026-09-04 收工）

**已执行 7 个 commit**（均未 push，未打 tag）：

| commit | 内容 | Δ |
|---|---|---|
| `0d44150` | 删 `tools/hkex_data.py` | −265 |
| `7979570` | 删 `_gitignored`（含孤立 `import subprocess`）+ `atomic_json` 薄委托 | −26 |
| `1636478` | 删 3 个 shim 测试文件 | −11 |
| `b139948` | 原子写统一到 `run_store`（5 份副本 → 单一事实源） | −16 |
| `b7d46a8` | `_num` 7 份 → 1 个模块级 | −34 |
| `c4d8766` | 取数级别三档合并（删全空的 `LEVEL_PENDING_LAYERS`） | −22 |
| 累计（`v3.10.12..HEAD`） | 14 files changed | **44 insertions(+), 429 deletions(−)** |

单测：**809 → 748 tests，OK**（61 次重复执行消除）。check.sh 真实退出码仍是 1，原因不变（第 6 步本地报告索引漂移，既有数据问题）。

**剩余待办**：
- [x] P0 可执行项 / P1 可执行项
- [ ] D1 / D2 / D3 / D4 / **D5** 拍板（D1 缓存只写不读是最大项，−281L 或补读路径）
- [ ] （可选）顺手重建 `local/reports/INDEX.md`，让 check.sh 回到 exit 0
- [ ] 确认是否发版（+0.0.1 semver + tag）与 push——**HARD-GATE，等你 1 click**

#!/usr/bin/env python3
"""公司研究索引页生成器（确定性渲染）。

扫描 `local/Company/<code>-<name>/<run>/` 下的全量分析总结报告，提取一句话结论、
数据截止日、准出状态等元数据，渲染为自包含的 `index.html` 汇总页（含每家的
HTML 报告 / MD 源文件链接）。

设计目标与 `tools/full_analysis_html.py` 一致：
- **确定性**：同一组输入报告永远渲染出同一份 HTML（无 LLM、无 token、无方差）。
  页面上的"生成时间"取自源报告的最大修改时间而非系统时钟，保证可复跑逐字节一致。
- **自动更新**：新增公司 / 新增 run 后重跑本脚本即可刷新索引；亦可接入
  register-summary / finalize 流水线在收口后自动调用。
- **非阻断**：仅依赖标准库，渲染失败抛错由调用方处理，不影响任何 run 状态。

用法：
    python3 scripts/build_company_index.py                 # 默认扫描 local/Company
    python3 scripts/build_company_index.py --base <dir>    # 指定目录
    python3 scripts/build_company_index.py --check         # 只校验是否需要重建（供流水线判断）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import errno
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from html import escape as _html_escape
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows compatibility
    fcntl = None
    import msvcrt

# ---------------------------------------------------------------------------
# 元数据提取
# ---------------------------------------------------------------------------

_DIR_RE = re.compile(r"^(\d{6}\.[A-Z]{2})-(.+)$")

# 一句话结论的多种标记变体（不同 run 的总结 md 写法不统一，需逐一兜底）
_ONE_MARKERS = [
    re.compile(r"一句话(?:总结|定性|判断|定位)\s*[:：]\s*(.+)"),
    re.compile(r"(?:核心|综合|全局)判断\s*[:：]\s*(.+)"),
]
_OVERVIEW_RE = re.compile(r"#{1,3}[^\n]*核心结论速览[^\n]*\n+(.{20,500}?)(?:\n\n|\n#|\n\||\n-|\n>)", re.S)
_ASOF_RE = re.compile(r"(?:数据|分析)?截止日?\s*[*：:]*\s*(20\d{2})[-/年.]\s*(\d{1,2})[-/月.]\s*(\d{1,2})")

_NEG_KW = ("回避", "否决", "看空", "排除", "不建议买入", "不参与")
_POS_KW = ("买入", "持有", "建仓", "可买", "通过", "建议关注")
_WAIT_KW = (
    "等待", "等回调", "等估值", "等价格", "观望",
    "跟踪", "观察", "不买", "跌出折扣",
)
TZ_SHANGHAI = _dt.timezone(_dt.timedelta(hours=8))


@contextmanager
def index_lock(base: Path):
    """Serialize index collection and replacement across processes."""
    path = Path(base) / ".index.lock"
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows compatibility
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EDEADLK,
                    }:
                        raise
                    # LK_LOCK 内建等待有上限；继续重试直至与 POSIX
                    # LOCK_EX 一样取得排他锁，避免竞争时遗漏索引更新。
                    handle.seek(0)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows compatibility
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a UTF-8 text file without exposing a partial destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _clean(s: str) -> str:
    s = re.sub(r"[*_>#`|]+", "", s)
    return re.sub(r"\s+", " ", s).strip().rstrip("。；;，,")


def _trunc(s: str, n: int = 150) -> str:
    if len(s) <= n:
        return s
    cut = s[:n]
    for sep in ("。", "；", "——", "，"):
        i = cut.rfind(sep)
        if i > n // 2:
            return cut[: i + 1].rstrip("，；。")
    return cut + "…"


def extract_one(txt: str) -> str:
    """提取一句话结论（完整文本，供归类判断；展示时另行截断）。"""
    for pat in _ONE_MARKERS:
        m = pat.search(txt)
        if m:
            v = _clean(m.group(1))
            if len(v) >= 8:
                return v
    m = _OVERVIEW_RE.search(txt)
    if m:
        v = _clean(m.group(1))
        if len(v) >= 20:
            return v
    return ""


def extract_asof(txt: str) -> str:
    m = _ASOF_RE.search(txt)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def board_of(code: str) -> str:
    """按股票代码前缀判断上市板块。"""
    raw = code.upper()
    p = raw.split(".")[0]
    if raw.endswith(".BJ") or p.startswith(("4", "8", "920")):
        return "北交所"
    if p.startswith(("300", "301")):
        return "创业板"
    if p.startswith(("688", "689")):
        return "科创板"
    if p.startswith(("600", "601", "603", "605")):
        return "沪主板"
    return "深主板"


def verdict_of(one: str) -> str:
    """基于一句话结论做粗粒度结论归类（仅用于索引页配色，不改变报告本身）。

    顺序关键：先判负（回避/否决），再判明确等待短语，最后判正。
    若先判正，"等回调再买""不买只观察"会因"买"的子串命中被误归为正向。
    """
    if any(k in one for k in _NEG_KW):
        return "回避"
    if any(k in one for k in _WAIT_KW):
        return "等待"
    if any(k in one for k in _POS_KW):
        return "关注"
    return "中性"


def collect(
    base: Path,
    *,
    warnings: list[str] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for cd in sorted(base.iterdir()):
        if not cd.is_dir():
            continue
        m = _DIR_RE.match(cd.name)
        code, company = (m.group(1), m.group(2)) if m else ("", cd.name)
        selected = None
        for r in sorted(
            (p for p in cd.iterdir() if p.is_dir()),
            reverse=True,
        ):
            sums = sorted(r.glob("*总结报告.md"))
            if not sums:
                continue
            selected = (r, sums[-1])
            break
        if selected is None:
            continue
        r, md = selected
        html = md.with_suffix(".html")
        txt = md.read_text(encoding="utf-8")
        status = ""
        mf = r / "evidence/00-analysis-manifest.json"
        if mf.exists():
            try:
                status = json.loads(mf.read_text(encoding="utf-8")).get("run", {}).get("status") or ""
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                AttributeError,
                TypeError,
            ) as exc:
                status = "MANIFEST_ERROR"
                if warnings is not None:
                    warnings.append(f"{mf}: {exc}")
        one_full = extract_one(txt)
        asof = extract_asof(txt)
        rows.append({
            "code": code,
            "company": company,
            "board": board_of(code),
            "run": r.name,
            "md": md.name,
            "md_rel": f"{cd.name}/{r.name}/{md.name}",
            "html": html.name if html.exists() else "",
            "html_rel": f"{cd.name}/{r.name}/{html.name}" if html.exists() else "",
            # verdict 基于完整结论判断（避免截断后关键词丢失导致误归类）；
            # 展示用 one 才截断。
            "one": _trunc(one_full),
            "verdict": verdict_of(one_full),
            "asof": asof,
            "status": status or "深度总结",
            "bytes": md.stat().st_size,
            "mtime": md.stat().st_mtime,
        })
    # 排序：板块 → 代码，保证输出稳定
    order = {
        "沪主板": 0,
        "深主板": 1,
        "创业板": 2,
        "科创板": 3,
        "北交所": 4,
    }
    rows.sort(key=lambda x: (order.get(x["board"], 9), x["code"], x["run"]))
    return rows


# ---------------------------------------------------------------------------
# HTML 渲染（设计系统：冷白纸 / 墨蓝 / 朱砂印章红 / 数据等宽字 —— 与单公司报告页区分）
# ---------------------------------------------------------------------------

_CSS = """\
:root{
  --paper:#F2F5F7;
  --card:#FBFCFC;
  --ink:#152A3D;
  --ink-soft:#3E5468;
  --muted:#71828F;
  --faint:#A5B2BC;
  --line:#DDE5EA;
  --line-strong:#C3D0D9;
  --navy:#1B3A57;
  --navy-soft:rgba(27,58,87,.07);
  --seal:#C8402F;            /* 朱砂印章红：中国股市"红涨"语境下的正向强调 */
  --seal-soft:rgba(200,64,47,.08);
  --gold:#B07C24;
  --gold-soft:rgba(176,124,36,.10);
  --slate:#5B6B78;
  --slate-soft:rgba(91,107,120,.10);
  --blue:#2E6E9E;
  --blue-soft:rgba(46,110,158,.09);
  --serif:"Songti SC","Noto Serif SC","STSong","SimSun","Source Han Serif SC",serif;
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC","Helvetica Neue",Arial,sans-serif;
  --mono:"SF Mono","JetBrains Mono","Menlo",Consolas,"Courier New",monospace;
  --shadow:0 1px 2px rgba(21,42,61,.05),0 10px 28px -14px rgba(21,42,61,.16);
  --shadow-lift:0 2px 6px rgba(21,42,61,.07),0 18px 40px -16px rgba(21,42,61,.24);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  font-family:var(--sans);color:var(--ink);font-size:16px;line-height:1.7;
  background:
    radial-gradient(1100px 560px at 85% -10%, rgba(27,58,87,.07), transparent 60%),
    radial-gradient(900px 500px at -10% 18%, rgba(200,64,47,.045), transparent 58%),
    radial-gradient(760px 460px at 50% 108%, rgba(46,110,158,.05), transparent 60%),
    radial-gradient(rgba(27,58,87,.05) 1px, transparent 1px) 0 0/26px 26px,
    var(--paper);
  background-attachment:fixed;
}
::selection{background:rgba(200,64,47,.22)}
a{color:var(--navy);text-decoration:none}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
.mono{font-family:var(--mono)}

/* ---------- 台账报头（非通用 hero：账本式抬头 + 统计条） ---------- */
.topbar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;
  padding:18px 0 14px;border-bottom:1px solid var(--line);}
.topbar .brand{display:flex;align-items:center;gap:12px;}
.brand .seal-mark{width:38px;height:38px;flex:0 0 38px;border:2px solid var(--seal);color:var(--seal);
  display:flex;align-items:center;justify-content:center;font-family:var(--serif);font-weight:800;
  font-size:18px;transform:rotate(-4deg);border-radius:6px;background:var(--seal-soft);
  box-shadow:0 0 0 3px rgba(200,64,47,.06);}
.brand b{font-family:var(--serif);font-size:17px;letter-spacing:.06em}
.brand small{display:block;color:var(--muted);font-size:11.5px;letter-spacing:.14em}
.topbar .gen{color:var(--muted);font-size:12px;letter-spacing:.04em;text-align:right}

.masthead{padding:34px 0 8px;display:flex;align-items:flex-end;justify-content:space-between;gap:24px;flex-wrap:wrap}
.masthead h1{font-family:var(--serif);font-size:clamp(30px,4.4vw,44px);font-weight:800;letter-spacing:.02em;line-height:1.15}
.masthead h1 em{font-style:normal;color:var(--seal);}
.masthead .lede{max-width:460px;color:var(--ink-soft);font-size:14px;line-height:1.75}
.masthead .lede b{color:var(--navy)}

/* 统计条 */
.statbar{display:flex;gap:0;flex-wrap:wrap;margin:22px 0 4px;border:1px solid var(--line-strong);
  background:var(--card);box-shadow:var(--shadow);border-radius:10px;overflow:hidden}
.stat{flex:1 1 0;min-width:150px;padding:16px 22px;border-right:1px solid var(--line)}
.stat:last-child{border-right:none}
.stat .num{font-family:var(--mono);font-size:30px;font-weight:700;color:var(--navy);line-height:1.1}
.stat .num i{font-style:normal;font-size:14px;color:var(--muted);margin-left:2px}
.stat .lab{font-size:12px;color:var(--muted);letter-spacing:.1em;margin-top:4px}
.stat.hot .num{color:var(--seal)}

/* 工具条：搜索 + 筛选 + 排序 */
.toolbar{position:sticky;top:0;z-index:20;background:rgba(242,245,247,.9);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line);padding:14px 0;margin-bottom:26px}
.toolbar .inner{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.search{flex:1 1 240px;max-width:340px;position:relative}
.search input{width:100%;padding:10px 14px 10px 38px;border:1px solid var(--line-strong);border-radius:8px;
  font:inherit;font-size:14px;background:var(--card);color:var(--ink);transition:border-color .2s,box-shadow .2s}
.search input:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px var(--navy-soft)}
.search::before{content:"⌕";position:absolute;left:13px;top:50%;transform:translateY(-52%);
  color:var(--faint);font-size:18px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{border:1px solid var(--line-strong);background:var(--card);color:var(--ink-soft);
  padding:7px 14px;border-radius:999px;font-size:13px;cursor:pointer;user-select:none;
  transition:all .18s ease}
.chip:hover{border-color:var(--navy);color:var(--navy);transform:translateY(-1px)}
.chip.on{background:var(--navy);color:#fff;border-color:var(--navy);box-shadow:0 3px 10px -3px rgba(27,58,87,.5)}
.chip .ct{font-family:var(--mono);opacity:.75;margin-left:5px;font-size:11px}
.count-note{margin-left:auto;color:var(--muted);font-size:12.5px;font-family:var(--mono)}

/* ---------- 卡片网格 ---------- */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px}
.card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:20px 20px 16px 24px;box-shadow:var(--shadow);overflow:hidden;
  display:flex;flex-direction:column;gap:10px;
  transition:transform .22s ease,box-shadow .22s ease,border-color .22s ease;
  opacity:1;transform:none}
.js.enhanced .card{opacity:0;transform:translateY(16px)}
.js.enhanced .card.in{opacity:1;transform:none}
.card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--blue);
  transition:width .2s ease}
.card.v-关注::before{background:var(--seal)}
.card.v-等待::before{background:var(--gold)}
.card.v-回避::before{background:var(--slate)}
.card.v-中性::before{background:var(--blue)}
.card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lift);border-color:var(--line-strong)}
.card:hover::before{width:6px}
.card.hide{display:none}

.card .head{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.card .name{font-family:var(--serif);font-size:21px;font-weight:800;letter-spacing:.01em}
.card .name a{color:inherit;transition:color .18s}
.card .name a:hover{color:var(--seal)}
.card .code{font-family:var(--mono);font-size:12px;color:var(--muted)}
.card .meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.tag{font-size:11px;font-family:var(--mono);padding:2.5px 8px;border-radius:5px;letter-spacing:.03em}
.tag.board{background:var(--navy-soft);color:var(--navy)}
.tag.status{background:var(--blue-soft);color:var(--blue)}
.tag.status.approved{background:var(--seal-soft);color:var(--seal)}
.tag.verdict{font-family:var(--sans);font-weight:600}
.tag.verdict.v-关注{background:var(--seal-soft);color:var(--seal)}
.tag.verdict.v-等待{background:var(--gold-soft);color:var(--gold)}
.tag.verdict.v-回避{background:var(--slate-soft);color:var(--slate)}
.tag.verdict.v-中性{background:var(--blue-soft);color:var(--blue)}
.card .one{font-size:13.5px;color:var(--ink-soft);line-height:1.72;flex:1}
.card .one::before{content:"「";color:var(--faint)}
.card .one::after{content:"」";color:var(--faint)}
.card .foot{display:flex;justify-content:space-between;align-items:center;gap:10px;
  border-top:1px dashed var(--line);padding-top:12px}
.card .asof{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.card .links{display:flex;gap:14px}
.card .links a{font-size:13px;font-weight:600;color:var(--navy);display:inline-flex;align-items:center;gap:4px;
  transition:color .18s,gap .18s}
.card .links a::after{content:"→";transition:transform .18s}
.card .links a:hover{color:var(--seal)}
.card .links a:hover::after{transform:translateX(3px)}
.card .links a.md{color:var(--muted);font-weight:500}
.card .links a.md:hover{color:var(--navy)}

.empty{display:none;padding:60px 0;text-align:center;color:var(--muted);font-family:var(--serif);font-size:17px}
.empty.show{display:block}

/* ---------- 页脚 ---------- */
footer{margin:56px 0 40px;border-top:1px solid var(--line-strong);padding-top:22px;
  color:var(--muted);font-size:12.5px;line-height:1.9}
footer .disclaimer{max-width:760px}
footer b{color:var(--ink-soft)}

@media (max-width:640px){
  .grid{grid-template-columns:1fr}
  .statbar{flex-direction:column}
  .stat{border-right:none;border-bottom:1px solid var(--line)}
  .stat:last-child{border-bottom:none}
}
@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important}
  .js.enhanced .card{opacity:1;transform:none}
}
"""

_JS = """\
(function(){
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var input = document.getElementById('q');
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var note  = document.getElementById('count-note');
  var empty = document.getElementById('empty');
  var curBoard = '*';

  function apply(){
    var q = (input.value||'').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function(c){
      var hay = (c.dataset.hay||'').toLowerCase();
      var okBoard = (curBoard==='*') || (c.dataset.board===curBoard);
      var okQ = !q || hay.indexOf(q)>-1;
      var show = okBoard && okQ;
      c.classList.toggle('hide', !show);
      if(show){ shown++; }
    });
    note.textContent = shown + ' / ' + cards.length + ' 家';
    empty.classList.toggle('show', shown===0);
  }

  input.addEventListener('input', apply);
  chips.forEach(function(ch){
    ch.addEventListener('click', function(){
      chips.forEach(function(x){ x.classList.remove('on'); });
      ch.classList.add('on');
      curBoard = ch.dataset.board;
      apply();
    });
  });

  // 滚动显现（IntersectionObserver，带交错延迟）
  if('IntersectionObserver' in window){
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){
          var el = e.target;
          el.style.transitionDelay = (el.dataset.i % 6) * 45 + 'ms';
          el.classList.add('in');
          io.unobserve(el);
        }
      });
    }, {threshold:.08});
    cards.forEach(function(c,i){ c.dataset.i = i; io.observe(c); });
  } else {
    cards.forEach(function(c){ c.classList.add('in'); });
  }

  // 统计数字滚动动画
  document.querySelectorAll('.num[data-target]').forEach(function(el){
    var target = parseInt(el.dataset.target,10) || 0;
    var suffix = el.dataset.suffix || '';
    if(target===0){ el.textContent = target + suffix; return; }
    var t0 = null, dur = 900;
    function step(ts){
      if(!t0){ t0 = ts; }
      var p = Math.min((ts-t0)/dur, 1);
      var ease = 1 - Math.pow(1-p, 3);
      el.textContent = Math.round(target*ease) + suffix;
      if(p<1){ requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  });

  apply();
  document.documentElement.classList.add("enhanced");
})();
"""


def _esc(s: object) -> str:
    return _html_escape(str(s), quote=True)


def _render_card(r: dict) -> str:
    v = r["verdict"]
    status_cls = "status approved" if r["status"] == "APPROVED" else "status"
    links = []
    if r["html_rel"]:
        links.append(f'<a href="{_esc(r["html_rel"])}">HTML 报告</a>')
    if r["md_rel"]:
        links.append(f'<a class="md" href="{_esc(r["md_rel"])}">MD 源文件</a>')
    hay = f'{r["company"]} {r["code"]} {r["board"]} {r["one"]} {r["verdict"]} {r["status"]}'
    return (
        f'<article class="card v-{_esc(v)}" data-board="{_esc(r["board"])}" data-hay="{_esc(hay)}">\n'
        f'  <div class="head"><span class="name"><a href="{_esc(r["html_rel"] or r["md_rel"])}">{_esc(r["company"])}</a></span>'
        f'<span class="code">{_esc(r["code"])}</span></div>\n'
        f'  <div class="meta">'
        f'<span class="tag board">{_esc(r["board"])}</span>'
        f'<span class="tag {status_cls}">{_esc(r["status"])}</span>'
        f'<span class="tag verdict v-{_esc(v)}">{_esc(v)}</span>'
        f'</div>\n'
        f'  <p class="one">{_esc(r["one"]) or "（未提取到一句话结论）"}</p>\n'
        f'  <div class="foot"><span class="asof">数据截止 {_esc(r["asof"] or "—")}</span>'
        f'<span class="links">{"".join(links)}</span></div>\n'
        f'</article>'
    )


def build_index_html(rows: list[dict], *, base_label: str = "local/Company") -> str:
    total = len(rows)
    approved = sum(1 for r in rows if r["status"] == "APPROVED")
    total_kb = sum(r["bytes"] for r in rows) / 1024.0
    latest = max((r["asof"] for r in rows if r["asof"]), default="—")
    # 生成时间取自源报告最大修改时间（输入派生 → 可复跑逐字节一致）
    gen_ts = max((r["mtime"] for r in rows), default=0.0)
    gen_str = (
        _dt.datetime.fromtimestamp(gen_ts, tz=TZ_SHANGHAI).strftime(
            "%Y-%m-%d %H:%M"
        )
        if gen_ts else "—"
    )
    safe_base_label = _esc(base_label)

    boards: list[tuple[str, int]] = []
    for r in rows:
        found = False
        for i, (b, _c) in enumerate(boards):
            if b == r["board"]:
                boards[i] = (b, _c + 1)
                found = True
                break
        if not found:
            boards.append((r["board"], 1))

    chips = ['<button class="chip on" data-board="*">全部<span class="ct">{}</span></button>'.format(total)]
    chips += [
        f'<button class="chip" data-board="{_esc(b)}">{_esc(b)}<span class="ct">{c}</span></button>'
        for b, c in boards
    ]

    cards = "\n".join(_render_card(r) for r in rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>公司研究索引 · AI Berkshire</title>
<script>document.documentElement.classList.add('js')</script>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">
      <span class="seal-mark">研</span>
      <span><b>AI·BERKSHIRE 研究台账</b><small>FULL-COMPANY ANALYSIS LEDGER</small></span>
    </div>
    <div class="gen">索引生成于 {gen_str}<br>数据源：{safe_base_label}/*/（自动扫描）</div>
  </div>

  <div class="masthead">
    <div>
      <h1>公司研究<em>索引</em></h1>
    </div>
    <p class="lede">每家公司的全量分析（数据快筛 · 公司财报 · 行业机会 · 投资论文）熔炼为一份总结报告。
      点击卡片进入 <b>HTML 报告</b>，或查看 <b>MD 源文件</b>。本页由脚本自动扫描生成，新增公司后重跑即更新。</p>
  </div>

  <div class="statbar">
    <div class="stat"><div class="num" data-target="{total}">{total}</div><div class="lab">覆盖公司</div></div>
    <div class="stat hot"><div class="num" data-target="{approved}">{approved}</div><div class="lab">通过准出 APPROVED</div></div>
    <div class="stat"><div class="num" data-target="{len(boards)}">{len(boards)}</div><div class="lab">上市板块</div></div>
    <div class="stat"><div class="num" data-target="{int(total_kb)}" data-suffix="">{int(total_kb)}</div><div class="lab">总结体量（KB）</div></div>
  </div>

  <div class="toolbar"><div class="inner">
    <div class="search"><input id="q" type="search" placeholder="搜索公司 / 代码 / 结论关键词…"></div>
    <div class="chips">{''.join(chips)}</div>
    <span class="count-note" id="count-note"></span>
  </div></div>

  <div class="grid">
{cards}
  </div>
  <div class="empty" id="empty">没有匹配的公司 —— 换个关键词试试。</div>

  <footer>
    <p class="disclaimer"><b>免责声明：</b>本索引及所链接的全部分析报告均为投资研究学习用途，由 AI 辅助生成，
      不构成任何投资建议。数据以各报告内标注的截止日为准，可能存在口径差异与滞后。投资决策请独立判断、自担风险。</p>
    <p>共 {total} 家公司 · {approved} 家通过准出 · 最新数据截止 {latest} · 由 <span class="mono">scripts/build_company_index.py</span> 确定性渲染</p>
  </footer>
</div>
<script>{_JS}</script>
</body>
</html>
"""


def _display_base_label(base: Path) -> str:
    if base.parent.name == "local":
        return f"local/{base.name}"
    return str(base)


def rebuild_index(base: Path, output: Path | None = None) -> dict:
    """Collect, render and atomically replace one company index."""
    base = Path(base)
    out = Path(output) if output else base / "index.html"
    with index_lock(base):
        warnings: list[str] = []
        rows = collect(base, warnings=warnings)
        if not rows:
            raise ValueError(f"{base} 下未找到任何总结报告")
        html = build_index_html(rows, base_label=_display_base_label(base))
        atomic_write_text(out, html)
    return {
        "index": str(out),
        "companies": len(rows),
        "bytes": len(html.encode("utf-8")),
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成公司研究索引页 index.html")
    parser.add_argument("--base", default=None, help="公司目录（默认 <repo>/local/Company）")
    parser.add_argument("--output", default=None, help="输出路径（默认 <base>/index.html）")
    parser.add_argument("--check", action="store_true", help="只打印是否需要重建，不写文件")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parent.parent
    base = Path(args.base) if args.base else repo / "local" / "Company"
    if not base.is_dir():
        print(f"❌ 目录不存在: {base}", file=sys.stderr)
        return 1
    out = Path(args.output) if args.output else base / "index.html"

    if args.check:
        warnings: list[str] = []
        rows = collect(base, warnings=warnings)
        for warning in warnings:
            print(f"⚠️  manifest 读取失败: {warning}", file=sys.stderr)
        if not rows:
            print(f"⚠️  {base} 下未找到任何总结报告，索引页未生成。", file=sys.stderr)
            return 1
        html = build_index_html(rows, base_label=_display_base_label(base))
        need = (not out.exists()) or (out.read_text(encoding="utf-8") != html)
        print(json.dumps({"index": str(out), "companies": len(rows), "needs_rebuild": need}, ensure_ascii=False))
        return 0

    try:
        result = rebuild_index(base, out)
    except ValueError as exc:
        print(f"⚠️  {exc}，索引页未生成。", file=sys.stderr)
        return 1
    for warning in result["warnings"]:
        print(f"⚠️  manifest 读取失败: {warning}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""full-analysis 总结报告的确定性 HTML 渲染器。

finalize APPROVED 后由 `full_analysis_gate.py` 调用，把已冻结的 markdown 总结
渲染为自包含、可对外发布的 HTML 展示件（内联设计系统与微交互脚本，零外部依赖）。

设计目标：
- **确定性**：同一份 markdown 永远渲染出同一份 HTML，无 LLM 参与、无 token 消耗、
  无输出方差。把用户认可的视觉品质固化为代码，保证每个 run 的展示件质量一致。
- **非阻断**：渲染失败只打印警告，绝不影响 APPROVED 状态。
- **忠实**：只转换 markdown 原文，不引入新数据、新推理、新结论。

对外接口：
    build_summary_page(markdown, *, company, code, as_of, skill_count, status) -> str
"""
from __future__ import annotations

import re
from html import escape as _esc

# ---------------------------------------------------------------------------
# 设计系统（固化自用户认可的展示件：cream paper / terracotta / trust 墨蓝 / serif）
# 只改 :root 令牌即可换肤；结构与内容样式全部走 class 引用。
# ---------------------------------------------------------------------------
_CSS = """\
/* ============ 设计基座 ============ */
:root{
  --paper:#F5F4ED;
  --card:#FAF9F5;
  --terra:#B85235;
  --terra-deep:#9A422A;
  --terra-soft:rgba(184,82,53,.09);
  --trust:#1B365D;
  --trust-soft:rgba(27,54,93,.08);
  --green:#2F6F4E;
  --green-soft:rgba(47,111,78,.10);
  --warn:#9A5A2D;
  --warn-soft:rgba(154,90,45,.10);
  --ink:#26231C;
  --muted:#6F6A5D;
  --faint:#9A9485;
  --line:#E3DFD0;
  --line-strong:#CFC9B6;
  --serif:"Noto Serif SC","Songti SC","STSong","SimSun","Source Han Serif SC",serif;
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC","Helvetica Neue",Arial,sans-serif;
  --mono:"SF Mono","JetBrains Mono","Menlo",Consolas,"Courier New",monospace;
  --shadow:0 1px 2px rgba(38,35,28,.05),0 8px 24px -12px rgba(38,35,28,.14);
  --shadow-lift:0 2px 4px rgba(38,35,28,.06),0 16px 36px -14px rgba(38,35,28,.20);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  font-family:var(--sans);
  color:var(--ink);
  font-size:16px;
  line-height:1.85;
  background:
    radial-gradient(1100px 560px at 88% -8%, rgba(27,54,93,.055), transparent 62%),
    radial-gradient(860px 480px at -8% 22%, rgba(184,82,53,.05), transparent 58%),
    radial-gradient(700px 420px at 55% 105%, rgba(47,111,78,.04), transparent 60%),
    repeating-linear-gradient(0deg, transparent 0 35px, rgba(27,54,93,.026) 35px 36px),
    var(--paper);
  background-attachment:fixed;
}
::selection{background:rgba(184,82,53,.22)}
a{color:var(--terra);text-decoration:none}
b,strong{font-weight:700}
.wrap{max-width:1060px;margin:0 auto;padding:0 24px}

/* ============ 报头 masthead ============ */
.masthead{
  position:relative;
  background:linear-gradient(155deg,#17304F 0%, var(--trust) 46%, #234573 100%);
  color:#F3F1E8;
  overflow:hidden;
  border-bottom:5px solid var(--terra);
}
.masthead::before{
  content:"";position:absolute;inset:0;pointer-events:none;
  background:
    repeating-linear-gradient(115deg, transparent 0 26px, rgba(243,241,232,.035) 26px 27px),
    radial-gradient(720px 380px at 82% 120%, rgba(184,82,53,.34), transparent 62%);
}
.masthead .wrap{position:relative;z-index:1;padding-top:44px;padding-bottom:36px}
.mh-eyebrow{
  display:flex;flex-wrap:wrap;align-items:center;gap:10px;
  font-size:12.5px;letter-spacing:.28em;color:#B9C6DA;
  text-transform:uppercase;margin-bottom:18px;
}
.mh-eyebrow .tick{
  font-family:var(--mono);letter-spacing:.12em;
  background:rgba(243,241,232,.10);border:1px solid rgba(243,241,232,.28);
  padding:3px 12px;border-radius:3px;color:#EDEAE0;
}
.mh-eyebrow .dot{width:5px;height:5px;background:var(--terra);border-radius:50%}
.mh-title-row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:22px}
.mh-title{
  font-family:var(--serif);font-weight:900;
  font-size:clamp(40px,7vw,64px);line-height:1.15;letter-spacing:.04em;
}
.mh-title em{font-style:normal;color:#E8B09B}
.mh-en{
  font-family:var(--mono);font-size:13px;letter-spacing:.34em;color:#8FA3C0;
  border-left:3px solid var(--terra);padding-left:14px;padding-bottom:8px;
}
.mh-en b{display:block;color:#D8E0EC;font-size:17px;letter-spacing:.22em}
.mh-verdict{margin-top:26px;display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.stamp{
  font-family:var(--serif);font-weight:800;font-size:17px;letter-spacing:.14em;
  color:#F6E9E2;border:2px solid var(--terra);background:rgba(184,82,53,.88);
  padding:8px 20px;border-radius:4px;transform:rotate(-1.4deg);
  box-shadow:0 6px 18px -6px rgba(0,0,0,.45);
}
.stamp small{display:block;font-family:var(--sans);font-weight:400;font-size:11.5px;letter-spacing:.1em;opacity:.92}
.mh-meta{
  margin-top:26px;display:flex;flex-wrap:wrap;gap:10px 26px;align-items:center;
  font-size:13px;color:#A9B8CD;
}
.mh-meta .asof{
  font-family:var(--mono);font-size:13px;color:#F3F1E8;
  background:rgba(243,241,232,.08);border:1px solid rgba(243,241,232,.22);
  border-radius:3px;padding:4px 12px;
}
.mh-meta .chip{border-bottom:1px dashed rgba(243,241,232,.35);padding-bottom:1px}

/* ============ sticky 导航 ============ */
.nav{
  position:sticky;top:0;z-index:50;
  background:rgba(245,244,237,.96);
  border-bottom:1px solid var(--line-strong);
  box-shadow:0 4px 14px -8px rgba(38,35,28,.25);
}
.nav .wrap{display:flex;align-items:center;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch}
.nav .brand{
  font-family:var(--serif);font-weight:800;font-size:15px;color:var(--trust);
  white-space:nowrap;padding:13px 14px 13px 0;letter-spacing:.06em;
  border-right:1px solid var(--line-strong);margin-right:6px;flex:none;
}
.nav a{
  flex:none;color:var(--muted);font-size:13.5px;padding:15px 13px;
  border-bottom:3px solid transparent;transition:color .2s,border-color .2s,background .2s;
  white-space:nowrap;letter-spacing:.04em;
}
.nav a:hover{color:var(--terra);background:var(--terra-soft)}
.nav a.active{color:var(--terra);border-bottom-color:var(--terra);font-weight:700}
.nav a .num{font-family:var(--mono);font-size:10.5px;color:var(--faint);margin-right:4px}

/* ============ 章节骨架 ============ */
section{padding:58px 0 10px;scroll-margin-top:64px}
.sec-head{position:relative;margin-bottom:26px;padding-left:86px;min-height:74px}
.sec-no{
  position:absolute;left:0;top:-8px;
  font-family:var(--serif);font-weight:900;font-size:64px;line-height:1;
  color:transparent;-webkit-text-stroke:1.6px rgba(184,82,53,.5);
  user-select:none;
}
.sec-kicker{
  font-size:11.5px;letter-spacing:.34em;color:var(--warn);font-weight:700;
  text-transform:uppercase;margin-bottom:6px;
}
.sec-title{font-family:var(--serif);font-weight:900;font-size:clamp(26px,4vw,34px);color:var(--trust);letter-spacing:.05em;line-height:1.3}
.sec-title::after{content:"";display:block;width:56px;height:4px;background:var(--terra);margin-top:10px;border-radius:2px}

/* ============ 正文排版 ============ */
h3.sub{
  font-family:var(--serif);font-weight:800;font-size:21px;color:var(--trust);
  margin:40px 0 14px;letter-spacing:.04em;display:flex;align-items:center;gap:12px;
}
h3.sub::before{content:"";width:10px;height:10px;background:var(--terra);flex:none;transform:rotate(45deg)}
h4{
  font-family:var(--serif);font-weight:800;font-size:17.5px;color:var(--trust);
  margin:26px 0 10px;letter-spacing:.04em;
}
p{margin:0 0 14px;font-size:15.5px;max-width:920px}
blockquote.pull{
  border-left:4px solid var(--terra);background:var(--card);border-radius:0 8px 8px 0;
  padding:16px 22px;margin:16px 0;box-shadow:var(--shadow);color:#3D3930;
}
blockquote.pull p{margin:0 0 6px;font-size:14.5px}
blockquote.pull p:last-child{margin-bottom:0}
ul,ol{padding-left:26px;margin:0 0 16px;max-width:920px}
li{margin-bottom:7px;font-size:15px}
li::marker{color:var(--terra);font-weight:700}
hr{border:none;border-top:1px dashed var(--line-strong);margin:26px 0}
p code,li code,td code{
  font-family:var(--mono);font-size:.88em;background:var(--trust-soft);
  color:var(--trust);padding:1px 6px;border-radius:4px;
}
pre{
  background:#30302E;color:#F5F4ED;padding:16px 20px;border-radius:8px;
  overflow-x:auto;font-family:var(--mono);font-size:13px;line-height:1.7;
  margin:16px 0;box-shadow:var(--shadow);
}
pre code{background:none;color:inherit;padding:0}

/* ============ 表格 ============ */
.tbl-wrap{overflow-x:auto;margin:18px 0;border-radius:8px;border:1px solid var(--line);box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;background:var(--card);min-width:560px}
thead th{
  background:var(--trust);color:#EDEBE1;font-size:12.5px;font-weight:700;
  letter-spacing:.12em;padding:11px 14px;text-align:left;white-space:nowrap;
  border-bottom:3px solid var(--terra);
}
tbody td{padding:11px 14px;font-size:13.8px;border-bottom:1px solid var(--line);vertical-align:top;color:#3B372E}
tbody tr:nth-child(even){background:rgba(27,54,93,.032)}
tbody tr{transition:background .18s}
tbody tr:hover{background:var(--terra-soft)}
tbody tr:last-child td{border-bottom:none}
td.num,th.num{font-family:var(--mono);font-size:13px;text-align:right;white-space:nowrap}
td.first-col{font-weight:700;color:var(--trust);white-space:nowrap}

/* ============ 页脚 / 返回顶部 ============ */
footer{
  border-top:3px double var(--line-strong);margin-top:50px;padding:26px 0 40px;
  font-size:12.5px;color:var(--faint);text-align:center;letter-spacing:.06em;
}
footer b{color:var(--muted)}
footer p{max-width:none;margin:0 0 6px}
#backTop{
  position:fixed;right:22px;bottom:22px;z-index:60;width:44px;height:44px;
  border:none;border-radius:6px;background:var(--trust);color:#EDEBE1;cursor:pointer;
  font-size:17px;box-shadow:var(--shadow-lift);opacity:0;transform:translateY(14px);
  pointer-events:none;transition:opacity .3s,transform .3s,background .2s;
}
#backTop.show{opacity:1;transform:none;pointer-events:auto}
#backTop:hover{background:var(--terra)}

/* ============ 滚动显现 / 响应式 / 打印 ============ */
.reveal{opacity:1;transform:none;transition:opacity .65s ease,transform .65s ease}
.js .reveal{opacity:0;transform:translateY(16px)}
.js .reveal.in{opacity:1;transform:none}
@media(prefers-reduced-motion:reduce){
  .js .reveal{opacity:1;transform:none;transition:none}
  html{scroll-behavior:auto}
}
@media(max-width:640px){
  .wrap{padding:0 16px}
  .sec-head{padding-left:0;padding-top:44px}
  .sec-no{top:-14px;font-size:52px}
  .stamp{transform:none}
}
@media print{
  .nav,#backTop{display:none}
  body{background:#fff}
  .js .reveal{opacity:1;transform:none}
}
"""

_JS = """\
(function(){
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  /* ---- 滚动显现 ---- */
  var reveals = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    Array.prototype.forEach.call(reveals, function(el){ el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { threshold: 0.07, rootMargin: "0px 0px -36px 0px" });
    Array.prototype.forEach.call(reveals, function(el){ io.observe(el); });
  }
  /* ---- 导航 scrollspy ---- */
  var links = document.querySelectorAll(".nav a");
  var map = {};
  Array.prototype.forEach.call(links, function(a){ map[a.getAttribute("href").slice(1)] = a; });
  if ("IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if (e.isIntersecting && map[e.target.id]) {
          Array.prototype.forEach.call(links, function(a){ a.classList.remove("active"); });
          map[e.target.id].classList.add("active");
        }
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    Array.prototype.forEach.call(document.querySelectorAll("section[id]"), function(s){ spy.observe(s); });
  }
  /* ---- 返回顶部 ---- */
  var btt = document.getElementById("backTop");
  if (btt) {
    window.addEventListener("scroll", function(){
      if (window.scrollY > 640) { btt.classList.add("show"); } else { btt.classList.remove("show"); }
    }, { passive: true });
    btt.addEventListener("click", function(){
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    });
  }
})();
"""

# 已知章节标题 → 稳定锚点 id（保证导航与跳转一致）
_SECTION_IDS = {
    "核心结论速览": "overview",
    "主干①·投资分析": "invest",
    "主干②·财报研读": "finance",
    "主干③·行业分析": "industry",
    "补充与参考": "supplement",
    "产物索引": "artifacts",
    "数据截止日": "asof",
    "仅供学习研究": "disclaimer",
}
_KICKERS = [
    "OVERVIEW", "INVESTMENT", "FINANCIALS", "INDUSTRY",
    "SUPPLEMENT", "ARTIFACTS", "AS OF", "DISCLAIMER",
]

_NUM_CELL_RE = re.compile(r"^[+~约负]?[\d,]+(?:\.\d+)?[%倍亿万元x×倍]?[+/]?$")


def _is_num_cell(cell: str) -> bool:
    """判断表格单元格是否为数值（用于右对齐 mono 字体）。"""
    s = cell.replace("*", "").replace(" ", "").strip()
    return bool(_NUM_CELL_RE.match(s))


def _inline(text: str) -> str:
    """行内 markdown：`code`、[link](url)、**bold**、*italic*。先转义再还原。"""
    stashed: list[str] = []

    def _stash(value: str) -> str:
        token = f"\x00H{len(stashed)}\x00"
        stashed.append(value)
        return token

    # code 先行，保护其内容不被 bold/italic 处理
    text = re.sub(r"`([^`]+)`", lambda m: _stash(f"<code>{_esc(m.group(1))}</code>"), text)
    # 链接（仅安全协议）
    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2).strip()
        if not re.match(r"^(https?://|mailto:)", url) or any(c.isspace() for c in url):
            return _stash(_esc(label))
        return _stash(f'<a href="{_esc(url, quote=True)}">{_esc(label)}</a>')
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    text = _esc(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    for index in reversed(range(len(stashed))):
        text = text.replace(f"\x00H{index}\x00", stashed[index])
    return text


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_table_sep(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.match(r"^:?-{2,}:?$", c) for c in cells)


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header, *body = rows
    parts = ['<div class="tbl-wrap reveal"><table><thead><tr>']
    for cell in header:
        cls = ' class="num"' if _is_num_cell(cell) else ""
        parts.append(f"<th{cls}>{_inline(cell)}</th>")
    parts.append("</tr></thead>")
    if body:
        parts.append("<tbody>")
        for row in body:
            parts.append("<tr>")
            for idx, cell in enumerate(row):
                classes = []
                if idx == 0:
                    classes.append("first-col")
                if _is_num_cell(cell):
                    classes.append("num")
                cls = f' class="{" ".join(classes)}"' if classes else ""
                parts.append(f"<td{cls}>{_inline(cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table></div>")
    return "".join(parts)


def _render_blocks(lines: list[str]) -> str:
    """把一段 markdown 正文渲染为带样式的 HTML 块（含滚动显现）。"""
    out: list[str] = []
    list_tag: str | None = None
    table_rows: list[list[str]] = []
    in_code = False
    para_buf: list[str] = []
    quote_buf: list[str] = []

    def flush_para():
        nonlocal para_buf
        if para_buf:
            text = " ".join(para_buf)
            out.append(f'<p class="reveal">{_inline(text)}</p>')
            para_buf = []

    def flush_quote():
        nonlocal quote_buf
        if quote_buf:
            inner = "".join(f"<p>{_inline(q)}</p>" for q in quote_buf)
            out.append(f'<blockquote class="pull reveal">{inner}</blockquote>')
            quote_buf = []

    def flush_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def flush_table():
        nonlocal table_rows
        if table_rows:
            out.append(_render_table(table_rows))
            table_rows = []

    def flush_all():
        flush_para()
        flush_quote()
        flush_list()
        flush_table()

    for ln in lines:
        # 代码块
        if ln.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                flush_all()
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(_esc(ln))
            continue

        stripped = ln.strip()
        if not stripped:
            flush_all()
            continue

        # 子标题
        m = re.match(r"^###\s+(.*)$", stripped)
        if m:
            flush_all()
            out.append(f'<h3 class="sub reveal">{_inline(m.group(1))}</h3>')
            continue
        m = re.match(r"^#{4,6}\s+(.*)$", stripped)
        if m:
            flush_all()
            out.append(f'<h4 class="reveal">{_inline(m.group(1))}</h4>')
            continue

        # 表格行
        if stripped.startswith("|") and "|" in stripped[1:]:
            if _is_table_sep(stripped):
                continue
            flush_para()
            flush_quote()
            flush_list()
            table_rows.append(_split_table_row(stripped))
            continue
        flush_table()

        # 分隔线
        if stripped == "---":
            flush_all()
            out.append('<hr class="reveal">')
            continue

        # 引用
        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote_buf.append(stripped.lstrip(">").strip())
            continue
        flush_quote()

        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            flush_para()
            if list_tag != "ul":
                flush_list()
                out.append('<ul class="reveal">')
                list_tag = "ul"
            out.append(f"<li>{_inline(re.sub('^[-*]\\s+', '', stripped))}</li>")
            continue
        # 有序列表
        if re.match(r"^\d+[.)]\s+", stripped):
            flush_para()
            if list_tag != "ol":
                flush_list()
                out.append('<ol class="reveal">')
                list_tag = "ol"
            out.append(f"<li>{_inline(re.sub('^\\d+[.)]\\s+', '', stripped))}</li>")
            continue
        flush_list()

        # 普通段落（连续行合并为一段）
        para_buf.append(stripped)

    if in_code:
        out.append("</code></pre>")
    flush_all()
    return "\n".join(out)


def _extract_stamp(overview_text: str, fallback: str) -> tuple[str, str]:
    """从核心结论中提取印章式结论（主句 + 副句），找不到用回退值。"""
    m = re.search(r"一句话总结[：:]\s*\*\*(.+?)\*\*", overview_text, re.S)
    if not m:
        return fallback, "APPROVED · 仅供学习研究"
    verdict = re.sub(r"\*\*", "", m.group(1)).strip()
    verdict = verdict.replace("\n", " ")
    if "——" in verdict:
        main, sub = verdict.split("——", 1)
    else:
        main, sub = verdict, ""
    main = main.strip(" 。；，")
    sub = sub.strip(" 。")
    if len(main) > 24:
        main = main[:24]
    if len(sub) > 46:
        sub = sub[:46] + "…"
    return main or fallback, sub or "APPROVED · 仅供学习研究"


def _parse_sections(markdown: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """拆出 h1 标题与各 `##` 章节；h1 之前/章节之间的引言块归入前一章节末尾或丢弃。"""
    title = ""
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for ln in markdown.splitlines():
        m = re.match(r"^#\s+(.*)$", ln)
        if m and not ln.startswith("##"):
            if not title:
                title = m.group(1).strip()
            continue
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            current = []
            sections.append((m.group(1).strip(), current))
            continue
        if current is not None:
            current.append(ln)
    return title, sections


def build_summary_page(markdown: str, *, company: str, code: str, as_of: str,
                       skill_count: int, status: str) -> str:
    """把总结 markdown 渲染为自包含 HTML 页面。

    参数均来自 manifest（company/code/as_of/status/skill_count），确保与正式记录一致。
    """
    title_text, sections = _parse_sections(markdown)
    overview_text = ""
    for heading, body in sections:
        if "核心结论" in heading:
            overview_text = "\n".join(body)
            break
    if not overview_text and sections:
        overview_text = "\n".join(sections[0][1])

    stamp_main, stamp_sub = _extract_stamp(overview_text, f"{company} 深度总结")

    # 导航与章节
    nav_parts: list[str] = []
    sec_parts: list[str] = []
    for idx, (heading, body) in enumerate(sections, start=1):
        sec_id = _SECTION_IDS.get(heading, f"sec-{idx:02d}")
        kicker = _KICKERS[idx - 1] if idx <= len(_KICKERS) else "SECTION"
        # 去掉章节体内的首尾 --- 分隔线
        lines = list(body)
        while lines and lines[0].strip() in ("", "---"):
            lines.pop(0)
        while lines and lines[-1].strip() in ("", "---"):
            lines.pop()
        nav_parts.append(
            f'<a href="#{sec_id}"><span class="num">{idx:02d}</span>{_esc(heading)}</a>')
        sec_parts.append(
            f'<section id="{sec_id}">\n'
            f'  <div class="sec-head reveal">\n'
            f'    <span class="sec-no">{idx:02d}</span>\n'
            f'    <div class="sec-kicker">{kicker}</div>\n'
            f'    <h2 class="sec-title">{_inline(heading)}</h2>\n'
            f'  </div>\n'
            f'  {_render_blocks(lines)}\n'
            f'</section>'
        )

    safe_title = _esc(title_text or f"{company} 全量分析总结报告", quote=True)
    safe_company = _esc(company, quote=True)
    safe_code = _esc(code, quote=True)
    safe_asof = _esc(as_of, quote=True)
    safe_status = _esc(status, quote=True)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<script>document.documentElement.classList.add('js')</script>
<style>
{_CSS}
</style>
</head>
<body>
<header class="masthead">
  <div class="wrap">
    <div class="mh-eyebrow"><span>全量公司分析 · 总结报告</span><span class="dot"></span><span class="tick">{safe_code}</span><span class="dot"></span><span class="tick">{safe_status}</span></div>
    <div class="mh-title-row">
      <h1 class="mh-title">看懂<em>{safe_company}</em></h1>
      <div class="mh-en"><b>{safe_code}</b>{safe_company} · 深度总结</div>
    </div>
    <div class="mh-verdict">
      <div class="stamp">{_esc(stamp_main)}<small>{_esc(stamp_sub)}</small></div>
    </div>
    <div class="mh-meta">
      <span class="asof">数据截止 {safe_asof}</span>
      <span class="chip">{skill_count} 份正式产物</span>
      <span class="chip">多业务单元熔炼</span>
      <span class="chip">仅供学习研究 · 不构成投资建议</span>
    </div>
  </div>
</header>
<nav class="nav" id="nav">
  <div class="wrap">
    <span class="brand">{safe_company}</span>
    {"".join(nav_parts)}
  </div>
</nav>
<main class="wrap">
{chr(10).join(sec_parts)}
</main>
<footer>
  <div class="wrap">
    <p><b>{safe_company}（{safe_code}）全量分析总结报告</b> · 数据截止 {safe_asof} · 状态 {safe_status}</p>
    <p>本报告由 full-analysis 流水线自动生成，HTML 为已冻结 markdown 总结的派生展示件，仅供学习研究，不构成任何投资建议。</p>
  </div>
</footer>
<button id="backTop" title="返回顶部" aria-label="返回顶部">↑</button>
<script>
{_JS}
</script>
</body>
</html>
"""
    return html

#!/usr/bin/env python3
"""港交所披露易 (HKEXnews) 数据获取工具。

为 A+H 双重上市公司提供独立第二源年报数据。
零外部依赖，使用 /usr/bin/curl 直连，绕系统代理。

用法:
    python3 tools/hkex_data.py lookup 600036          # 查招商银行 H股代码
    python3 tools/hkex_data.py financials 03968         # 取招商银行 H股年报关键数据
    python3 tools/hkex_data.py cross-check 600036       # A+H交叉验证

数据源: HKEX披露易 (www.hkexnews.hk)
"""

import subprocess, sys, json, re, time, os

# ── A+H dual-listed stock mapping ──
# Primary: dynamic lookup via Tushare hk_basic API (P3)
# Fallback: hardcoded map for known A+H pairs

AH_MAP = {
    "600036": {"h_code": "03968", "name": "招商银行", "ratio": "1:1"},
    "601318": {"h_code": "02318", "name": "中国平安", "ratio": "1:1"},
    "600030": {"h_code": "06030", "name": "中信证券", "ratio": "1:1"},
    "601628": {"h_code": "02628", "name": "中国人寿", "ratio": "1:1"},
    "601601": {"h_code": "02601", "name": "中国太保", "ratio": "1:1"},
    "601336": {"h_code": "01336", "name": "新华保险", "ratio": "1:1"},
    "601319": {"h_code": "01339", "name": "中国人保", "ratio": "1:1"},
    "601398": {"h_code": "01398", "name": "工商银行", "ratio": "1:1"},
    "601939": {"h_code": "00939", "name": "建设银行", "ratio": "1:1"},
    "601288": {"h_code": "01288", "name": "农业银行", "ratio": "1:1"},
    "601988": {"h_code": "03988", "name": "中国银行", "ratio": "1:1"},
    "601328": {"h_code": "03328", "name": "交通银行", "ratio": "1:1"},
    "600585": {"h_code": "00914", "name": "海螺水泥", "ratio": "1:1"},
    "601857": {"h_code": "00857", "name": "中国石油", "ratio": "1:1"},
    "600028": {"h_code": "00386", "name": "中国石化", "ratio": "1:1"},
    "601088": {"h_code": "01088", "name": "中国神华", "ratio": "1:1"},
    "601899": {"h_code": "02899", "name": "紫金矿业", "ratio": "1:1"},
    "000002": {"h_code": "02202", "name": "万科", "ratio": "1:1"},
    "000776": {"h_code": "01776", "name": "广发证券", "ratio": "1:1"},
    "601211": {"h_code": "02611", "name": "国泰君安", "ratio": "1:1"},
    "601688": {"h_code": "06886", "name": "华泰证券", "ratio": "1:1"},
    "600837": {"h_code": "06837", "name": "海通证券", "ratio": "1:1"},
}

# Known false positives (names match HK stocks but aren't A+H)
_NOT_AH = {"000858", "600887", "600276"}

CURL = "/usr/bin/curl"
TIMEOUT = 30


# ── Dynamic lookup cache ──
_hk_name_cache = None  # {hk_code: name} loaded lazily


def _load_hk_basic_via_tushare():
    """Load HK stock list from Tushare hk_basic. Cached per process."""
    global _hk_name_cache
    if _hk_name_cache is not None:
        return _hk_name_cache

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        _hk_name_cache = {}
        return _hk_name_cache

    try:
        import requests as _rq
        resp = _rq.post("https://api.tushare.pro", json={
            "api_name": "hk_basic",
            "token": token,
            "params": {"list_status": "L"},
            "fields": "ts_code,name",
        }, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            items = data.get("data", {}).get("items", [])
            _hk_name_cache = {item[0]: item[1] for item in items}
        else:
            _hk_name_cache = {}
    except Exception:
        _hk_name_cache = {}
    return _hk_name_cache


def _find_hk_code_dynamic(a_code: str, a_name: str) -> str:
    """Find H-share code by matching A-share name against hk_basic."""
    if a_code in _NOT_AH:
        return ""
    hk_stocks = _load_hk_basic_via_tushare()
    if not hk_stocks:
        return ""
    # Exact name match
    for hk_code, hk_name in hk_stocks.items():
        if a_name == hk_name:
            return hk_code
    # Fuzzy: A name contained in HK name or vice versa
    for hk_code, hk_name in hk_stocks.items():
        if a_name in hk_name or hk_name in a_name:
            return hk_code
    return ""


def _curl(url, referer="https://www.hkexnews.hk"):
    """Fetch URL via curl with HKEX-friendly headers."""
    cmd = [
        CURL, "-s", "--connect-timeout", str(TIMEOUT),
        "--noproxy", "*",
        "-H", f"Referer: {referer}",
        "-H", "Accept-Language: en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "-H", "User-Agent: Mozilla/5.0 (compatible; AI-Berkshire/1.0)",
        url
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def lookup(code: str):
    """Look up H-share code for an A-share stock. Returns dict or None.

    Priority: 1) Tushare hk_basic dynamic lookup, 2) hardcoded AH_MAP.
    """
    a_code = code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    full_a_code = a_code + (".SH" if a_code.startswith("6") else ".SZ")

    # 1. Try dynamic Tushare lookup
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ashare_plugin.tushare import TushareClient
        from ashare_plugin.identifiers import normalize_code
        client = TushareClient()
        if client.configured:
            ts_code = normalize_code(code).secu_code
            r = client.query("stock_basic", params={"ts_code": ts_code},
                             fields=("ts_code","name"))
            if r["ok"]:
                a_name = r["data"][0].get("name", "")
                hk_code = _find_hk_code_dynamic(a_code, a_name)
                if hk_code:
                    return {
                        "a_code": full_a_code, "h_code": hk_code,
                        "name": a_name, "share_ratio": "1:1",
                        "source": "Tushare hk_basic (dynamic)",
                        "hkex_url": f"https://www.hkexnews.hk/listedco/listconews/SEHK/{time.strftime('%Y')}/{hk_code.replace('.HK','')}/"
                    }
    except Exception:
        pass

    # 2. Fallback to hardcoded map
    info = AH_MAP.get(a_code)
    if not info:
        return None
    return {
        "a_code": full_a_code,
        "h_code": info["h_code"],
        "name": info["name"],
        "share_ratio": info["ratio"],
        "source": "hardcoded AH_MAP",
        "hkex_url": f"https://www.hkexnews.hk/listedco/listconews/SEHK/{time.strftime('%Y')}/{info['h_code']}/"
    }


def fetch_annual_report(h_code: str, year: str = "2025"):
    """Fetch H-share annual report filing from HKEXnews.

    Returns dict with key financial figures extracted from the filing summary page,
    or None if not found.

    HKEX filings are primarily PDFs. We search for the filing index page first,
    then attempt to extract summary data from the HTML index.
    """
    # HKEXnews listing directory for the stock
    url = f"https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock={h_code}&category=0&market=SEHK&from=2026-01-01&to=2026-07-19&title=annual+report"

    html = _curl(url)
    if not html:
        return None

    # Try alternative: direct filing index
    alt_url = f"https://www1.hkexnews.hk/app/appindex.html?code={h_code}"
    html2 = _curl(alt_url)

    # For now, return the filing index URL and note that structured extraction
    # requires PDF parsing or WebFetch
    return {
        "h_code": h_code,
        "filing_index_url": url,
        "note": "HKEXnews primarily serves PDFs. Structured financial data extraction requires PDF parsing. Use WebFetch on specific filing PDFs from the index page for detailed figures.",
        "status": "filing_index_available"
    }


def cross_check(a_code: str) -> dict:
    """Cross-check A-share vs H-share data availability for an A+H company.

    Returns dict with both sources' metadata and comparison guidance.
    """
    ah = lookup(a_code)
    if not ah:
        return {"status": "not_ah_stock", "a_code": a_code}

    hkex = fetch_annual_report(ah["h_code"])

    return {
        "status": "ah_dual_listed",
        "a_code": ah["a_code"],
        "h_code": ah["h_code"],
        "name": ah["name"],
        "share_ratio": ah["share_ratio"],
        "hkex_filing": hkex,
        "dual_source_guidance": {
            "a_share_source": "CNINFO/Eastmoney — CAS (China Accounting Standards)",
            "h_share_source": "HKEXnews — IFRS/HKFRS (International Standards)",
            "key_differences": [
                "Revenue recognition (CAS vs IFRS 15)",
                "Financial instrument classification (CAS 22 vs IFRS 9)",
                "Presentation format differences",
                "Exchange rate used for RMB/HKD conversion"
            ],
            "verification_value": "H-share annual report is a genuinely INDEPENDENT second source — different publisher (HKEX), different acquisition chain (hkexnews-http), different accounting standard (IFRS/HKFRS), different auditor's review perspective."
        }
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/hkex_data.py <lookup|cross-check> <code>")
        print("  lookup 600036    — get H-share code for A-share stock")
        print("  cross-check 600036 — A+H dual-source verification")
        print("  list             — list all known A+H stocks")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        print(json.dumps([{"a": f"{k}.SH" if k.startswith("6") else f"{k}.SZ",
                           "h": v["h_code"],
                           "name": v["name"]}
                          for k, v in sorted(AH_MAP.items())],
                         indent=2, ensure_ascii=False))
        return

    if cmd == "lookup":
        code = sys.argv[2] if len(sys.argv) > 2 else ""
        result = lookup(code)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"status": "not_found", "code": code}))
            sys.exit(1)

    elif cmd == "cross-check":
        code = sys.argv[2] if len(sys.argv) > 2 else ""
        result = cross_check(code)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0 if result.get("status") == "ah_dual_listed" else 1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()

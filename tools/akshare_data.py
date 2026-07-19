#!/usr/bin/env python3
"""AKShare 历史价格与估值数据工具。

为全量分析管线提供前复权历史价格序列、历史 PE/PB 分位、
历史估值区间等关键能力（ashare_data.py 明确标记的数据陷阱 #3）。

数据源: 腾讯证券 (proxy.finance.qq.com) —— 与 ashare_data.py 行情同源，
        不依赖 push2his.eastmoney.com（该端点已于 2026 年封禁非浏览器请求）。

依赖: akshare (pip install akshare --break-system-packages)
      AKShare 是社区维护的 Python 金融数据接口库。

用法:
    python3 tools/akshare_data.py price 600036 --years 5     # 5年前复权日线
    python3 tools/akshare_data.py pe-band 600036 --years 10  # 10年PE带
    python3 tools/akshare_data.py summary 600036             # 历史估值摘要
"""

import json, sys, os
from datetime import datetime

# ── Proxy bypass: AKShare uses requests → urllib3 which on macOS reads ──
# ── proxy config from env vars AND System Configuration (_scproxy).    ──
# ── trust_env=False blocks ALL sources: env vars + macOS system proxy. ──
# ── ashare_data.py / hkex_data.py are unaffected (they use curl).       ──

for _k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
           'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_k, None)
os.environ['NO_PROXY'] = '*'

import requests as _requests

_orig_session_init = _requests.Session.__init__
def _patched_init(self):
    _orig_session_init(self)
    self.trust_env = False
_requests.Session.__init__ = _patched_init


def _code_to_tx(code: str) -> str:
    """Convert A-share code to Tencent format: 600036 → sh600036, 000651 → sz000651."""
    code = code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if code.startswith("6") or code.startswith("9"):
        return f"sh{code}"
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return f"sz{code}"
    elif code.startswith("4") or code.startswith("8"):
        return f"bj{code}"
    else:
        return f"sh{code}"  # best-effort default


def _get_hist(code: str, years: int = 10):
    """Fetch historical daily data with forward-adjusted prices via Tencent API.

    Uses AKShare stock_zh_a_hist_tx which queries proxy.finance.qq.com —
    same source as ashare_data.py quote endpoint. Not affected by the
    push2his.eastmoney.com anti-bot lockdown.

    Returns (DataFrame, None) on success, (None, error_message) on failure.
    Column names are English: date, open, close, high, low, amount.
    """
    try:
        import akshare as ak
    except ImportError:
        return None, "AKShare not installed. Run: pip install akshare --break-system-packages"

    tx_symbol = _code_to_tx(code)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = f"{datetime.now().year - years}0101"

    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=tx_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",  # 前复权
        )
        if df is None or df.empty:
            return None, f"No data returned for {code} ({tx_symbol})"
        return df, None
    except Exception as e:
        return None, str(e)


def cmd_price(code: str, years: int = 5):
    """Fetch historical prices with forward adjustment."""
    df, err = _get_hist(code, years)
    if err:
        print(json.dumps({"status": "error", "message": err}))
        sys.exit(1)

    latest = df.iloc[-1]
    oldest = df.iloc[0]
    result = {
        "status": "ok",
        "code": code,
        "source": "腾讯证券 (proxy.finance.qq.com)",
        "adjust": "qfq（前复权）",
        "period": f"{df.iloc[0]['date']} - {df.iloc[-1]['date']}",
        "trading_days": len(df),
        "latest": {
            "date": str(latest["date"]),
            "close": float(latest["close"]),
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "amount": float(latest["amount"]),
        },
        "price_range": {
            "min": round(float(df["low"].min()), 2),
            "max": round(float(df["high"].max()), 2),
            "min_date": str(df.loc[df["low"].idxmin(), "date"]),
            "max_date": str(df.loc[df["high"].idxmax(), "date"]),
        },
        "returns": {
            f"{years}y_total_pct": round((float(latest["close"]) / float(oldest["close"]) - 1) * 100, 2),
            "annualized_pct": round(((float(latest["close"]) / float(oldest["close"])) ** (1 / max(years, 1)) - 1) * 100, 2),
        }
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_pe_band(code: str, years: int = 10):
    """Compute historical price distribution (PE band requires external EPS data)."""
    df, err = _get_hist(code, years)
    if err:
        print(json.dumps({"status": "error", "message": err}))
        sys.exit(1)

    close_series = df["close"]
    current = float(df.iloc[-1]["close"])
    result = {
        "status": "ok",
        "code": code,
        "source": "腾讯证券 (proxy.finance.qq.com)",
        "adjust": "qfq（前复权）",
        "years": years,
        "note": "价格为腾讯前复权日线。每日 PE 序列需 Tushare daily_basic "
                "或 Wind。可用 ashare_data financials 的年度 EPS + 当前价格"
                "估算 PE 分位。",
        "price_distribution": {
            "current": round(current, 2),
            "p10": round(float(close_series.quantile(0.10)), 2),
            "p25": round(float(close_series.quantile(0.25)), 2),
            "p50": round(float(close_series.quantile(0.50)), 2),
            "p75": round(float(close_series.quantile(0.75)), 2),
            "p90": round(float(close_series.quantile(0.90)), 2),
            "current_percentile": round((close_series <= current).mean() * 100, 1),
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_summary(code: str, years: int = 5, current_pe: float = None, current_pb: float = None):
    """Generate a valuation summary suitable for investment-research artifact."""
    df, err = _get_hist(code, years)
    if err:
        print(json.dumps({"status": "error", "message": err}))
        sys.exit(1)

    close_series = df["close"]
    latest = float(df.iloc[-1]["close"])
    low = float(df["low"].min())
    high = float(df["high"].max())
    p10 = float(close_series.quantile(0.10))
    p25 = float(close_series.quantile(0.25))
    p50 = float(close_series.quantile(0.50))
    p75 = float(close_series.quantile(0.75))
    p90 = float(close_series.quantile(0.90))
    pct = round((close_series <= latest).mean() * 100, 1)

    summary = {
        "status": "ok",
        "code": code,
        "source": "腾讯证券 (proxy.finance.qq.com)",
        "adjust": "qfq（前复权）",
        "years": years,
        "current_price": round(latest, 2),
        f"{years}y_price_range": {"low": round(low, 2), "high": round(high, 2)},
        f"{years}y_price_percentiles": {
            "p10": round(p10, 2), "p25": round(p25, 2),
            "p50": round(p50, 2), "p75": round(p75, 2),
            "p90": round(p90, 2),
        },
        "current_percentile": f"{pct}%（{'低于历史中位' if pct < 50 else '高于历史中位'}）",
        "price_vs_p50": f"{'低于' if latest < p50 else '高于'}中位数{abs(round((latest/p50-1)*100,1))}%",
    }

    # If PE/PB provided, add valuation context
    if current_pe and current_pb:
        pe = float(current_pe)
        pb = float(current_pb)
        summary["valuation_context"] = {
            "current_pe": pe,
            "current_pb": pb,
            "pe_level": ("历史低位" if pe < 10 else
                         "历史中位" if pe < 20 else
                         "历史高位"),
            "pb_level": ("破净（历史极端低位）" if pb < 1 else
                        "历史低位" if pb < 1.5 else
                        "历史中位" if pb < 3 else
                        "历史高位"),
        }

    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/akshare_data.py <price|pe-band|summary> <code> [--years N] [--pe PE] [--pb PB]")
        print("  price 600036 --years 5      — 5年前复权日线价格")
        print("  pe-band 600036 --years 10    — 10年PE估值分位（价格分布）")
        print("  summary 600036 --pe 6.36 --pb 0.85  — 估值摘要")
        sys.exit(1)

    cmd = sys.argv[1]
    code = sys.argv[2] if len(sys.argv) > 2 else ""

    # Parse optional args
    years = 5
    pe_val, pb_val = None, None
    for i, arg in enumerate(sys.argv):
        if arg == "--years" and i + 1 < len(sys.argv):
            years = int(sys.argv[i + 1])
        if arg == "--pe" and i + 1 < len(sys.argv):
            pe_val = float(sys.argv[i + 1])
        if arg == "--pb" and i + 1 < len(sys.argv):
            pb_val = float(sys.argv[i + 1])

    if cmd == "price":
        cmd_price(code, years)
    elif cmd == "pe-band":
        cmd_pe_band(code, years)
    elif cmd == "summary":
        cmd_summary(code, years, pe_val, pb_val)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(2)


if __name__ == "__main__":
    main()

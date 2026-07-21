#!/usr/bin/env python3
"""批量全量分析：一次收集 18 家公司数据并顺序执行 run_full_analysis.py"""
import json, os, re, subprocess, time

REPO = "/Users/psilhon/WorkSpace/stock/berkshire"
ASHARE = f"{REPO}/tools/ashare_data.py"
RUNNER = f"{REPO}/scripts/run_full_analysis.py"
AS_OF = "2026-07-19"

COMPANIES = [
    # (公司名, 代码, 行业, 是否金融)
    ("恒瑞医药", "600276.SH", "医药", 0),
    ("百济神州", "688235.SH", "医药", 0),
    ("中信证券", "600030.SH", "证券", 1),
    ("广发证券", "000776.SZ", "证券", 1),
    ("东方财富", "300059.SZ", "证券", 1),
    ("美的集团", "000333.SZ", "家电", 0),
    ("格力电器", "000651.SZ", "家电", 0),
    ("汇川技术", "300124.SZ", "工业自动化", 0),
    ("拓普集团", "601689.SH", "汽车零部件", 0),
    ("长江电力", "600900.SH", "电力", 0),
    ("北方华创", "002371.SZ", "半导体设备", 0),
    ("中微公司", "688012.SH", "半导体设备", 0),
    ("韦尔股份", "603501.SH", "半导体设计", 0),
    ("兆易创新", "603986.SH", "半导体设计", 0),
    ("佰维存储", "688525.SH", "存储", 0),
    ("中国平安", "601318.SH", "保险", 1),
    ("三花智控", "002050.SZ", "汽车零部件", 0),
    ("中际旭创", "300308.SZ", "光通信", 0),
]

def run_cmd(cmd, timeout=45):
    """Run command with timeout, return (returncode, stdout)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return -1, ""
    except Exception as e:
        return -1, str(e)

def parse_float(text, pattern, default="0.0"):
    m = re.search(pattern, text)
    return m.group(1) if m else default

def collect_company(name, code, industry, is_financial):
    """Collect all data needed for run_full_analysis.py from ashare_data.py."""
    prefix = code[:6]
    print(f"  📊 收集 {name} ({code})...", flush=True)

    data = {"name": name, "code": code, "industry": industry, "is_financial": is_financial}

    # 1. Quote
    rc, out = run_cmd(["python3", ASHARE, "quote", prefix])
    if rc != 0:
        print(f"    ❌ quote failed: rc={rc}")
        return None
    data["price"] = parse_float(out, r'当前价:\s+([\d.]+)', "0")
    data["mcap"] = parse_float(out, r'总市值:\s+([\d.]+)亿', "0")
    data["pe"] = parse_float(out, r'PE\(动\):\s+([\d.]+)', "0")
    data["pb"] = parse_float(out, r'PB:\s+([\d.]+)', "0")
    # Try Tushare PE/PB if available
    t_pe = re.search(r'Tushare 覆盖: pe [\d.]+\s*->\s*([\d.]+)', out)
    if t_pe: data["pe"] = t_pe.group(1)
    t_pb = re.search(r'Tushare 覆盖: pb [\d.]+\s*->\s*([\d.]+)', out)
    if t_pb: data["pb"] = t_pb.group(1)

    # 2. Valuation (for shares, eps, bps, div)
    rc, out = run_cmd(["python3", ASHARE, "valuation", prefix])
    if rc == 0:
        data["shares"] = parse_float(out, r'推算总股本:\s+([\d.]+)亿股', "0")
        # EPS and BPS from valuation output
        # Actually, let's get them from financials instead

    # 3. Financials (for EPS, BPS, ROE, revenue, net profit, growth)
    rc, out = run_cmd(["python3", ASHARE, "financials", prefix])
    if rc != 0:
        print(f"    ❌ financials failed: rc={rc}")
        return None
    # Parse latest year (2025)
    data["eps"] = "0"
    data["bps"] = "0"
    data["roe"] = "0"
    data["rev_2025"] = "0"
    data["ni_2025"] = "0"
    data["rev_g"] = "0"
    data["ni_g"] = "0"

    # Find 2025 data block
    m2025 = re.search(r'--- 2025-12-31 2025年报 ---(.*?)(?=--- 2024|$)', out, re.DOTALL)
    if m2025:
        block = m2025.group(1)
        data["rev_2025"] = parse_float(block, r'营收:\s+([\d.]+)亿', "0")
        data["ni_2025"] = parse_float(block, r'归母净利润:\s+([\d.]+)亿', "0")
        data["rev_g"] = parse_float(block, r'营收增速:\s+([\d.]+)%', "0")
        data["ni_g"] = parse_float(block, r'净利润增速:\s+([\d.]+)%', "0")
        data["eps"] = parse_float(block, r'基本每股收益:\s+([\d.]+)', "0")
        data["bps"] = parse_float(block, r'每股净资产:\s+([\d.]+)', "0")
        data["roe"] = parse_float(block, r'ROE\(加权\):\s+([\d.]+)%', "0")

    # 4. History (for 10-year averages, gross margin, OCF/NI, interest cover)
    rc, out = run_cmd(["python3", ASHARE, "history", prefix, "--years", "10"], timeout=60)
    if rc == 0:
        roes = []; margins = []; ocf_nis = []; interests = []
        for m in re.finditer(r'ROE\(加权\):\s+([\d.]+)%', out):
            roes.append(float(m.group(1)))
        for m in re.finditer(r'净利率:\s+([\d.]+)%', out):
            margins.append(float(m.group(1)))
        for m in re.finditer(r'经营现金流/净利润:\s+([\d.-]+)x', out):
            try: ocf_nis.append(float(m.group(1)))
            except: ocf_nis.append(0)
        for m in re.finditer(r'利息覆盖:\s+([\d.]+)x', out):
            try: interests.append(float(m.group(1)))
            except: pass
        # Gross margin from latest year
        gm_match = re.search(r'毛利率:\s+([\d.]+)%', out)
        data["gross_margin"] = gm_match.group(1) if gm_match else ("0" if is_financial else "30")

        if roes:
            data["roe_10y"] = f"{sum(roes)/len(roes):.2f}"
        else:
            data["roe_10y"] = data["roe"]
        if margins:
            data["net_margin"] = f"{sum(margins)/len(margins):.2f}"
        else:
            data["net_margin"] = "10"
        if ocf_nis:
            # Use 5-year average
            recent_5 = ocf_nis[:min(5, len(ocf_nis))]
            data["ocf_ni"] = f"{sum(recent_5)/len(recent_5):.2f}"
        else:
            data["ocf_ni"] = "0" if is_financial else "1.0"
        if interests:
            recent_5_i = interests[:min(5, len(interests))]
            data["interest_cover"] = f"{sum(recent_5_i)/len(recent_5_i):.2f}"
        else:
            data["interest_cover"] = "0" if is_financial else "10"
    else:
        data["gross_margin"] = "0" if is_financial else "30"
        data["roe_10y"] = data["roe"]
        data["net_margin"] = "10"
        data["ocf_ni"] = "0" if is_financial else "1.0"
        data["interest_cover"] = "0" if is_financial else "10"

    # 5. Equity history for shares change
    rc, out = run_cmd(["python3", ASHARE, "equity-history", prefix])
    if rc == 0:
        # Try to find 5-year shares change
        shares_vals = []
        for m in re.finditer(r'总股本:\s+([\d.]+)亿', out):
            shares_vals.append(float(m.group(1)))
        if len(shares_vals) >= 5:
            oldest = shares_vals[-1] if shares_vals[-1] > 0 else 1
            newest = shares_vals[0]
            data["shares_chg"] = f"{(newest - oldest) / oldest * 100:.1f}"
        elif shares_vals:
            data["shares_chg"] = "0"
        else:
            data["shares_chg"] = "0"
    else:
        data["shares_chg"] = "0"

    # Also get shares from valuation if not set
    if data.get("shares", "0") == "0" and data["mcap"] != "0" and data["price"] != "0":
        try:
            data["shares"] = f"{float(data['mcap']) / float(data['price']):.2f}"
        except: pass

    # 6. Dividend - try from signals
    rc, out = run_cmd(["python3", ASHARE, "signals", prefix])
    div_yield = "0"
    if rc == 0:
        dm = re.search(r'股息率:\s*([\d.]+)%', out)
        if dm: div_yield = dm.group(1)
    data["div_yield"] = div_yield

    # dividend per share from div_yield and price
    try:
        data["div"] = f"{float(data['price']) * float(div_yield) / 100:.4f}"
    except:
        data["div"] = "0.5"

    # Industry labels (simple default)
    data["ind_labels"] = json.dumps([(industry, "100")], ensure_ascii=False)

    print(f"    ✅ PE={data['pe']} PB={data['pb']} ROE={data['roe']}% "
          f"营收={data['rev_2025']}亿(+{data['rev_g']}%) 净利={data['ni_2025']}亿(+{data['ni_g']}%) "
          f"ROE10y={data['roe_10y']}% 净利率={data['net_margin']}% "
          f"毛利率={data['gross_margin']}% OCF/NI={data['ocf_ni']}x", flush=True)
    return data


def main():
    results = {}
    for name, code, industry, is_fin in COMPANIES:
        data = collect_company(name, code, industry, is_fin)
        if data:
            results[name] = data
        else:
            print(f"  ❌ 跳过 {name}——数据收集失败")
        time.sleep(1)  # Rate limit

    # Save collected data
    out_path = os.path.join(REPO, "local", "batch_collected_data.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n📦 已保存 {len(results)}/{len(COMPANIES)} 家公司数据到 {out_path}")

    # Now run pipeline for each company
    print("\n" + "="*60)
    print("开始顺序执行全量分析管线")
    print("="*60)

    outcomes = {}
    for name, code, industry, is_fin in COMPANIES:
        if name not in results:
            print(f"\n⏭️ 跳过 {name}——无数据")
            continue

        d = results[name]
        print(f"\n{'='*60}")
        print(f"🏭 {name} ({code}) — {industry}")
        print(f"{'='*60}")

        args = [
            "python3", RUNNER,
            name, code, industry, str(is_fin),
            d["price"], d["shares"], d["mcap"], d["pe"], d["pb"],
            d["eps"], d["bps"], d["roe"], d["div"],
            d["rev_2025"], d["ni_2025"], d["rev_g"], d["ni_g"],
            d["div_yield"], d["roe_10y"], d["net_margin"], d["shares_chg"],
            d["gross_margin"], d["ocf_ni"], d["interest_cover"],
            d["ind_labels"]
        ]

        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=300, cwd=REPO)
            print(r.stdout[-500:] if len(r.stdout) > 500 else r.stdout)
            if r.stderr:
                print(f"STDERR: {r.stderr[-300:]}")
            outcomes[name] = {"rc": r.returncode, "output": r.stdout[-200:]}
        except subprocess.TimeoutExpired:
            print(f"  ⏰ {name} 超时（5分钟）")
            outcomes[name] = {"rc": -1, "output": "TIMEOUT"}
        except Exception as e:
            print(f"  ❌ {name} 异常: {e}")
            outcomes[name] = {"rc": -1, "output": str(e)}

    # Summary
    print("\n" + "="*60)
    print("📊 批量全量分析完成")
    print("="*60)
    for name, outcome in outcomes.items():
        status = "✅" if outcome["rc"] == 0 else "❌"
        print(f"  {status} {name}: rc={outcome['rc']}")

    # Save outcomes
    out_path2 = os.path.join(REPO, "local", "batch_outcomes.json")
    with open(out_path2, 'w') as f:
        json.dump(outcomes, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存到 {out_path2}")


if __name__ == "__main__":
    main()

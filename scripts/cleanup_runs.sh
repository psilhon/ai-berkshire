#!/bin/bash
# Clean up round 5 run directories for 18 companies
COMPANIES=("恒瑞医药" "百济神州" "中信证券" "广发证券" "东方财富" "美的集团" "格力电器" "汇川技术" "拓普集团" "长江电力" "北方华创" "中微公司" "韦尔股份" "兆易创新" "佰维存储" "中国平安" "三花智控" "中际旭创")
BASE="/Users/psilhon/WorkSpace/stock/berkshire/local/筛选公司"

for name in "${COMPANIES[@]}"; do
  for rd in "$BASE/$name/全量分析"/20260719T194*; do
    if [ -d "$rd" ]; then
      rm -rf "$rd"
      echo "Deleted: $rd"
    fi
  done 2>/dev/null
done
echo "Cleanup complete"

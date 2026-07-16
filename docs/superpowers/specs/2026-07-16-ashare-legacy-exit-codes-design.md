# A 股旧命令退出码修复设计

## 背景与根因

`tools/ashare_data.py` 的 `quote`、`financials`、`valuation`、`search` 在无数据时直接返回 `None`；成功路径同样隐式返回 `None`。CLI 入口只把显式 `False` 映射为退出码 1，因此四个失败场景均错误地以 0 退出。

## 目标

- 四个旧命令成功且有数据时返回 `True`，CLI 退出码为 0。
- 无数据或已知请求失败时返回 `False`，CLI 退出码为 1。
- 参数错误继续返回 2。
- 保持原有成功输出、数据来源和命令参数不变。

## 实现设计

每个命令函数显式返回布尔值：

- `cmd_quote`、`cmd_valuation`：行情无法获取或无法解析时返回 `False`；完整输出后返回 `True`。
- `cmd_financials`：保留年报查询及原有回退查询；两次都没有数据时返回 `False`，输出财务数据后返回 `True`。
- `cmd_search`：无匹配结果时返回 `False`，输出至少一条结果后返回 `True`。
- 已知网络/解析失败转换为面向用户的 stderr 错误并返回 `False`，不打印 traceback。

CLI 入口已经具备 `outcome is False -> sys.exit(1)`，无需新增第二套退出码映射。

## 测试

在 `tests/test_ashare_data.py` 使用固定 mock 响应覆盖：

1. 四个命令无数据时均返回 `False`。
2. 四个命令获得最小有效数据时均返回 `True`。
3. 已知请求失败不会泄漏 traceback，并返回 `False`。
4. CLI 命令函数返回 `False` 时退出 1，返回 `True` 时退出 0。
5. `bash scripts/check.sh` 全部通过。

## 范围

本次只修改 `tools/ashare_data.py` 与 `tests/test_ashare_data.py`。不改接口字段、展示格式、README、skills、CI、报告文件或用户未提交改动。

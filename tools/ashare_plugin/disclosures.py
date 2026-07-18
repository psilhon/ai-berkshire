from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import DataResult, failure_result, success_result
from .errors import TransportError
from .identifiers import normalize_code
from .transport import FallbackChain, TransportClient


CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STOCK_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
SZSE_URL = "https://www.szse.cn/api/disc/announcement/annList"
EM_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"


def _date_from_ms(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(value or "")[:10]


def _cninfo_rows(payload: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    rows = payload.get("announcements") or payload.get("data") or []
    return [
        {
            "title": row.get("announcementTitle") or row.get("title") or "",
            "date": _date_from_ms(row.get("announcementTime") or row.get("publishTime")),
            "type": row.get("columnName") or row.get("announcementType") or "",
            "pdf": row.get("adjunctUrl") or row.get("pdf") or "",
        }
        for row in rows[:limit]
    ]


def _cninfo(identity, limit: int, client: TransportClient) -> DataResult:
    stocks = client.get_json(
        CNINFO_STOCK_URL,
        headers={"Referer": "https://www.cninfo.com.cn/"},
    ).get("stockList") or []
    org_id = next(
        (row.get("orgId") for row in stocks
         if str(row.get("code") or "") == identity.code),
        None,
    )
    if not org_id:
        return failure_result("cninfo", "empty_data", "巨潮证券标识为空")
    column, plate = {
        "SZ": ("szse", "sz"),
        "SH": ("sse", "sh"),
        "BJ": ("third", ""),
    }[identity.market]
    payload = {
        "pageNum": 1,
        "pageSize": limit,
        "column": column,
        "tabName": "fulltext",
        "plate": plate,
        "stock": f"{identity.code},{org_id}",
        "searchkey": "",
        "secid": "",
    }
    if hasattr(client, "post_json"):
        data = client.post_json(CNINFO_URL, data=payload, headers={"Referer": "https://www.cninfo.com.cn/"})
    else:
        data = client.get_json(CNINFO_URL, params=payload)
    rows = _cninfo_rows(data, limit)
    if not rows:
        return failure_result("cninfo", "empty_data", "巨潮公告为空")
    return success_result(rows, "cninfo")


def _szse(code: str, limit: int, client: TransportClient) -> DataResult:
    payload = {"channelCode": ["listedNotice_disc"], "pageSize": limit, "pageNum": 1, "stock": [code]}
    if hasattr(client, "post_json"):
        data = client.post_json(
            SZSE_URL,
            data=payload,
            headers={"Referer": "https://www.szse.cn/"},
            json_body=True,
        )
    else:
        data = client.get_json(SZSE_URL, params=payload)
    rows = data.get("data") or []
    normalized = [
        {
            "title": row.get("title") or "",
            "date": str(row.get("publishTime") or "")[:10],
            "type": row.get("typeName") or "",
            "pdf": "https://disc.static.szse.cn/download" + (row.get("attachPath") or ""),
        }
        for row in rows[:limit]
    ]
    if not normalized:
        return failure_result("szse", "empty_data", "深交所公告为空")
    return success_result(normalized, "szse", fallback_used=True)


def _eastmoney(code: str, limit: int, client: TransportClient) -> DataResult:
    params = {
        "sr": "-1",
        "page_size": limit,
        "page_index": 1,
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
        "f_node": 0,
        "s_node": 0,
    }
    data = client.get_json(EM_ANN_URL, params=params)
    rows = (data.get("data") or {}).get("list") or []
    normalized = [
        {
            "title": row.get("title") or "",
            "date": str(row.get("notice_date") or "")[:10],
            "type": row.get("notice_type") or "",
            "pdf": f"https://pdf.dfcfw.com/pdf/H2_{row.get('art_code', '')}_1.pdf",
        }
        for row in rows[:limit]
    ]
    if not normalized:
        return failure_result("eastmoney", "empty_data", "东方财富公告为空")
    return success_result(normalized, "eastmoney", fallback_used=True)


def fetch_official_announcements(
    code: str,
    *,
    limit: int = 20,
    client: Optional[TransportClient] = None,
) -> DataResult:
    identity = normalize_code(code)
    return _szse(identity.code, limit, client or TransportClient()) if identity.market == "SZ" else _eastmoney(identity.code, limit, client or TransportClient())


def fetch_announcements(
    code: str,
    *,
    limit: int = 20,
    client: Optional[TransportClient] = None,
) -> DataResult:
    try:
        identity = normalize_code(code)
    except ValueError as exc:
        return failure_result("identifier", "invalid_code", str(exc))
    if limit < 1 or limit > 100:
        return failure_result("identifier", "invalid_code", "公告数量必须在 1 到 100 之间")
    client = client or TransportClient()
    backup = (lambda: _szse(identity.code, limit, client)) if identity.market == "SZ" else (lambda: _eastmoney(identity.code, limit, client))
    return FallbackChain([
        lambda: _cninfo(identity, limit, client),
        backup,
    ]).run()


__all__ = ["fetch_announcements", "fetch_official_announcements"]

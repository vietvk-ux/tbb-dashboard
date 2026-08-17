"""
Ảnh chụp NGÀY MỚI NHẤT từ Supabase — dùng làm DỰ PHÒNG khi fetch live nhanh.ghn.vn
lỗi (token hết hạn / API sập). Trả về cấu trúc `agg` giống report.aggregate() để tái
dùng report_dashboard.gen_html, cùng dict backlog {bưu_cục: {"deliver": chưa_gán}}.

Chỉ đọc (SELECT) — cần SUPABASE_URL + SUPABASE_SERVICE_KEY (hoặc ANON_KEY).
Không có cấu hình / chưa có dữ liệu → trả None (bên gọi tự xử lý).
"""
from __future__ import annotations
import logging
import os
from datetime import date as _date

import requests

logger = logging.getLogger("snapshot")


def _sb_get(url, key, path):
    r = requests.get("%s/rest/v1/%s" % (url.rstrip("/"), path),
                     headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=60)
    r.raise_for_status()
    return r.json()


def _sb_all(url, key, path, page=1000):
    out, off = [], 0
    sep = "&" if "?" in path else "?"
    while True:
        c = _sb_get(url, key, "%s%slimit=%d&offset=%d" % (path, sep, page, off))
        out += c
        if len(c) < page:
            return out
        off += page


def load_snapshot():
    """(agg, backlog, 'YYYY-MM-DD') của ngày mới nhất trong Supabase, hoặc None."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        vung = _sb_get(url, key, "bao_cao_vung?order=ngay.desc&limit=1&select=*")
        if not vung:
            return None
        day = vung[0]["ngay"]
        bcs_raw = _sb_all(url, key, "bao_cao_buu_cuc?ngay=eq.%s&select=*" % day)
        nv_raw = _sb_all(url, key, "bao_cao_nhan_vien?ngay=eq.%s&select=*" % day)
    except Exception as e:
        logger.warning("Không đọc được snapshot Supabase: %s", str(e)[:150])
        return None
    if not bcs_raw:
        return None
    v = vung[0]
    grand = {"trips": v.get("so_chuyen") or 0, "total": v.get("don_giao") or 0,
             "success": v.get("gtc") or 0, "gtc": v.get("pct_gtc"), "ltc": 0,
             "vngh_total": v.get("vngh_don") or 0, "vngh_success": 0,
             "vngh_gtc": v.get("vngh_gtc")}
    bcs = [{"bc": b["buu_cuc"], "prov": b.get("tinh"), "trips": b.get("so_chuyen") or 0,
            "total": b.get("don_giao") or 0, "success": b.get("gtc") or 0,
            "gtc": b.get("pct_gtc"), "ltc": 0} for b in bcs_raw]
    provs = {}
    for b in bcs:
        p = provs.setdefault(b["prov"], {"prov": b["prov"], "bc_count": 0, "trips": 0,
                                         "total": 0, "success": 0, "ltc": 0})
        p["bc_count"] += 1
        p["trips"] += b["trips"]
        p["total"] += b["total"]
        p["success"] += b["success"]
    for p in provs.values():
        p["gtc"] = round(p["success"] / p["total"] * 100, 1) if p["total"] else None
    drivers = [{"driver_id": str(d.get("driver_id") or ""), "driver_name": d.get("ten_nv") or "—",
                "bc": d.get("buu_cuc"), "prov": d.get("tinh"), "trips": d.get("so_chuyen") or 0,
                "total": d.get("don_giao") or 0, "success": d.get("gtc") or 0,
                "gtc": d.get("pct_gtc"), "gtb_cod": d.get("cod_gtb") or 0, "ltc": 0}
               for d in nv_raw]
    y, m, dd = (int(x) for x in day.split("-"))
    agg = {"date": _date(y, m, dd), "hub_count": v.get("so_buu_cuc") or len(bcs),
           "errors": 0, "grand": grand, "provinces": list(provs.values()),
           "bcs": bcs, "drivers": drivers}
    backlog = {b["buu_cuc"]: {"deliver": b.get("chua_gan") or 0} for b in bcs_raw}
    return agg, backlog, day


def banner_html(day, extra=""):
    """Banner đỏ cảnh báo đang dùng số dự phòng. `day`='YYYY-MM-DD'."""
    dm = day[8:10] + "/" + day[5:7]
    return ("<div style=\"background:linear-gradient(135deg,#7a1d22,#4a1216);"
            "border:1px solid #f2585f;border-radius:14px;padding:12px 14px;margin:8px 0 4px;"
            "font-size:13px;line-height:1.5;color:#ffd9db\">"
            "⚠️ <b>Số real-time tạm dừng</b> — token nhanh.ghn.vn có thể đã hết hạn. "
            "Đang hiển thị <b>SỐ CHỐT ngày %s</b> từ kho dự phòng (Supabase).%s</div>"
            % (dm, (" " + extra) if extra else ""))

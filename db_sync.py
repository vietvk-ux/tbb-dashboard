"""
Đồng bộ số liệu báo cáo ngày Vùng TBB vào Supabase (Postgres đám mây) qua REST API.

- Chỉ chạy khi có SUPABASE_URL + SUPABASE_SERVICE_KEY (biến môi trường / GitHub secret).
  Không có thì BỎ QUA êm, không làm hỏng báo cáo.
- Dùng upsert (merge-duplicates) theo khóa duy nhất → chạy lại cùng ngày sẽ GHI ĐÈ,
  không nhân đôi dữ liệu.
- Bảng: bao_cao_vung · bao_cao_buu_cuc · bao_cao_nhan_vien · chi_tiet_don
  (tạo bằng file supabase_schema.sql).
"""
from __future__ import annotations
import logging
import os
import re
from datetime import date, timedelta

import requests

logger = logging.getLogger("db_sync")


def _upsert(url, key, table, rows, on_conflict, batch=1000):
    if not rows:
        return 0
    ep = "%s/rest/v1/%s?on_conflict=%s" % (url, table, on_conflict)
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        # Chịu lỗi: nếu 1 cột chưa tồn tại (migration chưa chạy) → bỏ cột đó rồi thử lại,
        # để phần dữ liệu còn lại vẫn được lưu (không làm hỏng cả lần sync).
        for _ in range(6):
            r = requests.post(ep, json=chunk, headers=headers, timeout=90)
            if r.status_code in (200, 201, 204):
                break
            m = re.search(r"Could not find the '(\w+)' column", r.text or "")
            if m:
                col = m.group(1)
                for row in chunk:
                    row.pop(col, None)
                logger.warning("Cột '%s' chưa có ở '%s' — bỏ cột, thử lại (chạy migration để lưu).",
                               col, table)
                continue
            raise RuntimeError("Supabase upsert '%s' lỗi %d: %s"
                               % (table, r.status_code, r.text[:250]))
        done += len(chunk)
    return done


def _cleanup_detail(url, key, keep_days):
    """Xóa chi tiết đơn cũ hơn keep_days ngày để tiết kiệm dung lượng (giữ mặc định 60 ngày)."""
    if keep_days <= 0:
        return
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    ep = "%s/rest/v1/chi_tiet_don?ngay=lt.%s" % (url, cutoff)
    headers = {"apikey": key, "Authorization": "Bearer " + key, "Prefer": "return=minimal"}
    try:
        r = requests.delete(ep, headers=headers, timeout=60)
        if r.status_code in (200, 204):
            logger.info("Đã dọn chi_tiet_don cũ hơn %d ngày (trước %s).", keep_days, cutoff)
        else:
            logger.warning("Dọn chi_tiet_don lỗi %d: %s", r.status_code, r.text[:150])
    except Exception as e:
        logger.warning("Dọn chi_tiet_don lỗi: %s", str(e)[:150])


def sync(date_iso, agg, orders, backlog=None, backlog_time="cuối ngày", detail=True):
    """Lưu 1 ngày báo cáo vào Supabase.
    date_iso: 'YYYY-MM-DD'; agg: kết quả report.aggregate(); orders: report.dedup_orders()."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        logger.info("Bỏ qua đồng bộ Supabase (chưa cấu hình SUPABASE_URL / SUPABASE_SERVICE_KEY).")
        return False

    backlog = backlog or {}
    g = agg["grand"]
    total_backlog = sum(v.get("deliver", 0) for v in backlog.values())
    total_cod = sum(x.get("gtb_cod", 0) for x in agg["drivers"])

    # 1) Toàn VÙNG (1 dòng/ngày)
    _upsert(url, key, "bao_cao_vung", [{
        "ngay": date_iso, "so_buu_cuc": agg["hub_count"], "so_chuyen": g["trips"],
        "don_giao": g["total"], "gtc": g["success"], "gtb": g["total"] - g["success"],
        "pct_gtc": g["gtc"], "cod_gtb": round(total_cod), "chua_gan": total_backlog,
        "ltc": g.get("ltc", 0),
        "vngh_don": g.get("vngh_total"), "vngh_gtc": g.get("vngh_gtc"),
    }], "ngay")

    # 2) Theo BƯU CỤC
    bc_rows = [{
        "ngay": date_iso, "buu_cuc": b["bc"], "tinh": b["prov"], "so_chuyen": b.get("trips", 0),
        "don_giao": b["total"], "gtc": b["success"], "gtb": b["total"] - b["success"],
        "pct_gtc": b["gtc"], "chua_gan": backlog.get(b["bc"], {}).get("deliver", 0),
        "ltc": b.get("ltc", 0),
    } for b in agg["bcs"]]
    _upsert(url, key, "bao_cao_buu_cuc", bc_rows, "ngay,buu_cuc")

    # 3) Theo NHÂN VIÊN
    nv_rows = [{
        "ngay": date_iso, "buu_cuc": d["bc"], "tinh": d["prov"], "ten_nv": d["driver_name"],
        "driver_id": str(d.get("driver_id", "") or ""), "so_chuyen": d.get("trips", 0),
        "don_giao": d["total"], "gtc": d["success"], "gtb": d["total"] - d["success"],
        "pct_gtc": d["gtc"], "cod_gtb": round(d.get("gtb_cod", 0)), "ltc": d.get("ltc", 0),
    } for d in agg["drivers"]]
    _upsert(url, key, "bao_cao_nhan_vien", nv_rows, "ngay,buu_cuc,driver_id")

    logger.info("Supabase: lưu vùng + %d bưu cục + %d nhân viên (ngày %s).",
                len(bc_rows), len(nv_rows), date_iso)

    # 4) CHI TIẾT từng đơn (tùy chọn — nặng; tắt bằng DB_SYNC_DETAIL=0)
    if detail and os.environ.get("DB_SYNC_DETAIL", "1").lower() in ("1", "true", "yes"):
        od = [{
            "ngay": date_iso, "buu_cuc": o["bc"], "tinh": o["prov"], "ma_don": o["ma_don"],
            "ma_chuyen": o.get("ma_chuyen", ""), "ten_nv": o["driver_name"],
            "driver_id": str(o.get("driver_id", "") or ""),
            "da_giao": bool(o["succ"]), "da_xu_ly": bool(o.get("att", False)),
            "cod": round(o.get("cod", 0)), "vngh": bool(o.get("vngh", False)),
        } for o in orders]
        n = _upsert(url, key, "chi_tiet_don", od, "ngay,buu_cuc,ma_don")
        logger.info("Supabase: lưu %d đơn chi tiết (ngày %s).", n, date_iso)
        # Giữ chi tiết đơn 60 ngày (~2 tháng, gọn gói Free 500MB) — đổi bằng env DB_KEEP_DETAIL_DAYS.
        # Bảng tổng hợp (vùng/bưu cục/nhân viên) KHÔNG xóa → giữ nhiều năm.
        keep = int(os.environ.get("DB_KEEP_DETAIL_DAYS", "60") or "60")
        _cleanup_detail(url, key, keep)
    return True


def sync_backlog(date_iso, rows):
    """Lưu tồn đọng Lấy·Giao·Trả·Luân chuyển (bảng bao_cao_ton_dong) cho 1 ngày.
    rows: list dict {buu_cuc,tinh,order_type,total,g_lt24,g_24_72,g_72_120,g_gt120}.
    Bảng chưa tạo (migration chưa chạy) → raise; bên gọi tự bắt để không hỏng sync chính."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key) or not rows:
        return 0
    out = [dict(r, ngay=date_iso) for r in rows]
    n = _upsert(url, key, "bao_cao_ton_dong", out, "ngay,buu_cuc,order_type")
    logger.info("Supabase: lưu %d dòng tồn đọng (ngày %s).", n, date_iso)
    return n

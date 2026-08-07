"""
CẢNH BÁO TỰ ĐỘNG — nhân viên TỤT SÂU %GTC.

Đọc view `v_nv_tut` từ Supabase (nhân viên có %GTC ngày mới nhất giảm ≥20 điểm so
trung bình 7 ngày trước của chính họ, ≥20 đơn) → gửi cảnh báo vào GTalk nhóm.

- Không ai tụt → KHÔNG gửi (tránh nhiễu).
- Thiếu SUPABASE / GTALK / view chưa tạo → bỏ qua êm, không lỗi.
- Chạy sau bước ghi dữ liệu ngày (EOD ~23:30).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, GTALK_OA_TOKEN, GTALK_CHANNEL_ID.
Tùy chọn: DROP_LIMIT (mặc định 20).
"""
from __future__ import annotations
import logging
import os

import requests

from report import send_gtalk

logger = logging.getLogger("alert_drop")


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def fetch_drops():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        logger.info("Thiếu SUPABASE_URL/SUPABASE_SERVICE_KEY — bỏ qua cảnh báo tụt.")
        return None
    limit = os.environ.get("DROP_LIMIT", "20")
    try:
        r = requests.get("%s/rest/v1/v_nv_tut?order=delta.asc&limit=%s" % (url, limit),
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=60)
        if r.status_code != 200:
            logger.warning("Không đọc được v_nv_tut (%d) — có thể chưa tạo view: %s",
                           r.status_code, r.text[:150])
            return None
        return r.json()
    except Exception as e:
        logger.warning("Lỗi đọc v_nv_tut: %s", str(e)[:150])
        return None


def build_message(rows):
    L = ["⚠️ **CẢNH BÁO TỤT %GTC — VÙNG TÂY BẮC BỘ**",
         "_%d nhân viên có %%GTC hôm nay giảm ≥20 điểm so trung bình 7 ngày (≥20 đơn)_" % len(rows),
         ""]
    for i, x in enumerate(rows, 1):
        L.append("%d. %s · %s — hôm nay **%s%%** (TB 7 ngày %s%%, ▼%s điểm) · %s đơn"
                 % (i, x.get("ten_nv"), x.get("buu_cuc"), x.get("pct_today"),
                    x.get("pct_base"), abs(x.get("delta") or 0), _n(x.get("dg_today"))))
    L += ["", "_🤖 Cảnh báo tự động — kiểm tra/đốc thúc ngay các trường hợp trên._"]
    return "\n".join(L)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    rows = fetch_drops()
    if rows is None:
        return
    if not rows:
        logger.info("Không có nhân viên tụt sâu — không gửi cảnh báo.")
        return
    msg = build_message(rows)
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    if not (oa and ch):
        logger.warning("Thiếu GTALK_OA_TOKEN/GTALK_CHANNEL_ID — chỉ in, không gửi.\n%s", msg)
        return
    send_gtalk(msg, oa, ch)
    logger.info("Đã gửi cảnh báo tụt: %d nhân viên.", len(rows))


if __name__ == "__main__":
    main()

"""
Đồng bộ dữ liệu ngày vào Supabase — LUỒNG RIÊNG, độc lập GitHub Pages.

Chạy ở workflow sync-23h.yml (KHÔNG có environment github-pages) nên không bao giờ
bị kẹt "waiting" như khi gộp chung với bước deploy trang. Đảm bảo dữ liệu luôn
được lưu dù trang web có kẹt deploy.

- Chỉ ghi Supabase (bao_cao_vung / bưu cục / nhân viên / chi tiết đơn). Upsert nên
  chạy trùng ngày cũng không nhân đôi.
- KHÔNG tạo eod.html, KHÔNG deploy Pages, KHÔNG gửi GTalk.

Env: NHANH_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_KEY. Tùy chọn EOD_DATE=YYYY-MM-DD.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import report
import db_sync
import report_backlog_web as BL
from report_dashboard import fetch_backlog

VN = timezone(timedelta(hours=7))
logger = logging.getLogger("db-sync")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        logger.info("Thiếu SUPABASE_URL/SUPABASE_SERVICE_KEY — bỏ qua, không sync.")
        return

    s = os.environ.get("EOD_DATE", "").strip()
    if s:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    else:
        now = datetime.now(VN)
        d = now.date() if now.hour >= 22 else (now.date() - timedelta(days=1))
    logger.info("Sync dữ liệu ngày %s vào Supabase...", d)

    try:
        payload = asyncio.run(report.fetch_report(token, d))
    except report.TokenExpiredError as e:
        raise SystemExit("Token hết hạn: %s" % e)
    agg = report.aggregate(payload)

    backlog = {}
    try:
        backlog = asyncio.run(fetch_backlog(token))
    except Exception as e:
        logger.warning("Không lấy được tồn chưa gán (bỏ qua field này): %s", str(e)[:120])

    db_sync.sync(d.isoformat(), agg, report.dedup_orders(payload), backlog)
    logger.info("XONG sync ngày %s · %d bưu cục · %d nhân viên · GTC %s%%.",
                d, len(agg["bcs"]), len(agg["drivers"]), agg["grand"]["gtc"])

    # Tồn đọng Lấy·Giao·Trả·Luân chuyển (số LIVE lúc chạy ~23h) → bảng bao_cao_ton_dong.
    # Lỗi (token/API/bảng chưa tạo) KHÔNG làm hỏng phần sync chính ở trên.
    try:
        entries, _ = asyncio.run(BL.fetch_all(token))
        rows = BL.backlog_rows(entries)
        db_sync.sync_backlog(d.isoformat(), rows)
    except Exception as e:
        logger.warning("Sync tồn đọng lỗi (bỏ qua): %s", str(e)[:200])


if __name__ == "__main__":
    main()

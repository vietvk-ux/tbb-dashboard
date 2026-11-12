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

    now = datetime.now(VN)
    latest = now.date() if now.hour >= 22 else (now.date() - timedelta(days=1))
    s = os.environ.get("EOD_DATE", "").strip()
    d = datetime.strptime(s, "%Y-%m-%d").date() if s else latest
    # CHẠY BÙ ngày cũ: số chuyến đã đóng thì cố định, nhưng số TỒN lấy lúc chạy là của hôm nay
    # → không ghi tồn (chưa gán, bảng tồn đọng) vào ngày cũ.
    backfill = d < latest
    logger.info("Sync dữ liệu ngày %s vào Supabase%s...", d, " (chạy bù — bỏ số tồn)" if backfill else "")

    try:
        payload = asyncio.run(report.fetch_report(token, d))
    except report.TokenExpiredError as e:
        raise SystemExit("Token hết hạn: %s" % e)
    agg = report.aggregate(payload)

    backlog = {}
    try:
        if backfill:
            raise RuntimeError("chạy bù ngày cũ")
        backlog = asyncio.run(fetch_backlog(token))
    except Exception as e:
        logger.warning("Không lấy được tồn chưa gán (bỏ qua field này): %s", str(e)[:120])

    # Backup 23:35: GIỮ chua_gan do bản EOD ~23:30 đã ghi (khớp số live), không đè bằng
    # số 'chưa gán' phình lên sau khi đóng chuyến. Chỉ ghi nếu ngày đó chưa có (EOD lỡ chạy).
    db_sync.sync(d.isoformat(), agg, report.dedup_orders(payload), backlog, preserve_backlog=True)
    logger.info("XONG sync ngày %s · %d bưu cục · %d nhân viên · GTC %s%%.",
                d, len(agg["bcs"]), len(agg["drivers"]), agg["grand"]["gtc"])

    # BẢN ĐỒ KHU VỰC (trang 9): tái dùng payload đã bóc item + geo → KHÔNG tốn call API.
    # Ghi file khuvuc_data/<ngày>.json (giữ 30 ngày). Lỗi KHÔNG làm hỏng sync chính.
    try:
        import report_khuvuc
        p = report_khuvuc.write_day(report_khuvuc.build_day_payload(payload))
        logger.info("Lưu bản đồ khu vực → %s", p)
    except Exception as e:
        logger.warning("Ghi dữ liệu khu vực lỗi (bỏ qua): %s", str(e)[:200])

    # MẶT HÀNG: tái dùng payload → KHÔNG tốn call API. Ghi mathang_data/<ngày>.json (giữ 30 ngày,
    # chỉ số đếm theo nhóm). Lỗi KHÔNG làm hỏng sync chính.
    try:
        import report_mathang
        _mh = report_mathang.build_day_payload(payload)
        logger.info("Lưu mặt hàng → %s (%d đơn, GTC %d)", report_mathang.write_day(_mh), _mh["tot"], _mh["gtc"])
    except Exception as e:
        logger.warning("Ghi dữ liệu mặt hàng lỗi (bỏ qua): %s", str(e)[:200])

    # Tồn đọng Lấy·Giao·Trả·Luân chuyển (số LIVE lúc chạy ~23h) → bảng bao_cao_ton_dong.
    # Lỗi (token/API/bảng chưa tạo) KHÔNG làm hỏng phần sync chính ở trên.
    if backfill:
        logger.info("Chạy bù: bỏ qua lưu tồn đọng (số hiện tại không thuộc ngày %s).", d)
        return
    try:
        entries, _ = asyncio.run(BL.fetch_all(token))
        rows = BL.backlog_rows(entries)
        db_sync.sync_backlog(d.isoformat(), rows)
    except Exception as e:
        logger.warning("Sync tồn đọng lỗi (bỏ qua): %s", str(e)[:200])


if __name__ == "__main__":
    main()

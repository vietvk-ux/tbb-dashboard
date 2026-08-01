"""Chụp số đơn CHƯA GÁN GIAO toàn Vùng TBB lúc ~15h, lưu backlog_15h.json.
Dashboard 23h sẽ đọc file này để hiển thị số của 15h.
Env: NHANH_TOKEN.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from report_dashboard import fetch_backlog

VN = timezone(timedelta(hours=7))


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    now = datetime.now(VN)
    bl = asyncio.run(fetch_backlog(token))
    total = sum(v.get("deliver", 0) for v in bl.values())
    with open("backlog_15h.json", "w", encoding="utf-8") as f:
        json.dump({"date": now.date().isoformat(), "time": now.strftime("%Hh%M"),
                   "hubs": bl, "total_deliver": total}, f, ensure_ascii=False)
    logging.info("Backlog %s: %d đơn chưa gán giao · %d bưu cục", now.strftime("%Hh%M"), total, len(bl))


if __name__ == "__main__":
    main()

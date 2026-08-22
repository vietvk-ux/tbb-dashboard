"""Báo cáo KHO CHUYỂN TIẾP · 08h/16h/22h hàng ngày → GTalk nhóm riêng.
Reuse `_fetch_transit_async` từ report_trend.py để lấy tồn theo mốc giờ
(<24h · 24-72h · 72-120h · >120h) cho LC giao + LC trả của 3 kho chuyển tiếp
(Yên Bái · Lào Cai · Sơn La).
Env: NHANH_TOKEN, GTALK_OA_TOKEN, GTALK_CHANNEL_KHO.
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta, timezone
from report_trend import fetch_transit
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _bucket_row(bk):
    """Format bucket dict {'<24h':X, '24-72h':X, '72-120h':X, '>120h':X}."""
    lt24 = bk.get("<24h", 0)
    _24_72 = bk.get("24-72h", 0)
    _72_120 = bk.get("72-120h", 0)
    gt120 = bk.get(">120h", 0)
    # Icon đỏ nếu >120h > 0
    tail = f"🔴 >120h: **{_n(gt120)}**" if gt120 > 0 else f">120h: {_n(gt120)}"
    return f"   `<24h:` {_n(lt24)} · `24-72h:` {_n(_24_72)} · `72-120h:` {_n(_72_120)} · {tail}"


def build_msg(transit):
    now = datetime.now(VN)
    L = [
        "📦 **KHO CHUYỂN TIẾP · VÙNG TÂY BẮC BỘ**",
        f"⏰ {now.strftime('%H:%M · %d/%m/%Y')}",
        "",
    ]
    if not transit:
        L.append("_⚠️ Không lấy được số tồn đọng luân chuyển (token/API lỗi)._")
        L.append("")
        L.append(f"_🤖 Kho chuyển tiếp · Vùng TBB · {now.strftime('%H:%M')}_")
        return "\n".join(L)

    # Tổng 3 kho
    tg = sum(t["giao"] for t in transit)
    tt = sum(t["tra"] for t in transit)
    tover = sum(t["over120"] for t in transit)
    L += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "▶️ **TỔNG QUAN 3 KHO CHUYỂN TIẾP**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🚚 LC giao: **{_n(tg)}** đơn",
        f"↩️ LC trả: **{_n(tt)}** đơn",
    ]
    if tover > 0:
        L.append(f"🔴 Quá 120h: **{_n(tover)}** đơn → cần xử lý gấp")
    L.append("")

    # Chi tiết từng kho
    for t in transit:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ 📦 **KHO {t['name'].upper()}**",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🚚 LC giao ({_n(t['giao'])} đơn)",
            _bucket_row(t["giao_g"]),
            f"↩️ LC trả ({_n(t['tra'])} đơn)",
            _bucket_row(t["tra_g"]),
            "",
        ]

    L.append(f"_🤖 Kho chuyển tiếp · Vùng TBB · {now.strftime('%H:%M %d/%m')}_")
    return "\n".join(L)


def main():
    token = os.environ.get("NHANH_TOKEN", "").strip()
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_KHO", "").strip()
    if not (token and oa and ch):
        raise SystemExit("Thiếu env: NHANH_TOKEN / GTALK_OA_TOKEN / GTALK_CHANNEL_KHO")

    transit = fetch_transit()
    msg = build_msg(transit)
    print(f"[INFO] Msg {len(msg)} chars · {len(transit) if transit else 0} kho")
    send_gtalk(msg, oa, ch)
    print("[INFO] Đã gửi KHO CHUYỂN TIẾP vào GTalk")


if __name__ == "__main__":
    main()

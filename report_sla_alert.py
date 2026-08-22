"""CẢNH BÁO ĐƠN QUÁ SLA GIAO · 16:00 hàng ngày → GTalk.
Focus: đơn tồn >24h nhưng chưa quá 120h — cần xử lý sớm trước cuối ngày.
Dùng bao_cao_ton_dong Supabase (bucket theo giờ) để lọc đơn còn có thể cứu.
"""
from __future__ import annotations
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from snapshot import _sb_all
from am_map import AM_OF
from am_user_ids import extract_ids_from_msg
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _icon(n):
    if n >= 50: return "🔴"
    if n >= 20: return "🟠"
    return "🟡"


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (oa and ch and url and key):
        raise SystemExit("Thiếu env: GTALK_OA_TOKEN / GTALK_CHANNEL_ID / SUPABASE / KEY")

    # Ngày HÔM QUA (Supabase sync mỗi ngày → data hôm qua)
    day = (datetime.now(VN).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    ton_rows = _sb_all(url, key, f"bao_cao_ton_dong?ngay=eq.{day}&select=buu_cuc,order_type,g_24_72,g_72_120,g_gt120")

    # Aggregate theo BC — chỉ tính DELIVER (đơn giao)
    by_bc = defaultdict(lambda: {"g_24_72": 0, "g_72_120": 0, "g_gt120": 0})
    total_g24_72 = total_g72_120 = total_gt120 = 0
    for r in ton_rows:
        # Chỉ tính đơn giao (DELIVER) - đơn LẤY/TRẢ có logic khác
        if r.get("order_type") not in ("DELIVER", "DELIVER_PRIORITY"):
            continue
        bc = r.get("buu_cuc")
        if not bc: continue
        by_bc[bc]["g_24_72"] += r.get("g_24_72") or 0
        by_bc[bc]["g_72_120"] += r.get("g_72_120") or 0
        by_bc[bc]["g_gt120"] += r.get("g_gt120") or 0
        total_g24_72 += r.get("g_24_72") or 0
        total_g72_120 += r.get("g_72_120") or 0
        total_gt120 += r.get("g_gt120") or 0

    # BC critical: có nhiều đơn 24-120h (còn cứu được)
    critical_bcs = []
    for bc, v in by_bc.items():
        savable = v["g_24_72"] + v["g_72_120"]  # còn có thể xử lý
        if savable >= 10:  # ngưỡng
            critical_bcs.append({
                "bc": bc,
                "am": AM_OF.get(bc, "?"),
                "savable": savable,
                "g_24_72": v["g_24_72"],
                "g_72_120": v["g_72_120"],
                "g_gt120": v["g_gt120"],
            })
    critical_bcs.sort(key=lambda x: -x["savable"])
    top_bcs = critical_bcs[:15]

    now = datetime.now(VN)
    total_savable = total_g24_72 + total_g72_120
    L = [
        "⏱️ **CẢNH BÁO ĐƠN QUÁ SLA GIAO · VÙNG TÂY BẮC BỘ**",
        f"⏰ {now.strftime('%H:%M · %d/%m/%Y')} · số chốt {day}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "▶️ **TỔNG QUAN VÙNG**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🟠 Đơn tồn 24-72h: **{_n(total_g24_72)}** đơn (còn cứu)",
        f"🟠 Đơn tồn 72-120h: **{_n(total_g72_120)}** đơn (cấp bách)",
        f"🔴 Đơn tồn >120h: **{_n(total_gt120)}** đơn (đã trễ)",
        f"📊 **Tổng cần xử lý ngay**: **{_n(total_savable)}** đơn (24-120h)",
        "",
    ]

    if top_bcs:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ 🚨 **TOP {len(top_bcs)} BC CÓ NHIỀU ĐƠN QUÁ SLA** _(24-120h · sort giảm dần)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(top_bcs, 1):
            L.append(f"{i}. {_icon(b['savable'])} {b['bc']} · AM **{b['am']}** — **{_n(b['savable'])}** đơn cứu · 🔴 >120h: {_n(b['g_gt120'])}")
        L.append("")

    L += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🎯 **HÀNH ĐỘNG NGAY (còn ~6h tới hết ngày)**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"• Gọi AM/BC top 5 → thúc giao đơn 24-72h (chưa quá SLA)",
        f"• Đơn 72-120h cấp bách → escalate lên GĐV/AM cấp cao",
        f"• Đơn >120h: đã trễ, cần call center khách hàng xin lỗi",
        "",
        f"📱 **[Xem chi tiết Backlog](https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/backlog.html)**", "", f"_🤖 Cảnh báo SLA · Vùng TBB · {now.strftime('%H:%M %d/%m')}_",
    ]
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · {len(critical_bcs)} BC critical (top {len(top_bcs)})")
    send_gtalk(msg, oa, ch, mentioned_user_ids=extract_ids_from_msg(msg))
    print("[INFO] Đã gửi CẢNH BÁO SLA vào GTalk")


if __name__ == "__main__":
    main()

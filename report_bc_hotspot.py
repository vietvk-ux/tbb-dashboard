"""BC ĐIỂM NÓNG NGÀY 08:00 · Vùng TBB → GTalk.
Lọc 2 danh sách:
  1) Top 10 BC %GTC THẤP NHẤT (từ bao_cao_buu_cuc)
  2) Top 10 BC BACKLOG TỒN ĐỌNG NHIỀU NHẤT (từ bao_cao_ton_dong · aging bucket)
     🔴 đỏ khi có tồn >120h nhiều nhất
Hiển thị AM phụ trách để user gọi ngay.
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


def _icon_gtc(pct):
    if pct is None: return "⚪"
    if pct < 50: return "🔴"
    if pct < 70: return "🟠"
    return "🟡"


def _icon_backlog(gt120):
    """Cảnh báo đỏ theo tồn >120h."""
    if gt120 >= 30: return "🔴"
    if gt120 >= 10: return "🟠"
    return "🟡"


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (oa and ch and url and key):
        raise SystemExit("Thiếu env: GTALK_OA_TOKEN / GTALK_CHANNEL_ID / SUPABASE_URL / SUPABASE_SERVICE_KEY")

    day = (datetime.now(VN).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    bcs = _sb_all(url, key, f"bao_cao_buu_cuc?ngay=eq.{day}&select=*")
    if not bcs:
        raise SystemExit(f"Không có data BC ngày {day}")

    # Chỉ lấy BC có đủ đơn giao (loại BC quá nhỏ để tránh nhiễu)
    valid = [b for b in bcs if (b.get("don_giao") or 0) >= 20]

    # Top 10 BC %GTC thấp nhất
    low_gtc = sorted([b for b in valid if b.get("pct_gtc") is not None],
                    key=lambda x: x["pct_gtc"])[:10]

    # Top 10 BC backlog TỒN ĐỌNG NHIỀU NHẤT — dùng bao_cao_ton_dong
    ton_rows = _sb_all(url, key, f"bao_cao_ton_dong?ngay=eq.{day}&select=buu_cuc,order_type,total,g_gt120")
    ton_by_bc = defaultdict(lambda: {"total": 0, "gt120": 0})
    for r in ton_rows:
        bc = r.get("buu_cuc")
        if not bc: continue
        ton_by_bc[bc]["total"] += r.get("total") or 0
        ton_by_bc[bc]["gt120"] += r.get("g_gt120") or 0
    # Sort theo tổng tồn desc → top 10
    high_backlog = sorted(
        [{"bc": bc, "total": v["total"], "gt120": v["gt120"]}
         for bc, v in ton_by_bc.items() if v["total"] > 0],
        key=lambda x: -x["total"]
    )[:10]

    now = datetime.now(VN)
    L = [
        f"🔥 **BC ĐIỂM NÓNG · VÙNG TÂY BẮC BỘ**",
        f"⏰ Sáng {now.strftime('%d/%m/%Y')} · số chốt ngày {day}",
        "",
    ]

    if low_gtc:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ 🔴 **TOP {len(low_gtc)} BC %GTC THẤP NHẤT** _(≥20 đơn/ngày)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(low_gtc, 1):
            bc = b.get("buu_cuc", "?")
            am = AM_OF.get(bc, "?")
            pct = b.get("pct_gtc", 0)
            vol = b.get("don_giao", 0)
            L.append(f"{i}. {_icon_gtc(pct)} {bc} · AM **{am}** — **{pct}%** · {_n(vol)} đơn")
        L.append("")

    if high_backlog:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ 📦 **TOP {len(high_backlog)} BC BACKLOG TỒN ĐỌNG NHIỀU NHẤT** _(🔴 nếu tồn >120h ≥30 đơn)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(high_backlog, 1):
            bc = b["bc"]
            am = AM_OF.get(bc, "?")
            L.append(f"{i}. {_icon_backlog(b['gt120'])} {bc} · AM **{am}** — **{_n(b['total'])}** đơn tồn · 🔴 >120h: **{_n(b['gt120'])}**")
        L.append("")

    # Hành động — union set BC xuất hiện ở cả 2 danh sách
    intersect = set(b.get("buu_cuc") for b in low_gtc) & set(b["bc"] for b in high_backlog)
    if intersect:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🎯 **{len(intersect)} BC CRITICAL** _(đồng thời %GTC thấp + backlog cao)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for bc in sorted(intersect):
            am = AM_OF.get(bc, "?")
            L.append(f"• {bc} · AM **{am}** → cần gọi ngay")
        L.append("")

    L.append(f"📱 **[Xem chi tiết EOD](https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/eod.html)**")
    L.append("")
    L.append(f"_🤖 BC Điểm nóng · Vùng TBB · sáng {now.strftime('%H:%M')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · low_gtc={len(low_gtc)} high_backlog={len(high_backlog)} intersect={len(intersect)}")
    send_gtalk(msg, oa, ch, mentioned_user_ids=extract_ids_from_msg(msg))
    print("[INFO] Đã gửi BC ĐIỂM NÓNG vào GTalk")


if __name__ == "__main__":
    main()

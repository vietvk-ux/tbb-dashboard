"""BC ĐIỂM NÓNG NGÀY 08:00 · Vùng TBB → GTalk.
Lọc 2 danh sách:
  1) Top 10 BC %GTC THẤP NHẤT (số hôm qua từ Supabase)
  2) Top 10 BC BACKLOG TỒN ĐỌNG NHIỀU NHẤT
Hiển thị AM phụ trách để user gọi ngay.
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from snapshot import _sb_all
from am_map import AM_OF
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _icon_gtc(pct):
    if pct is None: return "⚪"
    if pct < 50: return "🔴"
    if pct < 70: return "🟠"
    return "🟡"


def _icon_backlog(n):
    if n >= 100: return "🔴"
    if n >= 50: return "🟠"
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

    # Top 10 BC backlog tồn đọng nhiều nhất (chua_gan)
    high_backlog = sorted([b for b in bcs if (b.get("chua_gan") or 0) > 0],
                          key=lambda x: -(x.get("chua_gan") or 0))[:10]

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
            f"▶️ 📦 **TOP {len(high_backlog)} BC BACKLOG TỒN ĐỌNG NHIỀU NHẤT**",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(high_backlog, 1):
            bc = b.get("buu_cuc", "?")
            am = AM_OF.get(bc, "?")
            cg = b.get("chua_gan", 0)
            L.append(f"{i}. {_icon_backlog(cg)} {bc} · AM **{am}** — **{_n(cg)}** đơn tồn")
        L.append("")

    # Hành động — union set BC xuất hiện ở cả 2 danh sách
    intersect = set(b.get("buu_cuc") for b in low_gtc) & set(b.get("buu_cuc") for b in high_backlog)
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

    L.append(f"_🤖 BC Điểm nóng · Vùng TBB · sáng {now.strftime('%H:%M')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · low_gtc={len(low_gtc)} high_backlog={len(high_backlog)} intersect={len(intersect)}")
    send_gtalk(msg, oa, ch)
    print("[INFO] Đã gửi BC ĐIỂM NÓNG vào GTalk")


if __name__ == "__main__":
    main()

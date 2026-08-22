"""BC ĐIỂM NÓNG NGÀY 09:00 · cross-metric alert (Vùng TBB) → GTalk.
Kết hợp %GTC + backlog + COD GTB → top 5 BC critical cần intervention ngay.
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta, timezone
from snapshot import load_snapshot, _sb_all
from am_map import AM_OF
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _pct(p, t):
    return round(p * 100 / t, 1) if t else 0


def _tr(x):
    return f"{(x or 0) / 1_000_000:.1f} tr₫"


def _score(b):
    """Impact score: nặng đơn tồn + COD kẹt + %GTC thấp."""
    pct_gap = max(0, 70 - (b.get("pct_gtc") or 100))  # 0-70
    return (b.get("chua_gan") or 0) * 1.0 + (b.get("cod_gtb") or 0) / 1_000_000 * 2 + pct_gap * 3


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    if not (oa and ch):
        raise SystemExit("Thiếu GTALK_OA_TOKEN / GTALK_CHANNEL_ID")

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        raise SystemExit("Thiếu SUPABASE_URL / SUPABASE_SERVICE_KEY")

    day = (datetime.now(VN).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    bcs = _sb_all(url, key, f"bao_cao_buu_cuc?ngay=eq.{day}&select=*")
    if not bcs:
        raise SystemExit(f"Không có data BC ngày {day}")

    # Cross-metric filter: các BC có ít nhất 2 dấu hiệu yếu
    critical = []
    for b in bcs:
        pct = b.get("pct_gtc") or 100
        cg = b.get("chua_gan") or 0
        cod = b.get("cod_gtb") or 0
        flags = 0
        if pct < 70: flags += 1
        if cg >= 30: flags += 1
        if cod >= 20_000_000: flags += 1
        if flags >= 2:  # có ít nhất 2 vấn đề
            b["_score"] = _score(b)
            critical.append(b)
    critical.sort(key=lambda x: -x["_score"])
    top5 = critical[:5]

    now = datetime.now(VN)
    L = [
        f"🔥 **BC ĐIỂM NÓNG · VÙNG TÂY BẮC BỘ**",
        f"⏰ Sáng {now.strftime('%d/%m/%Y')} · số chốt ngày {day}",
        "",
    ]
    if not top5:
        L.append("✅ Không có BC nào chạm ≥2 tiêu chí yếu (GTC<70% · Tồn≥30 · COD≥20 tr₫).")
    else:
        L += [
            f"⚠️ **{len(critical)} BC** chạm ≥2 tiêu chí yếu (GTC<70% · Tồn≥30 · COD≥20 tr₫)",
            f"🎯 **TOP {len(top5)} BC CRITICAL NHẤT** _(sort theo impact score)_",
            "",
        ]
        for i, b in enumerate(top5, 1):
            bc = b.get("buu_cuc", "?")
            am = AM_OF.get(bc, "?")
            pct = b.get("pct_gtc") or 0
            cg = b.get("chua_gan") or 0
            cod = b.get("cod_gtb") or 0
            L.append(f"{i}. 🔴 **{bc}** — AM: **{am}**")
            L.append(f"   ├ %GTC: **{pct}%** · Tồn chưa gán: **{cg}** đơn · COD GTB: **{_tr(cod)}**")
            L.append(f"   └ 👉 gọi AM **{am}** thúc BC này ngay")
        L += ["", "🎯 **HÀNH ĐỘNG HÔM NAY**",
              f"• Gọi từng AM có tên trên → yêu cầu báo cáo tiến độ trước 12:00",
              f"• Focus BC top 1-2 trước (impact cao nhất)"]
    L += ["", f"_🤖 BC Điểm nóng · Vùng TBB · sáng {now.strftime('%H:%M')}_"]
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · {len(critical)} BC critical (top {len(top5)})")
    send_gtalk(msg, oa, ch)
    print("[INFO] Đã gửi BC ĐIỂM NÓNG vào GTalk")


if __name__ == "__main__":
    main()

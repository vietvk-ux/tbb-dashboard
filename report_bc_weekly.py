"""RECAP TUẦN BC · Thứ 2 07:30 · Vùng TBB → GTalk.
So sánh %GTC tuần này (7 ngày qua) vs tuần trước.
Top BC giảm/cải thiện + red flag <50% ≥3 ngày.
"""
from __future__ import annotations
import os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from snapshot import _sb_all
from am_map import AM_OF
from am_user_ids import extract_ids_from_msg
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _fetch_range(url, key, start, end):
    return _sb_all(url, key, f"bao_cao_buu_cuc?ngay=gte.{start}&ngay=lte.{end}&select=*")


def _avg_gtc(rows):
    tot = sum((r.get("don_giao") or 0) for r in rows)
    suc = sum((r.get("gtc") or 0) for r in rows)
    return round(suc * 100 / tot, 1) if tot else None


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (oa and ch and url and key):
        raise SystemExit("Thiếu env vars (GTALK / SUPABASE)")

    today = datetime.now(VN).date()
    # Tuần này: 7 ngày qua (không tính hôm nay vì có thể chưa đủ)
    w1_start = today - timedelta(days=7)
    w1_end = today - timedelta(days=1)
    w0_start = today - timedelta(days=14)
    w0_end = today - timedelta(days=8)

    w1_rows = _fetch_range(url, key, w1_start.isoformat(), w1_end.isoformat())
    w0_rows = _fetch_range(url, key, w0_start.isoformat(), w0_end.isoformat())
    if not w1_rows:
        raise SystemExit(f"Không có data BC tuần {w1_start}..{w1_end}")

    # Group by BC
    w1_by_bc = defaultdict(list)
    w0_by_bc = defaultdict(list)
    for r in w1_rows:
        w1_by_bc[r["buu_cuc"]].append(r)
    for r in w0_rows:
        w0_by_bc[r["buu_cuc"]].append(r)

    # Compute delta
    results = []
    for bc, rows in w1_by_bc.items():
        w1 = _avg_gtc(rows)
        w0 = _avg_gtc(w0_by_bc.get(bc, []))
        vol_w1 = sum((r.get("don_giao") or 0) for r in rows)
        low_days = sum(1 for r in rows if (r.get("pct_gtc") or 100) < 50)
        results.append({
            "bc": bc, "am": AM_OF.get(bc, "?"),
            "w1": w1, "w0": w0,
            "delta": (w1 - w0) if (w1 is not None and w0 is not None) else None,
            "vol": vol_w1,
            "low_days": low_days,
        })

    # Top 10 giảm (delta âm nhất, có vol đủ)
    drop = sorted([r for r in results if r["delta"] is not None and r["vol"] >= 100],
                  key=lambda x: x["delta"])[:10]
    # Top 10 cải thiện (delta dương nhất)
    imp = sorted([r for r in results if r["delta"] is not None and r["vol"] >= 100],
                 key=lambda x: -x["delta"])[:10]
    # Red flag: <50% ≥3 ngày trong tuần
    red = sorted([r for r in results if r["low_days"] >= 3],
                 key=lambda x: -x["low_days"])[:10]

    now = datetime.now(VN)
    L = [
        f"📊 **RECAP TUẦN BC · VÙNG TÂY BẮC BỘ**",
        f"⏰ Tuần {w1_start.strftime('%d/%m')} — {w1_end.strftime('%d/%m/%Y')} (7 ngày)",
        "",
    ]
    # Trend: red flag đầu tiên vì actionable nhất
    if red:
        L += [
            f"🚨 **{len(red)} BC RED FLAG** _(%GTC<50% ≥3 ngày/tuần)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, r in enumerate(red, 1):
            L.append(f"{i}. 🔴 {r['bc']} · AM {r['am']} — **{r['low_days']}/7 ngày** yếu · TB {r['w1']}%")
        L.append("")

    if drop:
        L += [
            f"📉 **TOP 10 BC GIẢM %GTC SO TUẦN TRƯỚC** _(≥100 đơn/tuần)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, r in enumerate(drop, 1):
            L.append(f"{i}. {r['bc']} · AM {r['am']} — {r['w1']}% ▼ {abs(r['delta']):.1f}đ (từ {r['w0']}%)")
        L.append("")

    if imp:
        L += [
            f"📈 **TOP 10 BC CẢI THIỆN %GTC** _(khen thưởng)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, r in enumerate(imp, 1):
            L.append(f"{i}. {r['bc']} · AM {r['am']} — {r['w1']}% ▲ {r['delta']:.1f}đ (từ {r['w0']}%)")
        L.append("")

    L.append(f"📱 **[Xem chi tiết Trend](https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/trend.html)**")
    L.append("")
    L.append(f"_🤖 Recap tuần BC · Vùng TBB · {now.strftime('%d/%m %H:%M')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · drop={len(drop)} imp={len(imp)} red={len(red)}")
    send_gtalk(msg, oa, ch, mentioned_user_ids=extract_ids_from_msg(msg))
    print("[INFO] Đã gửi RECAP TUẦN BC vào GTalk")


if __name__ == "__main__":
    main()

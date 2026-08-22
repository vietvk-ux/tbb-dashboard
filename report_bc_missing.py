"""BC MẤT TÍCH · sáng 08:00 hàng ngày · Vùng TBB → GTalk.
Phát hiện BC có bất thường: không có chuyến ≥3 ngày liên tiếp hoặc vol giảm >50%.
"""
from __future__ import annotations
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from snapshot import _sb_all
from am_map import AM_OF
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _fetch_range(url, key, start, end):
    return _sb_all(url, key, f"bao_cao_buu_cuc?ngay=gte.{start}&ngay=lte.{end}&select=*")


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (oa and ch and url and key):
        raise SystemExit("Thiếu env vars")

    today = datetime.now(VN).date()
    start = today - timedelta(days=7)
    end = today - timedelta(days=1)
    rows = _fetch_range(url, key, start.isoformat(), end.isoformat())
    if not rows:
        raise SystemExit(f"Không có data BC {start}..{end}")

    # Group by BC
    by_bc = defaultdict(dict)  # {bc: {ngay: row}}
    for r in rows:
        by_bc[r["buu_cuc"]][r["ngay"]] = r

    # Danh sách ngày 7 ngày qua
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    days_recent3 = [(end - timedelta(days=i)).isoformat() for i in range(3)]  # 3 ngày gần nhất

    # Phát hiện bất thường
    missing = []  # BC không có chuyến ≥3 ngày gần nhất
    vol_drop = []  # BC vol giảm >50%
    for bc, data in by_bc.items():
        # Check missing 3 ngày gần nhất
        recent_trips = [(data.get(d) or {}).get("so_chuyen") or 0 for d in days_recent3]
        if all(t == 0 for t in recent_trips):
            missing.append({
                "bc": bc, "am": AM_OF.get(bc, "?"),
                "days_no_trip": 3,
            })
            continue

        # Check vol drop
        vols = [(data.get(d) or {}).get("don_giao") or 0 for d in days]
        vol_7d = sum(vols)
        vol_recent = sum(vols[-3:])
        vol_earlier = sum(vols[:4])
        # So sánh 3 ngày gần vs 4 ngày trước (đổi thành TB/ngày)
        avg_recent = vol_recent / 3 if vol_recent else 0
        avg_earlier = vol_earlier / 4 if vol_earlier else 0
        if avg_earlier >= 20 and avg_recent < avg_earlier * 0.5:  # giảm >50%
            vol_drop.append({
                "bc": bc, "am": AM_OF.get(bc, "?"),
                "avg_recent": round(avg_recent, 1),
                "avg_earlier": round(avg_earlier, 1),
                "drop_pct": round((avg_earlier - avg_recent) / avg_earlier * 100, 1),
            })

    vol_drop.sort(key=lambda x: -x["drop_pct"])
    vol_drop = vol_drop[:10]

    now = datetime.now(VN)
    L = [
        f"🚨 **BC MẤT TÍCH · VÙNG TÂY BẮC BỘ**",
        f"⏰ Sáng {now.strftime('%d/%m/%Y')} · check 7 ngày qua",
        "",
    ]
    if not missing and not vol_drop:
        L.append("✅ Không phát hiện BC bất thường.")
    if missing:
        L += [
            f"🔴 **{len(missing)} BC KHÔNG CÓ CHUYẾN 3 NGÀY GẦN NHẤT**",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, m in enumerate(missing, 1):
            L.append(f"{i}. {m['bc']} · AM **{m['am']}** — 0 chuyến/3 ngày")
        L += ["", "👉 Liên hệ AM verify: BC nghỉ ca? Lỗi hệ thống? Hay đã ngừng hoạt động?", ""]
    if vol_drop:
        L += [
            f"📉 **{len(vol_drop)} BC VOL GIẢM >50%** _(3 ngày gần vs 4 ngày trước)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, v in enumerate(vol_drop, 1):
            L.append(f"{i}. {v['bc']} · AM {v['am']} — TB {v['avg_recent']}/ngày ▼**{v['drop_pct']}%** (từ {v['avg_earlier']}/ngày)")
        L.append("")

    L.append(f"_🤖 BC Mất tích · Vùng TBB · {now.strftime('%d/%m %H:%M')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · missing={len(missing)} vol_drop={len(vol_drop)}")
    send_gtalk(msg, oa, ch)
    print("[INFO] Đã gửi BC MẤT TÍCH vào GTalk")


if __name__ == "__main__":
    main()

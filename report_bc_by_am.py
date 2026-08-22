"""RANKING BC THEO AM · Thứ 2 08:00 · Vùng TBB → GTalk.
Xếp hạng BC yếu nhất trong danh sách mỗi AM (dùng TB %GTC 7 ngày qua).
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
        raise SystemExit("Thiếu env vars")

    today = datetime.now(VN).date()
    start = today - timedelta(days=7)
    end = today - timedelta(days=1)
    rows = _fetch_range(url, key, start.isoformat(), end.isoformat())
    if not rows:
        raise SystemExit(f"Không có data BC {start}..{end}")

    # Group by BC → tính TB 7 ngày
    by_bc = defaultdict(list)
    for r in rows:
        by_bc[r["buu_cuc"]].append(r)

    bc_stats = []
    for bc, rr in by_bc.items():
        pct = _avg_gtc(rr)
        vol = sum((r.get("don_giao") or 0) for r in rr)
        bc_stats.append({
            "bc": bc, "am": AM_OF.get(bc, "?"),
            "pct": pct, "vol": vol,
            "low_days": sum(1 for r in rr if (r.get("pct_gtc") or 100) < 50),
        })

    # Group by AM
    by_am = defaultdict(list)
    for bc in bc_stats:
        if bc["am"] != "?":
            by_am[bc["am"]].append(bc)

    # Compute stats per AM
    am_summary = []
    for am, bcs in by_am.items():
        pcts = [b["pct"] for b in bcs if b["pct"] is not None]
        avg_pct = round(sum(pcts) / len(pcts), 1) if pcts else 0
        weak_bcs = sorted([b for b in bcs if b["pct"] is not None and b["vol"] >= 50],
                          key=lambda x: x["pct"])[:3]
        am_summary.append({
            "am": am, "n_bc": len(bcs), "avg_pct": avg_pct,
            "n_low": sum(1 for b in bcs if b["pct"] is not None and b["pct"] < 60),
            "weak_bcs": weak_bcs,
        })
    # Sort AM: TB %GTC thấp lên đầu (AM cần quan tâm nhất)
    am_summary.sort(key=lambda x: x["avg_pct"])

    now = datetime.now(VN)
    L = [
        f"👤 **RANKING BC THEO AM · VÙNG TÂY BẮC BỘ**",
        f"⏰ Tuần {start.strftime('%d/%m')} — {end.strftime('%d/%m/%Y')} (TB %GTC 7 ngày)",
        f"📋 **{len(am_summary)} AM** · {len(bc_stats)} BC",
        "",
    ]
    for i, am in enumerate(am_summary, 1):
        icon = "🔴" if am["avg_pct"] < 60 else ("🟡" if am["avg_pct"] < 70 else "🟢")
        L.append(f"{icon} **{i}. AM {am['am']}** — TB **{am['avg_pct']}%** · {am['n_bc']} BC · {am['n_low']} BC <60%")
        if am["weak_bcs"]:
            for j, b in enumerate(am["weak_bcs"], 1):
                L.append(f"   {j}. {b['bc']} — **{b['pct']}%** ({b['vol']:,} đơn/tuần)")
        L.append("")

    L.append(f"📱 **[Xem chi tiết Trend](https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/trend.html)**")
    L.append("")
    L.append(f"_🤖 Ranking BC theo AM · Vùng TBB · {now.strftime('%d/%m %H:%M')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · {len(am_summary)} AM")
    send_gtalk(msg, oa, ch, mentioned_user_ids=extract_ids_from_msg(msg))
    print("[INFO] Đã gửi RANKING BC THEO AM vào GTalk")


if __name__ == "__main__":
    main()

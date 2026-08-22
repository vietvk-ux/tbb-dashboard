"""RECAP THÁNG · Ngày 1 hàng tháng 08:30 → GTalk.
So sánh MoM: %GTC · doanh số · tồn đọng · ranking AM/BC/NV.
Fetch 30 ngày tháng này + 30 ngày tháng trước từ Supabase.
"""
from __future__ import annotations
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from calendar import monthrange
from snapshot import _sb_all
from am_map import AM_OF
from am_user_ids import extract_ids_from_msg
from report import send_gtalk

VN = timezone(timedelta(hours=7))


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _tr(v):
    v = v or 0
    if abs(v) >= 1_000_000_000: return f"{v/1_000_000_000:.2f} tỷ ₫"
    if abs(v) >= 1_000_000:     return f"{v/1_000_000:.0f} tr ₫"
    return _n(v) + " ₫"


def _delta(cur, prev):
    """Trả string delta với ▲ ▼."""
    if cur is None or prev is None: return ""
    d = round(cur - prev, 1)
    if d > 0.05: return f" ▲{d}"
    if d < -0.05: return f" ▼{abs(d)}"
    return " ▬"


def _range(month_start, month_end):
    return (month_start.isoformat(), month_end.isoformat())


def _fetch_range(url, key, start, end):
    return _sb_all(url, key, f"bao_cao_buu_cuc?ngay=gte.{start}&ngay=lte.{end}&select=*")


def _fetch_vung_range(url, key, start, end):
    return _sb_all(url, key, f"bao_cao_vung?ngay=gte.{start}&ngay=lte.{end}&select=*")


def _avg_pct(rows, field="pct_gtc"):
    vals = [r.get(field) for r in rows if r.get(field) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _sum_col(rows, field):
    return sum((r.get(field) or 0) for r in rows)


def main():
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (oa and ch and url and key):
        raise SystemExit("Thiếu env")

    today = datetime.now(VN).date()
    # Tháng trước (recap tháng vừa hết)
    if today.month == 1:
        month_year = today.year - 1
        month = 12
    else:
        month_year = today.year
        month = today.month - 1
    m_start = today.replace(year=month_year, month=month, day=1)
    _, last_day = monthrange(month_year, month)
    m_end = today.replace(year=month_year, month=month, day=last_day)
    # Tháng trước nữa
    if month == 1:
        m0_year = month_year - 1
        m0 = 12
    else:
        m0_year = month_year
        m0 = month - 1
    m0_start = today.replace(year=m0_year, month=m0, day=1)
    _, m0_last = monthrange(m0_year, m0)
    m0_end = today.replace(year=m0_year, month=m0, day=m0_last)

    vung_this = _fetch_vung_range(url, key, m_start.isoformat(), m_end.isoformat())
    vung_prev = _fetch_vung_range(url, key, m0_start.isoformat(), m0_end.isoformat())
    bc_this = _fetch_range(url, key, m_start.isoformat(), m_end.isoformat())

    if not vung_this or not bc_this:
        # Supabase chưa có đủ data 1 tháng → gửi tin thông báo thay vì fail
        now = datetime.now(VN)
        msg = "\n".join([
            "📆 **RECAP THÁNG · VÙNG TÂY BẮC BỘ**",
            f"⏰ {now.strftime('%d/%m/%Y')}",
            "",
            f"_⚠️ Chưa đủ dữ liệu cho tháng **{month:02d}/{month_year}** trong Supabase._",
            f"_Supabase sync-23h mới bắt đầu → cần chờ tháng đầy đủ để recap._",
            "",
            f"_🤖 Recap tháng · Vùng TBB · {now.strftime('%d/%m/%Y')}_"
        ])
        print(f"[INFO] Msg {len(msg)} chars · empty month {month}/{month_year}")
        send_gtalk(msg, oa, ch)
        print("[INFO] Đã gửi tin thông báo empty vào GTalk")
        return

    # Vùng metrics
    pct_this = _avg_pct(vung_this)
    pct_prev = _avg_pct(vung_prev)
    don_this = _sum_col(vung_this, "don_giao")
    don_prev = _sum_col(vung_prev, "don_giao")
    gtc_this = _sum_col(vung_this, "gtc")
    gtc_prev = _sum_col(vung_prev, "gtc")
    cod_this = _sum_col(vung_this, "cod_gtb")
    cod_prev = _sum_col(vung_prev, "cod_gtb")
    don_delta_pct = round((don_this - don_prev) / don_prev * 100, 1) if don_prev else 0

    # BC ranking tháng
    by_bc = defaultdict(list)
    for r in bc_this:
        by_bc[r["buu_cuc"]].append(r)
    bc_stats = []
    for bc, rows in by_bc.items():
        vol = _sum_col(rows, "don_giao")
        gtc_count = _sum_col(rows, "gtc")
        pct = round(gtc_count * 100 / vol, 1) if vol else None
        if vol >= 500:  # ngưỡng: ≥500 đơn/tháng
            bc_stats.append({"bc": bc, "am": AM_OF.get(bc, "?"),
                             "vol": vol, "pct": pct})

    # Top 5 xuất sắc + Top 5 yếu
    good = sorted([b for b in bc_stats if b["pct"] is not None],
                  key=lambda x: -x["pct"])[:5]
    bad = sorted([b for b in bc_stats if b["pct"] is not None],
                 key=lambda x: x["pct"])[:5]

    # AM ranking tháng
    by_am = defaultdict(list)
    for b in bc_stats:
        if b["am"] != "?": by_am[b["am"]].append(b)
    am_stats = []
    for am, bcs in by_am.items():
        pcts = [b["pct"] for b in bcs if b["pct"] is not None]
        avg_pct = round(sum(pcts) / len(pcts), 1) if pcts else 0
        am_stats.append({"am": am, "n_bc": len(bcs), "avg_pct": avg_pct})
    am_stats.sort(key=lambda x: -x["avg_pct"])

    now = datetime.now(VN)
    L = [
        "📆 **RECAP THÁNG · VÙNG TÂY BẮC BỘ**",
        f"⏰ Tháng {month:02d}/{month_year} · so sánh tháng {m0:02d}/{m0_year}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "▶️ **KẾT QUẢ VÙNG**",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 %GTC TB: **{pct_this}%**{_delta(pct_this, pct_prev)} _(tháng trước {pct_prev}%)_",
        f"📦 Tổng đơn giao: **{_n(don_this)}** ({'▲' if don_delta_pct > 0 else '▼' if don_delta_pct < 0 else '='}{abs(don_delta_pct)}% so tháng trước)",
        f"✅ GTC tổng: **{_n(gtc_this)}** đơn",
        f"💰 COD GTB: **{_tr(cod_this)}** _(tháng trước {_tr(cod_prev)})_",
        "",
    ]

    if am_stats:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "▶️ 👤 **RANKING AM (theo TB %GTC)**",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, a in enumerate(am_stats, 1):
            icon = "🟢" if a["avg_pct"] >= 70 else ("🟡" if a["avg_pct"] >= 60 else "🔴")
            L.append(f"{i}. {icon} **AM {a['am']}** — TB **{a['avg_pct']}%** · {a['n_bc']} BC")
        L.append("")

    if good:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ 🏆 **TOP {len(good)} BC XUẤT SẮC** _(≥500 đơn/tháng)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(good, 1):
            L.append(f"{i}. 🟢 {b['bc']} · AM {b['am']} — **{b['pct']}%** · {_n(b['vol'])} đơn")
        L.append("")

    if bad:
        L += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"▶️ ⚠️ **TOP {len(bad)} BC CẦN CẢI THIỆN** _(≥500 đơn/tháng)_",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for i, b in enumerate(bad, 1):
            L.append(f"{i}. 🔴 {b['bc']} · AM {b['am']} — **{b['pct']}%** · {_n(b['vol'])} đơn")
        L.append("")

    L.append(f"📱 **[Xem chi tiết Trend](https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/trend.html)**")
    L.append("")
    L.append(f"_🤖 Recap tháng · Vùng TBB · {now.strftime('%d/%m/%Y')}_")
    msg = "\n".join(L)
    print(f"[INFO] Msg {len(msg)} chars · {len(bc_stats)} BC · {len(am_stats)} AM")
    send_gtalk(msg, oa, ch, mentioned_user_ids=extract_ids_from_msg(msg))
    print("[INFO] Đã gửi RECAP THÁNG vào GTalk")


if __name__ == "__main__":
    main()

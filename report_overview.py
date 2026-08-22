"""
Báo cáo TỔNG QUAN vận hành Vùng TBB → GTalk, mỗi 2 tiếng 9h–21h.
Tái dùng engine trang trực tiếp (report_live.fetch_live) + send_gtalk.

Env: NHANH_TOKEN, GTALK_OA_TOKEN, GTALK_CHANNEL_ID.
     OVERVIEW_DRY=1 -> chỉ in ra, KHÔNG gửi (để xem trước).
"""
from __future__ import annotations
import asyncio, logging, os, sys
from datetime import datetime

from report_live import fetch_live, _pct, _n, PROV_NAME, VN
from report import send_gtalk, TokenExpiredError

logger = logging.getLogger("overview")
DASH_URL = "https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/"


def _ico(pct):
    if pct is None:
        return "⚪"
    return "🔴" if pct < 50 else ("🟡" if pct < 80 else "🟢")


def _bar(pct, width=14):
    """Thanh %GTC trực quan bằng ký tự."""
    if pct is None:
        return "▱" * width
    f = max(0, min(width, round(pct / 100.0 * width)))
    return "▰" * f + "▱" * (width - f)


DIV = "━━━━━━━━━━━━━━━━"


def build_msg(rows):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "gtc": 0, "total": 0}
    prov = {}
    for r in rows:
        for k in R:
            R[k] += r[k]
        p = prov.setdefault(r["prov"], {"backlog": 0, "gtc": 0, "total": 0, "bc": 0})
        p["backlog"] += r["backlog"]; p["gtc"] += r["gtc"]; p["total"] += r["total"]; p["bc"] += 1
    reg = _pct(R["gtc"], R["total"])
    can_giao = R["total"] + R["backlog"]

    reg_txt = ("%s" % reg) if reg is not None else "—"
    L = [
        "📊 **TỔNG QUAN 2H · VÙNG TÂY BẮC BỘ**",
        "⏰ %s" % now.strftime("%H:%M · %d/%m/%Y"),
        "",
        "🎯 *%%GTC TOÀN VÙNG:  %s%%*  %s" % (reg_txt, _ico(reg)),
        "%s" % _bar(reg),
        "✅ Giao thành công:  *%s* / %s đơn" % (_n(R["gtc"]), _n(R["total"])),
        "⏳ Chưa gán giao:  *%s* đơn   ·   🏃 Đang chạy:  %s chuyến" % (_n(R["backlog"]), _n(R["ontrip"])),
        "📦 Tổng cần giao:  *%s* đơn" % _n(can_giao),
    ]

    # Theo tỉnh
    L += ["", DIV, "🗺 *%GTC THEO TỈNH*  (thấp → cao)", ""]
    for pv, v in sorted(prov.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        L.append("%s  *%s* — %s%%   ·   chưa gán %s"
                 % (_ico(pc), PROV_NAME.get(pv, pv), pc if pc is not None else "—", _n(v["backlog"])))

    # Top 5 BC chưa gán cao
    top_bl = sorted([r for r in rows if r["backlog"] > 0], key=lambda x: -x["backlog"])[:5]
    if top_bl:
        L += ["", DIV, "🔥 *ĐIỂM NÓNG — TỒN CHƯA GÁN GIAO*", ""]
        for i, r in enumerate(top_bl, 1):
            pc = _pct(r["gtc"], r["total"])
            L.append("%d.  *%s*  —  %s đơn  (GTC %s%%)" % (i, r["name"], _n(r["backlog"]),
                                                          pc if pc is not None else "—"))

    # Top 10 BC %GTC thấp (đủ sản lượng ≥ 30 đơn)
    elig = [r for r in rows if r["total"] >= 30]
    worst = sorted(elig, key=lambda x: _pct(x["gtc"], x["total"]))[:10]
    if worst:
        L += ["", DIV, "🔴 *10 BƯU CỤC %GTC THẤP NHẤT*", ""]
        for i, r in enumerate(worst, 1):
            pc = _pct(r["gtc"], r["total"])
            L.append("%d.  %s  —  *%s%%*  (%s/%s)" % (i, r["name"], pc, _n(r["gtc"]), _n(r["total"])))

    # Nhân viên toàn vùng
    drv_all = []
    for r in rows:
        for d in r.get("drivers", []):
            drv_all.append((d["name"], r["name"], d["total"], d["gtc"]))

    # Top 15 nhân viên %GTC thấp nhất vùng (đủ sản lượng ≥ 20 đơn)
    worst_d = sorted([x for x in drv_all if x[2] >= 20], key=lambda x: _pct(x[3], x[2]))[:15]
    if worst_d:
        L += ["", DIV, "👤🔴 *15 NHÂN VIÊN %GTC THẤP NHẤT*  (≥20 đơn)", ""]
        for i, (nm, bc, tot, g) in enumerate(worst_d, 1):
            L.append("%d.  %s  —  *%s%%*  (%s/%s · %s)" % (i, nm, _pct(g, tot), _n(g), _n(tot), bc))

    # Top 10 nhân viên GTB (giao thất bại = gán - GTC) cao nhất vùng
    gtb_top = sorted(drv_all, key=lambda x: -(x[2] - x[3]))[:10]
    if gtb_top and (gtb_top[0][2] - gtb_top[0][3]) > 0:
        L += ["", DIV, "📛 *10 NHÂN VIÊN GIAO THẤT BẠI NHIỀU NHẤT*", ""]
        for i, (nm, bc, tot, g) in enumerate(gtb_top, 1):
            L.append("%d.  %s  —  *%s GTB*  (%s/%s · %s)" % (i, nm, _n(tot - g), _n(g), _n(tot), bc))

    L += ["", DIV, "📱 Chi tiết realtime (cập nhật mỗi 15'):", DASH_URL,
          "", "_🤖 Overview 2h · Vùng TBB · %s_" % now.strftime("%H:%M %d/%m")]
    return "\n".join(L)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    try:
        rows = asyncio.run(fetch_live(token))
    except TokenExpiredError as e:
        raise SystemExit("Token hết hạn: %s" % e)
    msg = build_msg(rows)
    dry = os.environ.get("OVERVIEW_DRY", "").lower() in ("1", "true", "yes")
    if dry:
        print("=" * 50 + "\n" + msg + "\n" + "=" * 50)
        logger.info("DRY RUN — %d ký tự, KHÔNG gửi", len(msg))
        return
    oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
    ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    if not (oa and ch):
        raise SystemExit("Thiếu GTALK_OA_TOKEN / GTALK_CHANNEL_ID")
    send_gtalk(msg, oa, ch)
    logger.info("Đã gửi báo cáo tổng quan (%d ký tự)", len(msg))


if __name__ == "__main__":
    main()

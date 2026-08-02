"""
Tạo DASHBOARD HTML (mobile) tổng quan giao hàng toàn Vùng TBB — 61 bưu cục.
%GTC thật (giao thành công/tổng) qua get-trip-items. Tái dùng report.py.

Env: NHANH_TOKEN. Tùy chọn EOD_DATE=YYYY-MM-DD (mặc định hôm qua).
Xuất: docs/index.html (self-contained, xem được trên điện thoại) + dashboard_data.json
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from report import (fetch_report, aggregate, send_gtalk, TokenExpiredError,
                    _get_hubs, _post, CONCURRENCY)

logger = logging.getLogger("dash")
VN = timezone(timedelta(hours=7))
PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}


def _cls(g):
    if g is None:
        return "na"
    return "bad" if g < 60 else ("warn" if g < 80 else "good")


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


async def fetch_backlog(token):
    """Số đơn tồn CHƯA GÁN vào chuyến theo bưu cục (live) qua /oss/v4/count-orders-to-assign.
    Trả về {tên_bưu_cục: {'deliver':x,'pick':y,'return':z}}."""
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)

        async def one(h):
            hid = str(h["locationCode"])
            try:
                async with sem:
                    d = await _post(session, "/oss/v4/count-orders-to-assign",
                                    {"hub_id": hid}, hid, token)
                data = d.get("data") or {}
                return (h["locationName"], {"deliver": data.get("deliver") or 0,
                                            "pick": data.get("pick") or 0,
                                            "return": data.get("return") or 0})
            except Exception:
                return (h["locationName"], {"deliver": 0, "pick": 0, "return": 0})

        rows = await asyncio.gather(*[one(h) for h in hubs])
    return dict(rows)


def gen_html(agg, backlog=None, backlog_time="hiện tại"):
    backlog = backlog or {}
    g = agg["grand"]
    d = agg["date"]
    total_gtb = g["total"] - g["success"]
    total_cod = sum(x.get("gtb_cod", 0) for x in agg["drivers"])
    gen_at = datetime.now(VN).strftime("%H:%M %d/%m/%Y")

    # gom nhân viên theo bưu cục
    by_bc = {}
    for dr in agg["drivers"]:
        by_bc.setdefault(dr["bc"], []).append(dr)

    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<title>Báo cáo giao hàng TBB · %s</title>" % d.strftime("%d/%m"))
    P.append("""<style>
:root{--bg:#0f1220;--card:#191d2e;--tx:#e8eaf0;--mut:#9aa0b4;--good:#22c55e;--warn:#f59e0b;--bad:#ef4444;--line:#2a2f45}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1220;color:#e8eaf0;-webkit-text-size-adjust:100%}
.wrap{max-width:760px;margin:0 auto;padding:14px}
h1{font-size:18px;margin:2px 0}.sub{color:#9aa0b4;font-size:12px;margin-bottom:12px}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px}
.kpi{background:#191d2e;border:1px solid #2a2f45;border-radius:12px;padding:12px}
.kpi .v{font-size:22px;font-weight:700}.kpi .l{color:#9aa0b4;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
.big{grid-column:span 2;text-align:center}.big .v{font-size:34px}
.danger{background:#241316;border:1px solid #7f1d1d;border-radius:12px;padding:12px 14px;margin-bottom:14px}
.danger h2{margin:0 0 6px;color:#ef4444}
h2{font-size:14px;margin:18px 0 8px;color:#c7cbe0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 6px;text-align:right;border-bottom:1px solid #2a2f45}
th:first-child,td:first-child{text-align:left}
th{color:#9aa0b4;font-weight:600;font-size:11px;position:sticky;top:0;background:#0f1220}
.pill{display:inline-block;min-width:46px;padding:2px 7px;border-radius:20px;font-weight:700;font-size:12px;color:#0b0e17}
.good{background:#22c55e}.warn{background:#f59e0b}.bad{background:#ef4444}.na{background:#4b5168;color:#e8eaf0}
details{background:#191d2e;border:1px solid #2a2f45;border-radius:12px;margin:8px 0;overflow:hidden}
summary{padding:11px 12px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:8px}
summary::-webkit-details-marker{display:none}
.bc-name{font-weight:600}.bc-meta{color:#9aa0b4;font-size:12px}
details[open] summary{border-bottom:1px solid #2a2f45}
.dtl{padding:2px 12px 10px}
.foot{color:#9aa0b4;font-size:11px;text-align:center;margin:20px 0 8px}
.search{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #2a2f45;background:#191d2e;color:#e8eaf0;font-size:14px;margin-bottom:8px}
</style></head><body><div class='wrap'>""")

    P.append("<h1>📦 Giao hàng Vùng Tây Bắc Bộ</h1>")
    P.append("<div class='sub'>Ngày <b>%s</b> · %d bưu cục · %d chuyến · cập nhật %s</div>"
             % (d.strftime("%d/%m/%Y"), agg["hub_count"], g["trips"], gen_at))

    gtc = g["gtc"] if g["gtc"] is not None else 0
    P.append("<div class='kpis'>")
    P.append("<div class='kpi big'><div class='l'>%%GTC toàn vùng</div><div class='v' style='color:var(--%s)'>%s%%</div></div>"
             % (_cls(g["gtc"]), gtc))
    P.append("<div class='kpi'><div class='l'>Đơn giao</div><div class='v'>%s</div></div>" % _n(g["total"]))
    P.append("<div class='kpi'><div class='l'>Giao thành công</div><div class='v' style='color:var(--good)'>%s</div></div>" % _n(g["success"]))
    P.append("<div class='kpi'><div class='l'>Giao thất bại</div><div class='v' style='color:var(--bad)'>%s</div></div>" % _n(total_gtb))
    P.append("<div class='kpi'><div class='l'>COD GTB</div><div class='v'>%.0f tr</div></div>" % (total_cod / 1e6))
    total_backlog = sum(v.get("deliver", 0) for v in backlog.values())
    P.append("<div class='kpi big'><div class='l'>⏳ Chưa gán giao (chờ xếp chuyến · %s)</div><div class='v' style='color:var(--warn)'>%s đơn</div></div>"
             % (_esc(backlog_time), _n(total_backlog)))
    P.append("</div>")

    # ⚠️ Nhóm nhân viên nguy hiểm cần chú ý (GTC thấp + nhiều đơn hỏng, ≥20 đơn)
    MIN, NGUONG = 20, 45.0
    danger = [dr for dr in agg["drivers"]
              if dr["total"] >= MIN and dr["gtc"] is not None and dr["gtc"] < NGUONG]
    danger.sort(key=lambda x: (-(x["total"] - x["success"]), x["gtc"]))  # nhiều đơn hỏng nhất trước
    P.append("<div class='danger'>")
    P.append("<h2>⚠️ NHÓM NHÂN VIÊN NGUY HIỂM CẦN CHÚ Ý</h2>")
    if not danger:
        P.append("<div class='sub'>✅ Không có nhân viên nào %%GTC dưới %d%% (≥%d đơn). Vùng ổn định.</div>"
                 % (int(NGUONG), MIN))
    else:
        tot_gtb = sum(dr["total"] - dr["success"] for dr in danger)
        tot_cod = sum(dr.get("gtb_cod", 0) for dr in danger)
        top3 = " · ".join("%s (%s)" % (dr["driver_name"], dr["bc"]) for dr in danger[:3])
        P.append("<div class='sub'><b>%d nhân viên</b> %%GTC &lt;%d%% (≥%d đơn) → <b style='color:var(--bad)'>%s đơn giao hỏng</b>, kẹt <b>%.0f triệu</b> COD.<br>🔥 Nguy hiểm nhất: <b>%s</b> — cần đốc thúc/kiểm tra ngay.</div>"
                 % (len(danger), int(NGUONG), MIN, _n(tot_gtb), tot_cod / 1e6, _esc(top3)))
        P.append("<table><tr><th>#</th><th>Nhân viên</th><th>Bưu cục</th><th>Đơn</th><th>Hỏng</th><th>%GTC</th></tr>")
        for i, dr in enumerate(danger[:15], 1):
            gtb = dr["total"] - dr["success"]
            P.append("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td style='color:var(--bad);font-weight:700'>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                     % (i, _esc(dr["driver_name"]), _esc(dr["bc"]), _n(dr["total"]), _n(gtb),
                        _cls(dr["gtc"]), dr["gtc"]))
        P.append("</table>")
    P.append("</div>")

    # Theo tỉnh
    P.append("<h2>🗺 Theo tỉnh (GTC thấp → cao)</h2><table><tr><th>Tỉnh</th><th>BC</th><th>Chuyến</th><th>Đơn</th><th>%GTC</th></tr>")
    for p in sorted(agg["provinces"], key=lambda x: (x["gtc"] if x["gtc"] is not None else 999)):
        P.append("<tr><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                 % (_esc(PROV_NAME.get(p["prov"], p["prov"])), p["bc_count"], p["trips"], _n(p["total"]),
                    _cls(p["gtc"]), p["gtc"] if p["gtc"] is not None else "—"))
    P.append("</table>")

    # 61 bưu cục — bảng + drill nhân viên
    P.append("<h2>🏤 Tất cả bưu cục (%d) — GTC thấp → cao</h2>" % len(agg["bcs"]))
    P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput=\"filt()\">")
    bcs = sorted(agg["bcs"], key=lambda x: (x["gtc"] if x["gtc"] is not None else 999, -x["total"]))
    for b in bcs:
        drivers = sorted(by_bc.get(b["bc"], []),
                         key=lambda x: (x["gtc"] if x["gtc"] is not None else 999))
        P.append("<details class='bcrow' data-k=\"%s\">" % _esc((b["bc"] + " " + " ".join(dr["driver_name"] for dr in drivers)).lower()))
        bl = backlog.get(b["bc"], {})
        bl_d = bl.get("deliver", 0)
        blstr = ("⏳<b style='color:var(--warn)'>%s</b> chờ gán · " % _n(bl_d)) if bl_d else ""
        P.append("<summary><span class='bc-name'>%s</span><span class='bc-meta'>%s%s đơn · GTB %s · <span class='pill %s'>%s%%</span></span></summary>"
                 % (_esc(b["bc"]), blstr, _n(b["total"]), _n(b["total"] - b["success"]), _cls(b["gtc"]),
                    b["gtc"] if b["gtc"] is not None else "—"))
        P.append("<div class='dtl'>")
        if bl_d or bl.get("pick") or bl.get("return"):
            P.append("<div class='sub' style='margin:2px 0 8px'>⏳ Chưa gán chuyến: <b>%s</b> giao · %s lấy · %s trả</div>"
                     % (_n(bl_d), _n(bl.get("pick", 0)), _n(bl.get("return", 0))))
        P.append("<table><tr><th>Nhân viên</th><th>Đơn</th><th>GTC</th><th>GTB</th><th>%GTC</th></tr>")
        for dr in drivers:
            P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                     % (_esc(dr["driver_name"]), _n(dr["total"]), _n(dr["success"]),
                        _n(dr["total"] - dr["success"]), _cls(dr["gtc"]),
                        dr["gtc"] if dr["gtc"] is not None else "—"))
        P.append("</table></div></details>")

    if agg["errors"]:
        P.append("<div class='sub'>⚠️ %d chuyến không lấy được chi tiết (API lỗi tạm thời)</div>" % agg["errors"])
    P.append("<div class='foot'>%%GTC = giao thành công / tổng đơn giao · Tổng hợp tự động từ nhanh.ghn.vn</div>")
    P.append("""<script>
function filt(){var q=document.getElementById('q').value.toLowerCase().trim();
document.querySelectorAll('.bcrow').forEach(function(e){e.style.display=(!q||e.dataset.k.indexOf(q)>=0)?'':'none';});}
</script></div></body></html>""")
    return "\n".join(P)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    s = os.environ.get("EOD_DATE", "").strip()
    # Không chỉ định ngày: báo cáo NGÀY VỪA KẾT THÚC.
    # >=22h coi là báo cáo của hôm nay; ngược lại (kể cả run trễ qua nửa đêm, hoặc
    # xem ban ngày) lấy hôm qua — tránh bị "0 chuyến" khi lịch chạy trễ sang ngày mới.
    if s:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    else:
        now = datetime.now(VN)
        d = now.date() if now.hour >= 22 else (now.date() - timedelta(days=1))
    logger.info("Dashboard ngày %s", d)
    try:
        payload = asyncio.run(fetch_report(token, d))
        agg = aggregate(payload)
    except TokenExpiredError as e:
        raise SystemExit("Token hết hạn: %s" % e)

    # Đơn chưa gán giao: lấy số THẬT (live) tại thời điểm chạy báo cáo cuối ngày (23h).
    backlog, backlog_time = {}, "cuối ngày"
    try:
        backlog = asyncio.run(fetch_backlog(token))
    except TokenExpiredError:
        backlog = {}
    total_backlog = sum(v.get("deliver", 0) for v in backlog.values())
    logger.info("Tồn chưa gán giao toàn vùng (%s): %d đơn", backlog_time, total_backlog)
    # URL bí mật: ghi vào docs/<slug>/index.html (URL gốc sẽ 404)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "eod.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(agg, backlog, backlog_time))
    with open("dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump({"date": d.isoformat(), "grand": agg["grand"],
                   "provinces": agg["provinces"], "bcs": agg["bcs"],
                   "drivers": agg["drivers"], "hub_count": agg["hub_count"]},
                  f, ensure_ascii=False)
    logger.info("Xong · %d bưu cục · %d nhân viên · GTC %s%%",
                len(agg["bcs"]), len(agg["drivers"]), agg["grand"]["gtc"])

    # Gửi tóm tắt + link dashboard vào GTalk
    if os.environ.get("DASH_SEND", "").lower() in ("1", "true", "yes"):
        oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
        ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
        url = os.environ.get("DASH_URL", "").strip()
        if oa and ch:
            g = agg["grand"]
            gtb = g["total"] - g["success"]
            cod = sum(x.get("gtb_cod", 0) for x in agg["drivers"])
            worst = sorted([b for b in agg["bcs"] if b["gtc"] is not None],
                           key=lambda x: x["gtc"])[:5]
            L = ["📊 **DASHBOARD GIAO HÀNG TBB — %s**" % d.strftime("%d/%m/%Y"),
                 "%d bưu cục · %d chuyến kết thúc" % (agg["hub_count"], g["trips"]),
                 "",
                 "🎯 %%GTC vùng: **%s%%** · Giao TC %s · GTB %s" %
                 (g["gtc"] if g["gtc"] is not None else "—", _n(g["success"]), _n(gtb)),
                 "💰 COD GTB: **%.0f triệu₫**" % (cod / 1e6),
                 "⏳ Chưa gán giao (chờ xếp chuyến · %s): **%s đơn**" % (backlog_time, _n(total_backlog)),
                 "",
                 "🔴 5 bưu cục %GTC thấp nhất:"]
            for i, b in enumerate(worst, 1):
                L.append("%d. %s — %s%%" % (i, b["bc"], b["gtc"]))
            # ⚠️ Nhóm nhân viên nguy hiểm
            dng = [x for x in agg["drivers"]
                   if x["total"] >= 20 and x["gtc"] is not None and x["gtc"] < 45]
            dng.sort(key=lambda x: -(x["total"] - x["success"]))
            if dng:
                d_gtb = sum(x["total"] - x["success"] for x in dng)
                L += ["", "⚠️ **NHÂN VIÊN NGUY HIỂM: %d người** (%%GTC<45%%, ≥20đ) · %s đơn hỏng:" % (len(dng), _n(d_gtb))]
                for i, x in enumerate(dng[:5], 1):
                    L.append("%d. %s (%s) — hỏng %s/%s đơn · %s%%"
                             % (i, x["driver_name"], x["bc"], _n(x["total"] - x["success"]),
                                _n(x["total"]), x["gtc"]))
            top_bl = sorted(backlog.items(), key=lambda kv: -kv[1].get("deliver", 0))[:5]
            if top_bl and top_bl[0][1].get("deliver", 0) > 0:
                L += ["", "⏳ 5 bưu cục tồn chưa gán giao nhiều nhất:"]
                for i, (name, v) in enumerate(top_bl, 1):
                    L.append("%d. %s — %s đơn" % (i, name, _n(v.get("deliver", 0))))
            if url:
                L += ["", "📱 Xem đầy đủ 61 bưu cục + chi tiết nhân viên:", url]
            send_gtalk("\n".join(L), oa, ch)
            logger.info("Đã gửi tóm tắt dashboard vào GTalk")


if __name__ == "__main__":
    main()

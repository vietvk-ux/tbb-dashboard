"""
Trang GẦN REALTIME giao hàng Vùng TBB (mobile) — tự tạo lại mỗi ~30'.
Số LIVE: tồn chưa gán giao · chuyến đang chạy + tiến độ · đơn đã giao hôm nay tới hiện tại.
Nhẹ (không bóc từng đơn). %GTC đầy đủ ở báo cáo 23h.

Env: NHANH_TOKEN. Xuất: docs/<slug>/live.html (+ index.html cùng nội dung).
"""
from __future__ import annotations
import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from report import _get_hubs, _post, CONCURRENCY, TokenExpiredError

logger = logging.getLogger("live")
VN = timezone(timedelta(hours=7))
PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


def _prov(name):
    return name[name.find("(") + 1:name.find(")")] if "(" in name else "?"


def _pcls(p):
    if p is None:
        return "na"
    return "bad" if p < 50 else ("warn" if p < 80 else "good")


async def fetch_live(token):
    today = datetime.now(VN).date()
    ymd = today.year * 10000 + today.month * 100 + today.day
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)

        async def one(h):
            hid = str(h["locationCode"])
            name = h["locationName"]
            try:
                async with sem:
                    bl = await _post(session, "/oss/v4/count-orders-to-assign",
                                     {"hub_id": hid}, hid, token)
                backlog = (bl.get("data") or {}).get("deliver", 0)
                async with sem:
                    ot = await _post(session, "/lastmile/trip/get-trip-list-by-hub",
                                     {"hub_id": hid, "status": "ON_TRIP", "is_ready": 0,
                                      "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1},
                                     hid, token)
                ontrip = ot.get("data") or []
                async with sem:
                    fn = await _post(session, "/lastmile/trip/get-trip-list-by-hub",
                                     {"hub_id": hid, "status": "FINISHED",
                                      "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1},
                                     hid, token)
                fin_today = [t for t in (fn.get("data") or []) if t.get("endDateIndex") == ymd]
                codes = [t["tripCode"] for t in ontrip] + [t["tripCode"] for t in fin_today]
                pm = {}
                if codes:
                    async with sem:
                        pr = await _post(session, "/lastmile/trip/get-trip-profile",
                                         {"tripCodes": codes}, hid, token)
                    pm = {x["tripCode"]: x for x in (pr.get("data") or [])}
                ot_order = ot_upd = 0
                drivers = {}
                for t in ontrip:
                    p = pm.get(t["tripCode"], {})
                    o = p.get("orderCount") or 0
                    u = p.get("updatedCount") or 0
                    ot_order += o
                    ot_upd += u
                    k = t.get("driverName") or "—"
                    d = drivers.setdefault(k, {"name": k, "order": 0, "upd": 0})
                    d["order"] += o
                    d["upd"] += u
                giao_today = ot_upd
                for t in fin_today:
                    p = pm.get(t["tripCode"], {})
                    giao_today += p.get("updatedCount") or 0
                return {"name": name, "prov": _prov(name), "backlog": backlog,
                        "ontrip": len(ontrip), "ot_order": ot_order, "ot_upd": ot_upd,
                        "giao_today": giao_today, "drivers": list(drivers.values())}
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Hub %s lỗi: %s", name, str(e)[:100])
                return {"name": name, "prov": _prov(name), "backlog": 0, "ontrip": 0,
                        "ot_order": 0, "ot_upd": 0, "giao_today": 0, "drivers": []}

        rows = await asyncio.gather(*[one(h) for h in hubs])
    return rows


def gen_html(rows):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "ot_order": 0, "ot_upd": 0, "giao_today": 0}
    prov = {}
    for r in rows:
        for k in R:
            R[k] += r[k]
        p = prov.setdefault(r["prov"], {"backlog": 0, "ontrip": 0, "ot_order": 0, "ot_upd": 0, "giao_today": 0})
        for k in R:
            p[k] += r[k]
    ot_prog = round(R["ot_upd"] * 100 / R["ot_order"], 1) if R["ot_order"] else None

    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta http-equiv='refresh' content='300'>")
    P.append("<title>TBB trực tiếp · %s</title>" % now.strftime("%H:%M"))
    P.append("""<style>
:root{--bg:#0f1220;--card:#191d2e;--mut:#9aa0b4;--good:#22c55e;--warn:#f59e0b;--bad:#ef4444;--line:#2a2f45}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1220;color:#e8eaf0}
.wrap{max-width:760px;margin:0 auto;padding:14px}
h1{font-size:18px;margin:2px 0}.sub{color:#9aa0b4;font-size:12px;margin-bottom:12px}
.live{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:6px;animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px}
.kpi{background:#191d2e;border:1px solid #2a2f45;border-radius:12px;padding:12px}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{color:#9aa0b4;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
.big{grid-column:span 2;text-align:center}.big .v{font-size:32px}
h2{font-size:14px;margin:18px 0 8px;color:#c7cbe0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 6px;text-align:right;border-bottom:1px solid #2a2f45}
th:first-child,td:first-child{text-align:left}
th{color:#9aa0b4;font-weight:600;font-size:11px}
.pill{display:inline-block;min-width:42px;padding:2px 7px;border-radius:20px;font-weight:700;font-size:12px;color:#0b0e17}
.good{background:#22c55e}.warn{background:#f59e0b}.bad{background:#ef4444}.na{background:#4b5168;color:#e8eaf0}
details{background:#191d2e;border:1px solid #2a2f45;border-radius:12px;margin:8px 0}
summary{padding:11px 12px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;gap:8px}
summary::-webkit-details-marker{display:none}
.bc-name{font-weight:600}.bc-meta{color:#9aa0b4;font-size:12px}
.dtl{padding:0 12px 10px}.foot{color:#9aa0b4;font-size:11px;text-align:center;margin:20px 0}
.search{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #2a2f45;background:#191d2e;color:#e8eaf0;font-size:14px;margin-bottom:8px}
</style></head><body><div class='wrap'>""")
    P.append("<h1><span class='live'></span>TBB TRỰC TIẾP</h1>")
    P.append("<div class='sub'>Cập nhật <b>%s %s</b> · tự làm mới 5 phút/lần</div>"
             % (now.strftime("%H:%M"), now.strftime("%d/%m")))
    P.append("<div class='kpis'>")
    P.append("<div class='kpi big'><div class='l'>⏳ Đơn chưa gán giao (chờ xếp chuyến)</div><div class='v' style='color:var(--warn)'>%s</div></div>" % _n(R["backlog"]))
    P.append("<div class='kpi'><div class='l'>🚚 Chuyến đang chạy</div><div class='v'>%s</div></div>" % _n(R["ontrip"]))
    P.append("<div class='kpi'><div class='l'>📦 Đã giao hôm nay</div><div class='v' style='color:var(--good)'>%s</div></div>" % _n(R["giao_today"]))
    P.append("<div class='kpi big'><div class='l'>Tiến độ chuyến đang chạy</div><div class='v' style='color:var(--%s)'>%s%%</div></div>"
             % (_pcls(ot_prog), ot_prog if ot_prog is not None else "—"))
    P.append("</div>")
    P.append("<a href='eod.html' style='display:block;text-align:center;padding:11px;border:1px solid #2a2f45;border-radius:10px;background:#191d2e;color:#e8eaf0;text-decoration:none;margin-bottom:14px'>📊 Báo cáo %GTC cuối ngày (theo nhân viên) →</a>")

    P.append("<h2>🗺 Theo tỉnh</h2><table><tr><th>Tỉnh</th><th>Chưa gán</th><th>Đang chạy</th><th>Giao hôm nay</th></tr>")
    for pv, v in sorted(prov.items(), key=lambda kv: -kv[1]["backlog"]):
        P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (_esc(PROV_NAME.get(pv, pv)), _n(v["backlog"]), _n(v["ontrip"]), _n(v["giao_today"])))
    P.append("</table>")

    P.append("<h2>🏤 Bưu cục — tồn chưa gán cao nhất</h2>")
    P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'>")
    for r in sorted(rows, key=lambda x: -x["backlog"]):
        if r["backlog"] == 0 and r["ontrip"] == 0 and r["giao_today"] == 0:
            continue
        prog = round(r["ot_upd"] * 100 / r["ot_order"], 1) if r["ot_order"] else None
        keys = (r["name"] + " " + " ".join(d["name"] for d in r["drivers"])).lower()
        P.append("<details class='bcrow' data-k=\"%s\">" % _esc(keys))
        P.append("<summary><span class='bc-name'>%s</span><span class='bc-meta'>⏳<b style='color:var(--warn)'>%s</b> chưa gán · 🚚%s · giao %s</span></summary>"
                 % (_esc(r["name"]), _n(r["backlog"]), _n(r["ontrip"]), _n(r["giao_today"])))
        P.append("<div class='dtl'>")
        if r["drivers"]:
            P.append("<table><tr><th>Nhân viên (đang chạy)</th><th>Đơn</th><th>Đã giao</th><th>Tiến độ</th></tr>")
            for d in sorted(r["drivers"], key=lambda x: (x["upd"] / x["order"] if x["order"] else 1)):
                pc = round(d["upd"] * 100 / d["order"], 1) if d["order"] else None
                P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _n(d["order"]), _n(d["upd"]), _pcls(pc),
                            pc if pc is not None else "—"))
            P.append("</table>")
        else:
            P.append("<div class='sub'>Không có chuyến đang chạy.</div>")
        P.append("</div></details>")
    P.append("<div class='foot'>Số liệu LIVE tại thời điểm cập nhật · nhanh.ghn.vn · %%GTC đầy đủ ở báo cáo 23h</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim();document.querySelectorAll('.bcrow').forEach(function(e){e.style.display=(!q||e.dataset.k.indexOf(q)>=0)?'':'none';});}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


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
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    h = gen_html(rows)
    for fn in ("index.html", "live.html"):
        with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
            f.write(h)
    tot = sum(r["backlog"] for r in rows)
    logger.info("Live: %d bưu cục · chưa gán %d · đang chạy %d chuyến",
                len(rows), tot, sum(r["ontrip"] for r in rows))


if __name__ == "__main__":
    main()

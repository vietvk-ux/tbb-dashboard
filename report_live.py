"""
Trang GẦN REALTIME giao hàng Vùng TBB (mobile) — tự tạo lại mỗi ~30'.
Số LIVE (bóc từng đơn qua get-trip-items): tồn chưa gán · chuyến đang chạy ·
đơn GTC (giao thành công) hôm nay tới hiện tại · %GTC (GTC/đã xử lý) theo bưu cục & nhân viên.

Env: NHANH_TOKEN. Xuất: docs/<slug>/index.html (+ live.html).
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


def _pct(gtc, att):
    return round(gtc * 100 / att, 1) if att else None


def _cls(p):
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

                async def _list(status):
                    async with sem:
                        r = await _post(session, "/lastmile/trip/get-trip-list-by-hub",
                                        {"hub_id": hid, "status": status, "is_ready": 0,
                                         "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1},
                                        hid, token)
                    return r.get("data") or []
                ontrip = await _list("ON_TRIP")
                fin = [t for t in await _list("FINISHED") if t.get("endDateIndex") == ymd]

                async def _it(t):
                    dn = t.get("driverName") or "—"
                    try:
                        async with sem:
                            d = await _post(session, "/lastmile/trip/get-trip-items",
                                            {"tripCode": t["tripCode"]}, hid, token)
                        its = [x for x in (d.get("data") or []) if x.get("type") == "DELIVER"]
                        gtc = sum(1 for x in its if x.get("isSucceeded") is True)
                        att = sum(1 for x in its if x.get("isUpdated") is True)
                        return (dn, gtc, att, len(its))
                    except Exception:
                        return (dn, 0, 0, 0)

                res = await asyncio.gather(*[_it(t) for t in (ontrip + fin)])
                drivers = {}
                h_gtc = h_att = h_total = 0
                for dn, gtc, att, tot in res:
                    d = drivers.setdefault(dn, {"name": dn, "gtc": 0, "att": 0, "total": 0})
                    d["gtc"] += gtc
                    d["att"] += att
                    d["total"] += tot
                    h_gtc += gtc
                    h_att += att
                    h_total += tot
                return {"name": name, "prov": _prov(name), "backlog": backlog,
                        "ontrip": len(ontrip), "fin": len(fin), "gtc": h_gtc,
                        "att": h_att, "total": h_total, "drivers": list(drivers.values())}
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Hub %s lỗi: %s", name, str(e)[:100])
                return {"name": name, "prov": _prov(name), "backlog": 0, "ontrip": 0,
                        "fin": 0, "gtc": 0, "att": 0, "total": 0, "drivers": []}

        return await asyncio.gather(*[one(h) for h in hubs])


def gen_html(rows):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0}
    prov = {}
    for r in rows:
        for k in R:
            R[k] += r[k]
        p = prov.setdefault(r["prov"], {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "total": 0})
        for k in ("backlog", "ontrip", "fin", "gtc", "total"):
            p[k] += r[k]
    reg_pct = _pct(R["gtc"], R["total"])

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
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
.kpi{background:#191d2e;border:1px solid #2a2f45;border-radius:12px;padding:12px}
.kpi .v{font-size:24px;font-weight:700}.kpi .l{color:#9aa0b4;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
.big{grid-column:span 2;text-align:center}.big .v{font-size:32px}
a.eod{display:block;text-align:center;padding:11px;border:1px solid #2a2f45;border-radius:10px;background:#191d2e;color:#e8eaf0;text-decoration:none;margin-bottom:14px}
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
    can_giao = R["total"] + R["backlog"]
    P.append("<div class='kpis'>")
    P.append("<div class='kpi'><div class='l'>📥 Đã gán lên chuyến</div><div class='v'>%s</div></div>" % _n(R["total"]))
    P.append("<div class='kpi'><div class='l'>⏳ Chưa gán giao (chờ xếp chuyến)</div><div class='v' style='color:var(--warn)'>%s</div></div>" % _n(R["backlog"]))
    P.append("<div class='kpi'><div class='l'>🚚 Chuyến đang chạy</div><div class='v'>%s</div></div>" % _n(R["ontrip"]))
    P.append("<div class='kpi'><div class='l'>📦 Đơn GTC hôm nay (tới hiện tại)</div><div class='v' style='color:var(--good)'>%s</div></div>" % _n(R["gtc"]))
    P.append("<div class='kpi big'><div class='l'>🎯 %%GTC toàn vùng · tổng cần giao %s đơn</div><div class='v' style='color:var(--%s)'>%s%%</div></div>"
             % (_n(can_giao), _cls(reg_pct), reg_pct if reg_pct is not None else "—"))
    P.append("</div>")
    P.append("<a class='eod' href='eod.html'>📊 Báo cáo %GTC cuối ngày (chi tiết nhân viên) →</a>")

    P.append("<h2>🗺 Theo tỉnh (%GTC thấp → cao)</h2><table><tr><th>Tỉnh</th><th>Chuyến</th><th>Đã gán</th><th>Chưa gán</th><th>GTC</th><th>%GTC</th></tr>")
    for pv, v in sorted(prov.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                 % (_esc(PROV_NAME.get(pv, pv)), _n(v["ontrip"] + v["fin"]), _n(v["total"]),
                    _n(v["backlog"]), _n(v["gtc"]), _cls(pc), pc if pc is not None else "—"))
    P.append("</table>")

    P.append("<h2>🏤 Bưu cục — %GTC thấp → cao</h2>")
    P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'>")
    bcs = [r for r in rows if r["backlog"] or r["ontrip"] or r["total"]]
    for r in sorted(bcs, key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999, -x["backlog"])):
        pc = _pct(r["gtc"], r["total"])
        keys = (r["name"] + " " + " ".join(d["name"] for d in r["drivers"])).lower()
        P.append("<details class='bcrow' data-k=\"%s\">" % _esc(keys))
        P.append("<summary><span class='bc-name'>%s</span><span class='bc-meta'>🚚%s · 📥%s · ⏳<b style='color:var(--warn)'>%s</b> · GTC %s · <span class='pill %s'>%s%%</span></span></summary>"
                 % (_esc(r["name"]), _n(r["ontrip"] + r["fin"]), _n(r["total"]), _n(r["backlog"]),
                    _n(r["gtc"]), _cls(pc), pc if pc is not None else "—"))
        P.append("<div class='dtl'>")
        drv = [d for d in r["drivers"] if d["total"] > 0]
        if drv:
            P.append("<table><tr><th>Nhân viên</th><th>Tổng gán</th><th>GTC</th><th>%GTC</th></tr>")
            for d in sorted(drv, key=lambda x: -x["gtc"]):
                pc2 = _pct(d["gtc"], d["total"])
                P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td><span class='pill %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _n(d["total"]), _n(d["gtc"]), _cls(pc2),
                            pc2 if pc2 is not None else "—"))
            P.append("</table>")
        else:
            P.append("<div class='sub'>Chưa có đơn giao hôm nay.</div>")
        P.append("</div></details>")
    P.append("<div class='foot'>GTC = giao thành công · %GTC = GTC / tổng đơn gán giao · số LIVE tới thời điểm cập nhật · nhanh.ghn.vn</div>")
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
    tg = sum(r["gtc"] for r in rows)
    ta = sum(r["att"] for r in rows)
    logger.info("Live: %d bưu cục · chưa gán %d · đang chạy %d · GTC %d · %%GTC %s",
                len(rows), sum(r["backlog"] for r in rows), sum(r["ontrip"] for r in rows),
                tg, _pct(tg, ta))


if __name__ == "__main__":
    main()

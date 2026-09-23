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
from report import (fetch_report, aggregate, dedup_orders, send_gtalk, TokenExpiredError,
                    _get_hubs, _post, CONCURRENCY, fetch_chua_gan)
from am_map import AM_OF

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


def _cmpd(cur, ref, kind="n", higher_good=True):
    """Δ hôm nay vs hôm qua. kind: n(số)/pct(điểm %)/money(triệu).
    higher_good True→tăng xanh, False→tăng đỏ, None→trung tính (xám)."""
    if cur is None or ref is None:
        return "<span class='mut'>—</span>"
    d = cur - ref
    if abs(d) < (0.05 if kind != "n" else 1):
        return "<span class='mut'>—</span>"
    if kind == "money":
        s = ("%.1f" % (abs(d) / 1e6)).replace(".", ",") + "tr"
    elif kind == "pct":
        s = ("%.1f" % abs(d)).replace(".", ",")
    else:
        s = _n(abs(d))
    arr = "▲" if d > 0 else "▼"
    if higher_good is None:
        return "<span class='mut'>%s%s</span>" % (arr, s)
    cls = "up" if (d > 0) == higher_good else "down"
    return "<span class='%s'>%s%s</span>" % (cls, arr, s)


def _dmy(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m")
    except Exception:
        return iso or "—"


def _fmt(v, kind="n"):
    if v is None:
        return "—"
    if kind == "pct":
        return ("%s%%" % v)
    if kind == "money":
        return ("%.1f" % (v / 1e6)).replace(".", ",") + "tr"
    return _n(v)


async def fetch_backlog(token):
    """Số đơn tồn CHƯA GÁN CHUYẾN theo bưu cục (view 'Chưa có chuyến đi trong ngày',
    get-general-info order_type=DAILY_TRIP_NONE — chuẩn theo trang Tồn LGT, VD Bum Tở Giao 653).
    Trả về {tên_bưu_cục: {'deliver':x,'pick':y,'return':z}}."""
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)

        async def one(h):
            hid = str(h["locationCode"])
            async with sem:
                g = await fetch_chua_gan(session, hid, token)
            return (h["locationName"], {"deliver": g.get("deliver", 0),
                                        "pick": g.get("pick", 0),
                                        "return": g.get("return", 0)})

        rows = await asyncio.gather(*[one(h) for h in hubs])
    return dict(rows)


async def fetch_ontrip(token):
    """Nhân viên còn chuyến ĐANG CHẠY (CHƯA kết thúc) — đơn trên đó CHƯA tính vào %GTC.
    Dùng deliverCount trong danh sách chuyến (không cần bóc từng đơn). Gộp theo (NV, bưu cục)."""
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)

        async def one(h):
            hid = str(h["locationCode"])
            try:
                async with sem:
                    d = await _post(session, "/lastmile/trip/get-trip-list-by-hub",
                                    {"hub_id": hid, "status": "ON_TRIP", "is_ready": 0,
                                     "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1},
                                    hid, token)
                return [(h["locationName"], str(t.get("driverId") or ""), t.get("driverName") or "—",
                         t.get("deliverCount") or 0) for t in (d.get("data") or [])]
            except Exception:
                return []

        lists = await asyncio.gather(*[one(h) for h in hubs])
    agg = {}
    for lst in lists:
        for bc, did, dn, don in lst:
            k = (did, bc)
            a = agg.setdefault(k, {"driver_id": did, "driver_name": dn, "bc": bc, "trips": 0, "don": 0})
            a["trips"] += 1
            a["don"] += don
    # phân biệt trùng tên trong cùng bưu cục
    from collections import Counter
    nc = Counter((a["driver_name"], a["bc"]) for a in agg.values())
    for a in agg.values():
        if nc[(a["driver_name"], a["bc"])] > 1 and a["driver_id"]:
            a["driver_name"] = "%s #%s" % (a["driver_name"], a["driver_id"][-6:])
    return sorted(agg.values(), key=lambda x: -x["don"])


def _bar(pct, cls):
    w = pct if pct is not None else 0
    return "<div class='bar'><i class='%s' style='width:%s%%'></i></div>" % (cls, w)


def _bc_drv_details(b, drivers):
    """1 bưu cục dạng <details> LỒNG (bấm ra bảng nhân viên + COD) — dùng trong mục AM/tỉnh eod."""
    pc = b["gtc"]
    cls = _cls(pc)
    bcod = sum(d.get("gtb_cod", 0) for d in drivers)
    P = ["<details class='bc sub %s'><summary>" % cls]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
             "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(b["bc"]), cls, pc if pc is not None else "—"))
    P.append("<div class='bcm'><span>📦 %s</span><span>✅ %s</span>"
             "<span class='b'>❌ %s</span><span>💰 %.0ftr</span></div>"
             % (_n(b["total"]), _n(b["success"]), _n(b["total"] - b["success"]), bcod / 1e6))
    P.append("</summary><div class='dtl'>")
    if drivers:
        P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Đơn</th><th>GTC</th><th>LTC</th>"
                 "<th>GTB</th><th>COD tr</th><th>%GTC</th></tr></thead><tbody>")
        for dr in sorted(drivers, key=lambda x: (x["gtc"] if x["gtc"] is not None else 999)):
            dltc = dr.get("ltc", 0)
            ltc_cell = ("<b class='ltc'>%s</b>" % _n(dltc)) if dltc > 0 else "0"
            dcod = dr.get("gtb_cod", 0) or 0
            cod_cell = ("<b class='cod'>%s</b>" % ("%.1f" % (dcod / 1e6)).replace(".", ",")) if dcod >= 1e5 else "0"
            P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (_esc(dr["driver_name"]), _n(dr["total"]), _n(dr["success"]), ltc_cell,
                        _n(dr["total"] - dr["success"]), cod_cell, _cls(dr["gtc"]),
                        dr["gtc"] if dr["gtc"] is not None else "—"))
        P.append("</tbody></table>")
    else:
        P.append("<div class='note'>Không có nhân viên.</div>")
    P.append("</div></details>")
    return "".join(P)


_CSS = """<style>
:root{
 --bg:#0a0d18;--card:#161b2d;--card2:#1b2136;--line:#272d45;
 --mut:#8b92ab;--txt:#eef0f7;--good:#2fd07a;--warn:#f7b955;--bad:#f2585f;--ink:#0a0d18
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background:linear-gradient(180deg,#0b0f1c 0%,#0a0d18 240px,#0a0d18 100%);color:var(--txt);
 -webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%;font-size:15px;line-height:1.35}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(30px + env(safe-area-inset-bottom))}

.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
 padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0));margin-bottom:4px}
.brand{font-weight:800;letter-spacing:.04em;font-size:15px;display:flex;align-items:center;gap:8px}
.ts{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums;text-align:right}

.hero{border-radius:20px;padding:20px 18px 18px;margin:4px 0 12px;position:relative;overflow:hidden;
 background:radial-gradient(120% 90% at 100% 0,rgba(255,255,255,.05),transparent),var(--card);border:1px solid var(--line)}
.hero.good{box-shadow:0 10px 30px -12px rgba(47,208,122,.35)}
.hero.warn{box-shadow:0 10px 30px -12px rgba(247,185,85,.32)}
.hero.bad{box-shadow:0 10px 30px -12px rgba(242,88,95,.32)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.hpct{font-size:64px;font-weight:850;line-height:1;margin:8px 0 12px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}.hero.na .hpct{color:var(--mut)}
.hpct span{font-size:26px;font-weight:700;opacity:.6;margin-left:2px}
.hsub{color:var(--mut);font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}

.bar{height:7px;background:rgba(255,255,255,.07);border-radius:99px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:99px}
.bar i.good{background:linear-gradient(90deg,#25b56b,#2fd07a)}
.bar i.warn{background:linear-gradient(90deg,#e39a2e,#f7b955)}
.bar i.bad{background:linear-gradient(90deg,#d8434b,#f2585f)}
.bar i.na{background:#4b5168}

.strip{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:10px}
.st{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 4px;text-align:center}
.sv{font-size:15px;font-weight:800;font-variant-numeric:tabular-nums}
.sv.good{color:var(--good)}.sv.warn{color:var(--warn)}.sv.bad{color:var(--bad)}
.sl{color:var(--mut);font-size:10.5px;margin-top:3px;white-space:nowrap}
@media(max-width:430px){.strip{gap:4px}.st{padding:9px 2px}.sv{font-size:14px}.sl{font-size:9px;white-space:normal;line-height:1.15}}

.banner{display:flex;align-items:center;justify-content:space-between;gap:10px;border-radius:14px;padding:13px 15px;margin-bottom:8px;
 background:linear-gradient(135deg,rgba(247,185,85,.12),rgba(247,185,85,.05));border:1px solid rgba(247,185,85,.3)}
.banner .bl{color:#e7c894;font-size:12.5px;font-weight:600}
.banner .bv{font-size:20px;font-weight:800;color:var(--warn);font-variant-numeric:tabular-nums;white-space:nowrap}

.danger{border-radius:16px;padding:14px 15px;margin:14px 0;background:linear-gradient(135deg,rgba(242,88,95,.13),rgba(242,88,95,.04));
 border:1px solid rgba(242,88,95,.32);border-left:3px solid var(--bad)}
.dhead{color:#ff7b81;font-weight:800;font-size:13.5px;letter-spacing:.02em;margin-bottom:7px}
.dsub{color:#d7b3b5;font-size:12.5px;line-height:1.5;margin-bottom:10px}
.dsub b{color:#fff}.dsub .rd{color:var(--bad)}
.ok{color:var(--good);font-size:13px;font-weight:600}

.sec{font-size:12px;font-weight:700;letter-spacing:.05em;color:#b9c0da;text-transform:uppercase;margin:20px 4px 10px}

.provs{display:flex;flex-direction:column;gap:8px}
.prow{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:14px;padding:12px 14px;
 display:grid;grid-template-columns:1fr auto;gap:8px 10px;align-items:center}
.prow.good{border-left-color:var(--good)}.prow.warn{border-left-color:var(--warn)}.prow.bad{border-left-color:var(--bad)}
.pl{display:flex;align-items:center;gap:9px;font-size:15px}
.prow .bar{grid-column:1/-1}
.pmeta{grid-column:1/-1;color:var(--mut);font-size:10.5px;letter-spacing:-.1px;font-variant-numeric:tabular-nums;line-height:1.5}

.dot{width:9px;height:9px;border-radius:50%;flex:none;background:#4b5168}
.dot.good{background:var(--good)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}

.pill{display:inline-flex;align-items:center;justify-content:center;min-width:48px;padding:3px 9px;border-radius:99px;
 font-weight:800;font-size:12.5px;color:var(--ink);font-variant-numeric:tabular-nums;line-height:1.3}
.pill.sm{min-width:42px;font-size:11.5px;padding:2px 7px}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.bad{background:var(--bad)}
.pill.na{background:#3a4160;color:var(--mut)}

.sbar{position:sticky;top:calc(44px + env(safe-area-inset-top));z-index:10;padding:6px 0 10px;background:linear-gradient(180deg,#0a0d18 80%,rgba(10,13,24,0))}
.search{width:100%;padding:12px 14px;border-radius:13px;border:1px solid var(--line);background:var(--card);color:var(--txt);font-size:15px;outline:none}
.search:focus{border-color:#3a4470}
.empty{color:var(--mut);text-align:center;padding:20px;font-size:13px}

.bc{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:14px;margin:8px 0;overflow:hidden}
.bc.good{border-left-color:var(--good)}.bc.warn{border-left-color:var(--warn)}.bc.bad{border-left-color:var(--bad)}
.bc summary{padding:12px 14px;cursor:pointer;list-style:none;display:flex;flex-direction:column;gap:9px}
.bc summary::-webkit-details-marker{display:none}
.bc[open]{background:var(--card2)}
.bch{display:flex;align-items:center;gap:9px}
.bcn{font-weight:700;font-size:15px;flex:1;min-width:0}
.bcm{display:flex;flex-wrap:wrap;gap:5px 12px;color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
.bcm .w{color:var(--warn)}.bcm .b{color:var(--bad)}.bcm .g{color:var(--good)}
.dtl{padding:2px 12px 12px}
.bc .dtl{padding:2px 4px 10px}
.bc.sub{margin:6px 0;border-radius:11px;border-left-width:2px;background:rgba(255,255,255,.02)}
.bc.sub summary{padding:9px 10px;gap:7px}
.bc.sub[open]{background:rgba(255,255,255,.035)}
.bc.sub .dtl{padding:0 2px 6px}
.note{color:var(--mut);font-size:12px;margin:2px 0 8px}

table.drv{width:100%;border-collapse:collapse;font-size:12px}
table.drv th,table.drv td{padding:7px 3px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
table.drv th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em;border-bottom:1px solid var(--line)}
table.drv th:first-child,table.drv td:first-child{text-align:left}
table.drv tbody tr:last-child td{border-bottom:none}
table.drv .ltc{color:var(--good);font-weight:700}
table.drv .cod{color:var(--bad);font-weight:700}
td.nv{font-weight:600;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
table.drv th.lft{text-align:left}
td.nv .sc{color:var(--mut);font-size:11px;font-weight:500;margin-top:1px;overflow:hidden;text-overflow:ellipsis}
td.rd{color:var(--bad);font-weight:700}
.rank{color:var(--mut);font-weight:700;width:22px}

.cmp{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:2px 0 4px}
.cc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 6px;text-align:center}
.cl{color:var(--mut);font-size:10.5px;margin-bottom:4px}
.cv{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}
.cv.bad{color:var(--bad)}
.up{color:var(--good);font-weight:800}.down{color:var(--bad);font-weight:800}.mut{color:var(--mut)}
.failrow{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px}
.fg{border-radius:12px;padding:11px 6px;text-align:center;border:1px solid var(--line)}
.fg.good{background:rgba(47,208,122,.12);border-color:rgba(47,208,122,.3)}
.fg.warn{background:rgba(247,185,85,.12);border-color:rgba(247,185,85,.3)}
.fg.bad{background:rgba(242,88,95,.12);border-color:rgba(242,88,95,.3)}
.fv{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.fg.good .fv{color:var(--good)}.fg.warn .fv{color:var(--warn)}.fg.bad .fv{color:var(--bad)}
.fl{font-size:10.5px;color:var(--mut);margin-top:3px;line-height:1.35}
.mh{font-weight:700;font-size:12.5px;margin:8px 2px 5px}.mh.up{color:var(--good)}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:24px 0 4px}
body{background:radial-gradient(130% 100% at 50% -10%,rgba(129,140,248,.10),transparent 65%),#171640 !important;background-attachment:fixed}
.top{background:linear-gradient(180deg,#171640 62%,rgba(23,22,64,0)) !important}
</style></head><body>"""


def gen_html(agg, backlog=None, backlog_time="hiện tại", ontrip=None, hist=None, bc_days=None, red_bc=None):
    backlog = backlog or {}
    hist = hist or []
    bc_days = bc_days or []
    red_bc = red_bc or {}
    g = agg["grand"]
    d = agg["date"]
    total_gtb = g["total"] - g["success"]
    total_cod = sum(x.get("gtb_cod", 0) for x in agg["drivers"])
    gen_at = datetime.now(VN).strftime("%H:%M %d/%m/%Y")

    # gom nhân viên theo bưu cục
    by_bc = {}
    for dr in agg["drivers"]:
        by_bc.setdefault(dr["bc"], []).append(dr)

    total_backlog = sum(v.get("deliver", 0) for v in backlog.values())
    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta name='theme-color' content='#0a0d18'>")
    P.append("<title>Báo cáo giao hàng TBB · %s</title>" % d.strftime("%d/%m"))
    P.append(_CSS)
    P.append("<div class='wrap'>")

    # ===== Header dính =====
    P.append("<header class='top'><div class='brand'>📦 BÁO CÁO CUỐI NGÀY</div>"
             "<div class='ts'>%s<br>%d BC · %d chuyến</div></header>"
             % (d.strftime("%d/%m/%Y"), agg["hub_count"], g["trips"]))

    # ===== Hero %GTC =====
    gtc = g["gtc"] if g["gtc"] is not None else 0
    P.append("<section class='hero %s'>" % _cls(g["gtc"]))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG TÂY BẮC BỘ</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>" % gtc)
    P.append(_bar(g["gtc"], _cls(g["gtc"])))
    P.append("<div class='hsub'>%s / %s đơn giao thành công · GTB %s đơn · LTC %s</div>"
             % (_n(g["success"]), _n(g["total"]), _n(total_gtb), _n(g.get("ltc", 0))))
    P.append("</section>")

    # ===== Dải chỉ số =====
    P.append("<section class='strip'>")
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>📦 Đơn giao</div></div>" % _n(g["total"]))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>✅ Giao TC</div></div>" % _n(g["success"]))
    vngh_gtc = g.get("vngh_gtc")
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>🛍️ TikTok gán</div></div>" % _n(g.get("vngh_total", 0)))
    P.append("<div class='st'><div class='sv %s'>%s</div><div class='sl'>🛍️ %%GTC TikTok</div></div>"
             % (_cls(vngh_gtc), ("%s%%" % vngh_gtc) if vngh_gtc is not None else "—"))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>🛒 LTC</div></div>" % _n(g.get("ltc", 0)))
    P.append("</section>")

    # ===== Banner chưa gán giao =====
    P.append("<div class='banner'><div class='bl'>⏳ Chưa gán giao<br><span style='opacity:.75;font-weight:500'>chờ xếp chuyến · %s</span></div>"
             "<div class='bv'>%s đơn</div></div>" % (_esc(backlog_time), _n(total_backlog)))

    # ===== ② SO SÁNH HÔM NAY vs HÔM QUA (vùng đầy đủ + từng AM) =====
    if hist:
        y = hist[0]
        ydm = "?"
        try:
            ydm = datetime.strptime(y["ngay"], "%Y-%m-%d").strftime("%d/%m")
        except Exception:
            pass
        gy = y.get("pct_gtc")
        wk = [h.get("pct_gtc") for h in hist[:7] if h.get("pct_gtc") is not None]
        avg7 = round(sum(wk) / len(wk), 1) if wk else None

        def _pdelta(cur, ref):   # %GTC (điểm) — 3 thẻ headline
            if cur is None or ref is None:
                return "—"
            dv = round(cur - ref, 1)
            return "<span class='%s'>%s%s</span>" % ("up" if dv >= 0 else "down",
                                                     "▲" if dv >= 0 else "▼", str(abs(dv)).replace(".", ","))
        P.append("<div class='sec'>📊 So sánh hôm nay vs hôm qua (%s)</div><section class='cmp'>" % ydm)
        P.append("<div class='cc'><div class='cl'>%%GTC hôm nay</div><div class='cv'>%s%%</div></div>"
                 % (g["gtc"] if g["gtc"] is not None else "—"))
        P.append("<div class='cc'><div class='cl'>vs hôm qua (%s%%)</div><div class='cv'>%s</div></div>"
                 % (gy if gy is not None else "—", _pdelta(g["gtc"], gy)))
        P.append("<div class='cc'><div class='cl'>vs TB 7 ngày (%s%%)</div><div class='cv'>%s</div></div>"
                 % (avg7 if avg7 is not None else "—", _pdelta(g["gtc"], avg7)))
        P.append("</section>")

        # --- Bảng toàn bộ chỉ số VÙNG · 3 NGÀY gần nhất (ô hôm nay tô màu xu hướng vs hôm qua) ---
        # Cột: Hôm nay · Hôm qua · TB 7 ngày. Ô hôm nay tô màu theo xu hướng vs TB 7 ngày.
        h7 = hist[:7]

        def _avg(key, rnd=0):
            vals = [hh.get(key) for hh in h7 if hh.get(key) is not None]
            if not vals:
                return None
            m = sum(vals) / len(vals)
            return round(m) if rnd == 0 else round(m, rnd)

        def _c3(cur, ref, kind, hg):
            """Ô hôm nay: giá trị + mũi tên màu theo xu hướng vs ref (TB 7 ngày)."""
            val = _fmt(cur, kind)
            if hg is None or cur is None or ref is None or abs(cur - ref) < (0.05 if kind != "n" else 0.5):
                return val
            arr = "▲" if cur > ref else "▼"
            return "<span class='%s'>%s %s</span>" % ("up" if (cur > ref) == hg else "down", val, arr)
        rmetrics = [
            ("📦 Đơn giao", g["total"], y.get("don_giao"), _avg("don_giao"), "n", None),
            ("✅ Giao TC", g["success"], y.get("gtc"), _avg("gtc"), "n", True),
            ("🎯 %GTC", g["gtc"], gy, _avg("pct_gtc", 1), "pct", True),
            ("❌ GTB", total_gtb, y.get("gtb"), _avg("gtb"), "n", False),
            ("⏳ Chưa gán", total_backlog, y.get("chua_gan"), _avg("chua_gan"), "n", False),
            ("🛒 LTC", g.get("ltc", 0), y.get("ltc"), _avg("ltc"), "n", True),
            ("💰 COD GTB", total_cod, y.get("cod_gtb"), _avg("cod_gtb"), "money", False),
            ("🛍️ TikTok gán", g.get("vngh_total", 0), y.get("vngh_don"), _avg("vngh_don"), "n", None),
            ("🛍️ %GTC TikTok", g.get("vngh_gtc"), y.get("vngh_gtc"), _avg("vngh_gtc", 1), "pct", True),
        ]
        P.append("<section class='card'><table class='drv'><thead><tr><th class='lft'>Chỉ số vùng</th>"
                 "<th>Hôm nay</th><th>Hôm qua</th><th>TB 7 ngày</th></tr></thead><tbody>")
        for lbl, cur, hq, tb7, kind, hg in rmetrics:
            P.append("<tr><td class='nv'>%s</td><td>%s</td><td class='mut'>%s</td><td class='mut'>%s</td></tr>"
                     % (lbl, _c3(cur, tb7, kind, hg), _fmt(hq, kind), _fmt(tb7, kind)))
        P.append("</tbody></table></section>")
        P.append("<div class='note' style='margin:2px 2px 0'>Ô Hôm nay tô màu theo xu hướng so <b>TB 7 ngày</b> (🟢 tốt hơn · 🔴 kém hơn mức thường)</div>")

        # --- Từng AM: Hôm nay · Hôm qua · TB 7 ngày (bấm mở xem chi tiết) ---
        if bc_days:
            # hôm nay theo AM (từ agg)
            amn_now = {}
            for b in agg["bcs"]:
                a = AM_OF.get(b["bc"])
                if not a:
                    continue
                x = amn_now.setdefault(a, {"don": 0, "gtc": 0, "gtb": 0, "cg": 0, "ltc": 0})
                x["don"] += b["total"]; x["gtc"] += b["success"]
                x["gtb"] += b["total"] - b["success"]; x["ltc"] += b.get("ltc", 0)
                x["cg"] += backlog.get(b["bc"], {}).get("deliver", 0)
            # bc_days → gộp theo (AM, ngày)
            d1date = hist[0]["ngay"]
            amday = {}
            for b in bc_days:
                a = AM_OF.get(b.get("buu_cuc"))
                if not a:
                    continue
                k = (a, b.get("ngay"))
                x = amday.setdefault(k, {"don": 0, "gtc": 0, "gtb": 0, "cg": 0, "ltc": 0})
                x["don"] += b.get("don_giao") or 0; x["gtc"] += b.get("gtc") or 0
                x["gtb"] += b.get("gtb") or 0; x["cg"] += b.get("chua_gan") or 0
                x["ltc"] += b.get("ltc") or 0
            amn_hq = {}         # hôm qua theo AM
            am_days = {}        # AM → list các ngày
            for (a, day), v in amday.items():
                am_days.setdefault(a, []).append(v)
                if day == d1date:
                    amn_hq[a] = v
            amn_tb7 = {}        # TB 7 ngày theo AM
            for a, lst in am_days.items():
                nd = len(lst)
                t = {m: round(sum(v[m] for v in lst) / nd) for m in ("don", "gtc", "gtb", "cg", "ltc")}
                pcs = [v["gtc"] / v["don"] * 100 for v in lst if v["don"]]
                t["pc"] = round(sum(pcs) / len(pcs), 1) if pcs else None
                amn_tb7[a] = t
            EMPTY = {"don": 0, "gtc": 0, "gtb": 0, "cg": 0, "ltc": 0, "pc": None}

            def _pc(x):
                return round(x["gtc"] / x["don"] * 100, 1) if x["don"] else None
            P.append("<div class='sec'>🧑‍💼 So sánh từng AM: Hôm nay · Hôm qua · TB 7 ngày · bấm mở</div>")
            for a in sorted(amn_now, key=lambda k: _pc(amn_now[k]) or 999):
                n = amn_now[a]; hq = amn_hq.get(a, EMPTY); tb = amn_tb7.get(a, EMPTY)
                pc_n, pc_hq, pc_tb = _pc(n), _pc(hq), tb.get("pc")
                cls = _cls(pc_n)
                P.append("<details class='bc %s'><summary>"
                         "<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                         "<span class='pill %s'>%s%%</span></div>"
                         "<div class='pmeta'>🎯 %%GTC: %s · hôm qua <span class='mut'>%s%%</span> · TB7 <span class='mut'>%s%%</span></div></summary>"
                         % (cls, cls, _esc(a), cls, pc_n if pc_n is not None else "—",
                            _c3(pc_n, pc_tb, "pct", True),
                            pc_hq if pc_hq is not None else "—", pc_tb if pc_tb is not None else "—"))
                P.append("<div class='dtl'><table class='drv'><thead><tr><th class='lft'>Chỉ số</th>"
                         "<th>Hôm nay</th><th>Hôm qua</th><th>TB 7 ngày</th></tr></thead><tbody>")
                amrows = [
                    ("📦 Đơn giao", n["don"], hq["don"], tb.get("don"), "n", None),
                    ("✅ Giao TC", n["gtc"], hq["gtc"], tb.get("gtc"), "n", True),
                    ("🎯 %GTC", pc_n, pc_hq, pc_tb, "pct", True),
                    ("❌ GTB", n["gtb"], hq["gtb"], tb.get("gtb"), "n", False),
                    ("⏳ Chưa gán", n["cg"], hq["cg"], tb.get("cg"), "n", False),
                    ("🛒 LTC", n["ltc"], hq["ltc"], tb.get("ltc"), "n", True),
                ]
                for lbl, cur, hqv, tbv, kind, hg in amrows:
                    P.append("<tr><td class='nv'>%s</td><td>%s</td><td class='mut'>%s</td><td class='mut'>%s</td></tr>"
                             % (lbl, _c3(cur, tbv, kind, hg), _fmt(hqv, kind), _fmt(tbv, kind)))
                P.append("</tbody></table></div></details>")

    # ===== ③ TỒN CHUYỂN SANG MAI =====
    carry = total_backlog + total_gtb
    P.append("<div class='sec'>📦 Tồn chuyển sang ngày mai</div><section class='cmp'>")
    P.append("<div class='cc'><div class='cl'>⏳ Chưa gán</div><div class='cv'>%s</div></div>" % _n(total_backlog))
    P.append("<div class='cc'><div class='cl'>❌ GTB (giao lại)</div><div class='cv'>%s</div></div>" % _n(total_gtb))
    P.append("<div class='cc'><div class='cl'>Σ tồn sang mai</div><div class='cv bad'>%s</div></div>" % _n(carry))
    P.append("</section>")

    # ===== 🚨 BƯU CỤC NGUY HIỂM CỦA VÙNG · Top 10 (cuối ngày + 1 tuần) =====
    # %GTC TB 7 ngày theo bưu cục (từ bc_days)
    bc7 = {}
    for b in bc_days:
        x = bc7.setdefault(b.get("buu_cuc"), [0, 0])
        x[0] += b.get("don_giao") or 0; x[1] += b.get("gtc") or 0
    drecs = []
    for b in agg["bcs"]:
        nm = b["bc"]; tot = b["total"]; suc = b["success"]
        cg = backlog.get(nm, {}).get("deliver", 0)
        rd = red_bc.get(nm, 0)
        if tot == 0 and cg == 0 and rd == 0:
            continue
        pct_t = round(suc / tot * 100, 1) if tot else None
        p7 = bc7.get(nm)
        pct7 = round(p7[1] / p7[0] * 100, 1) if (p7 and p7[0]) else None
        parts = [p for p in (pct_t, pct7) if p is not None]
        pctb = sum(parts) / len(parts) if parts else 100  # kém dữ liệu → coi như tốt (không phạt)
        drecs.append({"bc": nm, "cg": cg, "rd": rd, "pct_t": pct_t, "pct7": pct7,
                      "pctb": pctb, "tot": tot})
    if drecs and (red_bc or backlog):
        m = len(drecs)

        def _ranks(key, higher_bad):
            order = sorted(range(m), key=lambda i: drecs[i][key], reverse=higher_bad)
            pr = [0] * m
            for r, i in enumerate(order):
                pr[i] = r          # 0 = tệ nhất
            return pr
        rk_cg = _ranks("cg", True); rk_rd = _ranks("rd", True); rk_pc = _ranks("pctb", False)
        for i, dd in enumerate(drecs):
            dd["score"] = rk_cg[i] + rk_rd[i] + rk_pc[i]
            dd["rcg"], dd["rrd"], dd["rpc"] = rk_cg[i], rk_rd[i], rk_pc[i]
        drecs.sort(key=lambda x: x["score"])
        top = drecs[:10]
        q = m * 0.30   # ngưỡng "xấu" = nằm trong ~30% tệ nhất của tiêu chí

        def _van_de(dd):
            bad = []
            if dd["rcg"] < q: bad.append("cg")
            if dd["rrd"] < q: bad.append("rd")
            if dd["rpc"] < q: bad.append("pc")
            if len(bad) >= 3:
                return ("bad", "Xấu toàn diện")
            worst = min([("cg", dd["rcg"]), ("rd", dd["rrd"]), ("pc", dd["rpc"])], key=lambda t: t[1])[0]
            return {"cg": ("warn", "Ùn tắc chưa gán"), "rd": ("bad", "Tồn đỏ quá hạn"),
                    "pc": ("warn", "%GTC yếu")}[worst]
        # phân tích ngắn
        n_full = sum(1 for dd in top if _van_de(dd)[1] == "Xấu toàn diện")
        n_untac = sum(1 for dd in top if "Ùn tắc" in _van_de(dd)[1])
        n_gtc = sum(1 for dd in top if "%GTC" in _van_de(dd)[1])
        worst1 = top[0]
        P.append("<div class='sec' style='color:var(--bad)'>🚨 Bưu cục nguy hiểm của vùng · Top 10</div>")
        P.append("<section class='card'>")
        P.append("<div class='dsub'>Điểm tổng hợp 3 tiêu chí (trên %d bưu cục): <b>chưa gán giao cao · tồn đỏ &gt;120h cao · %%GTC thấp</b> "
                 "(kết hợp cuối ngày + TB 7 ngày). 🔥 Nặng nhất: <b>%s</b> — %s.</div>"
                 % (m, _esc(worst1["bc"]), _van_de(worst1)[1]))
        P.append("<table class='drv'><thead><tr><th class='rank'>#</th><th class='lft'>Bưu cục</th>"
                 "<th>%GTC nay/TB7</th><th>Chưa gán</th><th>Đỏ&gt;120h</th><th>Vấn đề</th></tr></thead><tbody>")
        for i, dd in enumerate(top, 1):
            vcls, vlbl = _van_de(dd)
            pnow = ("%s%%" % dd["pct_t"]) if dd["pct_t"] is not None else "—"
            p7 = ("%s%%" % dd["pct7"]) if dd["pct7"] is not None else "—"
            P.append("<tr><td class='rank'>%d</td><td class='nv'>%s</td>"
                     "<td>%s<span class='sc'>TB7 %s</span></td>"
                     "<td class='rd'>%s</td><td class='rd'>%s</td>"
                     "<td><span class='pill sm %s'>%s</span></td></tr>"
                     % (i, _esc(dd["bc"]), pnow, p7, _n(dd["cg"]), _n(dd["rd"]), vcls, vlbl))
        P.append("</tbody></table>")
        P.append("<div class='note' style='margin:6px 2px 0'>Trong 10 bưu cục: 🔴 <b>%d</b> xấu toàn diện · "
                 "⏳ %d ùn tắc chưa gán · 🎯 %d yếu %%GTC. "
                 "Ùn tắc → đẩy xếp chuyến + xử lý đơn quá hạn; yếu %%GTC → rà lý do giao hỏng, kèm cặp NV.</div>"
                 % (n_full, n_untac, n_gtc))
        P.append("</section>")

    # ===== ⚠️ Top nhân viên COD GTB / ĐƠN GTB cao nhất (KHÔNG lọc %GTC; chỉ cần có đơn GTB) =====
    danger = [dr for dr in agg["drivers"] if (dr["total"] - dr["success"]) > 0]
    # Xếp theo COD GTB / ĐƠN GTB cao nhất (tiền thu hộ kẹt trên mỗi đơn giao thất bại)
    danger.sort(key=lambda x: -(x.get("gtb_cod", 0) / (x["total"] - x["success"])))
    P.append("<section class='danger'>")
    P.append("<div class='dhead'>⚠️ NHÓM NHÂN VIÊN NGUY HIỂM CẦN CHÚ Ý</div>")
    if not danger:
        P.append("<div class='ok'>✅ Không có đơn GTB nào. Vùng ổn định.</div>")
    else:
        tot_cod = sum(dr.get("gtb_cod", 0) for dr in danger)
        top3 = " · ".join("%s (%s)" % (dr["driver_name"], dr["bc"]) for dr in danger[:3])
        P.append("<div class='dsub'>Xếp theo <b>COD GTB / đơn GTB</b> — tiền thu hộ kẹt trên mỗi đơn hỏng. Tổng COD GTB vùng <b>%.0f triệu</b>.<br>🔥 Cao nhất: <b>%s</b> — cần đốc thúc/thu hồi ngay.</div>"
                 % (tot_cod / 1e6, _esc(top3)))
        P.append("<table class='drv'><thead><tr><th class='rank'>#</th><th class='lft'>Nhân viên · Bưu cục</th><th>GTB</th><th>COD/GTB (tr)</th><th>%GTC</th></tr></thead><tbody>")
        for i, dr in enumerate(danger[:10], 1):
            gtb = dr["total"] - dr["success"]
            codper = (dr.get("gtb_cod", 0) / gtb / 1e6) if gtb > 0 else 0  # triệu / đơn GTB
            P.append("<tr><td class='rank'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td><td>%s</td><td class='rd'>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (i, _esc(dr["driver_name"]), _esc(dr["bc"]), _n(gtb),
                        ("%.2f" % codper).replace(".", ","), _cls(dr["gtc"]), dr["gtc"]))
        P.append("</tbody></table>")
    P.append("</section>")

    # ===== 🚗 Nhân viên còn chuyến CHƯA kết thúc — gấp gọn AM → Bưu cục → NV =====
    ot = [x for x in (ontrip or []) if x.get("don", 0) > 0]
    if ot:
        tot_don = sum(x["don"] for x in ot)
        tot_ch = sum(x["trips"] for x in ot)
        # gộp AM → bưu cục → NV
        am_g = {}
        for x in ot:
            amn = AM_OF.get(x["bc"]) or "(chưa phân AM)"
            g = am_g.setdefault(amn, {"don": 0, "ch": 0, "nguoi": 0, "bcs": {}})
            g["don"] += x["don"]; g["ch"] += x["trips"]; g["nguoi"] += 1
            g["bcs"].setdefault(x["bc"], []).append(x)
        P.append("<div class='sec' style='color:var(--warn)'>🚗 Nhân viên còn chuyến CHƯA kết thúc</div>")
        P.append("<div class='note' style='margin:0 2px 6px'>%d người · %d chuyến đang chạy · <b>%s đơn</b> "
                 "chưa tính vào %%GTC — bấm AM → bưu cục để xem nhân viên. <i>(ảnh chụp %s)</i></div>"
                 % (len(ot), tot_ch, _n(tot_don), gen_at))
        for amn, g in sorted(am_g.items(), key=lambda kv: -kv[1]["don"]):
            P.append("<details class='bc warn'><summary>"
                     "<div class='bch'><span class='dot warn'></span><span class='bcn'>%s</span>"
                     "<span class='pill bad'>%s</span></div>"
                     "<div class='pmeta'>🧑 %d người · 🏤 %d bưu cục · 🚚 %s chuyến · <span style='color:var(--bad)'>📦 %s đơn treo</span></div>"
                     "</summary><div class='dtl'>"
                     % (_esc(amn), _n(g["don"]), g["nguoi"], len(g["bcs"]), _n(g["ch"]), _n(g["don"])))
            for bc, drs in sorted(g["bcs"].items(), key=lambda kv: -sum(d["don"] for d in kv[1])):
                bc_don = sum(d["don"] for d in drs)
                bc_ch = sum(d["trips"] for d in drs)
                P.append("<details class='bc sub warn'><summary>"
                         "<div class='bch'><span class='dot warn'></span><span class='bcn'>%s</span>"
                         "<span class='pill bad'>%s</span></div>"
                         "<div class='bcm'><span>🧑 %d NV</span><span>🚚 %s chuyến</span>"
                         "<span class='b'>📦 %s đơn treo</span></div>"
                         "</summary><div class='dtl'>"
                         % (_esc(bc), _n(bc_don), len(drs), _n(bc_ch), _n(bc_don)))
                P.append("<table class='drv'><thead><tr><th class='lft'>Nhân viên</th>"
                         "<th>Chuyến</th><th>Đơn treo</th></tr></thead><tbody>")
                for x in sorted(drs, key=lambda d: -d["don"]):
                    P.append("<tr><td class='nv'>%s</td><td>%s</td><td class='rd'><b>%s</b></td></tr>"
                             % (_esc(x["driver_name"]), _n(x["trips"]), _n(x["don"])))
                P.append("</tbody></table></div></details>")
            P.append("</div></details>")

    # ===== Theo AM (xếp hạng · bấm mở xem bưu cục) =====
    cod_by_bc = {}
    for dr in agg["drivers"]:
        cod_by_bc[dr["bc"]] = cod_by_bc.get(dr["bc"], 0) + dr.get("gtb_cod", 0)
    am, am_bcs = {}, {}
    for b in agg["bcs"]:
        amn = AM_OF.get(b["bc"])
        if not amn:
            continue
        a = am.setdefault(amn, {"bc": 0, "total": 0, "success": 0, "ltc": 0, "cod": 0.0})
        a["bc"] += 1
        a["total"] += b["total"]
        a["success"] += b["success"]
        a["ltc"] += b.get("ltc", 0)
        a["cod"] += cod_by_bc.get(b["bc"], 0)
        am_bcs.setdefault(amn, []).append(b)
    P.append("<div class='sec'>🧑‍💼 Theo AM · %GTC thấp → cao · bấm xem bưu cục</div>")
    for amn, v in sorted(am.items(), key=lambda kv: (round(kv[1]["success"] / kv[1]["total"] * 100, 1) if kv[1]["total"] else 999)):
        pc = round(v["success"] / v["total"] * 100, 1) if v["total"] else None
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(amn), cls, pc if pc is not None else "—"))
        P.append(_bar(pc, cls))
        P.append("<div class='pmeta'>🏤 %d BC·📦 %s·✅ %s·<span style='color:var(--bad)'>❌ %s</span>·💰 %.0ftr</div>"
                 % (v["bc"], _n(v["total"]), _n(v["success"]), _n(v["total"] - v["success"]), v["cod"] / 1e6))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        for b in sorted(am_bcs.get(amn, []), key=lambda x: (x["gtc"] if x["gtc"] is not None else 999)):
            P.append(_bc_drv_details(b, by_bc.get(b["bc"], [])))
        P.append("</div></details>")

    # ===== Theo tỉnh (bấm mở xem bưu cục) =====
    prov_bcs = {}
    for b in agg["bcs"]:
        prov_bcs.setdefault(b["prov"], []).append(b)
    P.append("<div class='sec'>🗺 Theo tỉnh · %GTC thấp → cao · bấm xem bưu cục</div>")
    for p in sorted(agg["provinces"], key=lambda x: (x["gtc"] if x["gtc"] is not None else 999)):
        pc = p["gtc"]
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(PROV_NAME.get(p["prov"], p["prov"])), cls, pc if pc is not None else "—"))
        P.append(_bar(pc, cls))
        P.append("<div class='pmeta'>🏤 %d BC·📦 %s·✅ %s·<span style='color:var(--bad)'>❌ %s</span>·<span style='color:var(--good)'>🛒 %s</span></div>"
                 % (p["bc_count"], _n(p["total"]), _n(p["success"]), _n(p["total"] - p["success"]), _n(p.get("ltc", 0))))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        for b in sorted(prov_bcs.get(p["prov"], []), key=lambda x: (x["gtc"] if x["gtc"] is not None else 999)):
            P.append(_bc_drv_details(b, by_bc.get(b["bc"], [])))
        P.append("</div></details>")

    # ===== 61 bưu cục — card + drill nhân viên =====
    P.append("<div class='sec'>🏤 Tất cả bưu cục (%d) · %%GTC thấp → cao</div>" % len(agg["bcs"]))
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput=\"filt()\"></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    bcs = sorted(agg["bcs"], key=lambda x: (x["gtc"] if x["gtc"] is not None else 999, -x["total"]))
    for b in bcs:
        drivers = sorted(by_bc.get(b["bc"], []),
                         key=lambda x: (x["gtc"] if x["gtc"] is not None else 999))
        cls = _cls(b["gtc"])
        keys = (b["bc"] + " " + " ".join(dr["driver_name"] for dr in drivers)).lower()
        bl = backlog.get(b["bc"], {})
        bl_d = bl.get("deliver", 0)
        P.append("<details class='bc %s' data-k=\"%s\">" % (cls, _esc(keys)))
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>"
                 % (cls, _esc(b["bc"]), cls, b["gtc"] if b["gtc"] is not None else "—"))
        P.append(_bar(b["gtc"], cls))
        blchip = ("<span class='w'>⏳ %s</span>" % _n(bl_d)) if bl_d else ""
        P.append("<div class='bcm'><span>📦 %s</span><span>✅ %s</span><span class='b'>❌ %s</span><span class='g'>🛒 %s</span>%s</div>"
                 % (_n(b["total"]), _n(b["success"]), _n(b["total"] - b["success"]), _n(b.get("ltc", 0)), blchip))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        if bl_d or bl.get("pick") or bl.get("return"):
            P.append("<div class='note'>⏳ Chưa gán chuyến: <b>%s</b> giao · %s lấy · %s trả</div>"
                     % (_n(bl_d), _n(bl.get("pick", 0)), _n(bl.get("return", 0))))
        if drivers:
            P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Đơn</th><th>GTC</th><th>LTC</th><th>GTB</th><th>COD tr</th><th>%GTC</th></tr></thead><tbody>")
            for dr in drivers:
                dltc = dr.get("ltc", 0)
                ltc_cell = ("<b class='ltc'>%s</b>" % _n(dltc)) if dltc > 0 else "0"
                dcod = dr.get("gtb_cod", 0) or 0
                cod_cell = ("<b class='cod'>%s</b>" % ("%.1f" % (dcod / 1e6)).replace(".", ",")) if dcod >= 1e5 else "0"
                P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                         % (_esc(dr["driver_name"]), _n(dr["total"]), _n(dr["success"]),
                            ltc_cell, _n(dr["total"] - dr["success"]), cod_cell, _cls(dr["gtc"]),
                            dr["gtc"] if dr["gtc"] is not None else "—"))
            P.append("</tbody></table>")
        else:
            P.append("<div class='note'>Không có nhân viên.</div>")
        P.append("</div></details>")

    if agg["errors"]:
        P.append("<div class='note' style='text-align:center'>⚠️ %d chuyến không lấy được chi tiết (API lỗi tạm thời)</div>" % agg["errors"])
    P.append("<div class='foot'>%GTC = giao thành công / tổng đơn giao · gộp theo mã đơn (đơn giao lại tính 1 lần)<br>"
             "GTB = giao thất bại · COD GTB = tiền thu hộ kẹt ở đơn GTB · nguồn nhanh.ghn.vn</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc[data-k]').forEach(function(e){var s=(!q||e.dataset.k.indexOf(q)>=0);"
             "e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=n?'none':'block';}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


def _write_eod_fallback(err):
    """Fetch báo cáo lỗi → dựng eod.html từ snapshot Supabase mới nhất + banner. True nếu ghi được.
    KHÔNG đồng bộ Supabase và KHÔNG gửi GTalk (số đã cũ)."""
    import snapshot as SNAP
    snap = SNAP.load_snapshot()
    if not snap:
        return False
    agg, backlog, day = snap
    dm = day[8:10] + "/" + day[5:7]
    html_out = gen_html(agg, backlog, backlog_time="chốt %s" % dm)
    banner = SNAP.banner_html(day, "Chỉ số LTC không có trong bản dự phòng.")
    html_out = html_out.replace("<div class='wrap'>", "<div class='wrap'>" + banner, 1)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "eod.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    logger.warning("ĐÃ GHI eod.html DỰ PHÒNG từ Supabase ngày %s · lỗi: %s", day, str(err)[:120])
    return True


def _fetch_hist(d):
    """%GTC + đơn + COD các ngày TRƯỚC ngày d (Supabase bao_cao_vung, ~8 ngày). Lỗi → []."""
    import requests
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return []
    since = (d - timedelta(days=8)).isoformat()
    try:
        r = requests.get(url + "/rest/v1/bao_cao_vung",
                         params=[("select", "ngay,pct_gtc,don_giao,gtc,gtb,cod_gtb,chua_gan,ltc,vngh_don,vngh_gtc"),
                                 ("ngay", "gte." + since), ("ngay", "lt." + d.isoformat()),
                                 ("order", "ngay.desc")],
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=30)
        return r.json() if r.ok else []
    except Exception:
        return []


def _fetch_bc_days(d, n=7):
    """bao_cao_buu_cuc n ngày TRƯỚC ngày d (để so từng AM: hôm qua + TB 7 ngày).
    Trả list rows có 'ngay'. Lỗi → []."""
    import requests
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return []
    since = (d - timedelta(days=n)).isoformat()
    try:
        rows, off = [], 0
        while True:
            r = requests.get(url + "/rest/v1/bao_cao_buu_cuc",
                             params=[("select", "ngay,buu_cuc,don_giao,gtc,gtb,chua_gan,ltc"),
                                     ("ngay", "gte." + since), ("ngay", "lt." + d.isoformat()),
                                     ("order", "id.asc"), ("limit", "1000"), ("offset", str(off))],
                             headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=30)
            if not r.ok:
                break
            c = r.json(); rows += c
            if len(c) < 1000:
                break
            off += 1000
        return rows
    except Exception:
        return []


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
    except Exception as e:
        # Token hết hạn / API lỗi → dựng eod.html từ snapshot Supabase + banner (thay vì đọng).
        if _write_eod_fallback(e):
            return
        raise SystemExit("Fetch báo cáo lỗi và không có snapshot dự phòng: %s" % e)

    # Đơn chưa gán giao: lấy số THẬT (live) tại thời điểm chạy báo cáo cuối ngày (23h).
    backlog, backlog_time = {}, "cuối ngày"
    try:
        backlog = asyncio.run(fetch_backlog(token))
    except TokenExpiredError:
        backlog = {}
    total_backlog = sum(v.get("deliver", 0) for v in backlog.values())
    logger.info("Tồn chưa gán giao toàn vùng (%s): %d đơn", backlog_time, total_backlog)
    # Nhân viên còn chuyến CHƯA kết thúc (đơn chưa tính vào %GTC) — lỗi thì bỏ qua
    ontrip = []
    try:
        ontrip = asyncio.run(fetch_ontrip(token))
    except Exception as e:
        logger.warning("Không lấy được chuyến đang chạy (bỏ qua): %s", str(e)[:120])
    # Lịch sử các ngày TRƯỚC (so sánh hôm nay vs hôm qua / TB tuần) — từ Supabase
    hist = _fetch_hist(d)
    # Bưu cục 7 ngày trước (để so từng AM: hôm qua + TB 7 ngày)
    bc_days = _fetch_bc_days(d, 7)
    # Tồn ĐỎ quá hạn theo bưu cục (Giao>120 + Trả>120 + LC>48) — cho mục "Bưu cục nguy hiểm"
    red_bc = {}
    try:
        import report_backlog_web as BL
        bl_entries, _hc = asyncio.run(BL.fetch_all(token))
        red_bc = {e["name"]: BL.red_of(e)["total"] for e in bl_entries}
    except Exception as e:
        logger.warning("Không lấy được tồn đỏ theo bưu cục (bỏ qua): %s", str(e)[:120])
    # URL bí mật: ghi vào docs/<slug>/index.html (URL gốc sẽ 404)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "eod.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(agg, backlog, backlog_time, ontrip, hist, bc_days, red_bc))
    with open("dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump({"date": d.isoformat(), "grand": agg["grand"],
                   "provinces": agg["provinces"], "bcs": agg["bcs"],
                   "drivers": agg["drivers"], "hub_count": agg["hub_count"]},
                  f, ensure_ascii=False)
    logger.info("Xong · %d bưu cục · %d nhân viên · GTC %s%%",
                len(agg["bcs"]), len(agg["drivers"]), agg["grand"]["gtc"])

    # Đồng bộ vào database Supabase (chỉ chạy nếu đã cấu hình secret; lỗi DB KHÔNG
    # làm hỏng báo cáo/trang web).
    try:
        import db_sync
        db_sync.sync(d.isoformat(), agg, dedup_orders(payload), backlog, backlog_time)
    except Exception as e:
        logger.warning("Đồng bộ Supabase lỗi (bỏ qua): %s", str(e)[:200])

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
                   if x["total"] >= 20 and x["gtc"] is not None and x["gtc"] < 50]
            dng.sort(key=lambda x: -(x["total"] - x["success"]))
            if dng:
                d_gtb = sum(x["total"] - x["success"] for x in dng)
                L += ["", "⚠️ **NHÂN VIÊN NGUY HIỂM: %d người** (%%GTC<50%%, ≥20đ) · %s đơn GTB:" % (len(dng), _n(d_gtb))]
                for i, x in enumerate(dng[:5], 1):
                    L.append("%d. %s (%s) — GTB %s/%s đơn · %s%%"
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

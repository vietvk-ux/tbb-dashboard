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
                    _get_hubs, _post, CONCURRENCY)
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

.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:24px 0 4px}
</style></head><body>"""


def gen_html(agg, backlog=None, backlog_time="hiện tại", ontrip=None):
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
    P.append("<div class='st'><div class='sv bad'>%s</div><div class='sl'>❌ GTB</div></div>" % _n(total_gtb))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>🛒 LTC</div></div>" % _n(g.get("ltc", 0)))
    P.append("<div class='st'><div class='sv'>%.0f<span style=\"font-size:12px\">tr</span></div><div class='sl'>💰 COD GTB</div></div>" % (total_cod / 1e6))
    P.append("</section>")

    # ===== Banner chưa gán giao =====
    P.append("<div class='banner'><div class='bl'>⏳ Chưa gán giao<br><span style='opacity:.75;font-weight:500'>chờ xếp chuyến · %s</span></div>"
             "<div class='bv'>%s đơn</div></div>" % (_esc(backlog_time), _n(total_backlog)))

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
        for i, dr in enumerate(danger[:15], 1):
            gtb = dr["total"] - dr["success"]
            codper = (dr.get("gtb_cod", 0) / gtb / 1e6) if gtb > 0 else 0  # triệu / đơn GTB
            P.append("<tr><td class='rank'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td><td>%s</td><td class='rd'>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (i, _esc(dr["driver_name"]), _esc(dr["bc"]), _n(gtb),
                        ("%.2f" % codper).replace(".", ","), _cls(dr["gtc"]), dr["gtc"]))
        P.append("</tbody></table>")
    P.append("</section>")

    # ===== 🚗 Nhân viên còn chuyến CHƯA kết thúc (đơn chưa tính vào %GTC) =====
    ot = [x for x in (ontrip or []) if x.get("don", 0) > 0]
    if ot:
        tot_don = sum(x["don"] for x in ot)
        tot_ch = sum(x["trips"] for x in ot)
        P.append("<div class='sec' style='color:var(--warn)'>🚗 Nhân viên còn chuyến CHƯA kết thúc</div>")
        P.append("<section class='card'>")
        P.append("<div class='note' style='margin:0 0 8px'>%d người · %d chuyến đang chạy · <b>%s đơn</b> "
                 "chưa tính vào %%GTC — cần đốc thúc đóng chuyến. <i>(ảnh chụp %s)</i></div>"
                 % (len(ot), tot_ch, _n(tot_don), gen_at))
        P.append("<table class='drv'><thead><tr><th class='rank'>#</th><th class='lft'>Nhân viên · Bưu cục</th>"
                 "<th>Chuyến</th><th>Đơn treo</th></tr></thead><tbody>")
        for i, x in enumerate(ot[:20], 1):
            P.append("<tr><td class='rank'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                     "<td>%s</td><td class='rd'>%s</td></tr>"
                     % (i, _esc(x["driver_name"]), _esc(x["bc"]), _n(x["trips"]), _n(x["don"])))
        if len(ot) > 20:
            P.append("<tr><td></td><td class='nv' style='color:var(--mut)'>… và %d người khác</td>"
                     "<td></td><td></td></tr>" % (len(ot) - 20))
        P.append("</tbody></table></section>")

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
             "document.querySelectorAll('.bc').forEach(function(e){var s=(!q||e.dataset.k.indexOf(q)>=0);"
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
    # URL bí mật: ghi vào docs/<slug>/index.html (URL gốc sẽ 404)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "eod.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(agg, backlog, backlog_time, ontrip))
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

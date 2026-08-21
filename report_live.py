"""
Trang GẦN REALTIME giao hàng Vùng TBB (mobile) — tự tạo lại mỗi ~30'.
Số LIVE (bóc từng đơn qua get-trip-items): tồn chưa gán · chuyến đang chạy ·
đơn GTC (giao thành công) hôm nay tới hiện tại · %GTC (GTC/đã xử lý) theo bưu cục & nhân viên.

Env: NHANH_TOKEN. Xuất: docs/<slug>/index.html (+ live.html).
"""
from __future__ import annotations
import asyncio
import html
import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import aiohttp
from report import _get_hubs, _post, _fetch_all_items, CONCURRENCY, TokenExpiredError
from am_map import AM_OF

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


def _bar(pct, cls):
    """Thanh tiến độ %GTC trực quan."""
    w = pct if pct is not None else 0
    return "<div class='bar'><i class='%s' style='width:%s%%'></i></div>" % (cls, w)


def _bc_drv_details(r):
    """1 bưu cục dạng <details> LỒNG (bấm mở ra bảng nhân viên) — dùng trong mục AM/tỉnh."""
    pc = _pct(r["gtc"], r["total"])
    cls = _cls(pc)
    P = ["<details class='bc sub %s'><summary>" % cls]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
             "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
    P.append("<div class='bcm'><span>📥 %s</span><span>✅ %s</span>"
             "<span class='gtb'>❌ %s</span><span class='ltc'>LTC %s</span></div>"
             % (_n(r["total"]), _n(r["gtc"]), _n(r["att"] - r["gtc"]), _n(r.get("ltc", 0))))
    P.append("</summary><div class='dtl'>")
    drv = r.get("drivers", [])
    if drv:
        P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Ch</th><th>Gán</th><th>GTC</th>"
                 "<th>LTC</th><th>❌GTB</th><th>%GTC</th></tr></thead><tbody>")
        for d in sorted(drv, key=lambda x: (-x["gtc"], -x["total"])):
            pc2 = _pct(d["gtc"], d["total"])
            gtb2 = d["att"] - d["gtc"]
            gtb_cell = ("<b class='gtb'>%s</b>" % _n(gtb2)) if gtb2 > 0 else "0"
            ltc = d.get("ltc", 0)
            ltc_cell = ("<b class='ltc'>%s</b>" % _n(ltc)) if ltc > 0 else "0"
            P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (_esc(d["name"]), _n(d.get("chuyen", 0)), _n(d["total"]), _n(d["gtc"]),
                        ltc_cell, gtb_cell, _cls(pc2), pc2 if pc2 is not None else "—"))
        P.append("</tbody></table>")
    else:
        P.append("<div class='none'>Chưa có chuyến hôm nay.</div>")
    P.append("</div></details>")
    return "".join(P)


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

                def _drv0(did, dn):
                    return {"id": did, "name": dn, "chuyen": 0, "gtc": 0, "att": 0, "total": 0,
                            "ltc": 0, "vngh": 0, "vngh_gtc": 0}

                def _dk(did, dn):
                    # GỘP theo driver_id (phân biệt 2 người TRÙNG TÊN); thiếu id → theo tên
                    return did or ("~" + dn)

                async def _it(t, is_ontrip):
                    dn = t.get("driverName") or "—"
                    did = str(t.get("driverId") or "")
                    try:
                        async with sem:
                            items = await _fetch_all_items(session, token, hid, t["tripCode"])
                        # DELIVER: (mã đơn, tài xế, đã giao?, đã xử lý?, đang chạy?)
                        recs = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True,
                                 x.get("isUpdated") is True, is_ontrip)
                                for x in items if x.get("type") == "DELIVER"]
                        # PICK: (mã đơn, tài xế, đã lấy thành công?)
                        picks = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True)
                                 for x in items if x.get("type") == "PICK"]
                        return (did, dn, recs, picks)
                    except Exception:
                        return (did, dn, [], [])

                res = await asyncio.gather(
                    *([_it(t, True) for t in ontrip] + [_it(t, False) for t in fin]))
                drivers = {}
                # Mỗi chuyến (dù trùng đơn) vẫn tính là 1 chuyến của tài xế
                for did, dn, _recs, _picks in res:
                    drivers.setdefault(_dk(did, dn), _drv0(did, dn))["chuyen"] += 1
                # GỘP theo MÃ ĐƠN: 1 đơn gán nhiều chuyến chỉ tính 1 lần.
                # Ưu tiên bản ghi: đã giao > đã xử lý > chuyến đang chạy (đơn còn treo
                # tính cho chuyến hiện tại). GTC=đơn giao xong ở BẤT KỲ chuyến nào.
                best = {}
                for did, dn, recs, _picks in res:
                    for oc, rdid, rdn, succ, att, ot in recs:
                        if not oc:
                            continue
                        score = (4 if succ else 0) + (2 if att else 0) + (1 if ot else 0)
                        cur = best.get(oc)
                        if cur is None or score > cur[0]:
                            best[oc] = (score, rdid, rdn, succ, att)
                for oc, (score, rdid, rdn, succ, att) in best.items():
                    d = drivers.setdefault(_dk(rdid, rdn), _drv0(rdid, rdn))
                    d["total"] += 1
                    if succ:
                        d["gtc"] += 1
                    if att:
                        d["att"] += 1
                    if oc.startswith("VNGH"):        # đơn TikTok Shop → tiến độ theo nhân viên
                        d["vngh"] += 1
                        if succ:
                            d["vngh_gtc"] += 1
                # LTC (lấy thành công): gộp mã đơn PICK, thành công ở bất kỳ chuyến nào
                bestp = {}
                for did, dn, _recs, picks in res:
                    for oc, rdid, rdn, psucc in picks:
                        if not oc:
                            continue
                        cur = bestp.get(oc)
                        if cur is None or (psucc and not cur[2]):
                            bestp[oc] = (rdid, rdn, psucc)
                for oc, (rdid, rdn, psucc) in bestp.items():
                    d = drivers.setdefault(_dk(rdid, rdn), _drv0(rdid, rdn))
                    if psucc:
                        d["ltc"] += 1
                # PHÂN BIỆT TRÙNG TÊN trong cùng bưu cục: thêm đuôi #id
                namec = Counter(d["name"] for d in drivers.values())
                for d in drivers.values():
                    if namec[d["name"]] > 1 and d.get("id"):
                        d["name"] = "%s #%s" % (d["name"], d["id"][-6:])
                h_total = sum(d["total"] for d in drivers.values())
                h_gtc = sum(d["gtc"] for d in drivers.values())
                h_att = sum(d["att"] for d in drivers.values())
                h_ltc = sum(d["ltc"] for d in drivers.values())
                # Đơn TikTok Shop (mã VNGH) — gộp theo mã đơn giao, tiến độ theo bưu cục
                h_vngh = sum(1 for oc in best if oc.startswith("VNGH"))
                h_vngh_gtc = sum(1 for oc, v in best.items() if oc.startswith("VNGH") and v[3])
                return {"name": name, "prov": _prov(name), "backlog": backlog,
                        "ontrip": len(ontrip), "fin": len(fin), "gtc": h_gtc,
                        "att": h_att, "total": h_total, "ltc": h_ltc,
                        "vngh": h_vngh, "vngh_gtc": h_vngh_gtc,
                        "drivers": list(drivers.values())}
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Hub %s lỗi: %s", name, str(e)[:100])
                return {"name": name, "prov": _prov(name), "backlog": 0, "ontrip": 0,
                        "fin": 0, "gtc": 0, "att": 0, "total": 0, "drivers": []}

        return await asyncio.gather(*[one(h) for h in hubs])


def gen_html(rows):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0,
         "vngh": 0, "vngh_gtc": 0}
    prov = {}
    am = {}
    for r in rows:
        for k in R:
            R[k] += r.get(k, 0)
        p = prov.setdefault(r["prov"], {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0})
        for k in ("backlog", "ontrip", "fin", "gtc", "att", "total", "ltc"):
            p[k] += r.get(k, 0)
        amn = AM_OF.get(r["name"])
        if amn:
            a = am.setdefault(amn, {"bc": 0, "backlog": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0})
            a["bc"] += 1
            for k in ("backlog", "gtc", "att", "total", "ltc"):
                a[k] += r.get(k, 0)
    reg_pct = _pct(R["gtc"], R["total"])

    can_giao = R["total"] + R["backlog"]
    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta http-equiv='refresh' content='300'>")
    P.append("<meta name='theme-color' content='#0a0d18'>")
    P.append("<title>TBB trực tiếp · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    P.append("<div class='wrap'>")

    # ===== Header dính =====
    P.append("<header class='top'>"
             "<div class='brand'><span class='live'></span>TBB TRỰC TIẾP</div>"
             "<div class='ts'>%s · %s</div></header>"
             % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    # ===== Hero %GTC =====
    P.append("<section class='hero %s'>" % _cls(reg_pct))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG TÂY BẮC BỘ</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>"
             % (reg_pct if reg_pct is not None else "—"))
    P.append(_bar(reg_pct, _cls(reg_pct)))
    P.append("<div class='hsub'>%s / %s đơn giao thành công · LTC %s · cần giao %s</div>"
             % (_n(R["gtc"]), _n(R["total"]), _n(R["ltc"]), _n(can_giao)))
    P.append("</section>")

    # ===== Dải chỉ số =====
    P.append("<section class='strip'>")
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>📥 Đã gán</div></div>" % _n(R["total"]))
    P.append("<div class='st'><div class='sv warn'>%s</div><div class='sl'>⏳ Chưa gán</div></div>" % _n(R["backlog"]))
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>🏃 Đang chạy</div></div>" % _n(R["ontrip"]))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>✅ GTC nay</div></div>" % _n(R["gtc"]))
    P.append("<div class='st'><div class='sv bad'>%s</div><div class='sl'>❌ GTB thao tác</div></div>" % _n(R["att"] - R["gtc"]))
    # 3 chỉ số đơn TikTok (VNGH) toàn vùng
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>🛍️ TikTok gán</div></div>" % _n(R["vngh"]))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>🛍️ TikTok GTC</div></div>" % _n(R["vngh_gtc"]))
    vpct = _pct(R["vngh_gtc"], R["vngh"])
    P.append("<div class='st'><div class='sv %s'>%s%%</div><div class='sl'>🛍️ %%GTC TikTok</div></div>"
             % (_cls(vpct), vpct if vpct is not None else "—"))
    P.append("</section>")

    P.append("<a class='eod' href='eod.html'><span>📊 Báo cáo %GTC cuối ngày</span>"
             "<span class='arw'>chi tiết nhân viên →</span></a>")
    P.append("<a class='eod' href='backlog.html'><span>📦 Tồn đọng Lấy · Giao · Trả</span>"
             "<span class='arw'>theo khung giờ →</span></a>")
    P.append("<a class='eod' href='trend.html'><span>📈 Xu hướng theo ngày</span>"
             "<span class='arw'>biểu đồ %GTC →</span></a>")
    P.append("<a class='eod' href='nhanvien.html'><span>⚡ Năng suất Nhân viên</span>"
             "<span class='arw'>xếp hạng GTC/ngày →</span></a>")
    P.append("<a class='eod' href='khochuyentiep.html'><span>📦 Kho Chuyển Tiếp</span>"
             "<span class='arw'>tồn luân chuyển →</span></a>")
    P.append("<a class='eod' href='vngh.html'><span>🛍️ Đơn TikTok</span>"
             "<span class='arw'>tiến độ theo bưu cục →</span></a>")

    # ===== Theo AM (xếp hạng · bấm mở xem bưu cục) =====
    am_rows = {}
    for r in rows:
        amn = AM_OF.get(r["name"])
        if amn:
            am_rows.setdefault(amn, []).append(r)
    P.append("<div class='sec'>🧑‍💼 Theo AM · %GTC thấp → cao · bấm xem bưu cục</div>")
    for amn, v in sorted(am.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(amn), cls, pc if pc is not None else "—"))
        P.append(_bar(pc, cls))
        P.append("<div class='pmeta'>🏤 %s BC·📥 %s·✅ %s·<span class='gtb'>❌ %s</span>·<span class='ltc'>LTC %s</span></div>"
                 % (v["bc"], _n(v["total"]), _n(v["gtc"]), _n(v["att"] - v["gtc"]), _n(v["ltc"])))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        for r in sorted(am_rows.get(amn, []), key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999)):
            P.append(_bc_drv_details(r))
        P.append("</div></details>")

    # ===== Theo tỉnh (bấm mở xem bưu cục) =====
    prov_rows = {}
    for r in rows:
        prov_rows.setdefault(r["prov"], []).append(r)
    P.append("<div class='sec'>🗺 Theo tỉnh · %GTC thấp → cao · bấm xem bưu cục</div>")
    for pv, v in sorted(prov.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(PROV_NAME.get(pv, pv)), cls, pc if pc is not None else "—"))
        P.append(_bar(pc, cls))
        P.append("<div class='pmeta'>🏃 %s·📥 %s·⏳ %s·✅ %s·<span class='gtb'>❌ %s</span>·<span class='ltc'>LTC %s</span></div>"
                 % (_n(v["ontrip"] + v["fin"]), _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]),
                    _n(v["att"] - v["gtc"]), _n(v["ltc"])))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        for r in sorted(prov_rows.get(pv, []), key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999)):
            P.append(_bc_drv_details(r))
        P.append("</div></details>")

    # ===== Bưu cục =====
    P.append("<div class='sec'>🏤 Bưu cục · Tồn chưa gán giao cao → thấp</div>")
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    bcs = [r for r in rows if r["backlog"] or r["ontrip"] or r["total"]]
    for r in sorted(bcs, key=lambda x: (-x["backlog"], _pct(x["gtc"], x["total"]) if x["total"] else 999)):
        pc = _pct(r["gtc"], r["total"])
        cls = _cls(pc)
        keys = (r["name"] + " " + " ".join(d["name"] for d in r["drivers"])).lower()
        P.append("<details class='bc %s' data-k=\"%s\">" % (cls, _esc(keys)))
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>"
                 % (cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
        P.append(_bar(pc, cls))
        P.append("<div class='bcm'><span>🏃 %s</span><span>🏁 %s</span><span>📥 %s</span>"
                 "<span class='w'>⏳ %s</span><span>✅ %s</span><span class='gtb'>❌ %s</span>"
                 "<span class='ltc'>LTC %s</span></div>"
                 % (_n(r["ontrip"]), _n(r["fin"]), _n(r["total"]), _n(r["backlog"]), _n(r["gtc"]),
                    _n(r["att"] - r["gtc"]), _n(r.get("ltc", 0))))
        P.append("</summary>")
        drv = r["drivers"]  # TẤT CẢ tài xế có chuyến hôm nay (kể cả chưa có đơn giao)
        P.append("<div class='dtl'>")
        if drv:
            P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Ch</th><th>Gán</th><th>GTC</th><th>LTC</th><th>❌GTB</th><th>%GTC</th></tr></thead><tbody>")
            for d in sorted(drv, key=lambda x: (-x["gtc"], -x["total"])):
                pc2 = _pct(d["gtc"], d["total"])
                gtb2 = d["att"] - d["gtc"]  # GTB đã thao tác (đã xử lý nhưng không thành công)
                gtb_cell = ("<b class='gtb'>%s</b>" % _n(gtb2)) if gtb2 > 0 else "0"
                ltc = d.get("ltc", 0)
                ltc_cell = ("<b class='ltc'>%s</b>" % _n(ltc)) if ltc > 0 else "0"
                P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _n(d.get("chuyen", 0)), _n(d["total"]), _n(d["gtc"]),
                            ltc_cell, gtb_cell, _cls(pc2), pc2 if pc2 is not None else "—"))
            P.append("</tbody></table>")
        else:
            P.append("<div class='none'>Chưa có chuyến hôm nay.</div>")
        P.append("</div></details>")

    P.append("<div class='foot'>🏃 đang chạy · 🏁 kết thúc hôm nay · 📥 đã gán · ⏳ chưa gán · ✅ GTC · ❌ GTB đã thao tác<br>"
             "%GTC = GTC / tổng đơn gán · gộp theo mã đơn (đơn giao lại tính 1 lần)<br>"
             "số LIVE gồm cả chuyến đã kết thúc trong ngày · nguồn nhanh.ghn.vn</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc').forEach(function(e){var s=(!q||e.dataset.k.indexOf(q)>=0);"
             "e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=n?'none':'block';}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


_CSS = """<style>
:root{
 --bg:#0a0d18;--bg2:#0e1220;--card:#161b2d;--card2:#1b2136;--line:#272d45;
 --mut:#8b92ab;--txt:#eef0f7;--good:#2fd07a;--warn:#f7b955;--bad:#f2585f;--ink:#0a0d18
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background:linear-gradient(180deg,#0b0f1c 0%,#0a0d18 240px,#0a0d18 100%);color:var(--txt);
 -webkit-font-smoothing:antialiased;font-size:15px;line-height:1.35}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(30px + env(safe-area-inset-bottom))}

.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
 padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0));margin-bottom:4px}
.brand{font-weight:800;letter-spacing:.06em;font-size:15px;display:flex;align-items:center;gap:8px}
.ts{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
.live{width:9px;height:9px;border-radius:50%;background:var(--good);box-shadow:0 0 0 0 rgba(47,208,122,.6);animation:pulse 1.8s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,208,122,.55)}70%{box-shadow:0 0 0 7px rgba(47,208,122,0)}100%{box-shadow:0 0 0 0 rgba(47,208,122,0)}}

.hero{border-radius:20px;padding:20px 18px 18px;margin:4px 0 12px;position:relative;overflow:hidden;
 background:radial-gradient(120% 90% at 100% 0,rgba(255,255,255,.05),transparent),var(--card);
 border:1px solid var(--line)}
.hero.good{box-shadow:0 10px 30px -12px rgba(47,208,122,.35)}
.hero.warn{box-shadow:0 10px 30px -12px rgba(247,185,85,.32)}
.hero.bad{box-shadow:0 10px 30px -12px rgba(242,88,95,.32)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.hpct{font-size:64px;font-weight:850;line-height:1;margin:8px 0 12px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}.hero.na .hpct{color:var(--mut)}
.hpct span{font-size:26px;font-weight:700;opacity:.6;margin-left:2px}
.hsub{color:var(--mut);font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}

.bar{height:7px;background:rgba(255,255,255,.07);border-radius:99px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:99px;transition:width .5s}
.bar i.good{background:linear-gradient(90deg,#25b56b,#2fd07a)}
.bar i.warn{background:linear-gradient(90deg,#e39a2e,#f7b955)}
.bar i.bad{background:linear-gradient(90deg,#d8434b,#f2585f)}
.bar i.na{background:#4b5168}

.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-bottom:12px}
.st{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 6px;text-align:center}
.sv{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}
.sv.good{color:var(--good)}.sv.warn{color:var(--warn)}.sv.bad{color:var(--bad)}
.sl{color:var(--mut);font-size:10.5px;margin-top:3px;white-space:nowrap}

.eod{display:flex;align-items:center;justify-content:space-between;gap:8px;text-decoration:none;color:var(--txt);
 background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;
 padding:14px 16px;margin-bottom:8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.eod:active{transform:scale(.99)}

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
.search{width:100%;padding:12px 14px;border-radius:13px;border:1px solid var(--line);background:var(--card);
 color:var(--txt);font-size:15px;outline:none}
.search:focus{border-color:#3a4470}
.empty{color:var(--mut);text-align:center;padding:20px;font-size:13px}

.bc{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:14px;margin:8px 0;overflow:hidden}
.bc.good{border-left-color:var(--good)}.bc.warn{border-left-color:var(--warn)}.bc.bad{border-left-color:var(--bad)}
.bc summary{padding:12px 14px;cursor:pointer;list-style:none;display:flex;flex-direction:column;gap:9px}
.bc summary::-webkit-details-marker{display:none}
.bc[open]{background:var(--card2)}
.bch{display:flex;align-items:center;gap:9px}
.bcn{font-weight:700;font-size:15px;flex:1;min-width:0}
.bcm{display:flex;flex-wrap:wrap;gap:4px 9px;color:var(--mut);font-size:10.5px;letter-spacing:-.1px;font-variant-numeric:tabular-nums}
.bcm .w{color:var(--warn)}
.gtb{color:var(--bad);font-weight:700}
.ltc{color:var(--good);font-weight:700}
.dtl{padding:2px 12px 12px}
.bc .dtl{padding:2px 4px 10px}
.bc.sub{margin:6px 0;border-radius:11px;border-left-width:2px;background:rgba(255,255,255,.02)}
.bc.sub summary{padding:9px 10px;gap:7px}
.bc.sub[open]{background:rgba(255,255,255,.035)}
.bc.sub .dtl{padding:0 2px 6px}
.none{color:var(--mut);font-size:12.5px;padding:6px 2px 10px}

table.drv{width:100%;border-collapse:collapse;font-size:12px}
table.drv th,table.drv td{padding:7px 3px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
table.drv th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em;border-bottom:1px solid var(--line)}
table.drv th:first-child,table.drv td:first-child{text-align:left}
table.drv tbody tr:last-child td{border-bottom:none}
td.nv{font-weight:600;max-width:104px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:24px 0 4px}
</style></head><body>"""


def _write_fallback(err):
    """Fetch live lỗi → dựng index/live.html từ snapshot Supabase + banner cảnh báo. True nếu ghi được."""
    import snapshot as SNAP
    snap = SNAP.load_snapshot()
    if not snap:
        return False
    import report_dashboard as RD
    agg, backlog, day = snap
    dm = day[8:10] + "/" + day[5:7]
    html_out = RD.gen_html(agg, backlog, backlog_time="chốt %s" % dm)
    banner = SNAP.banner_html(day, "Chỉ số LTC không có trong bản dự phòng.")
    html_out = html_out.replace("<div class='wrap'>", "<div class='wrap'>" + banner, 1)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    for fn in ("index.html", "live.html"):
        with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
            f.write(html_out)
    logger.warning("ĐÃ GHI TRANG DỰ PHÒNG (index/live) từ Supabase ngày %s · lỗi live: %s",
                   day, str(err)[:120])
    return True


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    try:
        rows = asyncio.run(fetch_live(token))
    except Exception as e:
        # Token hết hạn / API lỗi → rơi về snapshot Supabase thay vì để trang trắng/đọng.
        if _write_fallback(e):
            return
        raise SystemExit("Fetch live lỗi và không có snapshot dự phòng: %s" % e)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    h = gen_html(rows)
    for fn in ("index.html", "live.html"):
        with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
            f.write(h)

    # Trang đơn TikTok (VNGH) — tiến độ theo bưu cục, dùng lại rows (không fetch lại)
    try:
        import report_vngh
        with open(os.path.join(outdir, "vngh.html"), "w", encoding="utf-8") as f:
            f.write(report_vngh.gen_html(rows))
    except Exception as e:
        logger.warning("Tạo vngh.html lỗi (bỏ qua): %s", str(e)[:150])

    # JSON dữ liệu cho BOT đọc trực tiếp (khớp 100% trang) — cạnh dashboard
    payload = {
        "generated": datetime.now(VN).strftime("%H:%M · %d/%m/%Y"),
        "region": {"backlog": sum(r["backlog"] for r in rows),
                   "ontrip": sum(r["ontrip"] for r in rows),
                   "gtc": sum(r["gtc"] for r in rows),
                   "total": sum(r["total"] for r in rows)},
        "bcs": [{"name": r["name"], "prov": r["prov"], "backlog": r["backlog"],
                 "ontrip": r["ontrip"], "fin": r["fin"], "gtc": r["gtc"], "total": r["total"],
                 "drivers": [{"name": d["name"], "chuyen": d["chuyen"],
                              "gtc": d["gtc"], "total": d["total"]} for d in r["drivers"]]}
                for r in rows],
    }
    with open(os.path.join(outdir, "live.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    tg = sum(r["gtc"] for r in rows)
    ta = sum(r["att"] for r in rows)
    logger.info("Live: %d bưu cục · chưa gán %d · đang chạy %d · GTC %d · %%GTC %s",
                len(rows), sum(r["backlog"] for r in rows), sum(r["ontrip"] for r in rows),
                tg, _pct(tg, ta))


if __name__ == "__main__":
    main()

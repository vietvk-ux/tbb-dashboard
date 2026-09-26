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
from report import _get_hubs, _post, _fetch_all_items, CONCURRENCY, TokenExpiredError, _vn_time, _ended_after_cutoff, fetch_chua_gan
from am_map import AM_OF

logger = logging.getLogger("live")
VN = timezone(timedelta(hours=7))
PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


def _codm(v):
    """COD GTB (đồng) → chuỗi triệu, vd 1.234.567 → '1,2'. <100k hiện '0'."""
    v = v or 0
    if v < 1e5:
        return "0"
    return ("%.1f" % (v / 1e6)).replace(".", ",")


def _prov(name):
    return name[name.find("(") + 1:name.find(")")] if "(" in name else "?"


def _pct(gtc, att):
    return round(gtc * 100 / att, 1) if att else None


def _cls(p):
    if p is None:
        return "na"
    return "bad" if p < 50 else ("warn" if p < 80 else "good")


def _tt_cell(vg, vn):
    """Ô %GTC TikTok trong bảng NV (pill màu) — '—' nếu không có đơn TikTok."""
    if not vn:
        return "<span style='color:var(--mut)'>—</span>"
    vp = _pct(vg, vn)
    return "<span class='pill sm %s'>%s%%</span>" % (_cls(vp), vp)


def _tt_chip(vg, vn):
    """Chip TikTok ở dòng meta bưu cục/AM: 🛍️ %GTC (GTC/gán). Rỗng nếu không có đơn."""
    if not vn:
        return ""
    vp = _pct(vg, vn)
    return "<span class='tt'>🛍️ %s%% (%s/%s)</span>" % (vp, _n(vg), _n(vn))


def _bar(pct, cls, target=None):
    """Thanh tiến độ %GTC trực quan (target=vạch mục tiêu)."""
    w = pct if pct is not None else 0
    tick = ("<span class='tgt' style='left:%d%%'></span>" % target) if target else ""
    return "<div class='bar'><i class='%s' style='width:%s%%'></i>%s</div>" % (cls, w, tick)


def _khuvuc_ref():
    """Đọc khuvuc_data (file trong repo, 0 creds) → tham chiếu lịch sử:
    {yp:%GTC hôm qua, avg7p:%GTC TB tuần, avg7t:đơn TB tuần, ndays}. None nếu thiếu."""
    import glob
    try:
        days = []
        for f in sorted(glob.glob("khuvuc_data/*.json"))[-7:]:
            d = json.load(open(f, encoding="utf-8"))
            if d.get("tot"):
                days.append(d)
        if not days:
            return None
        y = days[-1]
        st = sum(x["tot"] for x in days)
        sg = sum(x["gtc"] for x in days)
        return {"yp": _pct(y["gtc"], y["tot"]), "avg7p": _pct(sg, st),
                "avg7t": round(st / len(days)), "ndays": len(days)}
    except Exception:
        return None


def _bc_drv_details(r):
    """1 bưu cục dạng <details> LỒNG (bấm mở ra bảng nhân viên) — dùng trong mục AM/tỉnh."""
    pc = _pct(r["gtc"], r["total"])
    cls = _cls(pc)
    P = ["<details class='bc sub %s'><summary>" % cls]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
             "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
    P.append("<div class='bcm'><span>📥 %s</span><span class='w'>⏳ %s</span><span>✅ %s</span>"
             "<span class='gtb'>❌COD %str</span><span class='ltc'>LTC %s</span>%s</div>"
             % (_n(r["total"]), _n(r.get("backlog", 0)), _n(r["gtc"]), _codm(r.get("cod_gtb", 0)),
                _n(r.get("ltc", 0)), _tt_chip(r.get("vngh_gtc", 0), r.get("vngh", 0))))
    P.append("</summary><div class='dtl'>")
    drv = r.get("drivers", [])
    if drv:
        P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Gán</th><th>GTC</th>"
                 "<th>LTC</th><th>COD GTB</th><th>%GTC</th><th>🛍️GTC</th></tr></thead><tbody>")
        for d in sorted(drv, key=lambda x: (-x["gtc"], -x["total"])):
            pc2 = _pct(d["gtc"], d["total"])
            cgtb = d.get("cod_gtb", 0)
            gtb_cell = ("<b class='gtb'>%str</b>" % _codm(cgtb)) if cgtb >= 1e5 else "0"
            ltc = d.get("ltc", 0)
            ltc_cell = ("<b class='ltc'>%s</b>" % _n(ltc)) if ltc > 0 else "0"
            P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td><span class='pill sm %s'>%s%%</span></td><td>%s</td></tr>"
                     % (_esc(d["name"]), _n(d["total"]), _n(d["gtc"]),
                        ltc_cell, gtb_cell, _cls(pc2), pc2 if pc2 is not None else "—",
                        _tt_cell(d.get("vngh_gtc", 0), d.get("vngh", 0))))
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
                    bl = await fetch_chua_gan(session, hid, token)
                backlog = bl.get("deliver", 0)   # Giao "chưa có chuyến đi trong ngày" (chuẩn Tồn LGT)
                backlog_wards = bl.get("wards", [])   # [(tên xã, số đơn Giao chưa gán)] giảm dần

                async def _list(status):
                    async with sem:
                        r = await _post(session, "/lastmile/trip/get-trip-list-by-hub",
                                        {"hub_id": hid, "status": status, "is_ready": 0,
                                         "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1},
                                        hid, token)
                    return r.get("data") or []
                ontrip = await _list("ON_TRIP")
                # Chỉ tính chuyến kết thúc ≥10h VN của HÔM NAY → loại chuyến hôm qua đóng
                # sau nửa đêm (endDateIndex=hôm nay, <10h) khỏi số "trực tiếp hôm nay".
                fin = [t for t in await _list("FINISHED")
                       if t.get("endDateIndex") == ymd and _ended_after_cutoff(t.get("endTime"))]

                def _drv0(did, dn):
                    return {"id": did, "name": dn, "chuyen": 0, "gtc": 0, "att": 0, "total": 0,
                            "ltc": 0, "vngh": 0, "vngh_gtc": 0, "cod_gtb": 0, "kien": 0, "kien_gtc": 0,
                            # hiệu suất chuyến đi: giờ xuất phát/kết thúc, scan, tiến độ chuyến đang chạy
                            "st": None, "en": None, "scan_ok": 0, "scan_tot": 0,
                            "ot_done": 0, "ot_tot": 0}

                def _dk(did, dn):
                    # GỘP theo driver_id (phân biệt 2 người TRÙNG TÊN); thiếu id → theo tên
                    return did or ("~" + dn)

                async def _it(t, is_ontrip):
                    dn = t.get("driverName") or "—"
                    did = str(t.get("driverId") or "")
                    # meta chuyến: giờ, ngày bắt đầu, đang chạy?, scan (đếm ở dưới)
                    meta = {"start": t.get("startTime"), "end": t.get("endTime"),
                            "sdi": t.get("startDateIndex"), "ot": is_ontrip,
                            "scan_ok": 0, "scan_tot": 0}
                    try:
                        async with sem:
                            items = await _fetch_all_items(session, token, hid, t["tripCode"])
                        # DELIVER: (mã đơn, tài xế, đã giao?, đã xử lý?, đang chạy?, COD, số kiện)
                        recs = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True,
                                 x.get("isUpdated") is True, is_ontrip, float(x.get("collectAmount") or 0),
                                 len(x.get("items") or []) or 1)
                                for x in items if x.get("type") == "DELIVER"]
                        # PICK: (mã đơn, tài xế, đã lấy thành công?)
                        picks = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True)
                                 for x in items if x.get("type") == "PICK"]
                        for x in items:
                            if x.get("type") == "DELIVER":
                                meta["scan_tot"] += 1
                                if x.get("isScanned") is True:
                                    meta["scan_ok"] += 1
                        return (did, dn, recs, picks, meta)
                    except Exception:
                        return (did, dn, [], [], meta)

                res = await asyncio.gather(
                    *([_it(t, True) for t in ontrip] + [_it(t, False) for t in fin]))
                drivers = {}
                # Mỗi chuyến (dù trùng đơn) vẫn tính là 1 chuyến của tài xế
                for did, dn, _recs, _picks, _meta in res:
                    drivers.setdefault(_dk(did, dn), _drv0(did, dn))["chuyen"] += 1
                # GỘP theo MÃ ĐƠN: 1 đơn gán nhiều chuyến chỉ tính 1 lần.
                # Ưu tiên bản ghi: đã giao > đã xử lý > chuyến đang chạy (đơn còn treo
                # tính cho chuyến hiện tại). GTC=đơn giao xong ở BẤT KỲ chuyến nào.
                best = {}
                for did, dn, recs, _picks, _meta in res:
                    for oc, rdid, rdn, succ, att, ot, cod, kien in recs:
                        if not oc:
                            continue
                        score = (4 if succ else 0) + (2 if att else 0) + (1 if ot else 0)
                        cur = best.get(oc)
                        if cur is None or score > cur[0]:
                            best[oc] = (score, rdid, rdn, succ, att, cod, kien)
                for oc, (score, rdid, rdn, succ, att, cod, kien) in best.items():
                    d = drivers.setdefault(_dk(rdid, rdn), _drv0(rdid, rdn))
                    d["total"] += 1
                    d["kien"] += kien              # số kiện của đơn giao (khối lượng)
                    if succ:
                        d["gtc"] += 1
                        d["kien_gtc"] += kien
                    if att:
                        d["att"] += 1
                        if not succ:               # GTB = đã thao tác nhưng giao HỎNG → COD kẹt
                            d["cod_gtb"] += cod
                    if oc.startswith("VNGH"):        # đơn TikTok Shop → tiến độ theo nhân viên
                        d["vngh"] += 1
                        if succ:
                            d["vngh_gtc"] += 1
                # LTC (lấy thành công): gộp mã đơn PICK, thành công ở bất kỳ chuyến nào
                bestp = {}
                for did, dn, _recs, picks, _meta in res:
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
                # HIỆU SUẤT CHUYẾN ĐI: giờ xuất phát (chuyến bắt đầu HÔM NAY) / kết thúc,
                # số đơn đã scan, tiến độ chuyến ĐANG CHẠY (đã giao/tổng).
                for did, dn, recs, _picks, meta in res:
                    d = drivers.setdefault(_dk(did, dn), _drv0(did, dn))
                    d["scan_ok"] += meta["scan_ok"]; d["scan_tot"] += meta["scan_tot"]
                    if meta["sdi"] == ymd:
                        st = _vn_time(meta["start"])
                        if st and (d["st"] is None or st < d["st"]):
                            d["st"] = st
                    en = _vn_time(meta["end"])
                    if en and (d["en"] is None or en > d["en"]):
                        d["en"] = en
                    if meta["ot"]:
                        for oc, rdid, rdn, succ, att, ot, cod, kien in recs:
                            if not oc:
                                continue
                            d["ot_tot"] += 1
                            if succ:
                                d["ot_done"] += 1
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
                h_cod_gtb = sum(d["cod_gtb"] for d in drivers.values())
                h_kien = sum(d["kien"] for d in drivers.values())
                h_kien_gtc = sum(d["kien_gtc"] for d in drivers.values())
                return {"name": name, "prov": _prov(name), "backlog": backlog,
                        "backlog_wards": backlog_wards,
                        "ontrip": len(ontrip), "fin": len(fin), "gtc": h_gtc,
                        "att": h_att, "total": h_total, "ltc": h_ltc,
                        "vngh": h_vngh, "vngh_gtc": h_vngh_gtc, "cod_gtb": h_cod_gtb,
                        "kien": h_kien, "kien_gtc": h_kien_gtc,
                        "drivers": list(drivers.values())}
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Hub %s lỗi: %s", name, str(e)[:100])
                return {"name": name, "prov": _prov(name), "backlog": 0, "ontrip": 0,
                        "backlog_wards": [],
                        "fin": 0, "gtc": 0, "att": 0, "total": 0, "drivers": []}

        return await asyncio.gather(*[one(h) for h in hubs])


def gen_html(rows):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0,
         "vngh": 0, "vngh_gtc": 0, "cod_gtb": 0, "kien": 0, "kien_gtc": 0}
    prov = {}
    am = {}
    for r in rows:
        for k in R:
            R[k] += r.get(k, 0)
        p = prov.setdefault(r["prov"], {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "cod_gtb": 0, "kien": 0, "vngh": 0, "vngh_gtc": 0})
        for k in ("backlog", "ontrip", "fin", "gtc", "att", "total", "ltc", "cod_gtb", "kien", "vngh", "vngh_gtc"):
            p[k] += r.get(k, 0)
        amn = AM_OF.get(r["name"])
        if amn:
            a = am.setdefault(amn, {"bc": 0, "backlog": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "cod_gtb": 0, "kien": 0, "vngh": 0, "vngh_gtc": 0})
            a["bc"] += 1
            for k in ("backlog", "gtc", "att", "total", "ltc", "cod_gtb", "kien", "vngh", "vngh_gtc"):
                a[k] += r.get(k, 0)
    reg_pct = _pct(R["gtc"], R["total"])

    # Chỉ số hiệu suất chuyến ĐANG CHẠY (từ drivers · 0 tốn call thêm)
    ot_tot = ot_done = late_cnt = 0
    for r in rows:
        for d in r.get("drivers", []):
            ot_tot += d.get("ot_tot", 0)
            ot_done += d.get("ot_done", 0)
            st = d.get("st")
            if st is not None and (st.hour * 60 + st.minute) > 570:  # xuất phát sau 9h30
                late_cnt += 1
    on_road = max(ot_tot - ot_done, 0)          # đơn đang trên đường (chuyến đang chạy)
    run_pct = round(ot_done / ot_tot * 100) if ot_tot else None

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

    # ===== Hero %GTC (có mốc so sánh) =====
    ref = _khuvuc_ref()
    P.append("<section class='hero %s'>" % _cls(reg_pct))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG TÂY BẮC BỘ</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>"
             % (reg_pct if reg_pct is not None else "—"))
    P.append(_bar(reg_pct, _cls(reg_pct), target=80))
    P.append("<div class='hsub'>%s / %s đơn giao thành công · LTC %s · cần giao %s</div>"
             % (_n(R["gtc"]), _n(R["total"]), _n(R["ltc"]), _n(can_giao)))
    if ref:
        gap = ""
        if reg_pct is not None and reg_pct < ref["yp"]:
            gap = "<span class='rc'>Còn <b>%d</b> điểm tới mốc hôm qua</span>" % (ref["yp"] - reg_pct)
        P.append("<div class='href'>"
                 "<span class='rc'>🎯 Mục tiêu <b>80%%</b></span>"
                 "<span class='rc'>Hôm qua chốt <b>%d%%</b></span>"
                 "<span class='rc'>TB %d ngày <b>%d%%</b></span>%s</div>"
                 % (ref["yp"], ref["ndays"], ref["avg7p"], gap))
    P.append("</section>")

    # ===== Dòng CHẨN ĐOÁN VÙNG (tự sinh từ rows) =====
    amg = {}
    for r in rows:
        a = AM_OF.get(r["name"])
        if not a:
            continue
        x = amg.setdefault(a, [0, 0]); x[0] += r["total"]; x[1] += r["gtc"]
    low_am = sum(1 for t, g in amg.values() if t and _pct(g, t) < 60)
    top_bl = max(rows, key=lambda x: x.get("backlog", 0), default=None)
    diag = []
    if top_bl and top_bl.get("backlog", 0) > 0:
        diag.append("🔴 Chưa gán dồn <b>%s</b> (%s đơn)" % (_esc(top_bl["name"]), _n(top_bl["backlog"])))
    if low_am:
        diag.append("🟠 <b>%d</b>/%d AM &lt;60%%" % (low_am, len(amg)))
    if late_cnt:
        diag.append("🕘 <b>%s</b> NV ra hàng muộn" % _n(late_cnt))
    if not diag:
        diag.append("✅ Vùng vận hành ổn định")
    P.append("<div class='diag'>⚡ %s</div>" % " · ".join(diag))

    # ===== Dải chỉ số · Bento (Mẫu 3) · màu theo từng chỉ số =====
    vpct = _pct(R["vngh_gtc"], R["vngh"])
    _cgo = ("onclick=\"var d=document.getElementById('cgd');if(d){d.open=true;"
            "d.scrollIntoView({behavior:'smooth',block:'start'});}\"")
    kpis = [
        ("📥", _n(R["total"]),                                      "Đã gán",         "91,140,255",  ""),
        ("⏳", _n(R["backlog"]),                                    "Chưa gán ▾",     "242,88,95",   "cg"),
        ("🏃", _n(R["ontrip"]),                                     "Đang chạy",      "55,211,232",  ""),
        ("🚛", _n(on_road),                                         "Còn phải giao",  "255,138,61",  ""),
        ("📊", (("%d%%" % run_pct) if run_pct is not None else "—"),"Tiến độ chạy",   "47,208,122",  ""),
        ("✅", _n(R["gtc"]),                                        "GTC nay",        "47,208,122",  ""),
        ("🕘", _n(late_cnt),                                        "XP muộn &gt;9h30","247,185,85",  ""),
        ("🛍️", _n(R["vngh"]),                                      "TikTok gán",     "232,121,200", ""),
        ("🛍️", _n(R["vngh_gtc"]),                                  "TikTok GTC",     "47,208,122",  ""),
        ("🛍️", (("%d%%" % vpct) if vpct is not None else "—"),     "%GTC TikTok",    "232,121,200", ""),
        ("💰", ("%str" % _codm(R["cod_gtb"])),                      "COD GTB kẹt",    "247,185,85",  ""),
        ("🛒", _n(R["ltc"]),                                        "LTC",            "169,112,255", ""),
    ]
    P.append("<section class='strip'>")
    for ic, val, lab, rgb, extra in kpis:
        cls = "st cg" if extra == "cg" else "st"
        oc = (" " + _cgo) if extra == "cg" else ""
        P.append("<div class='%s' style='--h:%s'%s><div class='sv'>%s</div>"
                 "<div class='sl'>%s %s</div></div>" % (cls, rgb, oc, val, ic, lab))
    P.append("</section>")

    # ===== Drill Chưa gán theo AM → Bưu cục → tuyến (xã) — mở từ tile ⏳ Chưa gán =====
    cg_am = {}
    for r in rows:
        if r.get("backlog", 0) <= 0:
            continue
        amn = AM_OF.get(r["name"]) or "(chưa phân AM)"
        cg_am.setdefault(amn, []).append(r)
    if cg_am:
        P.append("<details id='cgd' class='cgbento'><summary>")
        P.append("<div class='mic'>⏳</div>"
                 "<div class='mtx'><div class='mn'>Tồn chưa gán</div>"
                 "<div class='ms'>AM → Bưu cục → tuyến · bấm mở chi tiết</div></div>"
                 "<div class='mbig'>%s</div><span class='cvar'>▾</span></summary>"
                 "<div class='dtl'>" % _n(R["backlog"]))
        for amn, brows in sorted(cg_am.items(), key=lambda kv: -sum(x["backlog"] for x in kv[1])):
            am_tot = sum(x["backlog"] for x in brows)
            P.append("<details class='bc warn'><summary>")
            P.append("<div class='bch'><span class='dot warn'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill warn'>%s</span></div>" % (_esc(amn), _n(am_tot)))
            P.append("<div class='bcm'><span>%d bưu cục còn tồn chưa gán</span></div>" % len(brows))
            P.append("</summary><div class='dtl'>")
            for r in sorted(brows, key=lambda x: -x["backlog"]):
                wards = r.get("backlog_wards", [])
                P.append("<details class='bc sub warn'><summary>")
                P.append("<div class='bch'><span class='dot warn'></span><span class='bcn'>%s</span>"
                         "<span class='pill warn'>%s</span></div>" % (_esc(r["name"]), _n(r["backlog"])))
                P.append("<div class='bcm'><span>🏘 %d tuyến xã/phường</span></div>" % len(wards))
                P.append("</summary><div class='dtl'>")
                if wards:
                    P.append("<table class='drv'><thead><tr><th>Tuyến xã/phường</th>"
                             "<th>Đơn chưa gán</th></tr></thead><tbody>")
                    for wn, wc in wards:
                        P.append("<tr><td>%s</td><td><b>%s</b></td></tr>" % (_esc(wn), _n(wc)))
                    P.append("</tbody></table>")
                else:
                    P.append("<div class='note'>Không lấy được chi tiết tuyến (thử lại lần sau).</div>")
                P.append("</div></details>")
            P.append("</div></details>")
        P.append("</div></details>")

    # ===== MENU BÁO CÁO · Bento grid (Mẫu 3) =====
    #        href, icon, tên, phụ đề, màu RGB, [7 cột mini-nhịp]
    _menu = [
        ("eod.html", "📊", "%GTC cuối ngày", "chi tiết nhân viên", "47,208,122", [45, 60, 52, 74, 66, 88, 80]),
        ("backlog.html", "📦", "Tồn Lấy·Giao·Trả", "theo khung giờ", "247,185,85", [70, 55, 80, 48, 66, 40, 58]),
        ("trend.html", "📈", "Xu hướng theo ngày", "biểu đồ %GTC", "55,211,232", [30, 42, 50, 62, 58, 76, 90]),
        ("nhanvien.html", "⚡", "Năng suất Nhân viên", "xếp hạng GTC/ngày", "169,112,255", [60, 72, 55, 84, 66, 90, 78]),
        ("khuvuc.html", "🗺", "Bản đồ khu vực", "nhiệt · %GTC theo xã", "34,195,166", [50, 66, 44, 72, 58, 80, 62]),
        ("chuyendi.html", "🚚", "Hiệu suất chuyến đi", "đơn/giờ · giờ ra hàng", "255,138,61", [40, 58, 70, 52, 78, 64, 86]),
        ("xephang.html", "🏆", "Xếp hạng tổng hợp", "AM · bưu cục · NV", "255,213,74", [55, 48, 70, 62, 84, 74, 92]),
        ("khochuyentiep.html", "📦", "Kho Chuyển Tiếp", "tồn luân chuyển", "255,110,169", [48, 62, 54, 70, 60, 78, 66]),
    ]
    P.append("<div class='menu'>")
    # Ô nổi bật (span 2): Báo cáo tổng quan + %GTC vùng lớn
    P.append("<a class='mtile feat' href='tongquan.html' style='--h:91,140,255'>"
             "<div class='mic'>📋</div>"
             "<div class='mtx'><div class='mn'>Báo cáo tổng quan</div>"
             "<div class='ms'>số chính cần theo dõi</div></div>"
             "<div class='mbig'>%s<span>%%</span></div></a>"
             % (reg_pct if reg_pct is not None else "—"))
    for href, ic, nm, sub, rgb, bars in _menu:
        spark = "".join("<i style='height:%d%%'></i>" % b for b in bars)
        P.append("<a class='mtile' href='%s' style='--h:%s'>"
                 "<div class='mic'>%s</div>"
                 "<div class='mtx'><div class='mn'>%s</div><div class='ms'>%s</div></div>"
                 "<div class='mspark'>%s</div></a>"
                 % (href, rgb, ic, nm, sub, spark))
    P.append("</div>")

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
        P.append("<div class='pmeta'>🏤 %s BC·📥 %s·<span class='w'>⏳ %s</span>·✅ %s·<span class='gtb'>❌COD %str</span>·<span class='ltc'>LTC %s</span>%s</div>"
                 % (v["bc"], _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]), _codm(v.get("cod_gtb", 0)), _n(v["ltc"]),
                    ("·" + _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0))) if v.get("vngh") else ""))
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
        P.append("<div class='pmeta'>🏃 %s·📥 %s·⏳ %s·✅ %s·<span class='gtb'>❌COD %str</span>·<span class='ltc'>LTC %s</span>%s</div>"
                 % (_n(v["ontrip"] + v["fin"]), _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]),
                    _codm(v.get("cod_gtb", 0)), _n(v["ltc"]),
                    ("·" + _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0))) if v.get("vngh") else ""))
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
                 "<span class='w'>⏳ %s</span><span>✅ %s</span><span class='gtb'>❌COD %str</span>"
                 "<span class='ltc'>LTC %s</span>%s</div>"
                 % (_n(r["ontrip"]), _n(r["fin"]), _n(r["total"]), _n(r["backlog"]), _n(r["gtc"]),
                    _codm(r.get("cod_gtb", 0)), _n(r.get("ltc", 0)),
                    _tt_chip(r.get("vngh_gtc", 0), r.get("vngh", 0))))
        P.append("</summary>")
        drv = r["drivers"]  # TẤT CẢ tài xế có chuyến hôm nay (kể cả chưa có đơn giao)
        P.append("<div class='dtl'>")
        if drv:
            P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Gán</th><th>GTC</th><th>LTC</th><th>COD GTB</th><th>%GTC</th><th>🛍️GTC</th></tr></thead><tbody>")
            for d in sorted(drv, key=lambda x: (-x["gtc"], -x["total"])):
                pc2 = _pct(d["gtc"], d["total"])
                cgtb = d.get("cod_gtb", 0)  # COD kẹt trên đơn GTB (đã thao tác nhưng giao hỏng)
                gtb_cell = ("<b class='gtb'>%str</b>" % _codm(cgtb)) if cgtb >= 1e5 else "0"
                ltc = d.get("ltc", 0)
                ltc_cell = ("<b class='ltc'>%s</b>" % _n(ltc)) if ltc > 0 else "0"
                P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                         "<td><span class='pill sm %s'>%s%%</span></td><td>%s</td></tr>"
                         % (_esc(d["name"]), _n(d["total"]), _n(d["gtc"]),
                            ltc_cell, gtb_cell, _cls(pc2), pc2 if pc2 is not None else "—",
                            _tt_cell(d.get("vngh_gtc", 0), d.get("vngh", 0))))
            P.append("</tbody></table>")
        else:
            P.append("<div class='none'>Chưa có chuyến hôm nay.</div>")
        P.append("</div></details>")

    P.append("<div class='foot'><b>📖 Giải thích chỉ số</b><br>"
             "📥 <b>Đã gán</b> = đơn đã xếp vào chuyến hôm nay · ⏳ <b>Chưa gán</b> = đơn tồn ở kho chưa xếp chuyến<br>"
             "🏃 <b>Đang chạy</b> = số NV còn chuyến chưa kết thúc · 🚛 <b>Còn phải giao</b> = đơn của chuyến đang chạy CHƯA giao xong (đang trên đường)<br>"
             "📊 <b>Tiến độ chạy</b> = đã giao / tổng đơn của chuyến đang chạy (%) · ✅ <b>GTC nay</b> = đơn giao thành công (chuyến đã kết thúc)<br>"
             "🕘 <b>XP muộn &gt;9h30</b> = số NV xuất phát sau 9h30 (kỷ luật ra hàng) · 🛍️ <b>TikTok</b> = đơn mã VNGH<br>"
             "💰 <b>COD GTB kẹt</b> = tiền thu hộ kẹt trên đơn giao hỏng (triệu đồng) · 🛒 <b>LTC</b> = lấy hàng thành công<br>"
             "🎯 <b>%GTC</b> = GTC / tổng đơn đã gán · gộp theo mã đơn (đơn giao lại tính 1 lần)<br>"
             "<span style='opacity:.7'>Số LIVE gồm cả chuyến đã kết thúc trong ngày · %GTC còn thấp giữa ngày là bình thường (chuyến chưa đóng) · nguồn nhanh.ghn.vn</span></div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc[data-k]').forEach(function(e){var k=e.dataset.k||'';"
             "var s=(!q||k.indexOf(q)>=0);e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=(q&&!n)?'block':'none';}</script>")
    P.append("<button id='rf' class='fab' onclick='rf()' aria-label='Làm mới'>"
             "<span class='rfi'>⟳</span></button>")
    P.append("<script>function rf(){var b=document.getElementById('rf');"
             "b.classList.add('spin');location.replace(location.pathname+'?t='+Date.now());}</script>")
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

.hero{--h:139,146,171;border-radius:20px;padding:20px 18px 18px;margin:4px 0 12px;position:relative;overflow:hidden;
 background:radial-gradient(130% 100% at 100% 0,rgba(var(--h),.16),transparent 62%),
  radial-gradient(120% 90% at 0% 0,rgba(var(--h),.08),transparent 55%),var(--card);
 border:1px solid rgba(var(--h),.32)}
.hero.good{--h:47,208,122;box-shadow:0 10px 34px -14px rgba(47,208,122,.4)}
.hero.warn{--h:247,185,85;box-shadow:0 10px 34px -14px rgba(247,185,85,.36)}
.hero.bad{--h:242,88,95;box-shadow:0 10px 34px -14px rgba(242,88,95,.36)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.hpct{font-size:64px;font-weight:850;line-height:1;margin:8px 0 12px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}.hero.na .hpct{color:var(--mut)}
.hpct span{font-size:26px;font-weight:700;opacity:.6;margin-left:2px}
.hsub{color:var(--mut);font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}
.hsub .ld{color:var(--txt);font-weight:700}
.href{display:flex;flex-wrap:wrap;gap:6px 7px;margin-top:12px}
.rc{font-size:11px;color:var(--mut);background:rgba(255,255,255,.05);border:1px solid var(--line);
 border-radius:99px;padding:3px 10px;font-variant-numeric:tabular-nums;white-space:nowrap}
.rc b{color:var(--txt);font-weight:800}
.diag{background:radial-gradient(120% 100% at 0% 0%,rgba(255,255,255,.05),var(--card) 72%);
 border:1px solid var(--line);border-radius:14px;padding:10px 13px;margin:0 0 12px;
 font-size:12.5px;line-height:1.55;color:var(--mut)}
.diag b{color:var(--txt);font-weight:800}

.bar{position:relative;height:7px;background:rgba(255,255,255,.07);border-radius:99px;overflow:hidden}
.tgt{position:absolute;top:0;bottom:0;width:2px;background:rgba(255,255,255,.65);border-radius:2px;z-index:2}
.bar i{display:block;height:100%;border-radius:99px;transition:width .5s}
.bar i.good{background:linear-gradient(90deg,#25b56b,#2fd07a)}
.bar i.warn{background:linear-gradient(90deg,#e39a2e,#f7b955)}
.bar i.bad{background:linear-gradient(90deg,#d8434b,#f2585f)}
.bar i.na{background:#4b5168}

.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-bottom:12px}
.st{position:relative;overflow:hidden;text-align:center;padding:11px 6px 10px;border-radius:16px;
 background:radial-gradient(125% 105% at 0% 0%,rgba(var(--h),.16),var(--card) 72%);
 border:1px solid rgba(var(--h),.24)}
.st.cg{cursor:pointer}.st.cg:active{transform:scale(.98)}
/* Tồn chưa gán · ô feature bento đỏ */
.cgbento{--h:242,88,95;position:relative;overflow:hidden;display:block;border-radius:18px;margin:2px 0 12px;
 background:linear-gradient(110deg,rgba(var(--h),.32),#150e13 78%);border:1px solid rgba(var(--h),.5)}
.cgbento>summary{display:flex;align-items:center;gap:13px;padding:14px 15px;cursor:pointer;list-style:none}
.cgbento>summary::-webkit-details-marker{display:none}
.cgbento:active{transform:scale(.995)}
.cgbento .mic{width:44px;height:44px;border-radius:13px;flex:none;display:grid;place-items:center;font-size:22px;
 background:rgba(var(--h),.26);border:1px solid rgba(var(--h),.5)}
.cgbento .mtx{flex:1;min-width:0}
.cgbento .mn{font-weight:800;font-size:16px;letter-spacing:-.01em}
.cgbento .ms{font-size:11px;color:var(--mut);margin-top:2px}
.cgbento .mbig{font-weight:800;font-size:26px;color:#fff;flex:none;font-variant-numeric:tabular-nums}
.cgbento .cvar{flex:none;color:rgb(var(--h));font-size:14px;transition:transform .2s}
.cgbento[open] .cvar{transform:rotate(180deg)}
.cgbento .dtl{padding:0 12px 12px}
.sv{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums;color:rgb(var(--h))}
.sv.good{color:var(--good)}.sv.warn{color:var(--warn)}.sv.bad{color:var(--bad)}
.sl{color:var(--mut);font-size:10.5px;margin-top:3px;white-space:nowrap}

.eod{display:flex;align-items:center;justify-content:space-between;gap:8px;text-decoration:none;color:var(--txt);
 background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;
 padding:14px 16px;margin-bottom:8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.eod:active{transform:scale(.99)}
.eod.tq{background:linear-gradient(135deg,#1e3a8a,#2563eb);border-color:#3b82f6;font-size:15px}
.eod.tq .arw{color:#c7dbff}
/* ===== MENU BÁO CÁO · Bento (Mẫu 3) ===== */
.menu{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:2px 0 10px}
.mtile{position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:space-between;
 min-height:100px;padding:13px;border-radius:18px;text-decoration:none;color:var(--txt);
 background:radial-gradient(125% 105% at 0% 0%,rgba(var(--h),.20),#0e1424 68%);
 border:1px solid rgba(var(--h),.26);transition:transform .16s,border-color .16s}
.mtile:active{transform:scale(.98)}
.mtile .mic{width:34px;height:34px;border-radius:11px;display:grid;place-items:center;font-size:17px;
 background:rgba(var(--h),.24);border:1px solid rgba(var(--h),.4)}
.mtile .mn{font-weight:800;font-size:13.5px;line-height:1.18;letter-spacing:-.01em}
.mtile .ms{font-size:10.5px;color:var(--mut);margin-top:3px;line-height:1.25}
.mtile .mspark{display:flex;align-items:flex-end;gap:3px;height:16px;margin-top:9px}
.mtile .mspark i{flex:1;background:rgba(var(--h),.62);border-radius:2px 2px 0 0;min-height:2px}
.mtile.feat{grid-column:1 / -1;flex-direction:row;align-items:center;gap:13px;min-height:auto;
 background:linear-gradient(110deg,rgba(var(--h),.30),#0e1424 78%);border-color:rgba(var(--h),.46)}
.mtile.feat .mic{width:44px;height:44px;font-size:22px;flex:none}
.mtile.feat .mtx{flex:1;min-width:0}
.mtile.feat .mn{font-size:16px}
.mtile.feat .mbig{font-family:inherit;font-weight:800;font-size:30px;line-height:1;color:#fff;flex:none;
 font-variant-numeric:tabular-nums}
.mtile.feat .mbig span{font-size:15px;color:rgba(var(--h),1);margin-left:2px}

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

.bc{--h:139,146,171;position:relative;overflow:hidden;border-radius:16px;margin:9px 0;
 background:radial-gradient(120% 90% at 0% 0%,rgba(var(--h),.12),var(--card) 70%);
 border:1px solid rgba(var(--h),.26)}
.bc.good{--h:47,208,122}.bc.warn{--h:247,185,85}.bc.bad{--h:242,88,95}
.bc summary{padding:12px 14px;cursor:pointer;list-style:none;display:flex;flex-direction:column;gap:9px}
.bc summary::-webkit-details-marker{display:none}
.bc[open]{background:radial-gradient(120% 90% at 0% 0%,rgba(var(--h),.17),var(--card2) 72%)}
.bch{display:flex;align-items:center;gap:9px}
.bcn{font-weight:700;font-size:15px;flex:1;min-width:0}
.bcm{display:flex;flex-wrap:wrap;gap:4px 9px;color:var(--mut);font-size:10.5px;letter-spacing:-.1px;font-variant-numeric:tabular-nums}
.w{color:var(--bad);font-weight:700}
.gtb{color:var(--warn);font-weight:700}
.ltc{color:var(--good);font-weight:700}
.dtl{padding:2px 12px 12px}
.bc .dtl{padding:2px 4px 10px}
.bc.sub{margin:7px 0;border-radius:13px;border-color:rgba(var(--h),.22);
 background:linear-gradient(180deg,rgba(var(--h),.06),rgba(255,255,255,.015))}
.bc.sub summary{padding:9px 10px;gap:7px}
.bc.sub[open]{background:linear-gradient(180deg,rgba(var(--h),.1),rgba(255,255,255,.02))}
.bc.sub .dtl{padding:0 2px 6px}
.none{color:var(--mut);font-size:12.5px;padding:6px 2px 10px}

table.drv{width:100%;border-collapse:collapse;font-size:12px}
table.drv th,table.drv td{padding:7px 3px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
table.drv th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em;border-bottom:1px solid var(--line)}
table.drv th:first-child,table.drv td:first-child{text-align:left}
table.drv tbody tr:last-child td{border-bottom:none}
td.nv{font-weight:600;max-width:104px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tt{color:#e879c8;font-weight:700}
@media(max-width:430px){
  table.drv{font-size:11px}
  table.drv th,table.drv td{padding:6px 2px}
  table.drv th{font-size:8px;letter-spacing:0}
  td.nv{max-width:78px}
  .pill.sm{min-width:34px;font-size:10px;padding:2px 5px}
  .bcm,.pmeta{font-size:10px}
}

.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:24px 0 4px}

.fab{position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));z-index:50;
width:52px;height:52px;border:none;border-radius:50%;cursor:pointer;
background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;
box-shadow:0 6px 18px rgba(0,0,0,.45),0 0 0 1px rgba(255,255,255,.06) inset;
display:flex;align-items:center;justify-content:center;-webkit-tap-highlight-color:transparent}
.fab:active{transform:scale(.92)}
.fab .rfi{font-size:26px;line-height:1;font-weight:700}
.fab.spin .rfi{animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
body{background:radial-gradient(130% 100% at 50% -10%,rgba(34,197,94,.10),transparent 65%),#0e2318 !important;background-attachment:fixed}
.top{background:linear-gradient(180deg,#0e2318 62%,rgba(14,35,24,0)) !important}
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

    # (Trang đơn TikTok vngh.html đã bỏ 24/08 — 3 chỉ số TikTok vẫn giữ ở dải chỉ số index)

    # Trang hiệu suất chuyến đi NV — dùng lại rows (giờ XP/đóng, đơn/giờ, scan, đang chạy)
    try:
        import report_chuyendi
        with open(os.path.join(outdir, "chuyendi.html"), "w", encoding="utf-8") as f:
            f.write(report_chuyendi.gen_html(rows))
    except Exception as e:
        logger.warning("Tạo chuyendi.html lỗi (bỏ qua): %s", str(e)[:150])

    # Trang TỔNG QUAN — gom số chính từ trực tiếp + 30 ngày (dùng lại rows, 0 call thêm)
    try:
        import report_tongquan
        with open(os.path.join(outdir, "tongquan.html"), "w", encoding="utf-8") as f:
            f.write(report_tongquan.build_html(rows))
    except Exception as e:
        logger.warning("Tạo tongquan.html lỗi (bỏ qua): %s", str(e)[:150])

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

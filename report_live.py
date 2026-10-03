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
    """COD GTB (đồng) → chuỗi KÈM đơn vị: ≥1 tỷ '1,17 tỷ' · ≥100k '928,4tr' · <100k '0'."""
    v = v or 0
    if v < 1e5:
        return "0"
    if v >= 1e9:
        return ("%.2f tỷ" % (v / 1e9)).replace(".", ",")
    return ("%.1ftr" % (v / 1e6)).replace(".", ",")


def _kgfmt(kg):
    """Khối lượng kg → '12,3 tấn' nếu ≥1000kg, ngược lại '845 kg'."""
    kg = kg or 0
    if kg >= 1000:
        return ("%.1f tấn" % (kg / 1000.0)).replace(".", ",")
    return "%s kg" % _n(round(kg))


def _prov(name):
    return name[name.find("(") + 1:name.find(")")] if "(" in name else "?"


def _pct(gtc, att):
    return round(gtc * 100 / att, 1) if att else None


def _cls(p):
    if p is None:
        return "na"
    return "bad" if p < 60 else ("warn" if p < 70 else "good")


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


def _fetch_region_trend(days=8):
    """Lịch sử VÙNG N ngày (chốt cuối ngày) từ Supabase bao_cao_vung: %GTC + chưa gán.
    Trả list cũ→mới [{ngay,pct,chuagan}] (bỏ ngày null pct); None nếu thiếu creds/lỗi."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        import requests
        h = {"apikey": key, "Authorization": "Bearer " + key}
        r = requests.get("%s/rest/v1/bao_cao_vung?select=ngay,pct_gtc,chua_gan,don_giao,vngh_don,vngh_gtc,weight_kg"
                         "&order=ngay.desc&limit=%d" % (url, days), headers=h, timeout=20)
        if not r.ok:
            return None
        out = []
        for x in reversed(r.json()):
            if x.get("pct_gtc") is None:
                continue
            vd, vp = x.get("vngh_don") or 0, x.get("vngh_gtc")   # vngh_gtc lưu dạng % → ra số đơn
            out.append({"ngay": x["ngay"], "pct": x.get("pct_gtc"), "chuagan": x.get("chua_gan"),
                        "don_giao": x.get("don_giao"), "weight_kg": x.get("weight_kg"),
                        "tiktok_gtc": round(vd * vp / 100) if (vd and vp is not None) else None})
        return out or None
    except Exception:
        return None


def _store_phieuthu(so_nv, so_tien):
    """Ghi phiếu thu treo HÔM NAY vào bao_cao_phieuthu (upsert theo ngay) → lần chạy cuối
    ngày = bản chốt. Bảng chưa tạo (chưa chạy migration) hoặc lỗi → bỏ qua êm."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key) or so_nv is None:
        return
    try:
        import requests
        today = datetime.now(VN).date().isoformat()
        requests.post("%s/rest/v1/bao_cao_phieuthu?on_conflict=ngay" % url,
                      json=[{"ngay": today, "so_nv": int(so_nv), "so_tien": int(so_tien or 0)}],
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=20)
    except Exception:
        pass


def _store_weight(kg):
    """Ghi khối lượng đơn giao đã gán HÔM NAY (kg) vào bao_cao_vung (partial upsert theo ngay)
    → lần chạy cuối ngày = chốt. Cột thiếu (chưa migration) → bỏ qua êm."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key) or kg is None:
        return
    try:
        import requests
        today = datetime.now(VN).date().isoformat()
        requests.post("%s/rest/v1/bao_cao_vung?on_conflict=ngay" % url,
                      json=[{"ngay": today, "weight_kg": round(kg, 1)}],
                      headers={"apikey": key, "Authorization": "Bearer " + key,
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=20)
    except Exception:
        pass


def _fetch_phieuthu_trend(days=14):
    """Số NV chưa nộp tiền N ngày (chốt cuối ngày) từ bao_cao_phieuthu. List số cũ→mới; None nếu lỗi/thiếu."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        import requests
        h = {"apikey": key, "Authorization": "Bearer " + key}
        r = requests.get("%s/rest/v1/bao_cao_phieuthu?select=ngay,so_nv&order=ngay.desc&limit=%d"
                         % (url, days), headers=h, timeout=20)
        if not r.ok:
            return None
        vals = [x.get("so_nv") for x in reversed(r.json()) if x.get("so_nv") is not None]
        return vals if len(vals) >= 2 else None
    except Exception:
        return None


def _fetch_giao120h_trend(days=8):
    """Giao>120h N ngày (chốt cuối ngày) = Σ g_gt120 order_type=DELIVER mỗi ngày từ
    bao_cao_ton_dong. Trả list số cũ→mới (các ngày gần nhất); None nếu thiếu creds/lỗi."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        import requests
        from collections import OrderedDict
        since = (datetime.now(VN).date() - timedelta(days=days + 3)).isoformat()
        h = {"apikey": key, "Authorization": "Bearer " + key}
        r = requests.get("%s/rest/v1/bao_cao_ton_dong?select=ngay,g_gt120"
                         "&order_type=eq.DELIVER&ngay=gte.%s&order=ngay.asc&limit=3000"
                         % (url, since), headers=h, timeout=20)
        if not r.ok:
            return None
        by = OrderedDict()
        for x in r.json():
            by[x["ngay"]] = by.get(x["ngay"], 0) + (x.get("g_gt120") or 0)
        vals = [by[d] for d in sorted(by)][-days:]
        return vals if len(vals) >= 2 else None
    except Exception:
        return None


def _spark(vals, color, w=240, h=46, target=None, pad=6):
    """Đường xu hướng nhỏ (SVG) từ list số; target = vạch ngang đứt (mục tiêu). '' nếu <2 điểm."""
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return ""
    lo, hi = min(xs), max(xs)
    if target is not None:
        hi = max(hi, target); lo = min(lo, target)
    rng = (hi - lo) or 1
    n = len(vals)

    def X(i):
        return pad + i * (w - 2 * pad) / (n - 1)

    def Y(v):
        return pad + (hi - v) / rng * (h - 2 * pad)

    pts = " ".join("%.1f,%.1f" % (X(i), Y(v)) for i, v in enumerate(vals) if v is not None)
    tline = ""
    if target is not None:
        ty = Y(target)
        tline = ("<line x1='0' y1='%.1f' x2='%d' y2='%.1f' stroke='rgba(167,139,250,.5)' "
                 "stroke-width='1' stroke-dasharray='4 4'/>" % (ty, w, ty))
    lx, ly = X(n - 1), Y(xs[-1])
    return ("<svg class='spk' viewBox='0 0 %d %d' width='100%%' height='%d' preserveAspectRatio='none'>%s"
            "<polyline points='%s' fill='none' stroke='%s' stroke-width='2.4' "
            "stroke-linecap='round' stroke-linejoin='round'/>"
            "<circle cx='%.1f' cy='%.1f' r='3' fill='%s'/></svg>"
            % (w, h, h, tline, pts, color, lx, ly, color))


def _late_cnt(r):
    """Số NV của 1 bưu cục xuất phát SAU 9h30 (st > 570 phút)."""
    c = 0
    for d in r.get("drivers", []):
        st = d.get("st")
        if st is not None and (st.hour * 60 + st.minute) > 570:
            c += 1
    return c


def _late_badge(n):
    """Chip '🕘 N' cảnh báo xuất phát muộn, rỗng nếu n=0."""
    return ("<span class='lbc' title='%d NV xuất phát sau 9h30'>🕘 %s</span>" % (n, _n(n))) if n else ""


def _low_cnt(r):
    """Số NV %GTC <50% (≥20 đơn) — nhóm yếu, khớp chẩn đoán nv_low."""
    c = 0
    for d in r.get("drivers", []):
        p = _pct(d.get("gtc", 0), d.get("total", 0))
        if d.get("total", 0) >= 20 and p is not None and p < 50:
            c += 1
    return c


def _low_badge(n):
    """Chip '📉 N' NV %GTC <50% (nhóm yếu), rỗng nếu n=0."""
    return ("<span class='lwc' title='%d NV %%GTC &lt;50%% (≥20 đơn)'>📉 %s</span>" % (n, _n(n))) if n else ""


def _drv_table(drv):
    """Bảng nhân viên của 1 bưu cục. Mỗi NV BẤM MỞ được → hàng con %GTC theo xã/phường
    (từ d['wards'] gộp lúc bóc chuyến, 0 call thêm). NV không có đơn giao → không mở."""
    if not drv:
        return "<div class='none'>Chưa có chuyến hôm nay.</div>"
    P = ["<table class='drv'><thead><tr><th>Nhân viên</th><th>Gán</th><th>GTC</th>"
         "<th>LTC</th><th>%GTC</th><th>🛍️GTC</th></tr></thead><tbody>"]
    for d in sorted(drv, key=lambda x: (-x["gtc"], -x["total"])):
        pc2 = _pct(d["gtc"], d["total"])
        ltc = d.get("ltc", 0)
        ltc_cell = ("<b class='ltc'>%s</b>" % _n(ltc)) if ltc > 0 else "0"
        wards = {k: v for k, v in d.get("wards", {}).items() if v[0] > 0}
        has = bool(wards) and d["total"] > 0
        cx = "<span class='cx'>▸</span>" if has else ""
        attr = " class='dnv' onclick='tgw(this)'" if has else ""
        st = d.get("st")
        late = st is not None and (st.hour * 60 + st.minute) > 570   # xuất phát sau 9h30
        lb = (" <span class='lb' title='Xuất phát %02d:%02d · muộn (sau 9h30)'>🕘</span>"
              % (st.hour, st.minute)) if late else ""
        P.append("<tr%s><td class='nv'>%s%s%s</td><td>%s</td><td>%s</td><td>%s</td>"
                 "<td><span class='pill sm %s'>%s%%</span></td><td>%s</td></tr>"
                 % (attr, cx, _esc(d["name"]), lb, _n(d["total"]), _n(d["gtc"]),
                    ltc_cell, _cls(pc2), pc2 if pc2 is not None else "—",
                    _tt_cell(d.get("vngh_gtc", 0), d.get("vngh", 0))))
        if has:
            ws = sorted(wards.items(), key=lambda kv: -kv[1][0])
            cells = []
            for wn, (tot, gtc) in ws:
                pcw = _pct(gtc, tot)
                cells.append("<div class='wrow'><span class='wnm'>%s</span>"
                             "<span class='wct'>%s/%s</span><span class='pill sm %s'>%s%%</span></div>"
                             % (_esc(wn), _n(gtc), _n(tot), _cls(pcw), pcw if pcw is not None else "—"))
            P.append("<tr class='wsub'><td colspan='6'>"
                     "<div class='whd'>🏘 %%GTC theo xã/phường · %d tuyến (đơn giao hôm nay)</div>"
                     "<div class='wgrid'>%s</div></td></tr>" % (len(ws), "".join(cells)))
    P.append("</tbody></table>")
    return "".join(P)


def _bc_drv_details(r):
    """1 bưu cục dạng <details> LỒNG (bấm mở ra bảng nhân viên) — dùng trong mục AM/tỉnh."""
    pc = _pct(r["gtc"], r["total"])
    cls = _cls(pc)
    P = ["<details class='bc sub %s'><summary>" % cls]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
             "<span class='pill %s'>%s%%</span></div>" % (cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
    P.append("<div class='bcm'><span>📥 %s</span><span class='w'>⏳ %s</span><span>✅ %s</span>"
             "<span class='ltc'>LTC %s</span>%s%s%s</div>"
             % (_n(r["total"]), _n(r.get("backlog", 0)), _n(r["gtc"]),
                _n(r.get("ltc", 0)), _tt_chip(r.get("vngh_gtc", 0), r.get("vngh", 0)),
                _late_badge(_late_cnt(r)), _low_badge(_low_cnt(r))))
    P.append("</summary><div class='dtl'>")
    P.append(_drv_table(r.get("drivers", [])))
    P.append("</div></details>")
    return "".join(P)


async def fetch_giao_120h(session, hub_ids, token):
    """Tổng đơn GIAO tồn quá 120h toàn vùng (đơn đỏ SLA, khớp trang Tồn đọng).
    1 CALL: get-general-info truyền HẾT hub_ids (view WARD, order_type=ALL) →
    trả bản gộp cả vùng; cộng bucket 120_192 + 192 của DELIVER. Lỗi → None."""
    try:
        d = await _post(session, "/core/oss/v1/report/get-general-info",
                        {"hub_ids": [str(h) for h in hub_ids], "view_mode": "WARD",
                         "order_type": "ALL"}, hub_ids[0] if hub_ids else "1", token)
        tot = 0
        for e in (d.get("data") or []):
            for gi in (e.get("general_infos") or []):
                if gi.get("order_type") == "DELIVER":
                    tot += sum(i.get("total_order") or 0
                               for i in (gi.get("order_inventories") or [])
                               if i.get("duration") in ("120_192", "192"))
        return tot
    except Exception:
        return None


async def _giao120h_one(session, hid, token):
    """Giao>120h của 1 bưu cục (get-general-info hub đơn) → int; lỗi → 0. Dùng để drill AM→BC."""
    try:
        d = await _post(session, "/core/oss/v1/report/get-general-info",
                        {"hub_ids": [str(hid)], "view_mode": "WARD", "order_type": "ALL"}, hid, token)
        tot = 0
        for e in (d.get("data") or []):
            for gi in (e.get("general_infos") or []):
                if gi.get("order_type") == "DELIVER":
                    tot += sum(i.get("total_order") or 0
                               for i in (gi.get("order_inventories") or [])
                               if i.get("duration") in ("120_192", "192"))
        return tot
    except Exception:
        return 0


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
                backlog_weight = bl.get("deliver_weight", 0)   # kg thực (gram) Giao chưa gán (gồm ưu tiên)
                dagan_weight = bl.get("dagan_weight", 0)       # kg thực (gram) Giao ĐÃ GÁN (ALL−NONE, gồm ưu tiên)
                ton_lay = bl.get("pick", 0)       # đơn LẤY chưa có chuyến
                ton_tra = bl.get("return", 0)     # đơn TRẢ tồn
                backlog_wards = bl.get("wards", [])   # [(tên xã, số đơn Giao chưa gán)] giảm dần
                async with sem:
                    g120 = await _giao120h_one(session, hid, token)   # Giao>120h bưu cục này (drill)

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
                            "ltc": 0, "ltb": 0, "vngh": 0, "vngh_gtc": 0, "cod_gtb": 0, "kien": 0, "kien_gtc": 0,
                            # hiệu suất chuyến đi: giờ xuất phát/kết thúc, scan, tiến độ chuyến đang chạy
                            "st": None, "en": None, "scan_ok": 0, "scan_tot": 0,
                            "ot_done": 0, "ot_tot": 0,
                            "wards": {}}   # %GTC theo xã/phường: {tên xã: [gán, gtc]}

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
                        # DELIVER: (mã đơn, tài xế, đã giao?, đã xử lý?, đang chạy?, COD, số kiện, xã)
                        def _wd(x):
                            info = x.get("deliverInfo") or x.get("receiverContact") or {}
                            return (info.get("wardName") or "").strip() or None
                        recs = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True,
                                 x.get("isUpdated") is True, is_ontrip, float(x.get("collectAmount") or 0),
                                 len(x.get("items") or []) or 1, _wd(x))
                                for x in items if x.get("type") == "DELIVER"]
                        # PICK: (mã đơn, tài xế, đã lấy thành công?, đã thao tác?) → LTC + LTB
                        picks = [(x.get("orderCode"), did, dn, x.get("isSucceeded") is True,
                                  x.get("isUpdated") is True)
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
                    for oc, rdid, rdn, succ, att, ot, cod, kien, ward in recs:
                        if not oc:
                            continue
                        score = (4 if succ else 0) + (2 if att else 0) + (1 if ot else 0)
                        cur = best.get(oc)
                        if cur is None or score > cur[0]:
                            best[oc] = (score, rdid, rdn, succ, att, cod, kien, ward)
                for oc, (score, rdid, rdn, succ, att, cod, kien, ward) in best.items():
                    d = drivers.setdefault(_dk(rdid, rdn), _drv0(rdid, rdn))
                    d["total"] += 1
                    d["kien"] += kien              # số kiện của đơn giao
                    w = d["wards"].setdefault(ward or "— (không rõ xã)", [0, 0])
                    w[0] += 1                       # đơn gán ở xã này
                    if succ:
                        d["gtc"] += 1
                        d["kien_gtc"] += kien
                        w[1] += 1                   # GTC ở xã này
                    if att:
                        d["att"] += 1
                        if not succ:               # GTB = đã thao tác nhưng giao HỎNG → COD kẹt
                            d["cod_gtb"] += cod
                    if oc.startswith("VNGH"):        # đơn TikTok Shop → tiến độ theo nhân viên
                        d["vngh"] += 1
                        if succ:
                            d["vngh_gtc"] += 1
                # LẤY (PICK): gộp mã đơn → LTC (lấy thành công) + LTB (đã thao tác nhưng KHÔNG lấy được).
                # Ưu tiên bản ghi: lấy thành công > đã thao tác > chưa thao tác (score psucc*2+patt).
                bestp = {}
                for did, dn, _recs, picks, _meta in res:
                    for oc, rdid, rdn, psucc, patt in picks:
                        if not oc:
                            continue
                        sc = (2 if psucc else 0) + (1 if patt else 0)
                        cur = bestp.get(oc)
                        if cur is None or sc > cur[0]:
                            bestp[oc] = (sc, rdid, rdn, psucc, patt)
                for oc, (sc, rdid, rdn, psucc, patt) in bestp.items():
                    d = drivers.setdefault(_dk(rdid, rdn), _drv0(rdid, rdn))
                    if psucc:
                        d["ltc"] += 1
                    elif patt:                        # đã thao tác nhưng không lấy được = LTB
                        d["ltb"] += 1
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
                        for oc, rdid, rdn, succ, att, ot, cod, kien, _ward in recs:
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
                h_ltb = sum(d["ltb"] for d in drivers.values())
                # Đơn TikTok Shop (mã VNGH) — gộp theo mã đơn giao, tiến độ theo bưu cục
                h_vngh = sum(1 for oc in best if oc.startswith("VNGH"))
                h_vngh_gtc = sum(1 for oc, v in best.items() if oc.startswith("VNGH") and v[3])
                h_cod_gtb = sum(d["cod_gtb"] for d in drivers.values())
                h_kien = sum(d["kien"] for d in drivers.values())
                h_kien_gtc = sum(d["kien_gtc"] for d in drivers.values())
                return {"name": name, "prov": _prov(name), "backlog": backlog,
                        "backlog_weight_g": backlog_weight,
                        "ton_lay": ton_lay, "ton_tra": ton_tra,
                        "backlog_wards": backlog_wards, "giao120h": g120,
                        "ontrip": len(ontrip), "fin": len(fin), "gtc": h_gtc,
                        "att": h_att, "total": h_total, "ltc": h_ltc, "ltb": h_ltb,
                        "weight_g": dagan_weight,   # kg thực đơn giao ĐÃ GÁN (get-detail-by-status)
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

        rows = await asyncio.gather(*[one(h) for h in hubs])
        # Tổng Giao>120h = Σ per-hub (đã lấy trong one()) → khớp drill AM→BC.
        giao_120h = sum(r.get("giao120h", 0) for r in rows)
        return rows, giao_120h


def gen_html(rows, giao_120h=None, nv_xuly=None, nvm=None, collectable=None, trend=None,
             g120_trend=None, cx_trend=None, pt_trend=None, nvdat_trend=None):
    now = datetime.now(VN)
    R = {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "ltb": 0,
         "vngh": 0, "vngh_gtc": 0, "cod_gtb": 0, "kien": 0, "kien_gtc": 0, "weight_g": 0}
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
    P.append("<meta name='theme-color' content='#080a16'>")
    P.append("<link rel='preconnect' href='https://fonts.googleapis.com'>")
    P.append("<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>")
    P.append("<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
             "family=Sora:wght@600;700;800&family=Manrope:wght@400;500;600;700;800&display=swap'>")
    P.append("<title>TBB trực tiếp · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    if nvm is not None:
        P.append("<style>%s</style>" % nvm["css"])
    P.append("<div class='wrap'>")

    # ===== Header dính =====
    P.append("<header class='top'>"
             "<div class='brand'><span class='live'></span>TBB TRỰC TIẾP</div>"
             "<div class='ts'>%s · %s</div></header>"
             % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    ref = _khuvuc_ref()

    # ===== ĐÈN TRẠNG THÁI (thông minh theo tiến độ ngày) =====
    done_ratio = (R["att"] / R["total"]) if R["total"] else 0
    yp = ref["yp"] if ref else None
    if R["total"] == 0:
        vk, vic, vst, vsub = "neu", "⏳", "CHƯA CÓ DỮ LIỆU", "Đang chờ chuyến đầu ngày"
    elif done_ratio < 0.45:
        vk, vic, vst = "neu", "⏳", "ĐANG LUỸ KẾ TRONG NGÀY"
        vsub = ("Còn sớm — %%GTC chưa đủ để đánh giá%s · xem việc cần làm bên dưới"
                % (" · hôm qua chốt %d%%" % yp if yp is not None else ""))
    elif reg_pct is not None and reg_pct >= 70:
        vk, vic, vst, vsub = "good", "🟢", "ĐẠT MỤC TIÊU", "Vượt/đạt mốc 70% — giữ nhịp"
    elif reg_pct is not None and reg_pct >= 60:
        vk, vic, vst = "warn", "🟡", "CẦN CHÚ Ý"
        vsub = "Dưới mục tiêu · còn %d điểm tới 70%%" % (70 - reg_pct)
    else:
        vk, vic, vst = "bad", "🔴", "DƯỚI MỤC TIÊU"
        vsub = "Cần đốc gấp · còn %d điểm tới 70%%" % (70 - (reg_pct or 0))
    P.append("<section class='verdict %s'><div class='vic'>%s</div>"
             "<div class='vtx'><div class='vst'>%s</div><div class='vsub'>%s</div></div>"
             "<div class='vpct'><b>%s%s</b><i>%%GTC · %s</i></div></section>"
             % (vk, vic, vst, vsub,
                (reg_pct if reg_pct is not None else "—"),
                ("%" if reg_pct is not None else ""), now.strftime("%H:%M")))

    # ===== Hero %GTC + đường xu hướng 8 ngày (chốt cuối ngày) =====
    P.append("<section class='hero %s'>" % _cls(reg_pct))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG · TỚI HIỆN TẠI</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>"
             % (reg_pct if reg_pct is not None else "—"))
    P.append(_bar(reg_pct, _cls(reg_pct), target=70))
    P.append("<div class='hsub'>%s / %s đơn giao thành công · LTC %s · cần giao %s</div>"
             % (_n(R["gtc"]), _n(R["total"]), _n(R["ltc"]), _n(can_giao)))
    if trend and len(trend) >= 2:
        pcts = [t["pct"] for t in trend]
        P.append("<div class='sparkwrap'><div class='spklbl'>Xu hướng %d ngày (chốt cuối ngày) · "
                 "nay đang luỹ kế</div>%s</div>"
                 % (len(pcts), _spark(pcts, "#fbbf24", w=280, h=50, target=70)))
    if ref:
        gap = ""
        if reg_pct is not None and reg_pct < 70:
            gap = "<span class='rc'>Còn <b>%d</b> điểm tới 70%%</span>" % (70 - reg_pct)
        P.append("<div class='href'>"
                 "<span class='rc'>🎯 Mục tiêu <b>70%%</b></span>"
                 "<span class='rc'>Hôm qua <b>%d%%</b></span>"
                 "<span class='rc'>TB %d ngày <b>%d%%</b></span>%s</div>"
                 % (ref["yp"], ref["ndays"], ref["avg7p"], gap))
    P.append("</section>")

    # ===== ⚡ CẦN LÀM NGAY — việc ưu tiên (Mẫu 1) =====
    nvx_n = nvm["n"] if nvm else (len(nv_xuly) if nv_xuly else 0)
    coll_nv = sum(len(us) for us in collectable.values()) if collectable else None
    coll_amt = sum(u["amount"] for us in collectable.values() for u in us) if collectable else 0
    cg_spark = g120_spark = cx_spark = ""
    if trend and len(trend) >= 2:
        cg_spark = _spark([t["chuagan"] for t in trend], "#34d399", w=60, h=22)
    if g120_trend and len(g120_trend) >= 2:
        g120_spark = _spark(g120_trend, "#fb7185", w=60, h=22)
    if cx_trend and len(cx_trend) >= 2:
        cx_spark = _spark(cx_trend, "#a78bfa", w=60, h=22)
    pt_spark = ""
    if pt_trend and len(pt_trend) >= 2:
        pt_spark = _spark(pt_trend, "#fbbf24", w=60, h=22)
    def _opendrill(_id):
        return ("onclick=\"var d=document.getElementById('%s');if(d){d.open=true;"
                "d.scrollIntoView({behavior:'smooth',block:'start'});}\"" % _id)
    _open_cg, _open_nvx, _open_g120 = _opendrill('cgd'), _opendrill('nvxuly'), _opendrill('g120d')
    P.append("<div class='sectitle'>⚡ Tổng Quan Vận Hành Vùng TBB</div><section class='prilist'>")
    P.append("<div class='todo bd' %s><div class='tic'>🔴</div><div class='tdt'>"
             "<div class='ttn'>Backlog giao 120h</div><div class='tts'>bấm mở AM → bưu cục</div></div>"
             "%s<div class='ttv'>%s</div></div>"
             % (_open_g120, g120_spark, _n(giao_120h) if giao_120h is not None else "—"))
    P.append("<div class='todo wn' %s><div class='tic'>⏳</div><div class='tdt'>"
             "<div class='ttn'>Tồn chưa gán giao</div><div class='tts'>bấm mở AM → bưu cục → xã</div></div>"
             "%s<div class='ttv'>%s</div></div>"
             % (_open_cg, cg_spark, _n(R["backlog"])))
    # Tồn Lấy + Tồn Trả (chưa gán) — ngay dưới Tồn chưa gán giao, bấm mở AM → bưu cục
    _ton_lay = sum(r.get("ton_lay", 0) for r in rows)
    _ton_tra = sum(r.get("ton_tra", 0) for r in rows)
    P.append("<div class='todo wn' %s><div class='tic'>🛒</div><div class='tdt'>"
             "<div class='ttn'>Tồn Lấy chưa gán</div><div class='tts'>đơn lấy chưa có chuyến · bấm mở AM → bưu cục</div></div>"
             "<div class='ttv'>%s</div></div>" % (_opendrill('tld'), _n(_ton_lay)))
    P.append("<div class='todo wn' %s><div class='tic'>↩️</div><div class='tdt'>"
             "<div class='ttn'>Tồn Trả</div><div class='tts'>đơn trả tồn · bấm mở AM → bưu cục</div></div>"
             "<div class='ttv'>%s</div></div>" % (_opendrill('ttd'), _n(_ton_tra)))
    P.append("<div class='todo vi' %s><div class='tic'>👤</div><div class='tdt'>"
             "<div class='ttn'>NV cần xử lý</div><div class='tts'>%%GTC kém dai dẳng · bấm mở</div></div>"
             "%s<div class='ttv' style='color:var(--bad)'>%s</div></div>" % (_open_nvx, cx_spark, _n(nvx_n)))
    if coll_nv is not None:
        P.append("<a class='todo wn lnk' href='chuyendi.html'><div class='tic'>💵</div><div class='tdt'>"
                 "<div class='ttn'>NV chưa nộp tiền</div><div class='tts'>%s đang treo · xem chi tiết →</div></div>"
                 "%s<div class='ttv'>%s</div></a>" % (_codm(coll_amt), pt_spark, _n(coll_nv)))
    # NV đạt GTC ≥50% — ngay dưới NV chưa nộp tiền (sức khỏe đội ngũ)
    if nvdat_trend and len(nvdat_trend) >= 2:
        nv_dat = sum(1 for r in rows for d in r.get("drivers", [])
                     if d.get("total", 0) >= 20 and _pct(d["gtc"], d["total"]) is not None
                     and _pct(d["gtc"], d["total"]) >= 50)
        P.append("<div class='todo'><div class='tic'>🎯</div><div class='tdt'>"
                 "<div class='ttn'>NV đạt GTC ≥50%%</div>"
                 "<div class='tts'>số NV %%GTC ≥50%% (≥20 đơn) · 14 ngày</div></div>"
                 "%s<div class='ttv' style='color:var(--good)'>%s</div></div>"
                 % (_spark(nvdat_trend, "#34d399", w=60, h=22), _n(nv_dat)))
    # Sản lượng giao/ngày + TikTok giao TC/ngày — cùng kiểu, phía dưới
    if trend and len(trend) >= 2:
        sl_don = [t.get("don_giao") for t in trend]
        sl_ttg = [t.get("tiktok_gtc") for t in trend]
        if any(v is not None for v in sl_don):
            P.append("<div class='todo'><div class='tic'>📦</div><div class='tdt'>"
                     "<div class='ttn'>Sản lượng giao / ngày</div>"
                     "<div class='tts'>tổng đơn giao vùng · 14 ngày</div></div>"
                     "%s<div class='ttv'>%s</div></div>"
                     % (_spark(sl_don, "#22d3ee", w=60, h=22), _n(R["total"])))
        # Khối lượng (kg thực) đơn giao đã gán — ngay dưới Sản lượng · bấm mở drill AM→BC
        if R["weight_g"] > 0:
            sl_kg = [t.get("weight_kg") for t in trend]
            kg_spark = _spark(sl_kg, "#38bdf8", w=60, h=22) if any(v is not None for v in sl_kg) else ""
            P.append("<div class='todo' %s><div class='tic'>⚖️</div><div class='tdt'>"
                     "<div class='ttn'>Khối lượng giao / ngày</div>"
                     "<div class='tts'>kg thực · đã gán + chưa gán · bấm mở AM → bưu cục</div></div>"
                     "%s<div class='ttv'>%s</div></div>"
                     % (_opendrill('kgd'), kg_spark, _kgfmt(R["weight_g"] / 1000.0)))
        if any(v is not None for v in sl_ttg):
            P.append("<div class='todo'><div class='tic'>🛍️</div><div class='tdt'>"
                     "<div class='ttn'>TikTok giao TC / ngày</div>"
                     "<div class='tts'>số đơn Tiktok giao thành công · 14 ngày</div></div>"
                     "%s<div class='ttv'>%s</div></div>"
                     % (_spark(sl_ttg, "#e879c8", w=60, h=22), _n(R["vngh_gtc"])))
    P.append("</section>")

    # ===== Dòng CHẨN ĐOÁN VÙNG (tự sinh từ rows) =====
    amg = {}
    for r in rows:
        a = AM_OF.get(r["name"])
        if not a:
            continue
        x = amg.setdefault(a, [0, 0]); x[0] += r["total"]; x[1] += r["gtc"]
    am_pcts = [(a, _pct(g, t)) for a, (t, g) in amg.items() if t]  # (tên AM, %GTC)
    worst_am = min(am_pcts, key=lambda x: x[1]) if am_pcts else None
    top_bl = max(rows, key=lambda x: x.get("backlog", 0), default=None)
    # Bưu cục / Nhân viên %GTC <50% (chỉ tính ≥20 đơn đã đóng chuyến, tránh nhiễu số lẻ)
    bc_low = sum(1 for r in rows if r["total"] >= 20
                 and _pct(r["gtc"], r["total"]) is not None and _pct(r["gtc"], r["total"]) < 50)
    nv_low = sum(1 for r in rows for d in r.get("drivers", [])
                 if d.get("total", 0) >= 20 and _pct(d["gtc"], d["total"]) is not None
                 and _pct(d["gtc"], d["total"]) < 50)
    # Tỉ lệ NV đạt mục tiêu (%GTC ≥50%) trên tổng NV đủ điều kiện (≥20 đơn) — sức khỏe đội ngũ
    nv_qual = sum(1 for r in rows for d in r.get("drivers", [])
                  if d.get("total", 0) >= 20 and _pct(d["gtc"], d["total"]) is not None)
    nv_rate = round((nv_qual - nv_low) * 100 / nv_qual) if nv_qual else None
    diag = []
    if top_bl and top_bl.get("backlog", 0) > 0:
        diag.append("🔴 Chưa gán giao cao nhất <b>%s</b> (%s đơn)" % (_esc(top_bl["name"]), _n(top_bl["backlog"])))
    if worst_am:
        diag.append("🟠 AM yếu nhất <b>%s</b> (%d%%)" % (_esc(worst_am[0]), round(worst_am[1])))
    if bc_low:
        diag.append("🏤 <b>%d</b> bưu cục %%GTC &lt;50%%" % bc_low)
    if nv_low:
        diag.append("👤 <b>%d</b> NV %%GTC &lt;50%%" % nv_low)
    if nv_rate is not None:
        diag.append("✅ <b>%d%%</b> NV đạt (≥50%%, %d NV)" % (nv_rate, nv_qual))
    if not diag:
        diag.append("✅ Vùng vận hành ổn định")

    # ---- Top 5 tệ nhất từng mục (bấm mở dòng chú ý để đọc ngay) ----
    top_bl5 = sorted([r for r in rows if r.get("backlog", 0) > 0],
                     key=lambda x: -x["backlog"])[:5]
    am_worst5 = sorted(am_pcts, key=lambda x: x[1])[:5]
    bc_low5 = sorted([(r, _pct(r["gtc"], r["total"])) for r in rows
                      if r["total"] >= 20 and _pct(r["gtc"], r["total"]) is not None
                      and _pct(r["gtc"], r["total"]) < 50], key=lambda x: x[1])[:5]
    nv_low5 = sorted([(d, r["name"], _pct(d["gtc"], d["total"])) for r in rows
                      for d in r.get("drivers", [])
                      if d.get("total", 0) >= 20 and _pct(d["gtc"], d["total"]) is not None
                      and _pct(d["gtc"], d["total"]) < 50], key=lambda x: x[2])[:5]

    def _dgrp(title, items):
        if not items:
            return ""
        rows_html = "".join(items)
        return "<div class='dgrp'><div class='dgh'>%s</div>%s</div>" % (title, rows_html)

    grps = []
    if top_bl5:
        grps.append(_dgrp("🔴 Top tồn chưa gán giao", [
            "<div class='drow'><span class='dn'>%s</span><span class='dv w'>%s đơn</span></div>"
            % (_esc(r["name"]), _n(r["backlog"])) for r in top_bl5]))
    if am_worst5:
        grps.append(_dgrp("🟠 AM %GTC thấp nhất", [
            "<div class='drow'><span class='dn'>%s</span><span class='pill sm %s'>%s%%</span></div>"
            % (_esc(a), _cls(p), p) for a, p in am_worst5]))
    if bc_low5:
        grps.append(_dgrp("🏤 Top bưu cục %GTC thấp (≥20 đơn)", [
            "<div class='drow'><span class='dn'>%s</span><span class='pill sm %s'>%s%%</span></div>"
            % (_esc(r["name"]), _cls(p), p) for r, p in bc_low5]))
    if nv_low5:
        grps.append(_dgrp("👤 Top NV %GTC thấp (≥20 đơn)", [
            "<div class='drow'><span class='dn'>%s<i>%s</i></span><span class='pill sm %s'>%s%%</span></div>"
            % (_esc(d["name"]), _esc(bc), _cls(p), p) for d, bc, p in nv_low5]))

    if grps:
        P.append("<details class='diag'><summary>⚡ <b>Điểm Nóng Chú Ý</b> · %s<span class='dcv'>▾</span></summary>"
                 "<div class='ddtl'><div class='dnote'>Top 5 cần chú ý mỗi mục · "
                 "%%GTC luỹ kế trong ngày (sáng còn thấp là bình thường)</div>%s</div></details>"
                 % (" · ".join(diag), "".join(grps)))
    else:
        P.append("<div class='diag'>⚡ <b>Điểm Nóng Chú Ý</b> · %s</div>" % " · ".join(diag))

    # ===== Dải chỉ số · Bento (Mẫu 3) · màu theo từng chỉ số =====
    vpct = _pct(R["vngh_gtc"], R["vngh"])
    _cgo = ("onclick=\"var d=document.getElementById('cgd');if(d){d.open=true;"
            "d.scrollIntoView({behavior:'smooth',block:'start'});}\"")
    # Kỷ luật ngữ nghĩa: NEU = ô đếm số (tối trung tính) · màu chỉ dành cho ô cảnh báo
    NEU, AMBER, RED, GREEN = "128,140,174", "247,185,85", "242,88,95", "47,208,122"
    xp_rgb = RED if late_cnt else NEU                       # xuất phát muộn: đỏ khi >0
    tt_rgb = {"good": GREEN, "warn": AMBER, "bad": RED}.get(_cls(vpct), NEU) if vpct is not None else NEU
    #      icon, giá trị, nhãn, màu rgb, extra, neu(ô trung tính)
    kpis = [
        ("📥", _n(R["total"]),                                       "Đã gán",          NEU,     "",   True),
        ("🏃", _n(R["ontrip"]),                                      "Đang chạy",       NEU,     "",   True),
        ("🚛", _n(on_road),                                          "Còn phải giao",   NEU,     "",   True),
        ("✅", _n(R["gtc"]),                                         "GTC nay",         NEU,     "",   True),
        ("🕘", _n(late_cnt),                                         "XP muộn &gt;9h30",xp_rgb,  "",   not late_cnt),
        ("🛍️", _n(R["vngh"]),                                       "TikTok gán",      NEU,     "",   True),
        ("🛍️", _n(R["vngh_gtc"]),                                   "TikTok GTC",      NEU,     "",   True),
        ("🛍️", (("%d%%" % vpct) if vpct is not None else "—"),      "%GTC TikTok",     tt_rgb,  "",   vpct is None),
        ("💰", _codm(R["cod_gtb"]),                       "COD GTB",         AMBER,   "",   False),
        ("🛒", _n(R["ltc"]),                                         "LTC",             NEU,     "",   True),
        ("📦", _n(R["ltb"]),                                         "LTB",  (RED if R["ltb"] else NEU), "", not R["ltb"]),
        ("🎯", _n(nv_qual - nv_low),                                 "NV đạt ≥50%",     GREEN,   "",   (nv_qual - nv_low) == 0),
    ]
    P.append("<div class='sectitle'>📊 Chỉ số khác</div>")
    P.append("<section class='strip'>")
    for ic, val, lab, rgb, extra, neu in kpis:
        cls = "st" + (" cg" if extra == "cg" else "") + (" neu" if neu else "")
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
                 "<div class='mtx'><div class='mn'>Tồn chưa gán giao</div>"
                 "<div class='ms'>AM → Bưu cục → tuyến · bấm mở chi tiết</div></div>"
                 "<div class='mbig'>%s</div><span class='cvar'>▾</span></summary>"
                 "<div class='dtl'>" % _n(R["backlog"]))
        for amn, brows in sorted(cg_am.items(), key=lambda kv: -sum(x["backlog"] for x in kv[1])):
            am_tot = sum(x["backlog"] for x in brows)
            P.append("<details class='bc warn'><summary>")
            P.append("<div class='bch'><span class='dot warn'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill warn'>%s</span></div>" % (_esc(amn), _n(am_tot)))
            P.append("<div class='bcm'><span>%d bưu cục còn tồn chưa gán giao</span></div>" % len(brows))
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

    # ===== 🔴 Giao>120h drill — AM → bưu cục (mở từ ô 'Cần làm ngay') =====
    g_am = {}
    for r in rows:
        g = r.get("giao120h", 0)
        if g <= 0:
            continue
        amn = AM_OF.get(r["name"]) or "(chưa phân AM)"
        g_am.setdefault(amn, []).append((r["name"], g))
    if g_am:
        P.append("<details id='g120d' class='cgbento'><summary>")
        P.append("<div class='mic'>🔴</div>"
                 "<div class='mtx'><div class='mn'>Backlog giao 120h</div>"
                 "<div class='ms'>AM → bưu cục · đơn tồn quá 120 giờ · bấm mở</div></div>"
                 "<div class='mbig'>%s</div><span class='cvar'>▾</span></summary>"
                 "<div class='dtl'>" % _n(giao_120h or 0))
        for amn, bcs in sorted(g_am.items(), key=lambda kv: -sum(g for _, g in kv[1])):
            am_tot = sum(g for _, g in bcs)
            P.append("<details class='bc bad'><summary>")
            P.append("<div class='bch'><span class='dot bad'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill bad'>%s</span></div>"
                     "<div class='bcm'><span>%d bưu cục có đơn đỏ</span></div>"
                     "</summary><div class='dtl'>" % (_esc(amn), _n(am_tot), len(bcs)))
            P.append("<table class='drv'><thead><tr><th>Bưu cục</th>"
                     "<th>Backlog 120h</th></tr></thead><tbody>")
            for bcn, g in sorted(bcs, key=lambda x: -x[1]):
                P.append("<tr><td class='nv'>%s</td><td><b class='w'>%s</b></td></tr>"
                         % (_esc(bcn), _n(g)))
            P.append("</tbody></table></div></details>")
        P.append("</div></details>")

    # ===== ⚖️ Khối lượng drill — AM → bưu cục (ĐÃ GÁN + CHƯA GÁN, kg thực) =====
    kg_am = {}
    for r in rows:
        dg = (r.get("weight_g", 0) or 0) / 1000.0           # đã gán kg
        cg = (r.get("backlog_weight_g", 0) or 0) / 1000.0   # chưa gán kg
        if dg <= 0 and cg <= 0:
            continue
        amn = AM_OF.get(r["name"]) or "(chưa phân AM)"
        kg_am.setdefault(amn, []).append((r["name"], dg, cg))
    if kg_am:
        tot_dg = sum((r.get("weight_g", 0) or 0) for r in rows) / 1000.0
        tot_cg = sum((r.get("backlog_weight_g", 0) or 0) for r in rows) / 1000.0
        P.append("<details id='kgd' class='cgbento' style='--h:56,189,248'><summary>")
        P.append("<div class='mic'>⚖️</div>"
                 "<div class='mtx'><div class='mn'>Khối lượng giao (kg thực)</div>"
                 "<div class='ms'>đã gán %s · chưa gán %s · AM → bưu cục</div></div>"
                 "<div class='mbig'>%s</div><span class='cvar'>▾</span></summary>"
                 "<div class='dtl'>" % (_kgfmt(tot_dg), _kgfmt(tot_cg), _kgfmt(tot_dg)))
        for amn, bcs in sorted(kg_am.items(), key=lambda kv: -sum(x[1] + x[2] for x in kv[1])):
            a_dg = sum(x[1] for x in bcs); a_cg = sum(x[2] for x in bcs)
            P.append("<details class='bc'><summary>")
            P.append("<div class='bch'><span class='dot' style='background:#38bdf8'></span>"
                     "<span class='bcn'>🧑‍💼 %s</span></div>"
                     "<div class='bcm'><span>📦 đã gán <b class='ltc'>%s</b></span>"
                     "<span>⏳ chưa gán <b class='w'>%s</b></span></div>"
                     "</summary><div class='dtl'>" % (_esc(amn), _kgfmt(a_dg), _kgfmt(a_cg)))
            P.append("<table class='drv'><thead><tr><th class='lft'>Bưu cục</th>"
                     "<th>Đã gán</th><th>Chưa gán</th></tr></thead><tbody>")
            for bcn, dg, cg in sorted(bcs, key=lambda x: -(x[1] + x[2])):
                P.append("<tr><td class='nv'>%s</td><td><b class='ltc'>%s</b></td>"
                         "<td><b class='w'>%s</b></td></tr>"
                         % (_esc(bcn), _kgfmt(dg), _kgfmt(cg)))
            P.append("</tbody></table></div></details>")
        P.append("</div></details>")

    # ===== 🛒/↩️ Drill Tồn Lấy + Tồn Trả — AM → bưu cục =====
    def _ton_drill(_id, icon, title, key):
        am = {}
        for r in rows:
            v = r.get(key, 0)
            if v <= 0:
                continue
            amn = AM_OF.get(r["name"]) or "(chưa phân AM)"
            am.setdefault(amn, []).append((r["name"], v))
        if not am:
            return
        tot = sum(v for bcs in am.values() for _, v in bcs)
        P.append("<details id='%s' class='cgbento' style='--h:247,185,85'><summary>"
                 "<div class='mic'>%s</div>"
                 "<div class='mtx'><div class='mn'>%s</div>"
                 "<div class='ms'>AM → bưu cục · bấm mở</div></div>"
                 "<div class='mbig'>%s</div><span class='cvar'>▾</span></summary>"
                 "<div class='dtl'>" % (_id, icon, title, _n(tot)))
        for amn, bcs in sorted(am.items(), key=lambda kv: -sum(v for _, v in kv[1])):
            a_tot = sum(v for _, v in bcs)
            P.append("<details class='bc warn'><summary>"
                     "<div class='bch'><span class='dot warn'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill warn'>%s</span></div>"
                     "<div class='bcm'><span>%d bưu cục</span></div>"
                     "</summary><div class='dtl'>" % (_esc(amn), _n(a_tot), len(bcs)))
            P.append("<table class='drv'><thead><tr><th class='lft'>Bưu cục</th><th>Số đơn</th></tr></thead><tbody>")
            for bcn, v in sorted(bcs, key=lambda x: -x[1]):
                P.append("<tr><td class='nv'>%s</td><td><b class='w'>%s</b></td></tr>" % (_esc(bcn), _n(v)))
            P.append("</tbody></table></div></details>")
        P.append("</div></details>")

    _ton_drill("tld", "🛒", "Tồn Lấy chưa gán", "ton_lay")
    _ton_drill("ttd", "↩️", "Tồn Trả", "ton_tra")

    # ===== 👤 NV cần xử lý — NHÚNG TOÀN BỘ TRANG QUẢN LÝ NHÂN VIÊN (tra cứu hồ sơ +
    #        đủ danh sách + streak 14 ngày). Supabase (creds có trong step live);
    #        Supabase lỗi → fallback NV %GTC<50% HÔM NAY.
    if nvm is not None:
        P.append("<details id='nvxuly' class='cgbento'><summary>"
                 "<div class='mic'>👤</div>"
                 "<div class='mtx'><div class='mn'>NV cần xử lý · kém dai dẳng</div>"
                 "<div class='ms'>tra cứu hồ sơ + toàn bộ danh sách · %%GTC TB &lt;40%% qua ≥5/14 ngày</div></div>"
                 "<div class='mbig'>%d<span class='u'>NV</span></div><span class='cvar'>▾</span>"
                 "</summary><div class='dtl'>%s</div></details>" % (nvm["n"], nvm["html"]))
    else:
        # Fallback (không có Supabase/lỗi): NV %GTC <50% HÔM NAY (≥30 đơn)
        low_nv = sorted([(d, r["name"]) for r in rows for d in r.get("drivers", [])
                         if d.get("total", 0) >= 30 and _pct(d["gtc"], d["total"]) is not None
                         and _pct(d["gtc"], d["total"]) < 50],
                        key=lambda x: (_pct(x[0]["gtc"], x[0]["total"]), -x[0].get("total", 0)))
        if low_nv:
            P.append("<details class='cgbento'><summary>"
                     "<div class='mic'>👤</div>"
                     "<div class='mtx'><div class='mn'>NV GTC &lt; mục tiêu 50%% (hôm nay)</div>"
                     "<div class='ms'>≥30 đơn · %%GTC thấp → cao · bấm mở danh sách</div></div>"
                     "<div class='mbig'>%d<span class='u'>NV</span></div><span class='cvar'>▾</span>"
                     "</summary><div class='dtl'>" % len(low_nv))
            P.append("<table class='drv'><thead><tr><th>Nhân viên · bưu cục</th><th>Đơn</th>"
                     "<th>Hỏng</th><th>%GTC</th></tr></thead><tbody>")
            for d, bc in low_nv[:20]:
                pc = _pct(d["gtc"], d["total"])
                P.append("<tr><td class='nv'>%s<div class='sc'>%s</div></td><td>%s</td>"
                         "<td><b class='w'>%s</b></td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _esc(bc), _n(d["total"]),
                            _n(d["total"] - d["gtc"]), _cls(pc), pc))
            P.append("</tbody></table></div></details>")

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
    # (Ô 'Báo cáo tổng quan' đã BỎ 03/10 theo yêu cầu — trang tongquan.html cũng ngưng tạo)
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
        _am_late = sum(_late_cnt(r) for r in am_rows.get(amn, []))
        _am_low = sum(_low_cnt(r) for r in am_rows.get(amn, []))
        P.append("<div class='pmeta'>🏤 %s BC·📥 %s·<span class='w'>⏳ %s</span>·✅ %s·<span class='ltc'>LTC %s</span>%s%s%s</div>"
                 % (v["bc"], _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]), _n(v["ltc"]),
                    ("·" + _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0))) if v.get("vngh") else "",
                    ("·" + _late_badge(_am_late)) if _am_late else "",
                    ("·" + _low_badge(_am_low)) if _am_low else ""))
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
        _pv_late = sum(_late_cnt(r) for r in prov_rows.get(pv, []))
        _pv_low = sum(_low_cnt(r) for r in prov_rows.get(pv, []))
        P.append("<div class='pmeta'>🏃 %s·📥 %s·⏳ %s·✅ %s·<span class='ltc'>LTC %s</span>%s%s%s</div>"
                 % (_n(v["ontrip"] + v["fin"]), _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]),
                    _n(v["ltc"]),
                    ("·" + _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0))) if v.get("vngh") else "",
                    ("·" + _late_badge(_pv_late)) if _pv_late else "",
                    ("·" + _low_badge(_pv_low)) if _pv_low else ""))
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
                 "<span class='w'>⏳ %s</span><span>✅ %s</span>"
                 "<span class='ltc'>LTC %s</span>%s%s%s</div>"
                 % (_n(r["ontrip"]), _n(r["fin"]), _n(r["total"]), _n(r["backlog"]), _n(r["gtc"]),
                    _n(r.get("ltc", 0)),
                    _tt_chip(r.get("vngh_gtc", 0), r.get("vngh", 0)),
                    _late_badge(_late_cnt(r)), _low_badge(_low_cnt(r))))
        P.append("</summary>")
        # TẤT CẢ tài xế có chuyến hôm nay (kể cả chưa có đơn giao); bấm NV → %GTC theo xã
        P.append("<div class='dtl'>")
        P.append(_drv_table(r["drivers"]))
        P.append("</div></details>")

    P.append("<div class='foot'><b>📖 Giải thích chỉ số</b><br>"
             "📥 <b>Đã gán</b> = đơn đã xếp vào chuyến hôm nay · ⏳ <b>Chưa gán</b> = đơn tồn ở kho chưa xếp chuyến<br>"
             "🏃 <b>Đang chạy</b> = số NV còn chuyến chưa kết thúc · 🚛 <b>Còn phải giao</b> = đơn của chuyến đang chạy CHƯA giao xong (đang trên đường)<br>"
             "🔴 <b>Backlog giao 120h</b> = đơn Giao tồn quá 120 giờ toàn vùng (khớp trang Tồn đọng) · ✅ <b>GTC nay</b> = đơn giao thành công (chuyến đã kết thúc)<br>"
             "🕘 <b>XP muộn &gt;9h30</b> = số NV xuất phát sau 9h30 (kỷ luật ra hàng) · 🛍️ <b>TikTok</b> = đơn hàng sàn TikTok Shop<br>"
             "💰 <b>COD GTB</b> = tiền thu hộ kẹt trên đơn giao hỏng (triệu đồng) · 🛒 <b>LTC</b> = lấy hàng thành công · 📦 <b>LTB</b> = lấy hàng thất bại (đã thao tác nhưng không lấy được)<br>"
             "🎯 <b>NV đạt ≥50%</b> = số nhân viên có %GTC ≥50% (≥20 đơn đã gán)<br>"
             "🎯 <b>%GTC</b> = GTC / tổng đơn đã gán · gộp theo mã đơn (đơn giao lại tính 1 lần)<br>"
             "<span style='opacity:.7'>Số LIVE gồm cả chuyến đã kết thúc trong ngày · %GTC còn thấp giữa ngày là bình thường (chuyến chưa đóng) · nguồn nhanh.ghn.vn</span></div>")
    P.append("<script>function tgw(tr){tr.classList.toggle('op');"
             "var s=tr.nextElementSibling;if(s&&s.classList.contains('wsub'))s.classList.toggle('show');}</script>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc[data-k]').forEach(function(e){var k=e.dataset.k||'';"
             "var s=(!q||k.indexOf(q)>=0);e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=(q&&!n)?'block':'none';}</script>")
    P.append("<button id='rf' class='fab' onclick='rf()' aria-label='Làm mới'>"
             "<span class='rfi'>⟳</span></button>")
    P.append("<script>function rf(){var b=document.getElementById('rf');"
             "b.classList.add('spin');location.replace(location.pathname+'?t='+Date.now());}</script>")
    if nvm is not None:
        P.append("<script>%s</script>" % nvm["js"])
    P.append("</div></body></html>")
    return "\n".join(P)


_CSS = """<style>
:root{
 --bg:#0a0e1a;--bg2:#10131f;--card:rgba(26,32,56,.9);--card2:rgba(36,44,72,.9);--line:rgba(255,255,255,.24);
 --mut:#c6cde6;--txt:#fbfcff;--good:#17c983;--warn:#f5aa17;--bad:#f5455c;--ink:#0a0d18;
 --vi:#9d92e0;--cy:#3fb9d6}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:'Manrope',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 color:var(--txt);-webkit-font-smoothing:antialiased;font-size:15px;line-height:1.4;
 background:
  radial-gradient(80% 34% at 12% -6%,rgba(99,91,245,.56) 0%,transparent 50%),
  radial-gradient(76% 32% at 96% 3%,rgba(168,96,236,.42) 0%,transparent 48%),
  radial-gradient(110% 46% at 60% 105%,rgba(16,170,154,.4) 0%,transparent 56%),
  radial-gradient(62% 32% at 84% 66%,rgba(116,120,248,.3) 0%,transparent 54%),#0b0f1c;
 background-attachment:fixed}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(30px + env(safe-area-inset-bottom))}
/* Kính mờ dùng chung — đọc rõ cả nơi nền tối lẫn nơi có ánh sáng */
.hero,.verdict,.prilist,.diag,.st,.cgbento,.bc,.mtile,.foot{
 backdrop-filter:blur(8px) saturate(1.35) contrast(1.06);-webkit-backdrop-filter:blur(8px) saturate(1.35) contrast(1.06)}
.brand,.hpct,.sv,.mbig,.vst,.vpct b,.ttv,.hh,.amn{font-family:'Sora','Manrope',sans-serif}

.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
 padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,rgba(8,10,22,.85) 66%,rgba(8,10,22,0));backdrop-filter:blur(8px);margin-bottom:4px}
.brand{font-weight:800;letter-spacing:.04em;font-size:15px;display:flex;align-items:center;gap:8px}
.ts{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
.live{width:9px;height:9px;border-radius:50%;background:var(--good);box-shadow:0 0 0 0 rgba(47,208,122,.6);animation:pulse 1.8s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,208,122,.55)}70%{box-shadow:0 0 0 7px rgba(47,208,122,0)}100%{box-shadow:0 0 0 0 rgba(47,208,122,0)}}

.hero{--h:139,146,171;border-radius:20px;padding:20px 18px 18px;margin:4px 0 12px;position:relative;overflow:hidden;
 background:radial-gradient(130% 100% at 100% 0,rgba(var(--h),.10),transparent 62%),
  radial-gradient(120% 90% at 0% 0,rgba(var(--h),.08),transparent 55%),var(--card);
 border:1px solid rgba(var(--h),.22)}
.hero.good{--h:47,208,122;box-shadow:0 8px 26px -16px rgba(47,208,122,.22)}
.hero.warn{--h:247,185,85;box-shadow:0 8px 26px -16px rgba(247,185,85,.2)}
.hero.bad{--h:242,88,95;box-shadow:0 8px 26px -16px rgba(242,88,95,.2)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.hpct{font-size:64px;font-weight:850;line-height:1;margin:8px 0 12px;font-variant-numeric:tabular-nums;letter-spacing:-.02em;text-shadow:0 0 14px rgba(var(--h),.16)}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}.hero.na .hpct{color:var(--mut)}
.hpct span{font-size:26px;font-weight:700;opacity:.6;margin-left:2px}
.hsub{color:var(--mut);font-size:12.5px;margin-top:10px;font-variant-numeric:tabular-nums}
.hsub .ld{color:var(--txt);font-weight:700}
.href{display:flex;flex-wrap:wrap;gap:6px 7px;margin-top:12px}
.rc{font-size:11px;color:var(--mut);background:rgba(255,255,255,.05);border:1px solid var(--line);
 border-radius:99px;padding:3px 10px;font-variant-numeric:tabular-nums;white-space:nowrap}
.rc b{color:var(--txt);font-weight:800}
.sparkwrap{margin-top:12px}
.spklbl{font-size:9.5px;color:var(--mut);font-weight:600;letter-spacing:.02em;margin-bottom:3px}
svg.spk{display:block}
/* ĐÈN TRẠNG THÁI */
.verdict{display:flex;align-items:center;gap:12px;padding:13px 14px;margin:4px 0 12px;border-radius:18px;
 background:linear-gradient(120deg,rgba(139,147,255,.18),var(--card));border:1px solid rgba(139,147,255,.34)}
.verdict.good{background:linear-gradient(120deg,rgba(52,211,153,.2),var(--card));border-color:rgba(52,211,153,.4)}
.verdict.warn{background:linear-gradient(120deg,rgba(251,191,36,.2),var(--card));border-color:rgba(251,191,36,.4)}
.verdict.bad{background:linear-gradient(120deg,rgba(251,113,133,.22),var(--card));border-color:rgba(251,113,133,.45)}
.vic{width:44px;height:44px;border-radius:13px;flex:none;display:grid;place-items:center;font-size:22px;
 background:rgba(255,255,255,.08);border:1px solid var(--line)}
.vtx{flex:1;min-width:0}
.vst{font-weight:800;font-size:15px;letter-spacing:.02em}
.verdict.neu .vst{color:var(--vi)}.verdict.good .vst{color:var(--good)}.verdict.warn .vst{color:var(--warn)}.verdict.bad .vst{color:var(--bad)}
.vsub{font-size:11px;color:var(--mut);margin-top:2px;line-height:1.4}
.vpct{text-align:right;flex:none}
.vpct b{font-weight:800;font-size:26px;font-variant-numeric:tabular-nums}
.vpct i{display:block;font-style:normal;font-size:8.5px;color:var(--mut);font-weight:600}
/* CẦN LÀM NGAY */
.sectitle{font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin:14px 3px 8px}
.prilist{display:flex;flex-direction:column;gap:8px;margin-bottom:12px;background:none;border:none;padding:0}
.todo{display:flex;align-items:center;gap:11px;padding:13px 14px;border-radius:15px;text-decoration:none;color:var(--txt);
 background:var(--card);border:1px solid var(--line)}
.todo.bd{border-color:rgba(251,113,133,.4)}.todo.wn{border-color:rgba(251,191,36,.34)}.todo.vi{border-color:rgba(167,139,250,.4)}
.todo.lnk:active{transform:scale(.99)}
.todo .tic{width:34px;height:34px;border-radius:11px;flex:none;display:grid;place-items:center;font-size:17px;background:rgba(255,255,255,.06);border:1px solid var(--line)}
.todo.bd .tic{background:rgba(251,113,133,.16);border-color:rgba(251,113,133,.38)}
.todo.wn .tic{background:rgba(251,191,36,.14);border-color:rgba(251,191,36,.34)}
.todo.vi .tic{background:rgba(167,139,250,.16);border-color:rgba(167,139,250,.4)}
.todo .tdt{flex:1;min-width:0}
.todo .ttn{font-weight:800;font-size:14.5px;letter-spacing:-.01em}
.todo .tts{font-size:11px;color:var(--mut);margin-top:2px;font-weight:600}
.todo .ttv{font-weight:800;font-size:28px;flex:none;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.todo.bd .ttv{color:var(--bad)}.todo.wn .ttv{color:var(--warn)}.todo.vi .ttv{color:var(--txt)}
.todo svg.spk{width:56px;height:22px;flex:none}
.diag{background:radial-gradient(120% 100% at 0% 0%,rgba(255,255,255,.06),var(--card) 72%);
 border:1px solid var(--line);border-radius:14px;padding:10px 13px;margin:0 0 12px;
 font-size:12.5px;line-height:1.55;color:var(--mut)}
.diag b{color:var(--txt);font-weight:800}
details.diag>summary{cursor:pointer;list-style:none;display:block;position:relative;padding-right:18px}
details.diag>summary::-webkit-details-marker{display:none}
.dcv{position:absolute;right:0;top:0;color:var(--mut);font-size:12px;transition:transform .2s}
details.diag[open] .dcv{transform:rotate(180deg)}
.ddtl{margin-top:11px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:12px}
.dnote{font-size:10.5px;color:#6d7492;line-height:1.5;margin:-2px 0 -2px}
.dgrp{display:flex;flex-direction:column;gap:5px}
.dgh{font-size:11px;font-weight:800;letter-spacing:.02em;color:var(--txt)}
.drow{display:flex;align-items:center;gap:9px;background:rgba(255,255,255,.03);border:1px solid var(--line);
 border-radius:9px;padding:6px 10px}
.drow .dn{flex:1;min-width:0;font-size:12px;font-weight:600;color:var(--txt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.drow .dn i{font-style:normal;color:var(--mut);font-weight:400;font-size:10.5px;margin-left:6px}
.drow .dv{font-size:12px;font-weight:800;font-variant-numeric:tabular-nums}
.drow .dv.w{color:var(--warn)}

.bar{position:relative;height:7px;background:rgba(255,255,255,.07);border-radius:99px;overflow:hidden}
.tgt{position:absolute;top:0;bottom:0;width:2px;background:rgba(255,255,255,.65);border-radius:2px;z-index:2}
.bar i{display:block;height:100%;border-radius:99px;transition:width .5s}
.bar i.good{background:linear-gradient(90deg,#25b56b,#2fd07a)}
.bar i.warn{background:linear-gradient(90deg,#e39a2e,#f7b955)}
.bar i.bad{background:linear-gradient(90deg,#d8434b,#f2585f)}
.bar i.na{background:#4b5168}

.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-bottom:12px}
.st{position:relative;overflow:hidden;text-align:center;padding:11px 6px 10px;border-radius:16px;
 background:radial-gradient(125% 105% at 0% 0%,rgba(var(--h),.10),var(--card) 74%);
 border:1px solid rgba(var(--h),.24)}
.st.cg{cursor:pointer}.st.cg:active{transform:scale(.98)}
.st.neu{background:radial-gradient(125% 105% at 0% 0%,rgba(var(--h),.08),var(--card) 78%);border-color:rgba(var(--h),.15)}
.st.neu .sv{color:#f4f7ff}
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
.cgbento .mbig .u{font-size:13px;color:rgb(var(--h));margin-left:3px;font-weight:700}
.cgbento .cvar{flex:none;color:rgb(var(--h));font-size:14px;transition:transform .2s}
.cgbento[open] .cvar{transform:rotate(180deg)}
.cgbento .dtl{padding:0 12px 12px}
/* NV cần xử lý — thẻ NV + streak 14 ngày */
.nvx{padding:9px 0;border-top:1px solid rgba(255,255,255,.06)}
.nvx:first-child{border-top:none}
.nvxh{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.nvxn{flex:1;min-width:0;font-weight:700;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nvxn i{font-style:normal;color:var(--mut);font-weight:400;font-size:11px;margin-left:6px}
.streak{display:flex;gap:3px;flex-wrap:nowrap}
.streak .cel{flex:1;height:15px;border-radius:3px;min-width:5px}
.streak .cel.good{background:var(--good)}.streak .cel.warn{background:var(--warn)}
.streak .cel.bad{background:var(--bad)}.streak .cel.z{background:#2a3050}
.nvxm{margin-top:5px;color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums}
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
td.nv .sc{font-size:9.5px;color:var(--mut);font-weight:400;margin-top:1px;overflow:hidden;text-overflow:ellipsis}
.tt{color:#e879c8;font-weight:700}
/* NV bấm mở → %GTC theo xã/phường (live hôm nay) */
tr.dnv{cursor:pointer}
tr.dnv .cx{display:inline-block;color:var(--mut);font-size:8px;margin-right:5px;transition:transform .18s;vertical-align:middle}
tr.dnv.op .cx{transform:rotate(90deg);color:var(--txt)}
.lb{font-size:12px;vertical-align:middle;filter:drop-shadow(0 0 2px rgba(245,69,92,.6))}
.lbc{display:inline-flex;align-items:center;gap:2px;font-size:11px;font-weight:800;color:#ffd9a0;
 background:rgba(245,170,23,.18);border:1px solid rgba(245,170,23,.5);border-radius:999px;padding:1px 7px;white-space:nowrap}
.lwc{display:inline-flex;align-items:center;gap:2px;font-size:11px;font-weight:800;color:#ffc2cc;
 background:rgba(245,69,92,.18);border:1px solid rgba(245,69,92,.5);border-radius:999px;padding:1px 7px;white-space:nowrap}
tr.dnv.op>td{border-bottom:none}
tr.wsub{display:none}tr.wsub.show{display:table-row}
tr.wsub>td{padding:2px 6px 10px !important;text-align:left}
.whd{font-size:9.5px;color:var(--mut);font-weight:700;letter-spacing:.02em;margin:2px 0 6px;text-transform:uppercase}
.wgrid{display:flex;flex-direction:column;gap:5px}
.wrow{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.03);border:1px solid var(--line);
  border-radius:9px;padding:6px 9px}
.wrow .wnm{flex:1;min-width:0;font-size:11.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wrow .wct{color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums}
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
        rows, giao_120h = asyncio.run(fetch_live(token))
        # NV cần xử lý (kém dai dẳng 14 ngày, đọc Supabase — có creds trong step live).
        # Supabase thiếu/lỗi → None → gen_html tự fallback về "NV <50% hôm nay".
        nv_xuly, _nvr, nvm = None, None, None
        try:
            import report_nvxuly
            _nvr = report_nvxuly.fetch()
            if _nvr:
                nvm = report_nvxuly.embed(_nvr)   # khối nhúng đầy đủ (tra cứu + danh sách + hồ sơ)
                nv_xuly = sorted([x for x in report_nvxuly.build(_nvr)
                                  if x["act"] >= report_nvxuly.MIN_ACTIVE
                                  and x["avg"] is not None and x["avg"] < report_nvxuly.YEU],
                                 key=lambda x: (x["avg"], -x["yeu"]))
        except Exception as e:
            logger.warning("NV cần xử lý (Supabase) lỗi, dùng bản hôm nay: %s", str(e)[:120])
        # Xu hướng vùng 14 ngày (Supabase) cho hero + ô "cần làm ngay". Lỗi → None (ẩn spark).
        trend = _fetch_region_trend(14)
        g120_trend = _fetch_giao120h_trend(14)         # Giao>120h chốt ngày (bao_cao_ton_dong)
        try:
            import report_nvxuly as _rnx
            cx_trend = _rnx.canxuly_trend(14)          # NV cần xử lý chốt ngày (rolling 14 ngày)
            nvdat_tr = _rnx.nvdat_trend(14)            # NV đạt %GTC≥50% theo ngày
        except Exception:
            cx_trend = nvdat_tr = None
        # Phiếu thu CHƯA thu tiền (dùng cho ô "cần làm ngay" + trang chuyến đi). Fetch 1 lần.
        collectable = None
        try:
            import report_chuyendi
            collectable = report_chuyendi.fetch_collectable(token)
        except Exception as ce:
            logger.warning("Phiếu thu treo lỗi (ẩn): %s", str(ce)[:120])
        # Lưu phiếu thu treo hôm nay (chốt dần) + đọc trend 14 ngày để vẽ đồ thị.
        pt_trend = None
        if collectable is not None:
            _pt_nv = sum(len(us) for us in collectable.values())
            _pt_amt = sum(u["amount"] for us in collectable.values() for u in us)
            _store_phieuthu(_pt_nv, _pt_amt)
        pt_trend = _fetch_phieuthu_trend(14)
        # Lưu khối lượng đơn giao đã gán hôm nay (kg) → đồ thị (đọc lại trong _fetch_region_trend)
        _store_weight(sum(r.get("weight_g", 0) for r in rows) / 1000.0)
    except Exception as e:
        # Token hết hạn / API lỗi → rơi về snapshot Supabase thay vì để trang trắng/đọng.
        if _write_fallback(e):
            return
        raise SystemExit("Fetch live lỗi và không có snapshot dự phòng: %s" % e)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    h = gen_html(rows, giao_120h, nv_xuly, nvm, collectable, trend, g120_trend, cx_trend, pt_trend, nvdat_tr)
    for fn in ("index.html", "live.html"):
        with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
            f.write(h)

    # (Trang QUẢN LÝ NHÂN VIÊN riêng nvxuly.html đã BỎ 30/09 — dữ liệu NV nay nằm gọn
    #  trong ô bento "NV cần xử lý" ngay trên trang trực tiếp qua report_nvxuly.embed)
    # (Trang đơn TikTok vngh.html đã bỏ 24/08 — 3 chỉ số TikTok vẫn giữ ở dải chỉ số index)

    # Trang hiệu suất chuyến đi NV — dùng lại rows + collectable ĐÃ fetch ở trên (0 call thêm).
    try:
        import report_chuyendi
        with open(os.path.join(outdir, "chuyendi.html"), "w", encoding="utf-8") as f:
            f.write(report_chuyendi.gen_html(rows, collectable))
    except Exception as e:
        logger.warning("Tạo chuyendi.html lỗi (bỏ qua): %s", str(e)[:150])

    # (Trang TỔNG QUAN tongquan.html đã BỎ 03/10 theo yêu cầu — tự biến mất ở lần deploy tới)

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

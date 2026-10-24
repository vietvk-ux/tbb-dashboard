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


def _w_arrow(today_g, yest_g):
    """Mũi tên khối lượng TỒN KHO today vs CÙNG GIỜ HÔM QUA (gram). ▲ đỏ = tồn TĂNG (xấu) ·
    ▼ xanh = GIẢM (tốt). Thiếu số hôm qua / nền quá nhỏ (<100kg) → rỗng."""
    if not yest_g or yest_g < 100000 or today_g is None:
        return ""
    pc = (today_g - yest_g) / yest_g * 100
    if abs(pc) < 1:
        return "<span class='hwa fl' title='≈ cùng giờ hôm qua'>▬</span>"
    up = pc > 0
    return ("<span class='hwa %s' title='so cùng giờ hôm qua'>%s%d%%</span>"
            % ("up" if up else "dn", "▲" if up else "▼", min(round(abs(pc)), 999)))


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


def _tt_inline(vg, vn):
    """TikTok %GTC NV GỌN đặt kèm sau pill %GTC (🛍️nn%) — rỗng nếu không có đơn TikTok."""
    if not vn:
        return ""
    vp = _pct(vg, vn)
    return " <span class='ttn %s' title='%%GTC TikTok của NV (%s/%s)'>🛍️%s%%</span>" % (
        _cls(vp), _n(vg), _n(vn), vp)


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
    {yp:%GTC hôm qua, avg7p:%GTC TB tuần, avg7t:đơn TB tuần, ndays, avg30p:%GTC TB 30 ngày, ndays30}.
    None nếu thiếu."""
    import glob
    try:
        alld = []
        for f in sorted(glob.glob("khuvuc_data/*.json"))[-30:]:   # giữ 30 ngày
            d = json.load(open(f, encoding="utf-8"))
            if d.get("tot"):
                alld.append(d)
        if not alld:
            return None
        days = alld[-7:]
        y = days[-1]
        st = sum(x["tot"] for x in days)
        sg = sum(x["gtc"] for x in days)
        st30 = sum(x["tot"] for x in alld)
        sg30 = sum(x["gtc"] for x in alld)
        return {"yp": _pct(y["gtc"], y["tot"]), "avg7p": _pct(sg, st),
                "avg7t": round(st / len(days)), "ndays": len(days),
                "avg30p": _pct(sg30, st30), "ndays30": len(alld)}
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
        r = requests.get("%s/rest/v1/bao_cao_vung?select=ngay,pct_gtc,chua_gan,don_giao,vngh_don,vngh_gtc,weight_kg,ton_lay,ton_tra"
                         "&order=ngay.desc&limit=%d" % (url, days + 1), headers=h, timeout=20)
        if not r.ok:
            return None
        out = []
        for x in reversed(r.json()):
            if x.get("pct_gtc") is None:
                continue
            vd, vp = x.get("vngh_don") or 0, x.get("vngh_gtc")   # vngh_gtc lưu dạng % → ra số đơn
            out.append({"ngay": x["ngay"], "pct": x.get("pct_gtc"), "chuagan": x.get("chua_gan"),
                        "don_giao": x.get("don_giao"), "weight_kg": x.get("weight_kg"),
                        "ton_lay": x.get("ton_lay"), "ton_tra": x.get("ton_tra"),
                        "tiktok_gtc": round(vd * vp / 100) if (vd and vp is not None) else None})
        return out[-days:] or None
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


def _region_snapshot(rows, giao_120h=None, lgt_w=None):
    """Snapshot toàn bộ chỉ số DẢI của vùng (để lưu theo giờ → so cùng giờ hôm qua).
    Khớp đúng cách tính trong gen_html (R + on_road/late/nv_low/vpct). lgt_w (gram) để so
    khối lượng TỒN KHO cùng giờ hôm qua (mũi tên ▲/▼ trên hero)."""
    tot = gtc = vngh = vgtc = cod = ltc = ltb = ontrip = chua_gan = dagan = 0
    ot_tot = ot_done = late = nv_low = 0
    for r in rows:
        tot += r.get("total", 0); gtc += r.get("gtc", 0); ontrip += r.get("ontrip", 0)
        vngh += r.get("vngh", 0); vgtc += r.get("vngh_gtc", 0); cod += r.get("cod_gtb", 0)
        ltc += r.get("ltc", 0); ltb += r.get("ltb", 0); chua_gan += r.get("backlog", 0)
        dagan += r.get("dagan_giao", 0)
        for d in r.get("drivers", []):
            ot_tot += d.get("ot_tot", 0); ot_done += d.get("ot_done", 0)
            st = d.get("st")
            if st is not None and (st.hour * 60 + st.minute) > 570:
                late += 1
            if d.get("total", 0) >= 20:
                p = _pct(d.get("gtc", 0), d["total"])
                if p is not None and p < 50:
                    nv_low += 1
    s = {"total": tot, "ontrip": ontrip, "on_road": max(ot_tot - ot_done, 0), "gtc": gtc,
         "late": late, "vngh": vngh, "vngh_gtc": vgtc, "vpct": _pct(vgtc, vngh),
         "cod_gtb": cod, "ltc": ltc, "ltb": ltb, "nv_low": nv_low, "g120": giao_120h,
         "chua_gan": chua_gan, "dagan_giao": dagan}
    if lgt_w:   # khối lượng tồn kho (gram) → so cùng giờ hôm qua
        s["w_lay"] = lgt_w.get("lay", 0); s["w_giao"] = lgt_w.get("giao", 0)
        s["w_tra"] = lgt_w.get("tra", 0); s["w_lcg"] = lgt_w.get("lc_giao", 0)
        s["w_lct"] = lgt_w.get("lc_tra", 0)
    return s


def _bc_snapshot(rows):
    """Snapshot [gtc,total,backlog,dagan_giao] TỪNG BƯU CỤC. [gtc,total] = so %GTC cùng giờ hôm
    qua (AM/Tỉnh = tổng BC con; reader cũ chỉ đọc [0],[1]); [2] `backlog` (chưa gán giao) để báo cáo
    cuối ngày lấy 'tồn chưa gán tại ~12h'; [3] `dagan_giao` (đã gán đang có chuyến) — thêm để so
    'Đã gán' cùng giờ hôm qua ở bảng AM (reader guard len>=4)."""
    return {r["name"]: [r.get("gtc", 0), r.get("total", 0), r.get("backlog", 0), r.get("dagan_giao", 0)]
            for r in rows if r.get("total", 0) > 0 or r.get("backlog", 0) > 0}


def _nv_snapshot(rows):
    """Snapshot [gtc,total] TỪNG NHÂN VIÊN (khoá driver_id) → so %GTC TB 7 ngày của chính NV."""
    out = {}
    for r in rows:
        for d in r.get("drivers", []):
            did = d.get("id")
            if did and d.get("total", 0) > 0:
                out[str(did)] = [d.get("gtc", 0), d.get("total", 0)]
    return out


# TB 7 ngày cùng giờ theo NV ({driver_id: [Σgtc, Σtotal]}) — set trong gen_html (sau gate 10h).
_NV_AVG = {}


def _nv_cmp_cell(d):
    """Ô CỘT RIÊNG 'So TB7' — chênh %GTC NV vs TB 7 ngày của chính NV, cỡ chữ BẰNG các cột
    số khác (dễ nhìn). ▲ xanh tốt hơn · ▼ đỏ kém hơn · ▬ bằng. '—' nếu chưa đủ dữ liệu."""
    yv = _NV_AVG.get(str(d.get("id"))) if d.get("id") else None
    if not (isinstance(yv, (list, tuple)) and len(yv) >= 2):
        return "<span class='nvc fl'>—</span>"
    tp = _pct(d.get("gtc", 0), d.get("total", 0)); yp = _pct(yv[0], yv[1])
    if tp is None or yp is None:
        return "<span class='nvc fl'>—</span>"
    dd = round(tp - yp, 1)
    if dd == 0:
        return "<span class='nvc fl' title='= TB 7 ngày của NV'>▬</span>"
    up = dd > 0
    return "<span class='nvc %s' title='%s%sđ so TB 7 ngày của NV'>%s%s</span>" % (
        "up" if up else "dn", "+" if up else "-",
        ("%.1f" % abs(dd)).replace(".", ","),
        "▲" if up else "▼", ("%.1f" % abs(dd)).replace(".", ","))


def _prune_bao_cao_gio():
    """Dọn bao_cao_gio cho GỌN — chỉ giữ đúng thời gian mỗi cột cần:
    - snap/bcsnap (jsonb nặng) chỉ phục vụ so 'cùng giờ hôm qua' → giữ GIO_SNAP_DAYS ngày (mặc định 3),
      cũ hơn thì XOÁ nội dung jsonb (NULL) để nhẹ.
    - pct_gtc (nhẹ) phục vụ dự báo uplift (cần ~8 ngày) → giữ cả dòng GIO_KEEP_DAYS ngày (mặc định 14),
      cũ hơn XOÁ hẳn dòng.
    Chạy 1 lần/ngày (gọi có gate trong main). Lỗi/thiếu cột → bỏ qua êm."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        return
    try:
        import requests
        from datetime import timedelta
        today = datetime.now(VN).date()
        snap_days = int(os.environ.get("GIO_SNAP_DAYS", "8"))   # giữ 8 ngày để TB 7 ngày (mũi tên BC/AM)
        keep_days = int(os.environ.get("GIO_KEEP_DAYS", "14"))
        cut_snap = (today - timedelta(days=snap_days)).isoformat()
        cut_del = (today - timedelta(days=keep_days)).isoformat()
        hdr = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json", "Prefer": "return=minimal"}
        # 1) NULL jsonb cũ (chỉ đụng dòng còn snap → lần sau là no-op)
        requests.patch("%s/rest/v1/bao_cao_gio?ngay=lt.%s&snap=not.is.null" % (url, cut_snap),
                       json={"snap": None, "bcsnap": None, "nvsnap": None}, headers=hdr, timeout=20)
        # 2) Xoá dòng quá cũ (có filter ngay → PostgREST cho phép)
        requests.delete("%s/rest/v1/bao_cao_gio?ngay=lt.%s" % (url, cut_del), headers=hdr, timeout=20)
    except Exception:
        pass


def _store_hourly_pct(pct, gtc=None, total=None, g120=None, snap=None, bcsnap=None, nvsnap=None):
    """Ghi %GTC HIỆN TẠI theo GIỜ vào bao_cao_gio (upsert theo ngay+gio → giữ bản mới nhất
    trong giờ đó) → để DỰ BÁO về đích + đo NHỊP ĐỘ (đơn/giờ) + SO CÙNG GIỜ HÔM QUA. Cũng lưu
    gtc/total/phut/g120 (nếu đã migrate); cột thiếu → tự lùi về bản chỉ pct. Lỗi → êm."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key) or pct is None:
        return
    try:
        import requests
        now = datetime.now(VN)
        base = {"ngay": now.date().isoformat(), "gio": now.hour, "pct_gtc": round(pct, 1)}
        rich = dict(base, gtc=gtc, total=total, phut=now.minute, g120=g120,
                    snap=snap, bcsnap=bcsnap, nvsnap=nvsnap)
        hdr = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
        u = "%s/rest/v1/bao_cao_gio?on_conflict=ngay,gio" % url
        r = requests.post(u, json=[rich], headers=hdr, timeout=20)
        # Cột mới chưa migrate → PostgREST 400. Lùi dần: bỏ nvsnap (giữ snap/bcsnap) → nếu vẫn 400
        # thì chỉ pct (dự báo). Tránh mất snap/bcsnap chỉ vì cột nvsnap chưa có.
        if r.status_code >= 400:
            rich2 = {k: v for k, v in rich.items() if k != "nvsnap"}
            r2 = requests.post(u, json=[rich2], headers=hdr, timeout=20)
            if r2.status_code >= 400:
                requests.post(u, json=[base], headers=hdr, timeout=20)
    except Exception:
        pass


def _fetch_hour_vs_yesterday(hour):
    """SO CÙNG GIỜ HÔM QUA: đọc bao_cao_gio của HÔM QUA tại GIỜ này → {pct,gtc,total,g120}.
    Chỉ trả field có giá trị (cột mới chỉ đầy từ ngày bắt đầu log). None nếu thiếu/không có."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        return None
    try:
        import requests
        from datetime import timedelta
        y = (datetime.now(VN).date() - timedelta(days=1)).isoformat()
        r = requests.get("%s/rest/v1/bao_cao_gio?select=pct_gtc,gtc,total,g120,snap,bcsnap"
                         "&ngay=eq.%s&gio=eq.%d" % (url, y, hour),
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=20)
        if not r.ok or not r.json():
            return None
        x = r.json()[0]
        out = {}
        if x.get("pct_gtc") is not None:
            out["pct"] = x["pct_gtc"]
        for k in ("gtc", "total", "g120"):
            if x.get(k) is not None:
                out[k] = x[k]
        if isinstance(x.get("snap"), dict):
            out["snap"] = x["snap"]          # toàn bộ chỉ số dải cùng giờ hôm qua
        if isinstance(x.get("bcsnap"), dict):
            out["bcsnap"] = x["bcsnap"]      # (không dùng cho mũi tên nữa — xem _fetch_hour_bc_avg)
        return out or None
    except Exception:
        return None


def _fetch_hour_bc_avg(hour, days=7):
    """TRUNG BÌNH %GTC từng BƯU CỤC tại GIỜ này qua `days` ngày gần nhất (KHÔNG gồm hôm nay).
    Gộp Σgtc/Σtotal (pooled — ổn định hơn trung bình ratio, tránh nhiễu ngày ít đơn).
    Trả {bc: [Σgtc, Σtotal]} (AM/Tỉnh = tổng BC con). Đầy dần tới đủ 7 ngày. None nếu trống."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        return None
    try:
        import requests
        from datetime import timedelta
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days)).isoformat()
        yest = (today - timedelta(days=1)).isoformat()
        r = requests.get("%s/rest/v1/bao_cao_gio?select=bcsnap&gio=eq.%d"
                         "&ngay=gte.%s&ngay=lte.%s" % (url, hour, since, yest),
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=20)
        if not r.ok:
            return None
        agg = {}
        for row in r.json():
            bs = row.get("bcsnap")
            if not isinstance(bs, dict):
                continue
            for bc, gt in bs.items():
                if isinstance(gt, (list, tuple)) and len(gt) >= 2:
                    z = agg.setdefault(bc, [0, 0]); z[0] += gt[0]; z[1] += gt[1]
        return agg or None
    except Exception:
        return None


def _fetch_hour_nv_avg(hour, days=7):
    """TB %GTC từng NHÂN VIÊN (driver_id) tại GIỜ này qua `days` ngày gần nhất (trừ hôm nay).
    Pooled Σgtc/Σtotal từ cột nvsnap. Trả {driver_id: [Σgtc, Σtotal]}. None nếu trống/chưa migrate."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        return None
    try:
        import requests
        from datetime import timedelta
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days)).isoformat()
        yest = (today - timedelta(days=1)).isoformat()
        r = requests.get("%s/rest/v1/bao_cao_gio?select=nvsnap&gio=eq.%d"
                         "&ngay=gte.%s&ngay=lte.%s" % (url, hour, since, yest),
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=20)
        if not r.ok:
            return None
        agg = {}
        for row in r.json():
            ns = row.get("nvsnap")
            if not isinstance(ns, dict):
                continue
            for did, gt in ns.items():
                if isinstance(gt, (list, tuple)) and len(gt) >= 2:
                    z = agg.setdefault(did, [0, 0]); z[0] += gt[0]; z[1] += gt[1]
        return agg or None
    except Exception:
        return None


def _fetch_today_rate(gtc_now, min_gap_h=0.5):
    """NHỊP ĐỘ giao THỰC (đơn/giờ) trong ~1h qua: so gtc hiện tại với bản ghi bao_cao_gio
    gần nhất HÔM NAY cách ≥30' (cột gtc/phut). None nếu chưa migrate / chưa đủ mốc."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key) or gtc_now is None:
        return None
    try:
        import requests
        now = datetime.now(VN)
        today = now.date().isoformat()
        r = requests.get("%s/rest/v1/bao_cao_gio?select=gio,phut,gtc&ngay=eq.%s"
                         "&gtc=not.is.null&order=gio.asc" % (url, today),
                         headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=20)
        if not r.ok:
            return None
        now_t = now.hour + now.minute / 60.0
        ref = None   # mốc gần hiện tại NHẤT nhưng cách ≥min_gap_h
        for x in r.json():
            if x.get("gtc") is None:
                continue
            rt = x["gio"] + (x.get("phut") or 0) / 60.0
            if now_t - rt >= min_gap_h:
                ref = (rt, x["gtc"])
        if not ref:
            return None
        rt0, g0 = ref
        dt = now_t - rt0
        if dt <= 0:
            return None
        return (gtc_now - g0) / dt
    except Exception:
        return None


def _fetch_hour_uplift(hour, days=8):
    """Độ BỨT TỐC trung vị %GTC từ GIỜ hiện tại → chốt cuối ngày, qua N ngày gần đây.
    = median(eod_pct − pct_tại_giờ_h) của các ngày đã chốt. None nếu <2 ngày dữ liệu."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        import requests
        from datetime import timedelta
        h = {"apikey": key, "Authorization": "Bearer " + key}
        since = (datetime.now(VN).date() - timedelta(days=days)).isoformat()
        today = datetime.now(VN).date().isoformat()
        # %GTC tại GIỜ h từng ngày
        r1 = requests.get("%s/rest/v1/bao_cao_gio?select=ngay,pct_gtc&gio=eq.%d&ngay=gte.%s"
                          % (url, hour, since), headers=h, timeout=20)
        # %GTC CHỐT cuối ngày (bao_cao_vung) — chỉ ngày đã chốt
        r2 = requests.get("%s/rest/v1/bao_cao_vung?select=ngay,pct_gtc&don_giao=not.is.null&ngay=gte.%s"
                          % (url, since), headers=h, timeout=20)
        if not (r1.ok and r2.ok):
            return None
        hrp = {x["ngay"]: x["pct_gtc"] for x in r1.json() if x.get("pct_gtc") is not None}
        eod = {x["ngay"]: x["pct_gtc"] for x in r2.json() if x.get("pct_gtc") is not None}
        ups = [eod[d] - hrp[d] for d in hrp if d in eod and d != today]
        if len(ups) < 2:
            return None
        ups.sort()
        n = len(ups)
        return ups[n // 2] if n % 2 else (ups[n // 2 - 1] + ups[n // 2]) / 2
    except Exception:
        return None


def _store_ton(ton_lay, ton_tra):
    """Ghi Tồn Lấy/Tồn Trả (CHƯA GÁN) HÔM NAY vào bao_cao_vung (partial upsert theo ngay)
    → lần chạy cuối ngày = chốt → vẽ biểu đồ 14 ngày. Cột thiếu (chưa migration) → bỏ qua êm."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        return
    try:
        import requests
        today = datetime.now(VN).date().isoformat()
        requests.post("%s/rest/v1/bao_cao_vung?on_conflict=ngay" % url,
                      json=[{"ngay": today, "ton_lay": int(ton_lay or 0), "ton_tra": int(ton_tra or 0)}],
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


def _fetch_region_noon12_trend(days=14, noon_hour=12):
    """Tồn CHƯA GÁN GIAO CẤP VÙNG tại ~12h trưa, N ngày gần nhất (GỒM hôm nay nếu đã qua 12h) =
    Σ bcsnap[bc][2] của hàng gio=12 mỗi ngày trong bao_cao_gio. List cũ→mới; None nếu
    thiếu creds/lỗi/<2 điểm. (bao_cao_gio KHÔNG có cột id → order theo ngay.)"""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        import requests
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days + 4)).isoformat()
        h = {"apikey": key, "Authorization": "Bearer " + key}
        r = requests.get("%s/rest/v1/bao_cao_gio?select=ngay,bcsnap&gio=eq.%d"
                         "&ngay=gte.%s&order=ngay.desc&limit=%d"
                         % (url, noon_hour, since, days + 6),
                         headers=h, timeout=20)
        if not r.ok:
            return None
        by = {}
        for x in r.json():
            bs = x.get("bcsnap") or {}
            vs = [v[2] for v in bs.values()
                  if isinstance(v, list) and len(v) >= 3 and v[2] is not None]
            if vs:                              # ngày CÓ log backlog-12h (bỏ ngày khuyết → tránh '0' giả)
                by[x["ngay"]] = sum(vs)
        vals = [by[d] for d in sorted(by)][-days:]
        return vals if len(vals) >= 2 else None
    except Exception:
        return None


def _fetch_am_crit_trend(days=14):
    """14 ngày/AM cho 3 chỉ số điều hành (drill từ 3 card hero):
      pct  = %GTC CHỐT ngày per AM (Σgtc/Σdon_giao các BC trong AM, nguồn bao_cao_buu_cuc → KHỚP card vùng)
      noon = tồn chưa gán 12h per AM (Σ bcsnap[bc][2] gio=12)
      g120 = backlog giao >120h per AM (Σ g_gt120 DELIVER từ bao_cao_ton_dong)
    Trả (dates, {am:{'pct':{ngay:v},'noon':{ngay:v},'g120':{ngay:v}}}). Thiếu creds/lỗi → (None, {})."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return (None, {})
    try:
        import requests
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days + 4)).isoformat()
        hdr = {"apikey": key, "Authorization": "Bearer " + key}
        # bao_cao_buu_cuc: CLOSE ngày per BC (don_giao, gtc) → %GTC per AM KHỚP bao_cao_vung (card vùng).
        # (Trước dùng bao_cao_gio EOD snapshot → lệch ~1-2đ vs card vùng; nay dùng đúng số chốt ngày.)
        bcday, off = {}, 0
        while True:
            r = requests.get("%s/rest/v1/bao_cao_buu_cuc?select=ngay,buu_cuc,don_giao,gtc"
                             "&ngay=gte.%s&order=id.asc&limit=1000&offset=%d"
                             % (url, since, off), headers=hdr, timeout=30)
            if not r.ok:
                break
            c = r.json()
            for row in c:
                bc = row.get("buu_cuc")
                if bc:
                    bcday.setdefault(row["ngay"], {})[bc] = (row.get("don_giao") or 0, row.get("gtc") or 0)
            if len(c) < 1000:
                break
            off += 1000
        # bao_cao_gio gio=12: tồn chưa gán 12h per BC
        noonday = {}
        r = requests.get("%s/rest/v1/bao_cao_gio?select=ngay,bcsnap&gio=eq.12&ngay=gte.%s"
                         "&order=ngay.desc&limit=200" % (url, since), headers=hdr, timeout=30)
        if r.ok:
            for row in r.json():
                noonday[row["ngay"]] = row.get("bcsnap") or {}
        # bao_cao_ton_dong: backlog giao >120h per BC
        g120d, off = {}, 0
        while True:
            r = requests.get("%s/rest/v1/bao_cao_ton_dong?select=ngay,buu_cuc,g_gt120"
                             "&order_type=eq.DELIVER&ngay=gte.%s&order=id.asc&limit=1000&offset=%d"
                             % (url, since, off), headers=hdr, timeout=30)
            if not r.ok:
                break
            c = r.json()
            for row in c:
                bc = row.get("buu_cuc")
                if bc:
                    g120d.setdefault(row["ngay"], {})[bc] = (
                        g120d.get(row["ngay"], {}).get(bc, 0) + (row.get("g_gt120") or 0))
            if len(c) < 1000:
                break
            off += 1000
        dates = sorted(set(bcday) | set(noonday) | set(g120d))[-days:]
        res = {}
        for ng in dates:
            nn = noonday.get(ng) or {}
            acc = {}                                 # am -> [gtc,total] cho %GTC (từ bao_cao_buu_cuc)
            for bc, (dg, gt) in (bcday.get(ng) or {}).items():
                am = AM_OF.get(bc)
                if am:
                    a = acc.setdefault(am, [0, 0]); a[0] += gt; a[1] += dg
            for am, (gtc, tot) in acc.items():
                if tot:
                    res.setdefault(am, {}).setdefault("pct", {})[ng] = round(gtc / tot * 100, 1)
            accn = {}
            for bc, v in nn.items():
                if isinstance(v, list) and len(v) >= 3 and v[2] is not None:
                    am = AM_OF.get(bc)
                    if am:
                        accn[am] = accn.get(am, 0) + v[2]
            for am, sm in accn.items():
                res.setdefault(am, {}).setdefault("noon", {})[ng] = sm
            accg = {}
            for bc, val in (g120d.get(ng) or {}).items():
                am = AM_OF.get(bc)
                if am:
                    accg[am] = accg.get(am, 0) + val
            for am, sm in accg.items():
                res.setdefault(am, {}).setdefault("g120", {})[ng] = sm
        return (dates, res) if dates else (None, {})
    except Exception as e:
        logger.warning("Lấy xu hướng 3 chỉ số theo AM lỗi (bỏ qua drill): %s", str(e)[:120])
        return (None, {})


def _spark(vals, color, w=240, h=46, target=None, pad=6):
    """Đường xu hướng (SVG) RÕ NÉT: đường crisp 2px (non-scaling-stroke, không méo khi kéo giãn)
    + vùng tô gradient dưới đường + điểm cuối nổi bật. target = vạch mục tiêu. '' nếu <2 điểm."""
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

    pl = [(X(i), Y(v)) for i, v in enumerate(vals) if v is not None]
    pts = " ".join("%.1f,%.1f" % p for p in pl)
    area = ("M%.1f,%.1f " % pl[0] + " ".join("L%.1f,%.1f" % p for p in pl[1:])
            + " L%.1f,%.1f L%.1f,%.1f Z" % (pl[-1][0], h, pl[0][0], h))
    _spark._i = getattr(_spark, "_i", 0) + 1
    # Tiền tố NGẪU NHIÊN theo tiến trình → id gradient không trùng khi khối do tiến trình khác
    # (report_dashboard sinh khối watch) được NHÚNG cạnh sparkline của trang trực tiếp.
    if not getattr(_spark, "_pfx", None):
        import random
        _spark._pfx = "sg%04x" % random.randrange(0x10000)
    gid = "%s_%d" % (_spark._pfx, _spark._i)
    tline = ""
    if target is not None:
        ty = Y(target)
        tline = ("<line x1='0' y1='%.1f' x2='%d' y2='%.1f' stroke='rgba(167,139,250,.55)' "
                 "stroke-width='1' stroke-dasharray='4 4' vector-effect='non-scaling-stroke'/>" % (ty, w, ty))
    lx, ly = pl[-1]
    return ("<svg class='spk' viewBox='0 0 %d %d' width='100%%' height='%d' preserveAspectRatio='none'>"
            "<defs><linearGradient id='%s' x1='0' y1='0' x2='0' y2='1'>"
            "<stop offset='0' stop-color='%s' stop-opacity='.40'/>"
            "<stop offset='1' stop-color='%s' stop-opacity='0'/></linearGradient></defs>"
            "<path d='%s' fill='url(#%s)' stroke='none'/>%s"
            "<polyline points='%s' fill='none' stroke='%s' stroke-width='2' "
            "stroke-linecap='round' stroke-linejoin='round' vector-effect='non-scaling-stroke'/>"
            "<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' stroke='%s' stroke-width='5.5' "
            "stroke-linecap='round' vector-effect='non-scaling-stroke'/>"
            "<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' stroke='#fff' stroke-width='2' "
            "stroke-linecap='round' vector-effect='non-scaling-stroke' opacity='.92'/></svg>"
            % (w, h, h, gid, color, color, area, gid, tline, pts, color,
               lx, ly, lx, ly, color, lx, ly, lx, ly))


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


def _ai_hotspot(rows, bc_avg, now):
    """PHÂN TÍCH THÔNG MINH THEO KHUNG GIỜ (rule-based): chấm điểm từng bưu cục theo mức độ
    'đáng lo' phụ thuộc giờ trong ngày (sáng: tồn/gán·ra hàng muộn · chiều: tốc độ giao·%GTC ·
    tối: đơn chưa xong dồn sang mai · khuya: quá hạn), chọn ĐIỂM NÓNG #1 và sinh nhận xét +
    hành động. Trả HTML box <details>; '' nếu chưa đủ dữ liệu."""
    hr = now.hour
    if hr < 13:
        phase, urg = "sáng", 1.0
    elif hr < 17:
        phase, urg = "trưa-chiều", 1.6
    elif hr < 21:
        phase, urg = "chiều tối", 2.6
    else:
        phase, urg = "tối muộn", 3.5
    bc_avg = bc_avg or {}
    cands = []
    for r in rows:
        tot = r.get("total", 0)
        if tot < 30:                       # bỏ BC quá nhỏ (nhiễu)
            continue
        pc = _pct(r["gtc"], tot)
        if pc is None:
            continue
        backlog = r.get("backlog", 0)
        g120 = r.get("giao120h", 0)
        ot_t = ot_d = 0
        for d in r.get("drivers", []):
            ot_t += d.get("ot_tot", 0); ot_d += d.get("ot_done", 0)
        on_road = max(ot_t - ot_d, 0)
        late = _late_cnt(r); low = _low_cnt(r)
        yv = bc_avg.get(r["name"])
        avg_pc = _pct(yv[0], yv[1]) if (isinstance(yv, (list, tuple)) and len(yv) >= 2 and yv[1]) else None
        gtc_gap = max(0, 70 - pc)
        phong_gap = round(max(0.0, (avg_pc - pc)), 1) if avg_pc is not None else 0
        risk = on_road + backlog           # đơn chưa giao (áp lực cuối ngày)
        score = (risk * urg * (1 + gtc_gap / 40.0 + phong_gap / 25.0)
                 + g120 * urg * 0.8 + low * 25 * urg + late * 8 * urg)
        cands.append(dict(s=score, name=r["name"], am=AM_OF.get(r["name"]) or "(chưa phân AM)",
                          pc=pc, avg_pc=avg_pc, backlog=backlog, on_road=on_road, g120=g120,
                          low=low, late=late, tot=tot, gtc_gap=gtc_gap, phong_gap=phong_gap))
    cands = [c for c in cands if c["s"] > 0]
    if not cands:
        return ""
    cands.sort(key=lambda c: -c["s"])
    top = cands[:5]

    def _issues(b):
        pts = ["%%GTC <b>%d%%</b>%s" % (b["pc"], (" (dưới MT %dđ)" % b["gtc_gap"]) if b["gtc_gap"] > 0 else "")]
        if b["phong_gap"] > 2 and b["avg_pc"] is not None:
            pts.append("<b class='rk'>tụt %sđ phong độ</b>" % ("%g" % b["phong_gap"]).replace(".", ","))
        if b["on_road"]:
            pts.append("còn <b>%s</b> đơn giao" % _n(b["on_road"]))
        if b["backlog"]:
            pts.append("<b>%s</b> chưa gán" % _n(b["backlog"]))
        if b["g120"]:
            pts.append("<b class='rk'>%s</b> &gt;120h" % _n(b["g120"]))
        if b["low"]:
            pts.append("<b>%d</b> NV&lt;50%%" % b["low"])
        if b["late"]:
            pts.append("<b>%d</b> XP muộn" % b["late"])
        return " · ".join(pts)

    rk_icon = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    rrows = []
    for i, b in enumerate(top):
        rrows.append("<div class='airow'><span class='airk'>%s <b>%s</b> <i>· AM %s · %s gán</i></span>"
                     "<div class='aiis'>%s</div></div>"
                     % (rk_icon[i], _esc(b["name"]), _esc(b["am"]), _n(b["tot"]), _issues(b)))
    b = top[0]
    nbc = len(top)
    left = _n(b["on_road"] + b["backlog"])
    if phase == "sáng":
        act = ("Sáng còn tồn/chưa gán → đốc <b>%s</b> gán chuyến%s, bám ra hàng sớm (ưu tiên #1 <b>%s</b>)."
               % (_esc(b["am"]), (" + giục NV ra hàng muộn") if b["late"] else "", _esc(b["name"])))
    elif phase == "trưa-chiều":
        act = ("Giờ cao điểm → dồn lực %d BC trên tăng tốc giao, kéo %%GTC; nặng nhất <b>%s</b> (%s đơn chưa xong)."
               % (nbc, _esc(b["name"]), left))
    elif phase == "chiều tối":
        act = ("Chiều tối %d BC còn nhiều đơn chưa xong → NGUY CƠ dồn sang mai, ưu tiên <b>%s</b> (%s đơn) NGAY."
               % (nbc, _esc(b["name"]), left))
    else:
        act = ("Tối muộn → chốt backlog %d BC trên, nặng nhất <b>%s</b>, kế hoạch xử lý sớm mai."
               % (nbc, _esc(b["name"])))
    head = ("🤖 <b>Phân tích Top %d bưu cục điểm nóng nhất vùng TBB</b> · %s %dh" % (nbc, phase, hr))
    body = "<div class='aibd'>%s<div class='airow aiact'>👉 %s</div></div>" % ("".join(rrows), act)
    return "<details class='aidx'><summary>%s<span class='dcv'>▾</span></summary>%s</details>" % (head, body)


def _drv_table(drv):
    """Bảng nhân viên của 1 bưu cục. Mỗi NV BẤM MỞ được → hàng con %GTC theo xã/phường
    (từ d['wards'] gộp lúc bóc chuyến, 0 call thêm). NV không có đơn giao → không mở."""
    if not drv:
        return "<div class='none'>Chưa có chuyến hôm nay.</div>"
    P = ["<table class='drv'><thead><tr><th>Nhân viên</th><th>Gán</th><th>GTC</th>"
         "<th>LTC</th><th>%GTC</th><th>So TB7</th></tr></thead><tbody>"]
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
        # TÊN NV tô màu theo %GTC (giống bưu cục): đỏ<60 · vàng<70 · xanh≥70
        nmc = "nmc " + _cls(pc2)
        # %GTC: chip TikTok (nếu có) ĐỨNG TRƯỚC pill → pill LUÔN sát phải cột = THẲNG HÀNG mọi dòng.
        P.append("<tr%s><td class='nv'>%s<span class='%s'>%s</span>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                 "<td class='pgc'>%s<span class='pill sm %s'>%s%%</span></td><td>%s</td></tr>"
                 % (attr, cx, nmc, _esc(d["name"]), lb, _n(d["total"]), _n(d["gtc"]),
                    ltc_cell, _tt_inline(d.get("vngh_gtc", 0), d.get("vngh", 0)),
                    _cls(pc2), pc2 if pc2 is not None else "—", _nv_cmp_cell(d)))
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


def _bc_drv_details(r, arrow="", pos=None):
    """1 bưu cục dạng <details> LỒNG (bấm mở ra bảng nhân viên) — dùng trong mục AM/tỉnh.
    arrow = mũi tên So TB7 (_gtc_arrow) truyền từ gen_html → BC con đồng bộ & THẲNG CỘT với
    thẻ AM/BC cấp đầu và bảng NV (pill %GTC + So TB7 cùng cụm .bcmx + có thanh tiến độ).
    pos = vị trí 0→1 trong danh sách (0=đầu) → nền sáng GIẢM DẦN xuống dưới, tăng tương phản số."""
    pc = _pct(r["gtc"], r["total"])
    cls = _cls(pc)
    _st = (" style='--pos:%.3f'" % max(0.0, min(1.0, pos))) if pos is not None else ""
    P = ["<details class='bc sub %s'%s><summary>" % (cls, _st)]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
             "<span class='bcmx'><span class='pill %s'>%s%%</span>%s</span></div>"
             % (cls, cls, _esc(r["name"]), cls, pc if pc is not None else "—", arrow))
    P.append(_bar(pc, cls))
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


def _agg_lgt_weight(d, types, key="total_weight"):
    """Cộng `key` (mặc định total_weight gram; 'total_order' = SỐ ĐƠN) theo order_type từ response
    get-general-info / get-backlog-transport-info. Nhiều hub → chỉ entry 'ALL'; 1 hub → entry đó."""
    data = (d or {}).get("data") or []
    alle = [e for e in data if str(e.get("hub_id") or "") == "ALL"]
    use = alle if alle else data
    agg = {t: 0 for t in types}
    for e in use:
        for gi in (e.get("general_infos") or []):
            ot = gi.get("order_type")
            if ot in agg:
                agg[ot] += gi.get(key) or 0
    return agg


async def fetch_lgt_weight(session, hub_ids, token):
    """KHỐI LƯỢNG (kg thực, gram) TỒN ĐỌNG toàn vùng → {lay, giao, tra, lc_giao, lc_tra}.
    2 CALL gộp hết hub_ids (≤200): get-general-info (Lấy/Giao/Trả; Giao=DELIVER+DELIVER_PRIORITY)
    + get-backlog-transport-info (Luân chuyển giao=TRANSPORT_DELIVERY · trả=TRANSPORT_RETURN).
    Lỗi từng call → bỏ qua phần đó. None nếu không có gì."""
    hids = [str(h) for h in hub_ids]
    hid0 = hids[0] if hids else "1"
    out = {"lay": 0, "giao": 0, "tra": 0, "lc_giao": 0, "lc_tra": 0}
    try:
        d = await _post(session, "/core/oss/v1/report/get-general-info",
                        {"hub_ids": hids, "view_mode": "WARD", "order_type": "ALL"}, hid0, token)
        _dt = ("PICK", "DELIVER", "DELIVER_PRIORITY", "RETURN")
        a = _agg_lgt_weight(d, _dt)
        out["lay"] = a["PICK"]; out["giao"] = a["DELIVER"] + a["DELIVER_PRIORITY"]; out["tra"] = a["RETURN"]
        c = _agg_lgt_weight(d, _dt, "total_order")   # SỐ ĐƠN tồn kho (hiển thị cạnh kg)
        out["n_lay"] = c["PICK"]; out["n_giao"] = c["DELIVER"] + c["DELIVER_PRIORITY"]; out["n_tra"] = c["RETURN"]
    except Exception:
        pass
    try:
        d2 = await _post(session, "/core/oss/v1/report/get-backlog-transport-info",
                         {"hub_ids": hids}, hid0, token)
        _lt = ("TRANSPORT_DELIVERY", "TRANSPORT_RETURN")
        a2 = _agg_lgt_weight(d2, _lt)
        out["lc_giao"] = a2["TRANSPORT_DELIVERY"]; out["lc_tra"] = a2["TRANSPORT_RETURN"]
        c2 = _agg_lgt_weight(d2, _lt, "total_order")
        out["n_lc_giao"] = c2["TRANSPORT_DELIVERY"]; out["n_lc_tra"] = c2["TRANSPORT_RETURN"]
    except Exception:
        pass
    return out if any(out.values()) else None


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
                # Tồn CHƯA GÁN giao = Giao thường (DELIVER) + ƯU TIÊN giao (DELIVER_PRIORITY),
                # đồng nhất quy ước "Giao = DELIVER + DELIVER_PRIORITY" dùng khắp hệ thống (khối lượng,
                # %Gán giao, đã gán). Trước đây chỉ đếm DELIVER → thiếu đơn ưu tiên giao.
                backlog = bl.get("deliver", 0) + bl.get("deliver_priority", 0)
                # ĐÃ GÁN giao còn TỒN (đang có chuyến đi trên trang Tồn đọng GHN) = ALL − NONE.
                dagan_giao = bl.get("deliver_assigned", 0)
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
                        "backlog_weight_g": backlog_weight, "dagan_giao": dagan_giao,
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
        # Khối lượng tồn Lấy/Giao/Trả toàn vùng — 1 call gộp (hero).
        lgt_w = await fetch_lgt_weight(session, [str(h["locationCode"]) for h in hubs], token)
        return rows, giao_120h, lgt_w


def _read_watch_chot():
    """Đọc khối 'Bưu cục cần theo dõi sâu sát' đã CHỐT 23h30 (report_dashboard ghi watch_chot.json)
    → trang trực tiếp hiển thị FROZEN cả ngày (KHÔNG cập nhật live). Thiếu file → ''."""
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    try:
        with open(os.path.join("docs", slug, "watch_chot.json"), encoding="utf-8") as f:
            w = json.load(f)
        if w.get("html"):
            return ("<div class='wchot-hdr'><span class='hvi'>📌</span> Chốt <b>23h30 · %s</b> · giữ nguyên cả ngày</div>%s"
                    % (_esc(str(w.get("date", ""))), w["html"]))
    except Exception:
        pass
    return ""


def gen_html(rows, giao_120h=None, nv_xuly=None, nvm=None, collectable=None, trend=None,
             g120_trend=None, cx_trend=None, pt_trend=None, nvdat_trend=None, bcm=None,
             fc_uplift=None, pace=None, cmp_y=None, bc_avg=None, lgt_w=None, nv_avg=None,
             noon12_trend=None, am_ctrend=None):
    now = datetime.now(VN)
    # SO CÙNG GIỜ HÔM QUA / TB 7 NGÀY chỉ hiện từ ~10h sáng (ENV CMP_MIN_HOUR): trước đó %GTC
    # luỹ kế biến động mạnh do 'sóng gán đơn' đầu ngày (mẫu số nhảy vọt) → so sánh dễ hiểu nhầm.
    # Tắt sớm = dòng ⏱, chip dải .sd, mũi tên thẻ .ga đều ẩn tới khi đủ giờ.
    if now.hour < float(os.environ.get("CMP_MIN_HOUR", "10")):
        cmp_y = None
        bc_avg = None
        nv_avg = None
    globals()["_NV_AVG"] = nv_avg or {}   # mũi tên NV trong _drv_table đọc từ đây
    R = {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "ltb": 0,
         "vngh": 0, "vngh_gtc": 0, "cod_gtb": 0, "kien": 0, "kien_gtc": 0, "weight_g": 0,
         "dagan_giao": 0}
    prov = {}
    am = {}
    for r in rows:
        for k in R:
            R[k] += r.get(k, 0)
        p = prov.setdefault(r["prov"], {"backlog": 0, "ontrip": 0, "fin": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "cod_gtb": 0, "kien": 0, "vngh": 0, "vngh_gtc": 0, "dagan_giao": 0})
        for k in ("backlog", "ontrip", "fin", "gtc", "att", "total", "ltc", "cod_gtb", "kien", "vngh", "vngh_gtc", "dagan_giao"):
            p[k] += r.get(k, 0)
        amn = AM_OF.get(r["name"])
        if amn:
            a = am.setdefault(amn, {"bc": 0, "backlog": 0, "gtc": 0, "att": 0, "total": 0, "ltc": 0, "cod_gtb": 0, "kien": 0, "vngh": 0, "vngh_gtc": 0, "dagan_giao": 0})
            a["bc"] += 1
            for k in ("backlog", "gtc", "att", "total", "ltc", "cod_gtb", "kien", "vngh", "vngh_gtc", "dagan_giao"):
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
             "family=Geist:wght@400;500;600;700;800;900&family=Geist+Mono:wght@500;600;700;800&display=swap'>")
    P.append("<title>TBB trực tiếp · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    if nvm is not None:
        P.append("<style>%s</style>" % nvm["css"])
    if bcm is not None:
        P.append("<style>%s</style>" % bcm["css"])
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
        # Buổi sáng: hverd CHỈ là trạng thái 'đang luỹ kế' (không lặp số dự báo) — DỰ PHÓNG chốt
        # ~X% nay nằm GỌN trong dòng NHỊP ĐỘ bên dưới, tránh 2 ô cùng nói '~X%'. (gộp 08/10)
        vk, vic, vst = "neu", "⏳", "ĐANG LUỸ KẾ TRONG NGÀY"
        vsub = ("Còn sớm — %%GTC luỹ kế sẽ tăng dần%s · xem nhịp độ & việc cần làm bên dưới"
                % (" · hôm qua chốt %d%%" % yp if yp is not None else ""))
    elif reg_pct is not None and reg_pct >= 70:
        vk, vic, vst, vsub = "good", "🟢", "ĐẠT MỤC TIÊU", "Vượt/đạt mốc 70% — giữ nhịp"
    elif reg_pct is not None and reg_pct >= 60:
        vk, vic, vst = "warn", "🟡", "CẦN CHÚ Ý"
        vsub = "Dưới mục tiêu · còn %d điểm tới 70%%" % (70 - reg_pct)
    else:
        vk, vic, vst = "bad", "🔴", "DƯỚI MỤC TIÊU"
        vsub = "Cần đốc gấp · còn %d điểm tới 70%%" % (70 - (reg_pct or 0))
    # (Ô đèn trạng thái riêng đã BỎ 04/10 — đưa thành DẢI MỎNG đánh giá trong Hero, tránh trùng số %GTC)

    # ===== CỜ RỦI RO VẬN HÀNH (ưu tiên 1, 08/10) — verdict tổng quát hơn %GTC =====
    # Gắn cảnh báo khi TẮC GÁN (chưa gán cao bất thường) hoặc BACKLOG 120h tăng mạnh so CÙNG GIỜ
    # HÔM QUA (cmp_y). Chỉ hiện khi có số hôm qua (gate 10h); baseline nhỏ → bỏ (tránh nhiễu).
    _oprisk = []
    if cmp_y:
        _ycg = (cmp_y.get("snap") or {}).get("chua_gan")
        if _ycg and _ycg >= 500 and R["backlog"] > _ycg * 1.20:
            _oprisk.append("TẮC GÁN ↑%d%%" % round((R["backlog"] - _ycg) * 100.0 / _ycg))
        _yg = cmp_y.get("g120")
        if _yg and _yg >= 100 and giao_120h is not None and giao_120h > _yg * 1.15:
            _oprisk.append("Backlog 120h ↑%d%%" % round((giao_120h - _yg) * 100.0 / _yg))

    # ===== Hero %GTC + dải đánh giá + đường xu hướng 14 ngày (chốt cuối ngày) =====
    P.append("<section class='hero %s'>" % _cls(reg_pct))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG · TỚI HIỆN TẠI</div>")
    # SỐ %GTC là TÂM ĐIỂM: số rất to + badge trạng thái bên cạnh (ĐẠT / CẦN CHÚ Ý / DƯỚI MỤC TIÊU).
    # Tồn kho (kg thực) tách xuống KHỐI RIÊNG bên dưới để không tranh chỗ với số %GTC.
    _hcls = _cls(reg_pct)
    _hmap = {"good": ("🟢", "ĐẠT MỤC TIÊU"), "warn": ("🟡", "CẦN CHÚ Ý"),
             "bad": ("🔴", "DƯỚI MỤC TIÊU"), "na": ("⚪", "—")}
    _hic, _hst = _hmap.get(_hcls, _hmap["na"])
    P.append("<div class='htop'>")
    P.append("<div class='hpct'>%s<span>%%</span></div>"
             % (reg_pct if reg_pct is not None else "—"))
    # Gộp mốc tham chiếu vào badge cho THỐNG NHẤT: mục tiêu 70% · hôm qua · TB7 · TB30.
    _href2 = "<span class='hrf'>🎯 MT <b>70%</b></span>"
    if ref:
        _href2 += "<span class='hrf'>Hôm qua <b>%d%%</b></span>" % ref["yp"]
        _href2 += "<span class='hrf'>TB%d <b>%d%%</b></span>" % (ref["ndays"], ref["avg7p"])
        if ref.get("avg30p") is not None and ref.get("ndays30", 0) > ref["ndays"]:
            _href2 += "<span class='hrf'>TB%d <b>%d%%</b></span>" % (ref["ndays30"], ref["avg30p"])
    P.append("<div class='hstat %s'><div class='hstt'><span class='hsi'>%s</span>"
             "<b>%s</b></div><div class='hstr'>%s</div></div>"
             % (_hcls, _hic, _hst, _href2))
    P.append("</div>")
    P.append(_bar(reg_pct, _cls(reg_pct), target=70))
    # 🎯 3 CHỈ SỐ ĐIỀU HÀNH — đặt NGAY dưới thanh %GTC (nổi bật nhất, điều hành số vùng).
    # Dùng SLOT: giữ chỗ ở đây, nội dung dựng bên dưới (sau khi đủ biến trend/cmp) rồi ghi vào slot.
    _htri_slot = len(P)
    P.append("")
    # Dòng số liệu phụ (Giao TC/LTC/%Gán giao) — xuống DƯỚI 3 chỉ số điều hành.
    # %Gán giao = ĐÃ GÁN GIAO (đang có chuyến, còn tồn) / TỔNG đơn cần giao (= đã gán + tồn chưa gán).
    # Nguồn trang Tồn đọng GHN: đã gán = ALL − NONE (R["dagan_giao"]); tồn chưa gán = NONE (R["backlog"]).
    # KHÔNG dùng R["total"] (gồm cả đơn đã giao xong → to hơn, không khớp 'đang có chuyến đi' của GHN).
    # Tổng đơn CẦN GIAO = đã gán giao (đang có chuyến, R["dagan_giao"]) + tồn chưa gán (R["backlog"]).
    _dg_giao = R["dagan_giao"]
    _tcg = R["dagan_giao"] + R["backlog"]
    _pg = (_dg_giao / _tcg * 100.0) if _tcg else 0.0
    _pgs = ("%.1f%%" % _pg).replace(".", ",")   # %gán = đang giao / tổng cần giao (ngữ cảnh nhỏ)
    P.append("<div class='hsc'>"
             "<div class='hsx good'><span>✅ Giao TC</span><b>%s</b>"
             "<span class='hfr'>/%s đã gán</span></div>"
             "<div class='hsx'><span>🔁 LTC</span><b>%s</b>"
             "<span class='hfr%s'>📦 LTB %s</span></div>"
             "<div class='hsx'><span>📦 Cần giao</span><b>%s</b>"
             "<span class='hfr'>%s đã gán</span></div></div>"
             % (_n(R["gtc"]), _n(R["total"]), _n(R["ltc"]),
                (" bad" if R["ltb"] else ""), _n(R["ltb"]),
                _n(_tcg), _pgs))
    # ⚖️ TỒN KHO (kg thực) — khối riêng, gọn, DƯỚI dòng số liệu
    if lgt_w:
        _ysnap = (cmp_y.get("snap") or {}) if cmp_y else {}   # khối lượng tồn kho CÙNG GIỜ HÔM QUA

        def _wi(icon, lbl, key, ykey):
            _ncnt = lgt_w.get("n_" + key, 0)
            _ns = ("<span class='hwn'>%s đơn</span>" % _n(_ncnt)) if _ncnt else ""
            return ("<div class='hwi'><span>%s %s</span><b>%s</b>%s%s</div>"
                    % (icon, lbl, _kgfmt(lgt_w.get(key, 0) / 1000.0), _ns,
                       _w_arrow(lgt_w.get(key, 0), _ysnap.get(ykey))))
        _lc = ""
        if lgt_w.get("lc_giao") or lgt_w.get("lc_tra"):
            _lc = _wi("🔁", "LC giao", "lc_giao", "w_lcg") + _wi("🔁", "LC trả", "lc_tra", "w_lct")
        P.append("<div class='hwh'><div class='hwh-t'>⚖️ Tồn kho <i>(kg thực)</i></div>"
                 "<div class='hwh-g'>%s%s%s%s</div></div>"
                 % (_wi("📥", "Lấy", "lay", "w_lay"), _wi("🚚", "Giao", "giao", "w_giao"),
                    _wi("↩️", "Trả", "tra", "w_tra"), _lc))

    # ===== 3 CHỈ SỐ ĐIỀU HÀNH (ngay dưới số %GTC): %GTC · Backlog>120h · Tồn chưa gán giao =====
    # Mỗi card = SỐ LIVE bây giờ (KHỚP tile bên dưới) + so cùng giờ HÔM QUA + đường 14 ngày chốt +
    # vạch mục tiêu. Đồng bộ: số to trên card = số live, không lệch với tile Tổng quan vận hành.
    _hq_pct = cmp_y.get("pct") if cmp_y else None
    _hq_g120 = cmp_y.get("g120") if cmp_y else None
    _hq_cg = (cmp_y.get("snap") or {}).get("chua_gan") if cmp_y else None

    def _cmp_hq(live, yest, higher_better, pct=False):
        """So LIVE vs cùng giờ hôm qua → (class good/bad, chữ '▲/▼ Δ vs HQ'). '' nếu thiếu số."""
        if live is None or yest is None:
            return ("", "")
        d = live - yest
        if abs(d) < (0.05 if pct else 0.5):
            return ("cfl", "= HQ")
        up = d > 0
        good = up if higher_better else (not up)
        dv = ("%g" % round(abs(d), 1)).replace(".", ",") if pct else _n(round(abs(d)))
        return ("cok" if good else "cbad", "%s%s vs HQ" % ("▲" if up else "▼", dv))

    def _st_pct(p):
        return "na" if p is None else "gok" if p >= 70 else "gwarn" if p >= 60 else "gbad"

    def _trim_lead(xs):
        i = next((k for k, x in enumerate(xs) if x is not None), None)
        return xs[i:] if i is not None else xs

    def _am_drill(field, hb, tgt, color):
        """Bấm 1 card → biểu đồ 14 ngày của TỪNG AM cho chỉ số đó (xếp tệ→tốt)."""
        if not (am_ctrend and am_ctrend[0]):
            return ""
        adates, amap = am_ctrend
        rr = []
        for am, dd in amap.items():
            ser = [dd.get(field, {}).get(x) for x in adates]
            last = next((x for x in reversed(ser) if x is not None), None)
            if last is not None:
                rr.append((am, last, ser))
        if not rr:
            return ""
        rr.sort(key=lambda r: r[1], reverse=(not hb))     # tệ nhất lên đầu
        P2 = ["<div class='amdr'><div class='amdr-h'>📊 Theo AM · 14 ngày (tệ → tốt) · ▲▼ so hôm qua</div>"]
        for am, last, ser in rr:
            vv = ("%s%%" % _n(round(last))) if field == "pct" else _n(round(last))
            _nn2 = [x for x in ser if x is not None]       # so NGÀY mới nhất vs ngày trước đó
            _cc, _ct = _cmp_hq(last, (_nn2[-2] if len(_nn2) >= 2 else None), hb, pct=(field == "pct"))
            _ar = ("<span class='amda %s'>%s</span>" % (_cc, _ct.replace(" vs HQ", ""))) if _ct else ""
            sp = _spark(_trim_lead(ser), color, w=320, h=28, target=tgt)
            P2.append("<div class='amdrow'><div class='amdn'><span class='amnm'>%s</span>"
                      "<b>%s</b>%s</div><div class='amdk'>%s</div></div>"
                      % (_esc(am), vv, _ar, sp))
        P2.append("</div>")
        return "".join(P2)

    _tri = []
    # 1) %GTC — live reg_pct (khớp số to Hero), đường 14 ngày chốt, MT 70
    _pc = [t["pct"] for t in trend][-14:] if (trend and len(trend) >= 2) else None
    _c1, _t1 = _cmp_hq(reg_pct, _hq_pct, True, pct=True)
    _tri.append(("🎯 %GTC", ("%s%%" % _n(round(reg_pct))) if reg_pct is not None else "—",
                 _spark(_pc, "#fbbf24", w=300, h=40, target=70) if _pc else "",
                 "⌁ MT 70%", _st_pct(reg_pct), _c1, _t1, "pct", True, 70, "#fbbf24"))
    # 2) Backlog >120h — live giao_120h (khớp tile), đường 14 ngày, MT 500
    _g = g120_trend[-14:] if (g120_trend and len(g120_trend) >= 2) else None
    _c2, _t2 = _cmp_hq(giao_120h, _hq_g120, False)
    _st2 = "na" if giao_120h is None else ("gok" if giao_120h <= 500 else "gbad")
    _tri.append(("🔴 Giao &gt;120h", _n(giao_120h) if giao_120h is not None else "—",
                 _spark(_g, "#fb7185", w=300, h=40, target=500) if _g else "",
                 "⌁ MT 500", _st2, _c2, _t2, "g120", False, 500, "#fb7185"))
    # 3) Tồn chưa gán tại MỐC 12h (để ĐÁNH GIÁ — chuẩn như xếp hạng/theo dõi sâu sát): giá trị =
    #    12h HÔM NAY, so 12h HÔM QUA, đường 14 ngày, MT 3.000. (noon12_trend gồm hôm nay nếu >12h.)
    _nn = noon12_trend[-14:] if (noon12_trend and len(noon12_trend) >= 2) else None
    _nv = _nn[-1] if _nn else None
    _nprev = _nn[-2] if (_nn and len(_nn) >= 2) else None
    _c3, _t3 = _cmp_hq(_nv, _nprev, False)
    _st3 = "na" if _nv is None else ("gok" if _nv <= 3000 else "gbad")
    _tri.append(("⏳ Chưa gán 12h", _n(_nv) if _nv is not None else "—",
                 _spark(_nn, "#f5aa17", w=300, h=40, target=3000) if _nn else "",
                 "⌁ MT 3.000", _st3, _c3, _t3, "noon", False, 3000, "#f5aa17"))
    if _tri:
        _cells = "".join(
            "<details class='htl %s'><summary><div class='htll'>%s<span class='htcar'>▾</span></div>"
            "<div class='htlv'>%s</div>%s"
            "<div class='htlf'>%s<span class='htlt'>%s</span></div></summary>%s</details>"
            % (gc, l, v, ("<div class='htlk'>%s</div>" % s) if s else "<div class='htlk'></div>",
               ("<span class='htld %s'>%s</span>" % (cc, ct)) if ct else "", mt,
               _am_drill(fld, hb, tgt, col))
            for l, v, s, mt, gc, cc, ct, fld, hb, tgt, col in _tri)
        P[_htri_slot] = ("<div class='htri'><div class='htri-h'>🎯 3 CHỈ SỐ ĐIỀU HÀNH VÙNG "
                         "<span class='htri-s'>· số LIVE · bấm xem từng AM 14 ngày</span></div>"
                         "<div class='htri-g'>%s</div></div>" % _cells)

    # DÒNG CẢNH BÁO RỦI RO VẬN HÀNH — CHỈ hiện khi có rủi ro (tắc gán / backlog tăng bất thường).
    # (Bỏ dòng đánh giá thường ngày '🟡 CẦN CHÚ Ý · còn X điểm' 08/10 — trùng số %GTC + bar + chips.)
    if _oprisk:
        P.append("<div class='hverd bad'><span class='hvi'>⚠️</span> "
                 "<b>RỦI RO VẬN HÀNH</b> · %s</div>" % " · ".join(_oprisk))

    # ===== ⏱ SO CÙNG GIỜ HÔM QUA — đánh giá vùng theo mốc giờ (07/10) =====
    # So số HÔM NAY (live) với bản ghi bao_cao_gio của HÔM QUA tại GIỜ này. %GTC có ngay;
    # Đã gán/GTC/Backlog đầy đủ từ ngày bắt đầu log cột (hôm qua chưa có → tự ẩn chỉ số đó).
    if cmp_y and reg_pct is not None:
        _ch = now.hour

        def _dpp(td, yd):  # chênh ĐIỂM phần trăm (percentage points) cho %GTC
            d = round(td - yd, 1)
            s = ("+%s" % d if d > 0 else ("%s" % d if d < 0 else "±0")).replace(".", ",")
            return d, s

        def _dpc(td, yd):  # chênh % tương đối cho số đếm
            if not yd:
                return 0, "±0%"
            d = round((td - yd) * 100.0 / abs(yd))
            return d, ("+%d%%" % d if d > 0 else ("%d%%" % d if d < 0 else "±0%"))

        parts = []
        # MÀU (user 19/10): Đã gán/GTC/Backlog → TĂNG=ĐỎ (dn)·GIẢM=XANH (up). %GTC GIỮ KIỂU CHẤT LƯỢNG.
        def _dir(d):
            return "dn" if d > 0 else ("up" if d < 0 else "fl")
        # Mỗi chỉ số = 1 CHIP (nhãn nhỏ · số to · delta màu) → lướt nhanh trên iPhone.
        # %GTC (chênh điểm) — TĂNG=XANH (tốt) · GIẢM=ĐỎ (xấu).
        if "pct" in cmp_y:
            d, s = _dpp(reg_pct, cmp_y["pct"])
            _cp = "up" if d > 0 else ("dn" if d < 0 else "fl")
            parts.append("<span class='scq'><i>%%GTC</i><b>%d%%</b><span class='cq %s'>%s đ</span></span>"
                         % (reg_pct, _cp, s))
        if "total" in cmp_y:
            d, s = _dpc(R["total"], cmp_y["total"])
            parts.append("<span class='scq'><i>Đã gán</i><b>%s</b><span class='cq %s'>%s</span></span>"
                         % (_n(R["total"]), _dir(d), s))
        if "gtc" in cmp_y:
            d, s = _dpc(R["gtc"], cmp_y["gtc"])
            parts.append("<span class='scq'><i>GTC</i><b>%s</b><span class='cq %s'>%s</span></span>"
                         % (_n(R["gtc"]), _dir(d), s))
        if "g120" in cmp_y and giao_120h is not None:
            d, s = _dpc(giao_120h, cmp_y["g120"])
            parts.append("<span class='scq'><i>Backlog</i><b>%s</b><span class='cq %s'>%s</span></span>"
                         % (_n(giao_120h), _dir(d), s))
        _miss = "" if ("total" in cmp_y and "gtc" in cmp_y and "g120" in cmp_y) else \
                " · <i>(Đã gán/GTC/Backlog đủ từ mai)</i>"
        # CHI TIẾT THEO AM (bấm dòng ⏱ để xổ): %GTC + Đã gán + GTC từng AM vs cùng giờ hôm qua.
        _ybc_h = cmp_y.get("bcsnap") if isinstance(cmp_y.get("bcsnap"), dict) else None
        _am_tbl = ""
        if _ybc_h:
            _yam_h = {}
            for _bc, _gt in _ybc_h.items():
                if not (isinstance(_gt, (list, tuple)) and len(_gt) >= 2):
                    continue
                _a = AM_OF.get(_bc)
                if _a:
                    z = _yam_h.setdefault(_a, [0, 0, 0])
                    z[0] += _gt[0]; z[1] += _gt[1]
                    z[2] += (_gt[3] if len(_gt) >= 4 else 0)   # đã gán đang có chuyến (hôm qua)
            _rows_am = []
            for _amn, _v in am.items():
                _yv = _yam_h.get(_amn)
                if not _yv:
                    continue
                _tp = _pct(_v["gtc"], _v["total"]); _yp = _pct(_yv[0], _yv[1])
                if _tp is None or _yp is None:
                    continue
                _rows_am.append((_amn, _v, _tp, _yp, _yv))
            _rows_am.sort(key=lambda x: (x[2] - x[3]))   # %GTC tụt nhiều nhất → đầu

            def _cqd(d, s, good_up=True, neutral=False, suffix=""):
                cc = "nt" if neutral else ("fl" if d == 0 else ("up" if (d > 0) == good_up else "dn"))
                return "<span class='cq %s'>%s%s</span>" % (cc, s, suffix)

            if _rows_am:
                _R2 = ["<table class='amtab'><thead><tr><th class='aml'>AM</th><th>%GTC</th>"
                       "<th>Đã gán</th><th>GTC</th></tr></thead><tbody>"]
                for _amn, _v, _tp, _yp, _yv in _rows_am:
                    _dp, _sp = _dpp(_tp, _yp)
                    _dg, _sg = _dpc(_v["total"], _yv[1])            # 'đã gán' chi tiết AM giữ nguyên (luỹ kế)
                    _dv, _sv = _dpc(_v["gtc"], _yv[0])
                    _R2.append("<tr><td class='aml'>%s</td><td><b>%s%%</b> %s</td>"
                               "<td>%s %s</td><td>%s %s</td></tr>"
                               % (_esc(_amn), ("%g" % _tp).replace(".", ","),
                                  _cqd(_dp, _sp, True, suffix="đ"),
                                  _n(_v["total"]), _cqd(_dg, _sg, False),
                                  _n(_v["gtc"]), _cqd(_dv, _sv, False)))
                _R2.append("</tbody></table>")
                _am_tbl = "".join(_R2)

        if parts:
            _hdr = ("<div class='hcmp-h'><span class='hvi'>⏱</span> <b>SO CÙNG GIỜ HÔM QUA · %dh</b>%s</div>"
                    % (_ch, _miss))
            _chips = "<div class='scqrow'>%s</div>" % "".join(parts)
            if _am_tbl:
                P.append("<details class='hcmp cx'><summary>%s%s<span class='hcv'>▾ chi tiết AM</span></summary>"
                         "<div class='amwrap'><div class='amnote'>Chi tiết từng AM · so cùng giờ "
                         "hôm qua · %%GTC tụt nhiều nhất lên đầu</div>%s</div></details>"
                         % (_hdr, _chips, _am_tbl))
            else:
                P.append("<div class='hcmp'>%s%s</div>" % (_hdr, _chips))

    # ⚡ ĐIỂM NÓNG CHÚ Ý — chèn vào ĐÂY (TRONG hero, ngay dưới ⏱ so cùng giờ). Nội dung tính ở
    # dưới (cần am_pcts…) rồi ghi vào P[_diag_slot]. (Chuyển từ trên lưới vào hero 08/10.)
    _diag_slot = len(P)
    P.append("")
    # 🤖 PHÂN TÍCH ĐIỂM NÓNG theo khung giờ (rule-based) — ngay dưới Điểm Nóng, từ ~10h.
    if now.hour >= float(os.environ.get("CMP_MIN_HOUR", "10")):
        _aibox = _ai_hotspot(rows, bc_avg, now)
        if _aibox:
            P.append(_aibox)

    # (4 chip tham chiếu Mục tiêu/Hôm qua/TB7/TB30 ĐÃ GỘP lên badge trạng thái cạnh số %GTC — bỏ ở đây cho gọn.)
    # 🔎 BƯU CỤC CẦN THEO DÕI SÂU SÁT — bản CHỐT 23h30 (frozen) — ĐƯA VÀO HERO (user 22/10) cho gọn.
    _wchot = _read_watch_chot()
    if _wchot:
        P.append(_wchot)
    P.append("</section>")

    # ===== 🎯 BẢNG ĐIỀU HÀNH NHANH (Dự báo về đích + Việc cần làm ngay) — chỉ hiện TỪ 14h =====
    # Dành cho GĐV điều hành buổi chiều: trái = dự phóng %GTC chốt + tốc độ + cần thêm/giờ;
    # phải = tóm tắt việc ưu tiên (AM yếu nhất, N BC/NV <50%, bưu cục tồn nặng nhất).
    if now.hour >= int(os.environ.get("HACT_MIN_HOUR", "14")) and R["total"] and reg_pct is not None:
        _fc = (reg_pct + fc_uplift) if (fc_uplift is not None) else None
        _fc_cls = "good" if (_fc is not None and _fc >= 70) else ("warn" if (_fc is not None and _fc >= 62) else "bad")
        _hleft = max(22 - (now.hour + now.minute / 60.0), 0.5)         # giờ còn lại tới ~22h
        _need = max((0.70 * R["total"] - R["gtc"]) / _hleft, 0)
        _pace_s = ("%d đơn/giờ" % round(pace)) if pace else "đang đo…"
        _fcb = []
        if _fc is not None:
            _fcb.append("<div class='hac-v %s'>~%d%%</div><div class='hac-s'>theo đà chốt cuối ngày</div>"
                        % (_fc_cls, round(_fc)))
        else:
            _fcb.append("<div class='hac-v'>…</div><div class='hac-s'>đang học dữ liệu (đủ sau vài ngày)</div>")
        _fcb.append("<div class='hac-l'>🚀 Tốc độ giao: <b>%s</b></div>" % _pace_s)
        _fcb.append("<div class='hac-l'>🎯 Cần <b>+%s đơn/giờ</b> để đạt 70%%</div>" % _n(int(round(_need))))
        # Việc cần làm ngay
        _am_lo = sorted(((k, _pct(v["gtc"], v["total"]), v) for k, v in am.items() if v["total"] >= 50),
                        key=lambda x: (x[1] if x[1] is not None else 999))
        _worst = _am_lo[0] if _am_lo else None
        _bc_lo = sum(1 for r in rows if r["total"] >= 20 and (_pct(r["gtc"], r["total"]) or 99) < 50)
        _nv_lo = sum(1 for r in rows for d in r.get("drivers", [])
                     if d.get("total", 0) >= 20 and (_pct(d.get("gtc", 0), d["total"]) or 99) < 50)
        _bl = max(rows, key=lambda r: r.get("backlog", 0)) if rows else None
        _act = []
        if _worst and _worst[1] is not None:
            _act.append("<div class='hac-l'>🔴 AM yếu nhất: <b>%s</b> <span class='bad'>%s%%</span></div>"
                        % (_esc(_worst[0]), ("%g" % _worst[1]).replace(".", ",")))
        _act.append("<div class='hac-l'>🏤 <b>%d</b> bưu cục &lt;50%% · 👤 <b>%d</b> NV cần đốc</div>"
                    % (_bc_lo, _nv_lo))
        if _bl and _bl.get("backlog", 0) > 0:
            _act.append("<div class='hac-l'>📦 Tồn nặng nhất: <b>%s</b> <span class='warn'>%s</span></div>"
                        % (_esc(_bl["name"]), _n(_bl["backlog"])))
        P.append("<div class='hact'>"
                 "<div class='hac fc %s'><div class='hac-h'>📈 DỰ BÁO VỀ ĐÍCH</div>%s</div>"
                 "<div class='hac act'><div class='hac-h'>⚡ VIỆC CẦN LÀM NGAY</div>%s</div></div>"
                 % (_fc_cls, "".join(_fcb), "".join(_act)))

    # ===== ⚡ CẦN LÀM NGAY — việc ưu tiên (Mẫu 1) =====
    nvx_n = nvm["n"] if nvm else (len(nv_xuly) if nv_xuly else 0)
    coll_nv = sum(len(us) for us in collectable.values()) if collectable else None
    coll_amt = sum(u["amount"] for us in collectable.values() for u in us) if collectable else 0
    cg_spark = g120_spark = cx_spark = tl_spark = tt_spark = ""
    if trend and len(trend) >= 2:
        cg_spark = _spark([t["chuagan"] for t in trend], "#34d399", w=60, h=22)
        _tl = [t.get("ton_lay") for t in trend]
        _tt = [t.get("ton_tra") for t in trend]
        if any(v is not None for v in _tl):
            tl_spark = _spark(_tl, "#f5aa17", w=60, h=22)
        if any(v is not None for v in _tt):
            tt_spark = _spark(_tt, "#f5aa17", w=60, h=22)
    if g120_trend and len(g120_trend) >= 2:
        g120_spark = _spark(g120_trend, "#fb7185", w=60, h=22)
    if cx_trend and len(cx_trend) >= 2:
        cx_spark = _spark(cx_trend, "#a78bfa", w=60, h=22)
    pt_spark = ""
    if pt_trend and len(pt_trend) >= 2:
        pt_spark = _spark(pt_trend, "#fbbf24", w=60, h=22)

    # ----- Delta ▲/▼ so NGÀY CHỐT TRƯỚC (2 điểm cuối trend: hôm qua vs hôm kia) -----
    def _dchip(vals, up_good):
        xs = [v for v in (vals or []) if v is not None]
        if len(xs) < 2:
            return ""
        prev = xs[-2]
        # Baseline quá nhỏ (chuỗi dữ liệu mới bắt đầu / gần 0) → % thay đổi vô nghĩa
        # (vd 455 vs 1 = 45.400%). Bỏ chip thay vì hiện số nổ.
        if abs(prev) < 5:
            return ""
        d = round((xs[-1] - prev) / abs(prev) * 100)
        if d == 0:
            return " <span class='dl fl'>▬</span>"
        up = d > 0
        cls = "up" if (up == up_good) else "dn"   # thay đổi TỐT = xanh · XẤU = đỏ
        if abs(d) > 300:            # % nổ (nền hôm qua nhỏ) → chữ thay vì số phi lý "999%"
            return " <span class='dl %s'>%s nhiều</span>" % (cls, "▲ tăng" if up else "▼ giảm")
        return " <span class='dl %s'>%s%d%%</span>" % (cls, "▲" if up else "▼", abs(d))

    _tr = trend or []
    _dc_g120 = _dchip(g120_trend, False)
    _dc_cg = _dchip([t.get("chuagan") for t in _tr], False)
    _dc_tl = _dchip([t.get("ton_lay") for t in _tr], False)
    _dc_tt = _dchip([t.get("ton_tra") for t in _tr], False)
    _dc_cx = _dchip(cx_trend, False)
    _dc_pt = _dchip(pt_trend, False)
    _dc_nvd = _dchip(nvdat_trend, True)
    _dc_sl = _dchip([t.get("don_giao") for t in _tr], True)
    _dc_kg = _dchip([t.get("weight_kg") for t in _tr], True)
    _dc_ttk = _dchip([t.get("tiktok_gtc") for t in _tr], True)

    # ----- Nội dung chi tiết (inner) của từng dòng — bung NGAY TẠI CHỖ khi bấm -----
    def _row(cls, icon, title, sub, spark, value, vstyle, inner, onclick="", href=""):
        """1 Ô LƯỚI (Phương án A). inner != '' → <details> bung full-chiều-ngang tại chỗ;
        href → ô dạng link. sub bỏ (ô gọn). spark co giãn đáy ô."""
        car = ("<span class='gtcar'>▸</span>" if inner
               else ("<span class='gtcar'>›</span>" if href else ""))
        body = ("<div class='gth'><span class='gti'>%s</span><span class='gtl'>%s</span>%s</div>"
                "<div class='gtv'%s>%s</div>%s" % (icon, title, car, vstyle, value, spark))
        if inner:
            return ("<details class='gt exp %s'><summary>%s</summary>"
                    "<div class='gtd'>%s</div></details>" % (cls, body, inner))
        if href:
            return "<a class='gt lnk %s' href='%s'>%s</a>" % (cls, href, body)
        return "<div class='gt %s' %s>%s</div>" % (cls, onclick, body)

    def _drill_am_bc(items_by_am, dotcls, pillcls, am_sub, col_header):
        """Dựng AM → bảng bưu cục (số đơn). items_by_am: {AM:[(bc,val)]}. Trả inner html."""
        if not items_by_am:
            return ""
        B = []
        for amn, bcs in sorted(items_by_am.items(), key=lambda kv: -sum(v for _, v in kv[1])):
            a_tot = sum(v for _, v in bcs)
            B.append("<details class='bc %s'><summary>"
                     "<div class='bch'><span class='dot %s'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill %s'>%s</span></div>"
                     "<div class='bcm'><span>%s</span></div></summary><div class='dtl'>"
                     % (dotcls, dotcls, _esc(amn), pillcls, _n(a_tot), am_sub % len(bcs)))
            B.append("<table class='drv'><thead><tr><th class='lft'>Bưu cục</th><th>%s</th></tr></thead><tbody>" % col_header)
            for bcn, v in sorted(bcs, key=lambda x: -x[1]):
                B.append("<tr><td class='nv'>%s</td><td><b class='w'>%s</b></td></tr>" % (_esc(bcn), _n(v)))
            B.append("</tbody></table></div></details>")
        return "".join(B)

    def _group_am(key):
        g = {}
        for r in rows:
            v = r.get(key, 0)
            if v <= 0:
                continue
            g.setdefault(AM_OF.get(r["name"]) or "(chưa phân AM)", []).append((r["name"], v))
        return g

    # Backlog 120h inner (AM → BC)
    _in_g120 = _drill_am_bc(_group_am("giao120h"), "bad", "bad", "%d bưu cục có đơn đỏ", "Backlog 120h")
    # Tồn chưa gán inner (AM → BC → xã)
    _in_cgd = ""
    cg_am = {}
    for r in rows:
        if r.get("backlog", 0) > 0:
            cg_am.setdefault(AM_OF.get(r["name"]) or "(chưa phân AM)", []).append(r)
    if cg_am:
        C = []
        for amn, brows in sorted(cg_am.items(), key=lambda kv: -sum(x["backlog"] for x in kv[1])):
            C.append("<details class='bc warn'><summary>"
                     "<div class='bch'><span class='dot warn'></span><span class='bcn'>🧑‍💼 %s</span>"
                     "<span class='pill warn'>%s</span></div>"
                     "<div class='bcm'><span>%d bưu cục còn tồn chưa gán giao</span></div>"
                     "</summary><div class='dtl'>" % (_esc(amn), _n(sum(x["backlog"] for x in brows)), len(brows)))
            for r in sorted(brows, key=lambda x: -x["backlog"]):
                wards = r.get("backlog_wards", [])
                C.append("<details class='bc sub warn'><summary>"
                         "<div class='bch'><span class='dot warn'></span><span class='bcn'>%s</span>"
                         "<span class='pill warn'>%s</span></div>"
                         "<div class='bcm'><span>🏘 %d tuyến xã/phường</span></div>"
                         "</summary><div class='dtl'>" % (_esc(r["name"]), _n(r["backlog"]), len(wards)))
                if wards:
                    C.append("<table class='drv'><thead><tr><th>Tuyến xã/phường</th><th>Đơn chưa gán</th></tr></thead><tbody>")
                    for wn, wc in wards:
                        C.append("<tr><td>%s</td><td><b>%s</b></td></tr>" % (_esc(wn), _n(wc)))
                    C.append("</tbody></table>")
                else:
                    C.append("<div class='note'>Không lấy được chi tiết tuyến (thử lại lần sau).</div>")
                C.append("</div></details>")
            C.append("</div></details>")
        _in_cgd = "".join(C)
    # Tồn Lấy / Tồn Trả inner
    _ton_lay = sum(r.get("ton_lay", 0) for r in rows)
    _ton_tra = sum(r.get("ton_tra", 0) for r in rows)
    _in_tld = _drill_am_bc(_group_am("ton_lay"), "warn", "warn", "%d bưu cục", "Số đơn")
    _in_ttd = _drill_am_bc(_group_am("ton_tra"), "warn", "warn", "%d bưu cục", "Số đơn")

    # (Điểm Nóng Chú Ý đã chuyển VÀO HERO 08/10 — placeholder _diag_slot nằm trong hero, dưới ⏱.)
    P.append("<div class='sectitle'>⚡ Tổng Quan Vận Hành Vùng TBB</div><section class='prilist'>")
    P.append(_row("bd", "🔴", "Backlog giao 120h", "bấm xem AM → bưu cục", g120_spark,
                  (_n(giao_120h) if giao_120h is not None else "—") + _dc_g120, "", _in_g120))
    P.append(_row("wn", "⏳", "Tồn chưa gán giao", "bấm xem AM → bưu cục → xã", cg_spark,
                  _n(R["backlog"]) + _dc_cg, "", _in_cgd))
    P.append(_row("wn", "🛒", "Tồn Lấy chưa gán", "đơn lấy chưa có chuyến · bấm xem AM → bưu cục", tl_spark,
                  _n(_ton_lay) + _dc_tl, "", _in_tld))
    P.append(_row("wn", "↩️", "Tồn Trả", "đơn trả tồn · bấm xem AM → bưu cục", tt_spark,
                  _n(_ton_tra) + _dc_tt, "", _in_ttd))
    # NV cần xử lý — bung TOÀN BỘ embed NGAY TẠI CHỖ (Supabase; lỗi → fallback NV<50% hôm nay)
    if nvm is not None:
        _in_nvx = nvm["html"]
    else:
        _lown = sorted([(d, r["name"]) for r in rows for d in r.get("drivers", [])
                        if d.get("total", 0) >= 30 and _pct(d["gtc"], d["total"]) is not None
                        and _pct(d["gtc"], d["total"]) < 50],
                       key=lambda x: (_pct(x[0]["gtc"], x[0]["total"]), -x[0].get("total", 0)))
        if _lown:
            T = ["<table class='drv'><thead><tr><th>Nhân viên · bưu cục</th><th>Đơn</th>"
                 "<th>Hỏng</th><th>%GTC</th></tr></thead><tbody>"]
            for d, bc in _lown[:20]:
                pc = _pct(d["gtc"], d["total"])
                T.append("<tr><td class='nv'>%s<div class='sc'>%s</div></td><td>%s</td>"
                         "<td><b class='w'>%s</b></td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _esc(bc), _n(d["total"]),
                            _n(d["total"] - d["gtc"]), _cls(pc), pc))
            T.append("</tbody></table>")
            _in_nvx = "".join(T)
        else:
            _in_nvx = ""
    P.append(_row("vi", "👤", "NV cần xử lý", "%GTC kém dai dẳng · bấm xem", cx_spark,
                  _n(nvx_n) + _dc_cx, " style='color:var(--bad)'", _in_nvx))
    if coll_nv is not None:
        P.append(_row("wn", "💵", "NV chưa nộp tiền", "", pt_spark, _n(coll_nv) + _dc_pt, "", "",
                      href="chuyendi.html"))
    # NV đạt GTC ≥50% — (sức khỏe đội ngũ)
    if nvdat_trend and len(nvdat_trend) >= 2:
        nv_dat = sum(1 for r in rows for d in r.get("drivers", [])
                     if d.get("total", 0) >= 20 and _pct(d["gtc"], d["total"]) is not None
                     and _pct(d["gtc"], d["total"]) >= 50)
        P.append(_row("", "🎯", "NV đạt GTC ≥50%", "", _spark(nvdat_trend, "#34d399", w=60, h=22),
                      _n(nv_dat) + _dc_nvd, " style='color:var(--good)'", ""))
    # Sản lượng giao/ngày + TikTok giao TC/ngày
    if trend and len(trend) >= 2:
        sl_don = [t.get("don_giao") for t in trend]
        sl_ttg = [t.get("tiktok_gtc") for t in trend]
        if any(v is not None for v in sl_don):
            # Bung AM → bưu cục → XÃ/PHƯỜNG: đơn ĐÃ GÁN + CHƯA GÁN + TỔNG.
            sl_am = {}
            for r in rows:
                if r.get("total", 0) > 0 or r.get("backlog", 0) > 0:
                    sl_am.setdefault(AM_OF.get(r["name"]) or "(chưa phân AM)", []).append(r)
            _in_sld = ""
            if sl_am:
                S = []
                for amn, brows in sorted(sl_am.items(), key=lambda kv: -sum(x.get("total", 0) for x in kv[1])):
                    a_dg = sum(x.get("total", 0) for x in brows)
                    a_cg = sum(x.get("backlog", 0) for x in brows)
                    S.append("<details class='bc'><summary>"
                             "<div class='bch'><span class='dot' style='background:#22d3ee'></span>"
                             "<span class='bcn'>🧑‍💼 %s</span></div>"
                             "<div class='bcm'><span>📦 đã gán <b class='ltc'>%s</b></span>"
                             "<span>⏳ chưa gán <b class='w'>%s</b></span>"
                             "<span>Σ tổng <b>%s</b></span></div>"
                             "</summary><div class='dtl'>" % (_esc(amn), _n(a_dg), _n(a_cg), _n(a_dg + a_cg)))
                    for r in sorted(brows, key=lambda x: -x.get("total", 0)):
                        b_dg = r.get("total", 0); b_cg = r.get("backlog", 0)
                        # Đã gán theo xã (gộp từ drivers' wards[xã][0]) + chưa gán theo xã (backlog_wards).
                        # CHUẨN HOÁ tên xã về phần gốc (bỏ đuôi ", Thành phố…/Huyện…") vì nguồn ĐÃ GÁN
                        # (get-trip-items) dùng tên ngắn còn CHƯA GÁN (get-detail-by-status) dùng tên đầy đủ
                        # → nếu không gộp sẽ bị TÁCH 1 xã thành 2 dòng (đã gán riêng, chưa gán riêng).
                        def _wbase(nm):
                            return nm.split(",")[0].strip()
                        w_dg = {}
                        for d in r.get("drivers", []):
                            for ward, gv in d.get("wards", {}).items():
                                if gv and gv[0] > 0:
                                    k = _wbase(ward)
                                    w_dg[k] = w_dg.get(k, 0) + gv[0]
                        w_cg = {}
                        for ward, cnt in r.get("backlog_wards", []):
                            k = _wbase(ward)
                            w_cg[k] = w_cg.get(k, 0) + cnt
                        allw = set(w_dg) | set(w_cg)
                        inner = ""
                        if allw:
                            wr = sorted(((w, w_dg.get(w, 0), w_cg.get(w, 0)) for w in allw),
                                        key=lambda x: -(x[1] + x[2]))
                            I = ["<table class='drv'><thead><tr><th class='lft'>Xã/phường</th>"
                                 "<th>Đã gán</th><th>Chưa gán</th><th>Tổng</th></tr></thead><tbody>"]
                            for w, dg, cg in wr:
                                I.append("<tr><td class='nv'>%s</td><td><b class='ltc'>%s</b></td>"
                                         "<td><b class='w'>%s</b></td><td><b>%s</b></td></tr>"
                                         % (_esc(w), _n(dg), _n(cg), _n(dg + cg)))
                            I.append("</tbody></table>")
                            inner = "".join(I)
                        else:
                            inner = "<div class='note'>Chưa có chi tiết xã (chuyến chưa bóc đủ).</div>"
                        S.append("<details class='bc sub'><summary>"
                                 "<div class='bch'><span class='dot' style='background:#22d3ee'></span>"
                                 "<span class='bcn' style='font-size:14px'>%s</span></div>"
                                 "<div class='bcm'><span>📦 đã gán <b class='ltc'>%s</b></span>"
                                 "<span>⏳ chưa gán <b class='w'>%s</b></span>"
                                 "<span>Σ <b>%s</b></span></div>"
                                 "</summary><div class='dtl'>%s</div></details>"
                                 % (_esc(r["name"]), _n(b_dg), _n(b_cg), _n(b_dg + b_cg), inner))
                    S.append("</div></details>")
                _in_sld = "".join(S)
            P.append(_row("", "📦", "Sản lượng giao / ngày",
                          "bấm AM → bưu cục → xã · đã gán + chưa gán + tổng",
                          _spark(sl_don, "#22d3ee", w=60, h=22), _n(R["total"]) + _dc_sl, "", _in_sld))
        # Khối lượng (kg thực) đơn giao đã gán — ngay dưới Sản lượng · bung chi tiết AM→BC tại chỗ
        if R["weight_g"] > 0:
            sl_kg = [t.get("weight_kg") for t in trend]
            kg_spark = _spark(sl_kg, "#38bdf8", w=60, h=22) if any(v is not None for v in sl_kg) else ""
            kg_am = {}
            for r in rows:
                dg = (r.get("weight_g", 0) or 0) / 1000.0
                cg = (r.get("backlog_weight_g", 0) or 0) / 1000.0
                if dg > 0 or cg > 0:
                    kg_am.setdefault(AM_OF.get(r["name"]) or "(chưa phân AM)", []).append((r["name"], dg, cg))
            _in_kgd = ""
            if kg_am:
                K = []
                for amn, bcs in sorted(kg_am.items(), key=lambda kv: -sum(x[1] + x[2] for x in kv[1])):
                    a_dg = sum(x[1] for x in bcs); a_cg = sum(x[2] for x in bcs)
                    K.append("<details class='bc'><summary>"
                             "<div class='bch'><span class='dot' style='background:#38bdf8'></span>"
                             "<span class='bcn'>🧑‍💼 %s</span></div>"
                             "<div class='bcm'><span>📦 đã gán <b class='ltc'>%s</b></span>"
                             "<span>⏳ chưa gán <b class='w'>%s</b></span></div>"
                             "</summary><div class='dtl'>" % (_esc(amn), _kgfmt(a_dg), _kgfmt(a_cg)))
                    K.append("<table class='drv'><thead><tr><th class='lft'>Bưu cục</th>"
                             "<th>Đã gán</th><th>Chưa gán</th></tr></thead><tbody>")
                    for bcn, dg, cg in sorted(bcs, key=lambda x: -(x[1] + x[2])):
                        K.append("<tr><td class='nv'>%s</td><td><b class='ltc'>%s</b></td>"
                                 "<td><b class='w'>%s</b></td></tr>" % (_esc(bcn), _kgfmt(dg), _kgfmt(cg)))
                    K.append("</tbody></table></div></details>")
                _in_kgd = "".join(K)
            P.append(_row("", "⚖️", "Khối lượng giao / ngày",
                          "kg thực · đã gán + chưa gán · bấm xem AM → bưu cục",
                          kg_spark, _kgfmt(R["weight_g"] / 1000.0) + _dc_kg, "", _in_kgd))
        if any(v is not None for v in sl_ttg):
            P.append(_row("", "🛍️", "TikTok giao TC / ngày", "", _spark(sl_ttg, "#e879c8", w=60, h=22),
                          _n(R["vngh_gtc"]) + _dc_ttk, "", ""))
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
    # Mỗi điểm nóng = 1 DÒNG (nhãn trái · số/giá trị phải) → dễ lướt số trên iPhone (thay câu dài).
    def _dgr(icon, lbl, val):
        return ("<div class='dgr'><span class='dgl'>%s %s</span>"
                "<span class='dgv'>%s</span></div>" % (icon, lbl, val))
    diag = []
    if top_bl and top_bl.get("backlog", 0) > 0:
        diag.append(_dgr("🔴", "Chưa gán cao nhất",
                         "%s · <b>%s</b>" % (_esc(top_bl["name"]), _n(top_bl["backlog"]))))
    if worst_am:
        diag.append(_dgr("🟠", "AM yếu nhất",
                         "%s · <b>%d%%</b>" % (_esc(worst_am[0]), round(worst_am[1]))))
    if bc_low:
        diag.append(_dgr("🏤", "Bưu cục %GTC &lt;50%", "<b>%d</b>" % bc_low))
    if nv_low:
        diag.append(_dgr("👤", "Nhân viên %GTC &lt;50%", "<b>%d</b>" % nv_low))
    if nv_rate is not None:
        diag.append(_dgr("✅", "NV đạt ≥50%",
                         "<b>%d</b> <span class='dgs'>/ %d NV · %d%%</span>"
                         % (nv_qual - nv_low, nv_qual, nv_rate)))
    if not diag:
        diag.append(_dgr("✅", "Vùng vận hành ổn định", ""))

    # ---- Top 5 tệ nhất từng mục (bấm mở dòng chú ý để đọc ngay) ----
    top_bl5 = sorted([r for r in rows if r.get("backlog", 0) > 0],
                     key=lambda x: -x["backlog"])[:5]
    am_worst5 = sorted(am_pcts, key=lambda x: x[1])[:5]
    bc_low5 = sorted([(r, _pct(r["gtc"], r["total"])) for r in rows
                      if r["total"] >= 20 and _pct(r["gtc"], r["total"]) is not None
                      and _pct(r["gtc"], r["total"]) < 50], key=lambda x: x[1])[:5]
    # Top 5 bưu cục NHIỀU ĐƠN BACKLOG (giao >120h) nhất — thay nhóm 'Top NV %GTC thấp' (07/10).
    bl120_5 = sorted([r for r in rows if r.get("giao120h", 0) > 0],
                     key=lambda x: -x["giao120h"])[:5]

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
    if bl120_5:
        grps.append(_dgrp("🕙 Top bưu cục Backlog giao 120h", [
            "<div class='drow'><span class='dn'>%s</span><span class='dv w'>%s đơn</span></div>"
            % (_esc(r["name"]), _n(r["giao120h"])) for r in bl120_5]))

    # Ghi vào CHỖ ĐẶT SẴN trên đầu (trước lưới Tổng Quan Vận Hành) thay vì append cuối.
    if grps:
        P[_diag_slot] = (
            "<details class='diag'><summary><div class='dgh2'>⚡ <b>ĐIỂM NÓNG CHÚ Ý</b>"
            "<span class='dcv'>▾ Top 5</span></div><div class='dgrows'>%s</div></summary>"
            "<div class='ddtl'><div class='dnote'>Top 5 cần chú ý mỗi mục · "
            "%%GTC luỹ kế trong ngày (sáng còn thấp là bình thường)</div>%s</div></details>"
            % ("".join(diag), "".join(grps)))
    else:
        P[_diag_slot] = ("<div class='diag'><div class='dgh2'>⚡ <b>ĐIỂM NÓNG CHÚ Ý</b></div>"
                         "<div class='dgrows'>%s</div></div>" % "".join(diag))

    # ===== Dải chỉ số · Bento (Mẫu 3) · màu theo từng chỉ số =====
    vpct = _pct(R["vngh_gtc"], R["vngh"])
    _cgo = ("onclick=\"var d=document.getElementById('cgd');if(d){d.open=true;"
            "d.scrollIntoView({behavior:'smooth',block:'start'});}\"")
    # Kỷ luật ngữ nghĩa: NEU = ô đếm số (tối trung tính) · màu chỉ dành cho ô cảnh báo
    NEU, AMBER, RED, GREEN = "128,140,174", "247,185,85", "242,88,95", "47,208,122"
    xp_rgb = RED if late_cnt else NEU                       # xuất phát muộn: đỏ khi >0
    tt_rgb = {"good": GREEN, "warn": AMBER, "bad": RED}.get(_cls(vpct), NEU) if vpct is not None else NEU
    #      icon, giá trị, nhãn, màu rgb, extra, neu, key(snap), raw(số nay), up_good
    # Bỏ ô 'Còn phải giao', 'GTC nay', 'LTC'/'LTB' khỏi dải (LTC/LTB đã hiển thị ở card hero).
    kpis = [
        ("🚚", _n(R["dagan_giao"]),"Đơn đang giao",    NEU,   "", True,  "dagan_giao", R["dagan_giao"], None),
        ("🏃", _n(R["ontrip"]),    "Đang chạy",       NEU,   "", True,  "ontrip",   R["ontrip"],   None),
        ("🕘", _n(late_cnt),       "XP muộn &gt;9h30",xp_rgb,"", not late_cnt, "late", late_cnt,   False),
        ("🛍️", _n(R["vngh"]),     "TikTok gán",      NEU,   "", True,  "vngh",     R["vngh"],     None),
        ("🛍️", _n(R["vngh_gtc"]), "TikTok GTC",      NEU,   "", True,  "vngh_gtc", R["vngh_gtc"], True),
        ("🛍️", (("%d%%" % vpct) if vpct is not None else "—"), "%GTC TikTok", tt_rgb, "", vpct is None, "vpct", vpct, True),
        ("💰", _codm(R["cod_gtb"]),"COD GTB",         AMBER, "", False, "cod_gtb",  R["cod_gtb"],  False),
        ("📉", _n(nv_low),         "NV %GTC &lt;50%", (RED if nv_low else NEU), "", not nv_low, "nv_low", nv_low, False),
    ]
    # Snapshot CÙNG GIỜ HÔM QUA (để so ▲/▼ mỗi ô) — đầy đủ từ ngày sau khi bắt đầu log snap.
    ysnap = (cmp_y or {}).get("snap") or {}

    def _sd(raw, y, up_good, is_pct=False):
        """Chip ▲/▼ so cùng giờ hôm qua. is_pct → chênh điểm; khác → % tương đối."""
        if y is None or raw is None:
            return ""
        if is_pct:
            d = round(raw - y, 1)
            if d == 0:
                return "<div class='sd fl'>▬</div>"
            up = d > 0
            mag = ("%.1f" % abs(d)).replace(".", ",") + "đ"
        else:
            if abs(y) < 5:              # baseline quá nhỏ → % vô nghĩa
                return ""
            pc = round((raw - y) * 100.0 / abs(y))
            if pc == 0:
                return "<div class='sd fl'>▬</div>"
            if abs(pc) > 999:
                pc = 999 if pc > 0 else -999
            up = pc > 0
            mag = "%d%%" % abs(pc)
        cls = "nt" if up_good is None else ("up" if (up == up_good) else "dn")
        return "<div class='sd %s'>%s%s</div>" % (cls, "▲" if up else "▼", mag)

    P.append("<div class='sectitle'>📊 Chỉ số quan trọng của vùng</div>")
    P.append("<section class='strip'>")
    for ic, val, lab, rgb, extra, neu, key, raw, up_good in kpis:
        cls = "st" + (" cg" if extra == "cg" else "") + (" neu" if neu else "")
        oc = (" " + _cgo) if extra == "cg" else ""
        sub = extra if (extra and extra != "cg") else ""   # chữ nhỏ phụ trên card (vd LTB dưới LTC)
        sd = _sd(raw, ysnap.get(key), up_good, is_pct=(key == "vpct")) if key else ""
        P.append("<div class='%s' style='--h:%s'%s><div class='sv'>%s</div>"
                 "<div class='sl'>%s %s</div>%s%s</div>" % (cls, rgb, oc, val, ic, lab, sd, sub))
    P.append("</section>")

    # ===== 🏤 BẢNG TỔNG QUÁT BƯU CỤC — ô NỔI BẬT, DƯỚI dải 'Chỉ số quan trọng' (bung scorecard AM→BC) =====
    if bcm is not None:
        # Số AM thực (có bưu cục trong vùng) + số NV đi làm trong ngày (có chuyến/đơn gán, gộp driverId)
        _am_lv = len({AM_OF.get(r["name"]) for r in rows if AM_OF.get(r["name"])})
        _nv_lv = len({(d.get("id") or ("~" + d.get("name", "")))
                      for r in rows for d in r.get("drivers", [])})
        _bc_sub = ("Vùng TBB · <b class='ld'>%d</b> AM · <b class='ld'>%s</b> NV đi làm trong ngày"
                   % (_am_lv, _n(_nv_lv)))
        P.append("<details class='cgbento bcfeat' style='--h:99,179,237'><summary>"
                 "<div class='mic'>🏤</div>"
                 "<div class='mtx'><div class='mn'>BẢNG TỔNG QUÁT BƯU CỤC</div>"
                 "<div class='ms'>%s</div></div>"
                 "<div class='mbig'>%d<span class='u'>BC</span></div><span class='cvar'>▾</span>"
                 "</summary><div class='dtl'>%s</div></details>" % (_bc_sub, bcm["n"], bcm["html"]))


    # ===== MENU BÁO CÁO · Bento grid (Mẫu 3) =====
    #        href, icon, tên, phụ đề, màu RGB, [7 cột mini-nhịp]
    # Ô '%GTC cuối ngày' (eod.html) ĐÃ BỎ 13/10 (user) — thay bằng card lớn 'BẢNG TỔNG QUÁT BƯU CỤC'
    # ở ngay trên lưới này (eod.html vẫn sinh ra phục vụ watch_chot, chỉ không link ở menu).
    _menu = [
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
    # Số ô LẺ (7, sau khi bỏ %GTC cuối ngày) → ô CUỐI span full-width (feat) cho cân đối, không lẻ 1 ô.
    _last = len(_menu) - 1
    for _i, (href, ic, nm, sub, rgb, bars) in enumerate(_menu):
        spark = "".join("<i style='height:%d%%'></i>" % b for b in bars)
        _wide = " feat" if (len(_menu) % 2 == 1 and _i == _last) else ""
        P.append("<a class='mtile%s' href='%s' style='--h:%s'>"
                 "<div class='mic'>%s</div>"
                 "<div class='mtx'><div class='mn'>%s</div><div class='ms'>%s</div></div>"
                 "<div class='mspark'>%s</div></a>"
                 % (_wide, href, rgb, ic, nm, sub, spark))
    P.append("</div>")

    # ===== Mũi tên %GTC SO TB CÙNG GIỜ 7 NGÀY cho thẻ AM / Tỉnh / Bưu cục =====
    # ▲ xanh = %GTC tốt hơn mức TB cùng giờ 7 ngày · ▼ đỏ = kém hơn. Đầy dần tới đủ 7 ngày.
    # Nguồn: bc_avg = {bc:[Σgtc,Σtotal]} pooled 7 ngày; AM/Tỉnh = tổng BC con.
    _ybc = bc_avg or {}
    _y_am, _y_prov = {}, {}
    for _bcn, _gt in _ybc.items():
        if not (isinstance(_gt, (list, tuple)) and len(_gt) >= 2):
            continue
        _a = AM_OF.get(_bcn)
        if _a:
            z = _y_am.setdefault(_a, [0, 0]); z[0] += _gt[0]; z[1] += _gt[1]
        z = _y_prov.setdefault(_prov(_bcn), [0, 0]); z[0] += _gt[0]; z[1] += _gt[1]

    def _gtc_arrow(tg, tt, yv):
        """Mũi tên %GTC today vs TB cùng giờ 7 ngày. yv=[Σgtc,Σtotal] pooled. ▲ xanh=tốt·▼ đỏ=kém."""
        if not (isinstance(yv, (list, tuple)) and len(yv) >= 2):
            return ""
        tp = _pct(tg, tt); yp2 = _pct(yv[0], yv[1])
        if tp is None or yp2 is None:
            return ""
        d = round(tp - yp2, 1)
        if d == 0:
            return "<span class='ga fl' title='= TB 7 ngày cùng giờ'>▬</span>"
        up = d > 0
        return "<span class='ga %s' title='so TB 7 ngày cùng giờ (điểm)'>%s%s</span>" % (
            "up" if up else "dn", "▲" if up else "▼", ("%.1f" % abs(d)).replace(".", ","))

    # ===== Theo AM (xếp hạng · bấm mở xem bưu cục) =====
    am_rows = {}
    for r in rows:
        amn = AM_OF.get(r["name"])
        if amn:
            am_rows.setdefault(amn, []).append(r)
    # GOM 3 phần drill thành TAB (AM · Tỉnh · Bưu cục) cho gọn — giữ nguyên bấm-mở chi tiết bên trong.
    P.append("<div class='dtabs'>"
             "<button class='dtab on' onclick=\"dtab(this,'am')\">🧑‍💼 Theo AM</button>"
             "<button class='dtab' onclick=\"dtab(this,'tinh')\">🗺 Tỉnh</button>"
             "<button class='dtab' onclick=\"dtab(this,'bc')\">🏤 Bưu cục</button></div>")
    P.append("<div class='tpane' id='tp-am'>")
    P.append("<div class='tsub'>🧑‍💼 AM · %GTC thấp → cao · bấm xem bưu cục → nhân viên</div>")
    for amn, v in sorted(am.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "<span class='bcmx'><span class='pill %s'>%s%%</span>%s</span></div>"
                 % (cls, cls, _esc(amn), cls, pc if pc is not None else "—",
                    _gtc_arrow(v["gtc"], v["total"], _y_am.get(amn))))
        P.append(_bar(pc, cls))
        _am_late = sum(_late_cnt(r) for r in am_rows.get(amn, []))
        _am_low = sum(_low_cnt(r) for r in am_rows.get(amn, []))
        P.append("<div class='pmeta'><span>🏤 %s BC</span><span>📥 %s</span><span class='w'>⏳ %s</span>"
                 "<span>✅ %s</span><span class='ltc'>LTC %s</span>%s%s%s</div>"
                 % (v["bc"], _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]), _n(v["ltc"]),
                    _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0)) if v.get("vngh") else "",
                    _late_badge(_am_late) if _am_late else "",
                    _low_badge(_am_low) if _am_low else ""))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        _bcs = sorted(am_rows.get(amn, []), key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999))
        _den = max(len(_bcs) - 1, 1)
        for _i, r in enumerate(_bcs):
            P.append(_bc_drv_details(r, _gtc_arrow(r["gtc"], r["total"], _ybc.get(r["name"])), pos=_i / _den))
        P.append("</div></details>")

    # ===== Theo tỉnh (bấm mở xem bưu cục) =====
    P.append("</div>")                                    # đóng pane AM
    prov_rows = {}
    for r in rows:
        prov_rows.setdefault(r["prov"], []).append(r)
    P.append("<div class='tpane' id='tp-tinh' hidden>")
    P.append("<div class='tsub'>🗺 Tỉnh · %GTC thấp → cao · bấm xem bưu cục → nhân viên</div>")
    for pv, v in sorted(prov.items(), key=lambda kv: (_pct(kv[1]["gtc"], kv[1]["total"]) if kv[1]["total"] else 999)):
        pc = _pct(v["gtc"], v["total"])
        cls = _cls(pc)
        P.append("<details class='bc %s'>" % cls)
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "<span class='bcmx'><span class='pill %s'>%s%%</span>%s</span></div>"
                 % (cls, cls, _esc(PROV_NAME.get(pv, pv)), cls, pc if pc is not None else "—",
                    _gtc_arrow(v["gtc"], v["total"], _y_prov.get(pv))))
        P.append(_bar(pc, cls))
        _pv_late = sum(_late_cnt(r) for r in prov_rows.get(pv, []))
        _pv_low = sum(_low_cnt(r) for r in prov_rows.get(pv, []))
        P.append("<div class='pmeta'><span>🏃 %s</span><span>📥 %s</span><span class='w'>⏳ %s</span>"
                 "<span>✅ %s</span><span class='ltc'>LTC %s</span>%s%s%s</div>"
                 % (_n(v["ontrip"] + v["fin"]), _n(v["total"]), _n(v["backlog"]), _n(v["gtc"]),
                    _n(v["ltc"]),
                    _tt_chip(v.get("vngh_gtc", 0), v.get("vngh", 0)) if v.get("vngh") else "",
                    _late_badge(_pv_late) if _pv_late else "",
                    _low_badge(_pv_low) if _pv_low else ""))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        _bcs = sorted(prov_rows.get(pv, []), key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999))
        _den = max(len(_bcs) - 1, 1)
        for _i, r in enumerate(_bcs):
            P.append(_bc_drv_details(r, _gtc_arrow(r["gtc"], r["total"], _ybc.get(r["name"])), pos=_i / _den))
        P.append("</div></details>")

    # ===== Bưu cục =====
    P.append("</div>")                                    # đóng pane Tỉnh
    P.append("<div class='tpane' id='tp-bc' hidden>")
    P.append("<div class='tsub'>🏤 Bưu cục · Tồn gán giao cao → thấp · bấm xem nhân viên</div>")
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    bcs = [r for r in rows if r["backlog"] or r["ontrip"] or r["total"]]
    for r in sorted(bcs, key=lambda x: (-x["backlog"], _pct(x["gtc"], x["total"]) if x["total"] else 999)):
        pc = _pct(r["gtc"], r["total"])
        cls = _cls(pc)
        keys = (r["name"] + " " + " ".join(d["name"] for d in r["drivers"])).lower()
        P.append("<details class='bc %s' data-k=\"%s\">" % (cls, _esc(keys)))
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "<span class='bcmx'><span class='pill %s'>%s%%</span>%s</span></div>"
                 % (cls, cls, _esc(r["name"]), cls, pc if pc is not None else "—",
                    _gtc_arrow(r["gtc"], r["total"], _ybc.get(r["name"])))
                 )
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

    P.append("</div>")                                    # đóng pane Bưu cục
    P.append("<div class='foot'><b>📖 Giải thích chỉ số</b><br>"
             "<span class='up' style='color:var(--good);font-weight:800'>▲</span>/<span class='dn' style='color:var(--bad);font-weight:800'>▼ %</span> cạnh số ở lưới = thay đổi <b>ngày chốt hôm qua so hôm kia</b> · <span style='color:var(--good)'>xanh = tốt lên</span> · <span style='color:var(--bad)'>đỏ = xấu đi</span> (tồn/NV cần xử lý tăng là xấu; sản lượng/GTC/NV đạt tăng là tốt)<br>"
             "🚚 <b>Đơn đang giao</b> (dải vùng) = đơn đã gán chuyến ĐANG giao, còn tồn (khớp trang Tồn đọng GHN · 'đang có chuyến đi', gồm ưu tiên giao) · ⏳ <b>Chưa gán</b> = đơn tồn ở kho chưa xếp chuyến (gồm ưu tiên)<br>"
             "📥 <b>Đã gán</b> (thẻ AM/Tỉnh/Bưu cục) = đơn đã xếp vào chuyến hôm nay (luỹ kế, gồm đơn đã giao xong — là mẫu số %GTC)<br>"
             "🏃 <b>Đang chạy</b> = số NV còn chuyến chưa kết thúc · 🚛 <b>Còn phải giao</b> = đơn của chuyến đang chạy CHƯA giao xong (đang trên đường)<br>"
             "🔴 <b>Backlog giao 120h</b> = đơn Giao tồn quá 120 giờ toàn vùng (khớp trang Tồn đọng) · ✅ <b>GTC nay</b> = đơn giao thành công (chuyến đã kết thúc)<br>"
             "🕘 <b>XP muộn &gt;9h30</b> = số NV xuất phát sau 9h30 (kỷ luật ra hàng) · 🛍️ <b>TikTok</b> = đơn hàng sàn TikTok Shop<br>"
             "💰 <b>COD GTB</b> = tiền thu hộ kẹt trên đơn giao hỏng (triệu đồng) · 🛒 <b>LTC</b> = lấy hàng thành công · 📦 <b>LTB</b> = lấy hàng thất bại (đã thao tác nhưng không lấy được)<br>"
             "🎯 <b>NV đạt ≥50%</b> = số nhân viên có %GTC ≥50% (≥20 đơn đã gán)<br>"
             "🎯 <b>%GTC</b> = GTC / tổng đơn đã gán · gộp theo mã đơn (đơn giao lại tính 1 lần)<br>"
             "🕘 <b>cạnh tên NV / chip 🕘 N</b> = nhân viên xuất phát sau 9h30 (số trên thẻ Bưu cục·AM·Tỉnh = tổng NV muộn của đơn vị đó)<br>"
             "🎨 <b>Màu tên NV · Bưu cục · AM · Tỉnh</b> = theo %GTC: <span style='color:var(--bad)'>đỏ &lt;60%</span> · <span style='color:var(--warn)'>vàng 60–70%</span> · <span style='color:var(--good)'>xanh ≥70%</span> · <span style='opacity:.7'>buổi sáng %GTC luỹ kế còn thấp nên đa số đỏ, phản ánh đúng dần về chiều/tối</span><br>"
             "📉 <b>chip 📉 N</b> ở thẻ Bưu cục·AM·Tỉnh = số NV %GTC &lt;50% (≥20 đơn) — nhóm yếu cần chú ý của đơn vị đó<br>"
             "<span style='opacity:.7'>Số LIVE gồm cả chuyến đã kết thúc trong ngày · %GTC còn thấp giữa ngày là bình thường (chuyến chưa đóng) · nguồn nhanh.ghn.vn</span></div>")
    P.append("<script>function tgw(tr){tr.classList.toggle('op');"
             "var s=tr.nextElementSibling;if(s&&s.classList.contains('wsub'))s.classList.toggle('show');}</script>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc[data-k]').forEach(function(e){var k=e.dataset.k||'';"
             "var s=(!q||k.indexOf(q)>=0);e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=(q&&!n)?'block':'none';}</script>")
    P.append("<script>function dtab(b,id){"
             "document.querySelectorAll('.dtab').forEach(function(x){x.classList.remove('on')});b.classList.add('on');"
             "['am','tinh','bc'].forEach(function(t){var p=document.getElementById('tp-'+t);if(p)p.hidden=(t!==id)});"
             "window.scrollBy(0,0);}</script>")
    P.append("<button id='rf' class='fab' onclick='rf()' aria-label='Làm mới'>"
             "<span class='rfi'>⟳</span></button>")
    P.append("<script>function rf(){var b=document.getElementById('rf');"
             "b.classList.add('spin');location.replace(location.pathname+'?t='+Date.now());}</script>")
    if nvm is not None:
        P.append("<script>%s</script>" % nvm["js"])
    P.append("</div></body></html>")
    return "\n".join(P)


_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800;900&family=Geist+Mono:wght@500;600;700;800&display=swap');
:root{
 --bg:#0a0e1a;--bg2:#10131f;--card:#171c2e;--card2:#212840;--line:rgba(255,255,255,.08);
 --mut:#dce2f6;--txt:#ffffff;--good:#25dd90;--warn:#f9b534;--bad:#ff5668;--ink:#0a0d18;
 --vi:#9d92e0;--cy:#3fb9d6}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:'Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 color:var(--txt);-webkit-font-smoothing:antialiased;font-size:15px;line-height:1.4;
 background:#0a0e1a}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(30px + env(safe-area-inset-bottom))}
/* DARK MATTE: thẻ đặc, KHÔNG backdrop-blur (bỏ hiệu ứng kính/bóng gương) — số liệu sắc, không nhoè */
.brand,.hh,.amn{font-family:'Geist',sans-serif;letter-spacing:-.01em}
.sv,.mbig,.vst,.vpct b,.ttv{font-family:'Geist Mono','Geist',monospace;font-feature-settings:'tnum' 1}

.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
 padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:var(--bg);margin-bottom:4px}
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
/* Hàng trên hero: SỐ %GTC là tâm điểm (rất to) + badge trạng thái bên cạnh */
.htop{display:flex;align-items:center;gap:8px 16px;flex-wrap:wrap;margin:6px 0 11px}
.hpct{font-family:'Geist',sans-serif;font-size:84px;font-weight:900;line-height:.9;margin:0;font-variant-numeric:tabular-nums;letter-spacing:-.05em}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}.hero.na .hpct{color:var(--mut)}
.hpct span{font-size:30px;font-weight:700;opacity:.55;margin-left:1px;letter-spacing:0}
/* Badge trạng thái %GTC — đọc nhanh ĐẠT/CẦN CHÚ Ý/DƯỚI MỤC TIÊU */
.hstat{flex:1 1 100%;display:flex;flex-direction:column;gap:8px;padding:11px 14px;border-radius:14px;border:1px solid var(--line);background:rgba(255,255,255,.04)}
.hstat.good{background:rgba(23,201,131,.12);border-color:rgba(23,201,131,.4)}
.hstat.warn{background:rgba(249,181,52,.12);border-color:rgba(249,181,52,.4)}
.hstat.bad{background:rgba(242,88,95,.13);border-color:rgba(242,88,95,.42)}
.hstt{display:flex;align-items:center;gap:9px}
.hsi{font-size:20px;line-height:1}
.hstt b{font-family:'Geist',sans-serif;font-size:16px;font-weight:800;letter-spacing:.02em;color:#fff}
.hstat.good .hstt b{color:#2ee88c}.hstat.warn .hstt b{color:#f9b534}.hstat.bad .hstt b{color:#ff7b81}
.hstr{display:flex;flex-wrap:wrap;gap:6px 7px}
.hrf{font-size:12px;color:var(--mut);font-weight:600;background:rgba(255,255,255,.055);border:1px solid var(--line);
 border-radius:99px;padding:4px 11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.hrf b{color:#fff;font-weight:800;font-size:13.5px;margin-left:3px}
.hwh{margin-top:11px;padding:9px 12px;border-radius:12px;
 border:1px solid var(--line);background:rgba(255,255,255,.04)}
.hwh-t{font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#cdd4ea;margin-bottom:8px}
.hwh-t i{font-style:normal;text-transform:none;font-weight:500;opacity:.7}
.hwh-g{display:grid;grid-template-columns:repeat(3,1fr);gap:9px 12px}
.hwi{display:flex;flex-direction:column;gap:1px;min-width:0}
.hwi span{font-size:10px;color:var(--mut);font-weight:500;white-space:nowrap}
.hwi b{font-family:'Geist',sans-serif;font-size:18px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.01em;line-height:1.15;white-space:nowrap}
.hwi span.hwn{font-size:10px;color:#f3d88c;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap;margin-top:1px}
.hwa{font-size:10.5px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em;margin-top:2px;white-space:nowrap}
.hwa.up{color:#ff6274}.hwa.dn{color:#2ee88c}.hwa.fl{color:var(--mut)}
.hsc{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0 2px}
.hsx{border:1px solid var(--line);border-radius:12px;padding:9px 11px;
 background:rgba(255,255,255,.04);min-width:0;display:flex;flex-direction:column;gap:3px}
.hsx span{font-size:10px;color:var(--mut);font-weight:600;white-space:nowrap;letter-spacing:-.1px;
 overflow:hidden;text-overflow:ellipsis}
.hsx b{font-family:'Geist',sans-serif;font-size:21px;font-weight:900;color:#fff;
 font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1.1;white-space:nowrap}
.hsx b i{font-style:normal;font-size:13px;font-weight:600;color:var(--mut)}
.hsx .hfr{font-size:10px;color:var(--mut);font-weight:700;margin-top:1px;
 letter-spacing:-.02em;font-variant-numeric:tabular-nums;overflow:visible}
.hsx .hfr.wrap{white-space:normal;line-height:1.25}
.hsx .hfr.bad{color:var(--bad)}
.hsx.good b{color:#2ee88c}
.hsx.warn b{color:var(--warn)}
.href{display:flex;flex-wrap:wrap;gap:8px 9px;margin-top:14px}
.rc{font-size:13.5px;color:#cfd6ec;background:rgba(255,255,255,.06);border:1px solid var(--line);
 border-radius:99px;padding:7px 14px;font-variant-numeric:tabular-nums;white-space:nowrap}
.rc b{color:#fff;font-weight:800;font-size:14.5px}
.sparkwrap{margin-top:10px}
.spklbl{font-size:9.5px;color:var(--mut);font-weight:600;letter-spacing:.02em;margin-bottom:3px}
svg.spk{display:block}
/* 3 ĐƯỜNG XU HƯỚNG 14 NGÀY ngay dưới số %GTC (%GTC · Backlog>120h · Tồn gán 12h) */
/* KHỐI ĐIỀU HÀNH NỔI BẬT — khung nhấn riêng, đặt ngay dưới số %GTC */
.htri{margin-top:14px;padding:11px 11px 12px;border-radius:16px;
 background:linear-gradient(180deg,rgba(167,139,250,.07),rgba(255,255,255,.02));
 border:1px solid rgba(167,139,250,.22)}
.htri-h{font-size:12px;color:#e8ebfa;font-weight:800;letter-spacing:.03em;text-transform:uppercase;margin-bottom:9px}
.htri-h .htri-s{font-weight:500;text-transform:none;letter-spacing:0;color:var(--mut)}
.htri-g{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;align-items:stretch}
.htl{position:relative;background:rgba(255,255,255,.05);border:1px solid var(--line);
 border-radius:13px;padding:0;min-width:0;overflow:hidden;display:flex;flex-direction:column}
.htl>summary{display:flex;flex-direction:column;list-style:none;cursor:pointer;padding:10px 11px 8px;flex:1}
.htl>summary::-webkit-details-marker{display:none}
.htl[open]{grid-column:1/-1}
.htcar{position:absolute;top:8px;right:9px;font-size:10px;color:var(--mut);transition:transform .2s;z-index:1}
.htl[open] .htcar{transform:rotate(180deg)}
/* Drill TỪNG AM 14 ngày (bấm card mở) */
.amdr{padding:0 10px 10px;border-top:1px solid var(--line)}
.amdr-h{font-size:10px;color:var(--mut);font-weight:700;letter-spacing:.03em;text-transform:uppercase;margin:9px 0 6px}
.amdrow{display:flex;flex-direction:column;gap:3px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05)}
.amdrow:last-child{border-bottom:0}
.amdn{display:flex;align-items:baseline;gap:7px}
.amnm{flex:1;min-width:0;font-size:12px;color:#cdd4ea;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.amdn b{flex:none;font-size:13.5px;color:#fff;font-weight:800;font-family:'Geist Mono','Geist',monospace;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.amda{flex:none;font-size:11px;font-weight:800;font-variant-numeric:tabular-nums}
.amda.cok{color:#4de39a}.amda.cbad{color:#ff8e93}.amda.cfl{color:var(--mut)}
.amdk{width:100%;background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:8px;padding:3px 7px 1px}
.amdk svg.spk{width:100%;height:28px;display:block}
.htl.gbad{background:rgba(242,88,95,.1);border-color:rgba(242,88,95,.32)}
.htl.gwarn{background:rgba(249,181,52,.1);border-color:rgba(249,181,52,.32)}
.htl.gok{background:rgba(23,201,131,.09);border-color:rgba(23,201,131,.3)}
.htll{font-size:11px;color:#dce2f6;font-weight:700;line-height:1.2;min-height:26px;padding-right:13px;
 display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
/* caret chỉ chiếm góc trên-phải → dòng 2 của nhãn KHÔNG cần chừa chỗ (bỏ padding ở dòng 2 nhờ box narrow) */
.htlv{font-family:'Geist Mono','Geist',monospace;font-weight:900;font-size:31px;font-variant-numeric:tabular-nums;letter-spacing:-.045em;line-height:1;margin:4px 0 6px;color:#fff}
.htl.gbad .htlv{color:#ff7b81}.htl.gwarn .htlv{color:#f9b534}.htl.gok .htlv{color:#2ee88c}.htl.na .htlv{color:var(--mut)}
.htlk{height:40px}.htlk svg.spk{width:100%;height:40px}
.htlf{margin-top:auto;padding-top:4px;display:flex;flex-direction:column;gap:1px}
.htlt{font-size:9.5px;color:#a78bfa;font-weight:600;font-variant-numeric:tabular-nums;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htld{font-size:10px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis}
.htld.cbad{color:#ff8e93}.htld.cok{color:#4de39a}.htld.cfl{color:var(--mut)}
@media(max-width:412px){.htri{padding:10px 9px 11px}.htri-g{gap:7px}.htl>summary{padding:9px 8px 7px}
 .htll{font-size:10px}.htlv{font-size:27px}.htlt{font-size:8.5px}.htld{font-size:9.5px}}
/* DẢI MỎNG ĐÁNH GIÁ trong Hero (thay dòng Xu hướng) */
.hverd{margin-top:10px;font-size:13.5px;font-weight:600;color:#d4dbf0;line-height:1.6;
 padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.04)}
.hverd .hvi{font-size:16px}
.hverd b{font-family:'Geist',sans-serif;font-size:15px;font-weight:800;letter-spacing:.01em;color:#fff}
.hverd.good{border-color:rgba(23,201,131,.42);background:rgba(23,201,131,.09)}.hverd.good b{color:var(--good)}
.hverd.warn{border-color:rgba(245,170,23,.42);background:rgba(245,170,23,.09)}.hverd.warn b{color:var(--warn)}
.hverd.bad{border-color:rgba(245,69,92,.42);background:rgba(245,69,92,.09)}.hverd.bad b{color:var(--bad)}
.hverd b.rk{color:var(--bad)!important;background:rgba(245,69,92,.16);padding:1px 8px;border-radius:7px;
 font-size:12.5px;white-space:nowrap}
/* 🎯 NHỊP ĐỘ CÁN ĐÍCH — dòng điều hành (cần X đơn/giờ) */
.hpace{margin-top:10px;font-size:13.5px;font-weight:600;color:#d4dbf0;line-height:1.6;
 padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.04)}
.hpace .hvi{font-size:16px}
.hpace b{font-family:'Geist',sans-serif;font-size:15px;font-weight:800;letter-spacing:.01em;color:#fff}
.hpace i{font-style:normal;color:var(--mut);font-weight:500}
.hpace.good{border-color:rgba(23,201,131,.45);background:rgba(23,201,131,.10)}.hpace.good b{color:var(--good)}
.hpace.warn{border-color:rgba(245,170,23,.45);background:rgba(245,170,23,.10)}.hpace.warn b{color:var(--warn)}
.hpace.bad{border-color:rgba(245,69,92,.45);background:rgba(245,69,92,.10)}.hpace.bad b{color:var(--bad)}
/* 🎯 BẢNG ĐIỀU HÀNH NHANH (Dự báo + Việc cần làm) — 2 cột dưới hero, từ 14h */
.hact{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:0 0 12px}
.hac{border:1px solid var(--line);border-radius:16px;padding:12px 13px;background:var(--card);min-width:0}
.hac-h{font-size:10.5px;font-weight:800;color:#e8ebfa;text-transform:uppercase;letter-spacing:.03em;margin-bottom:7px}
.hac-v{font-family:'Geist Mono','Geist',monospace;font-size:34px;font-weight:900;letter-spacing:-.045em;line-height:1;color:#fff}
.hac-v.good{color:var(--good)}.hac-v.warn{color:var(--warn)}.hac-v.bad{color:var(--bad)}
.hac-s{font-size:10px;color:var(--mut);font-weight:600;margin:1px 0 7px}
.hac-l{font-size:11.5px;color:#d4dbf0;font-weight:600;line-height:1.5;margin-top:3px}
.hac-l b{color:#fff;font-weight:800}
.hac-l .bad{color:var(--bad);font-weight:800}.hac-l .warn{color:var(--warn);font-weight:800}
.hac.fc.good{border-color:rgba(23,201,131,.4);background:rgba(23,201,131,.07)}
.hac.fc.warn{border-color:rgba(245,170,23,.4);background:rgba(245,170,23,.07)}
.hac.fc.bad{border-color:rgba(245,69,92,.4);background:rgba(245,69,92,.07)}
.hac.act{border-color:rgba(167,139,250,.28)}
@media(max-width:360px){.hact{grid-template-columns:1fr}}
/* ⏱ SO CÙNG GIỜ HÔM QUA — dòng đánh giá vùng theo mốc giờ */
.hcmp{margin-top:10px;font-size:13.5px;font-weight:600;color:#d4dbf0;line-height:1.7;
 padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.04)}
.hcmp .hvi{font-size:16px}
.hcmp b{font-family:'Geist',sans-serif;font-size:14.5px;font-weight:800;letter-spacing:.01em;color:#fff}
.hcmp i{font-style:normal;color:var(--mut);font-weight:400;font-size:12px}
.hcmp .cq{display:inline-block;padding:2px 8px;border-radius:7px;font-weight:800;font-size:13px;
 font-variant-numeric:tabular-nums;margin-left:1px}
.hcmp .cq.up{color:#2ee88c;background:rgba(23,201,131,.2)}
.hcmp .cq.dn{color:#ff6274;background:rgba(245,69,92,.2)}
.hcmp .cq.fl{color:var(--mut);background:rgba(255,255,255,.06)}
.hcmp .cq.nt{color:var(--txt);background:rgba(255,255,255,.08)}
/* SO CÙNG GIỜ — header + hàng CHIP (nhãn nhỏ · số to Mono · delta màu) */
.hcmp-h{font-size:11.5px;font-weight:700;color:var(--mut);letter-spacing:.03em;margin-bottom:9px;text-transform:uppercase}
.hcmp-h b{font-family:'Geist',sans-serif;font-size:11.5px;color:#e3e8f7;font-weight:800}
.hcmp-h i{font-style:normal;color:var(--mut);font-weight:400;font-size:10px;text-transform:none}
.scqrow{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.scq{display:flex;flex-direction:column;align-items:flex-start;gap:1px;background:rgba(255,255,255,.04);
 border:1px solid var(--line);border-radius:11px;padding:7px 11px}
.scq i{font-style:normal;color:var(--mut);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.03em}
.scq>b{font-family:'Geist Mono','Geist',monospace;font-weight:800;font-size:19px;color:#fff;font-variant-numeric:tabular-nums;letter-spacing:-.03em;line-height:1.05}
.scq .cq{margin:2px 0 0;font-size:11px;padding:2px 7px}
/* ĐIỂM NÓNG — header + DÒNG (nhãn trái · số phải) */
.dgh2{font-size:12px;font-weight:700;color:#e3e8f7;letter-spacing:.03em;display:flex;align-items:center;gap:7px}
.dgh2 b{font-family:'Geist',sans-serif;font-weight:800;color:#ff7b81}
.diag .dcv{font-size:10.5px;font-weight:700;white-space:nowrap}
.dgrows{display:flex;flex-direction:column;margin-top:8px}
.dgr{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid var(--line)}
.dgr:first-child{border-top:0}
.dgl{font-size:12.5px;color:var(--mut);font-weight:600;min-width:0}
.dgv{font-size:13px;color:#cfd6ec;font-weight:600;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.dgv b{font-family:'Geist Mono','Geist',monospace;color:#fff;font-weight:800;font-size:15.5px;letter-spacing:-.02em}
.dgs{color:var(--mut);font-weight:500;font-size:11px}
/* ⏱ bấm xổ ra bảng chi tiết SO CÙNG GIỜ HÔM QUA theo từng AM */
details.hcmp.cx>summary{cursor:pointer;list-style:none;display:block;position:relative;padding-right:18px}
details.hcmp.cx>summary::-webkit-details-marker{display:none}
details.hcmp.cx .hcv{position:absolute;right:11px;top:11px;color:var(--mut);font-size:12px;transition:transform .2s}
details.hcmp.cx[open] .hcv{transform:rotate(180deg)}
.amwrap{margin-top:9px;border-top:1px solid var(--line);padding-top:9px;overflow-x:auto}
.amnote{font-size:10px;color:var(--mut);margin-bottom:6px}
table.amtab{width:100%;border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums}
table.amtab th,table.amtab td{padding:6px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06);white-space:nowrap}
table.amtab th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em}
table.amtab th.aml,table.amtab td.aml{text-align:left;font-weight:700;color:var(--txt)}
table.amtab tbody tr:last-child td{border-bottom:none}
table.amtab td b{font-weight:800}
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
.sectitle{font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#e3e8f7;margin:14px 3px 8px}
/* ===== LƯỚI CHỈ SỐ (Phương án A) — ô gọn 2 cột, bấm bung full-width ===== */
.prilist{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:12px;background:none;border:none;padding:0}
.gt{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:11px 12px 10px;
 text-decoration:none;color:var(--txt);overflow:hidden;min-height:84px;
 display:flex;flex-direction:column;gap:5px}
.gt.bd{border-color:rgba(251,113,133,.42)} .gt.wn{border-color:rgba(251,191,36,.36)} .gt.vi{border-color:rgba(167,139,250,.4)}
.gt .gth{display:flex;align-items:center;gap:7px}
.gt .gti{width:26px;height:26px;border-radius:9px;flex:none;display:grid;place-items:center;font-size:14px;
 background:rgba(255,255,255,.06);border:1px solid var(--line)}
.gt.bd .gti{background:rgba(251,113,133,.16);border-color:rgba(251,113,133,.4)}
.gt.wn .gti{background:rgba(251,191,36,.14);border-color:rgba(251,191,36,.34)}
.gt.vi .gti{background:rgba(167,139,250,.16);border-color:rgba(167,139,250,.4)}
.gt .gtl{font-size:11.5px;font-weight:700;color:var(--mut);line-height:1.2}
.gt .gtcar{margin-left:auto;color:var(--mut);font-size:11px;flex:none;transition:transform .2s}
.gt .gtv{font-family:'Geist Mono','Geist',monospace;font-weight:800;font-size:24px;letter-spacing:-.01em;line-height:1;
 font-variant-numeric:tabular-nums}
.gt.bd .gtv{color:var(--bad)} .gt.wn .gtv{color:var(--warn)}
.gt svg.spk{width:100%;height:26px;display:block;margin-top:auto}
.gt .dl{font-family:Manrope,sans-serif;font-size:11px;font-weight:800;vertical-align:middle;margin-left:4px;
 letter-spacing:-.02em;white-space:nowrap}
.gt .dl.up{color:var(--good)} .gt .dl.dn{color:var(--bad)} .gt .dl.fl{color:var(--mut)}
.gt .gtd{padding-top:8px;margin-top:4px;border-top:1px solid var(--line)}
.gt .gtd .bc{margin:8px 0 0}
details.gt{padding:11px 12px 10px}
details.gt>summary{display:flex;flex-direction:column;gap:5px;cursor:pointer;list-style:none}
details.gt>summary::-webkit-details-marker{display:none}
details.gt>summary:active{transform:scale(.997)}
details.gt[open]{grid-column:1/-1}          /* bung → chiếm cả 2 cột */
details.gt[open] .gtcar{transform:rotate(90deg);color:var(--txt)}
.diag{background:rgba(255,255,255,.04);
 border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0 0;
 font-size:13.5px;line-height:1.7;color:#d4dbf0}
/* 🤖 Phân tích điểm nóng theo khung giờ */
.aidx{margin:10px 0 0;border-radius:14px;padding:12px 14px;font-size:13.5px;line-height:1.7;color:#d4dbf0;
 border:1px solid rgba(245,170,23,.42);background:linear-gradient(120deg,rgba(245,170,23,.12),rgba(255,255,255,.03))}
details.aidx>summary{cursor:pointer;list-style:none;display:block;position:relative;padding-right:18px;color:var(--txt);font-weight:600}
details.aidx>summary::-webkit-details-marker{display:none}
.aidx summary b{color:var(--warn)}
.aidx .dcv{position:absolute;right:0;top:2px;color:var(--mut);font-size:13px;transition:transform .2s}
details.aidx[open] .dcv{transform:rotate(180deg)}
.aibd{margin-top:9px;border-top:1px solid rgba(255,255,255,.08);padding-top:9px;display:flex;flex-direction:column;gap:7px}
.aibd b{color:var(--txt);font-weight:800}
.airow.aiact{color:var(--txt);font-weight:600;border-top:1px solid rgba(255,255,255,.08);padding-top:8px;margin-top:2px}
.aibd b.rk{color:var(--bad)}
.airk{color:var(--txt);font-size:13.5px}.airk i{color:var(--mut);font-weight:400;font-size:11.5px;font-style:normal}
.aiis{margin-top:3px;padding-left:2px;font-size:12.5px;line-height:1.55}
.diag b{color:#fff;font-weight:800;font-size:14px}
details.diag>summary{cursor:pointer;list-style:none;display:block;position:relative;padding-right:18px}
details.diag>summary::-webkit-details-marker{display:none}
.dcv{position:absolute;right:0;top:2px;color:var(--mut);font-size:13px;transition:transform .2s}
details.diag[open] .dcv{transform:rotate(180deg)}
/* 🔎 Bưu cục cần theo dõi sâu sát — bản CHỐT 23h30 (frozen, đọc từ watch_chot.json) */
.wchot-hdr{font-size:11.5px;color:var(--mut);font-weight:600;margin:12px 2px 0;letter-spacing:.01em}
.wchot-hdr .hvi{font-size:13px}.wchot-hdr b{color:var(--warn);font-weight:800}
.wchot-hdr + .watch{margin-top:5px}
.watch{border-radius:14px;padding:13px 14px 12px;margin:10px 0 0;
 background:linear-gradient(160deg,rgba(242,69,92,.14),rgba(245,170,23,.05) 55%,transparent);
 border:1px solid rgba(242,69,92,.34)}
.whead{font-weight:800;font-size:14px;letter-spacing:.02em;color:#ff7b81;margin-bottom:5px}
details.watch>summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px}
details.watch>summary::-webkit-details-marker{display:none}
details.watch>summary .whead{margin-bottom:0;flex:1;min-width:0}
details.watch[open]>summary{margin-bottom:12px}
.wcv{flex:none;color:#ff9aab;font-size:11.5px;font-weight:800;white-space:nowrap}
.wcv .wvc{display:none}
details.watch[open] .wcv .wvo{display:none}
details.watch[open] .wcv .wvc{display:inline}
.wsub{font-size:12px;color:var(--mut);line-height:1.5;margin-bottom:12px}
.wsub b{color:var(--txt)}.wsub i{color:#d7b3b5;font-style:normal;font-size:11px}
.wbc{position:relative;border:1px solid var(--line);border-radius:12px;background:var(--card);
 padding:10px 12px 9px;margin-bottom:8px;overflow:hidden}
.wbc:last-child{margin-bottom:0}
.wbc::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:rgb(var(--h,242,69,92))}
.wbc.bad{--h:242,69,92}.wbc.warn{--h:245,170,23}.wbc.good{--h:23,201,131}
.wbh{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.wrk{flex:none;width:22px;height:22px;border-radius:7px;background:rgba(242,69,92,.2);color:#ff9aab;
 font-weight:800;font-size:12px;display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums}
.wbn{flex:1;min-width:0;font-weight:800;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wbm{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11px;color:var(--mut);margin-bottom:5px;font-variant-numeric:tabular-nums}
.wbm b{color:var(--txt);font-weight:800}.wbm .bd b{color:#ff9aab}
.wtag{font-size:10.5px;color:var(--warn);font-weight:700}
details.wbc>summary{list-style:none;cursor:pointer}
details.wbc>summary::-webkit-details-marker{display:none}
details.wbc .wtag{margin-bottom:0}
.wcar{flex:none;color:rgb(var(--h,242,69,92));font-size:12px;transition:transform .2s;margin-left:2px}
details.wbc[open] .wcar{transform:rotate(180deg)}
.wdet{margin-top:10px;border-top:1px solid var(--line);padding-top:10px;display:flex;flex-direction:column;gap:9px}
.wdet.wempty{font-size:11px;color:var(--mut)}
.wsrow{display:flex;flex-direction:column;gap:4px}
.wsl{font-size:11px;color:var(--mut);font-weight:600}
.wsl b{color:var(--txt);font-weight:800;margin-left:3px;font-variant-numeric:tabular-nums}
.wsk{background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:9px;padding:6px 8px 3px}
.wsk svg.spk{width:100%;height:34px;display:block}
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
/* 🏤 Bảng tổng quát Bưu cục — ô NỔI BẬT, to & cân bằng */
.bcfeat{margin:6px 0 16px;border-width:1.5px;border-color:rgba(var(--h),.7);
 background:linear-gradient(120deg,rgba(var(--h),.30),#0d1526 70%);
 box-shadow:0 10px 34px rgba(var(--h),.20),inset 0 1px 0 rgba(255,255,255,.05)}
.bcfeat>summary{padding:18px 18px;gap:16px}
.bcfeat .mic{width:58px;height:58px;border-radius:17px;font-size:30px;background:rgba(var(--h),.32);border-color:rgba(var(--h),.6)}
.bcfeat .mn{font-size:21px}
.bcfeat .ms{font-size:12.5px;margin-top:4px}
.bcfeat .ms b{color:#9ec9f0;font-weight:800}
.bcfeat .mbig{font-size:38px}
.bcfeat .mbig .u{font-size:15px;margin-left:4px}
.bcfeat .cvar{font-size:18px}
@media(max-width:430px){.bcfeat>summary{padding:15px 14px;gap:12px}.bcfeat .mic{width:50px;height:50px;font-size:26px}
 .bcfeat .mn{font-size:18px}.bcfeat .mbig{font-size:32px}}
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
.sl{color:var(--mut);font-size:10.5px;margin-top:3px;line-height:1.3;word-break:break-word}
.slx{color:var(--mut);font-size:9.5px;margin-top:3px;font-variant-numeric:tabular-nums;white-space:nowrap}
.slx b{color:#fff;font-weight:800}.slx b.bad{color:var(--bad)}
/* chip ▲/▼ so cùng giờ hôm qua trên mỗi ô dải chỉ số */
.sd{margin-top:4px;font-size:9.5px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em;
 display:inline-block;padding:1px 5px;border-radius:7px;line-height:1.35}
.sd.up{color:var(--good);background:rgba(23,201,131,.14)}
.sd.dn{color:var(--bad);background:rgba(245,69,92,.14)}
.sd.nt{color:var(--mut);background:rgba(255,255,255,.07)}
.sd.fl{color:var(--mut);background:rgba(255,255,255,.05)}
/* mũi tên %GTC so TB 7 ngày trên thẻ AM / Tỉnh / Bưu cục — ĐỒNG BỘ cỡ/kiểu với cột So TB7 của
   bảng NV (.nvc): chữ trần xanh/đỏ, KHÔNG nền chip, bỏ đuôi 'đ' (▲ xanh tốt hơn · ▼ đỏ kém) */
.ga{flex:none;font-size:14px;font-weight:850;font-variant-numeric:tabular-nums;
 letter-spacing:-.02em;line-height:1.4;min-width:46px;text-align:right}
.ga.up{color:var(--good)}
.ga.dn{color:var(--bad)}
.ga.fl{color:var(--mut)}
/* Cụm chỉ số phải header thẻ: pill %GTC (dưới cột %GTC NV) + mũi tên So TB7 (dưới cột So TB7 NV),
   neo phải trùng mép bảng drv để THẲNG CỘT với bảng nhân viên bên dưới. */
.bcmx{flex:none;display:flex;align-items:center;width:123px;margin-right:-7px}
.bcmx .pill{flex:none;margin-right:auto}
/* CỘT RIÊNG "So TB7" — chênh %GTC NV vs TB 7 ngày, cỡ chữ BẰNG các cột số khác */
.nvc{font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em;white-space:nowrap}
.nvc.up{color:var(--good)}.nvc.dn{color:var(--bad)}.nvc.fl{color:var(--mut)}
/* TikTok %GTC NV GỌN đặt kèm sau pill %GTC */
.ttn{font-size:9px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em;white-space:nowrap;margin-left:6px}
.ttn.good{color:var(--good)}.ttn.warn{color:var(--warn)}.ttn.bad{color:var(--bad)}.ttn.na{color:var(--mut)}
/* cột %GTC: pill LUÔN sát phải (min-width cố định), chip TikTok đứng trước → pill THẲNG HÀNG dọc */
table.drv td:nth-child(5),td.pgc{white-space:nowrap;text-align:right}
table.drv td:nth-child(5) .pill,td.pgc .pill{min-width:50px;vertical-align:middle}
td.pgc .ttn{margin:0 4px 0 0;vertical-align:middle}
/* cột So TB7: nowrap canh phải thẳng hàng dọc */
table.drv td:nth-child(6){white-space:nowrap;text-align:right}

.eod{display:flex;align-items:center;justify-content:space-between;gap:8px;text-decoration:none;color:var(--txt);
 background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;
 padding:14px 16px;margin-bottom:8px;font-weight:700;font-size:14px}
.eod .arw{color:#d4dbf0;font-size:12px;font-weight:600}
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
.mtile.feat .mspark{width:66px;flex:none;margin-top:0;align-self:center;height:20px}
.mtile.feat .mn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mtile.feat .mbig span{font-size:15px;color:rgba(var(--h),1);margin-left:2px}

.sec{font-size:12.5px;font-weight:800;letter-spacing:.05em;color:#e3e8f7;text-transform:uppercase;margin:20px 4px 10px}
/* TAB gom 3 phần drill (AM · Tỉnh · Bưu cục) — sticky dưới header cho dễ chuyển */
.dtabs{display:flex;gap:6px;margin:20px 0 10px;position:sticky;top:calc(44px + env(safe-area-inset-top));z-index:15;
 background:var(--bg);padding:4px 0}
.dtab{flex:1;text-align:center;padding:10px 4px;border-radius:12px;font-size:12.5px;font-weight:800;
 border:1px solid var(--line);color:var(--mut);background:var(--card);cursor:pointer;white-space:nowrap;
 font-family:'Geist',sans-serif;-webkit-tap-highlight-color:transparent;transition:.15s}
.dtab.on{background:rgba(56,185,248,.14);border-color:rgba(56,185,248,.5);color:#8bd4fb}
.dtab:active{transform:scale(.97)}
.tsub{font-size:11.5px;color:var(--mut);font-weight:600;margin:0 4px 11px;letter-spacing:.01em}
.tpane[hidden]{display:none}

.provs{display:flex;flex-direction:column;gap:8px}
.prow{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:14px;padding:12px 14px;
 display:grid;grid-template-columns:1fr auto;gap:8px 10px;align-items:center}
.prow.good{border-left-color:var(--good)}.prow.warn{border-left-color:var(--warn)}.prow.bad{border-left-color:var(--bad)}
.pl{display:flex;align-items:center;gap:9px;font-size:15px}
.prow .bar{grid-column:1/-1}
.pmeta{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:3px 9px;align-items:center;color:var(--mut);font-size:10.5px;letter-spacing:-.1px;font-variant-numeric:tabular-nums;line-height:1.5}

.dot{width:9px;height:9px;border-radius:50%;flex:none;background:#4b5168}
.dot.good{background:var(--good)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}

.pill{display:inline-flex;align-items:center;justify-content:center;min-width:58px;padding:4px 13px;border-radius:99px;
 font-family:'Geist Mono','Geist',monospace;font-weight:800;font-size:16px;color:var(--ink);font-variant-numeric:tabular-nums;line-height:1.25;letter-spacing:-.02em}
.pill.sm{min-width:50px;font-size:14px;padding:3px 9px}
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
/* Bưu cục LỒNG trong AM/tỉnh: nền SÁNG GIẢM DẦN xuống dưới (--pos 0→1) → tăng tương phản,
   số liệu nổi rõ hơn (chữ sáng trên nền tối dần). --pos mặc định 0 cho mục không xếp hạng. */
.bc.sub{--pos:0;margin:7px 0;border-radius:13px;border-color:rgba(var(--h),calc(.30 - .14*var(--pos)));
 background:linear-gradient(0deg,rgba(6,9,17,calc(.36*var(--pos))),rgba(6,9,17,calc(.36*var(--pos)))),
  linear-gradient(180deg,rgba(var(--h),calc(.12 - .07*var(--pos))),rgba(255,255,255,.015))}
.bc.sub summary{padding:9px 10px;gap:7px}
.bc.sub[open]{background:linear-gradient(0deg,rgba(6,9,17,calc(.30*var(--pos))),rgba(6,9,17,calc(.30*var(--pos)))),
  linear-gradient(180deg,rgba(var(--h),.13),rgba(255,255,255,.02))}
.bc.sub .dtl{padding:0 2px 6px}
/* Tương phản số liệu trong bưu cục con rõ hơn (chữ sáng, số đậm) */
.bc.sub .bcn{color:#f3f6ff}
.bc.sub .bcm{color:#b7c0de;font-weight:600}
.bc.sub .bcm>span{color:#cdd5ee}
.bc.sub .bcm .w{color:#ff8e93}.bc.sub .bcm .ltc{color:#4de39a}.bc.sub .bcm .gtb{color:#ffc658}
.none{color:var(--mut);font-size:12.5px;padding:6px 2px 10px}

table.drv{width:100%;border-collapse:collapse;font-size:12px}
table.drv th,table.drv td{padding:7px 3px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
table.drv th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em;border-bottom:1px solid var(--line)}
table.drv th:first-child,table.drv td:first-child{text-align:left}
table.drv tbody tr:last-child td{border-bottom:none}
td.nv{font-weight:600;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.nv .sc{font-size:9.5px;color:var(--mut);font-weight:400;margin-top:1px;overflow:hidden;text-overflow:ellipsis}
.tt{color:#e879c8;font-weight:700}
/* NV bấm mở → %GTC theo xã/phường (live hôm nay) */
tr.dnv{cursor:pointer}
tr.dnv .cx{display:inline-block;color:var(--mut);font-size:8px;margin-right:5px;transition:transform .18s;vertical-align:middle}
tr.dnv.op .cx{transform:rotate(90deg);color:var(--txt)}
.lb{font-size:12px;vertical-align:middle;filter:drop-shadow(0 0 2px rgba(245,170,23,.6))}
/* Tên NV/BC/AM/tỉnh tô màu theo %GTC (đỏ<60 · vàng<70 · xanh≥70) */
.nmc.bad,.bcn.bad{color:var(--bad)!important}
.nmc.warn,.bcn.warn{color:var(--warn)!important}
.nmc.good,.bcn.good{color:var(--good)!important}
.nmc.na,.bcn.na{color:var(--mut)!important}
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
  td.nv{max-width:112px}
  .pill.sm{min-width:46px;font-size:12.5px;padding:3px 7px}
  .ga{font-size:12.5px}   /* So TB7 — to hơn cho dễ nhìn trên iPhone */
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
        rows, giao_120h, lgt_w = asyncio.run(fetch_live(token))
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
        noon12_trend = _fetch_region_noon12_trend(14)  # Tồn gán 12h cấp vùng (bao_cao_gio gio=12)
        am_ctrend = _fetch_am_crit_trend(14)           # 3 chỉ số điều hành theo AM (drill từ 3 card)
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
        # Lưu KHỐI LƯỢNG đơn giao đã gán → đồ thị. CHỐT SỐ ~12h TRƯA (xe đã ra hàng đầy đủ,
        # đại diện tải cả ngày; cuối ngày giao gần hết nên số nhỏ, không đúng). Cửa sổ VN
        # 11:50–12:40; các slot khác KHÔNG ghi → giữ số 12h cả ngày. ENV WEIGHT_NOON=1 để ép.
        _vnm = datetime.now(VN).hour * 60 + datetime.now(VN).minute
        if ((11 * 60 + 50) <= _vnm <= (12 * 60 + 40)
                or os.environ.get("WEIGHT_NOON", "").strip() == "1"):
            _store_weight(sum(r.get("weight_g", 0) for r in rows) / 1000.0)
        # Lưu Tồn Lấy / Tồn Trả (chưa gán) hôm nay → đồ thị 14 ngày (builds dần, chốt cuối ngày)
        _store_ton(sum(r.get("ton_lay", 0) for r in rows), sum(r.get("ton_tra", 0) for r in rows))
        # DỰ BÁO VỀ ĐÍCH: lưu %GTC theo GIỜ + đọc độ bứt tốc lịch sử (same-hour → cuối ngày)
        _gtc_now = sum(r["gtc"] for r in rows)
        _tot_now = sum(r["total"] for r in rows)
        _rp_now = _pct(_gtc_now, _tot_now)
        _snap_now = _region_snapshot(rows, giao_120h, lgt_w)
        _bcsnap_now = _bc_snapshot(rows)
        _nvsnap_now = _nv_snapshot(rows)
        _store_hourly_pct(_rp_now, gtc=_gtc_now, total=_tot_now, g120=giao_120h,
                          snap=_snap_now, bcsnap=_bcsnap_now, nvsnap=_nvsnap_now)
        fc_uplift = _fetch_hour_uplift(datetime.now(VN).hour)
        # NHỊP ĐỘ: đọc SAU khi lưu (bản vừa lưu bị loại vì cách <30'); cần mốc giờ trước.
        pace = _fetch_today_rate(_gtc_now)
        # SO CÙNG GIỜ HÔM QUA: đọc bao_cao_gio hôm qua tại giờ này (cho dòng ⏱ + chip dải).
        cmp_y = _fetch_hour_vs_yesterday(datetime.now(VN).hour)
        # Mũi tên BC/AM/Tỉnh: so TRUNG BÌNH cùng giờ 7 NGÀY gần nhất (tổng quan chính xác hơn).
        bc_avg = _fetch_hour_bc_avg(datetime.now(VN).hour)
        # Mũi tên từng NHÂN VIÊN: so TB cùng giờ 7 ngày của chính NV.
        nv_avg = _fetch_hour_nv_avg(datetime.now(VN).hour)
        # Dọn bao_cao_gio cho gọn — 1 lần/ngày (khung 6h sáng, slot đầu ngày).
        if datetime.now(VN).hour == 6:
            _prune_bao_cao_gio()
    except Exception as e:
        # Token hết hạn / API lỗi → rơi về snapshot Supabase thay vì để trang trắng/đọng.
        if _write_fallback(e):
            return
        raise SystemExit("Fetch live lỗi và không có snapshot dự phòng: %s" % e)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    # Khối nhúng Bảng điều khiển Bưu cục — ĐÓNG BĂNG BẢN CHỐT 23h15 (giữ nguyên cả ngày).
    #   • Cửa sổ VN 23:10–23:40 (lần chạy đầu, nếu chốt chưa phải hôm nay) → CHỤP số hiện tại,
    #     ghi buucuc_chot.json vào docs/ (deploy lên Pages). ENV BUUCUC_CHOT=1 để ép chụp.
    #   • Các slot khác → ĐỌC bản chốt đã deploy (giữ qua slot như eod.html) → đóng băng.
    #   • Chưa có chốt (ngày đầu) → tạm hiện live.
    bcm = None
    _bc_rows, _bc_coll, _bc_label = rows, collectable, None
    try:
        import report_buucuc
        now_vn = datetime.now(VN)
        today = now_vn.strftime("%Y-%m-%d")
        vn_min = now_vn.hour * 60 + now_vn.minute
        force_chot = os.environ.get("BUUCUC_CHOT", "").strip() == "1"
        chot = report_buucuc.load_chot_http(slug)
        in_window = (23 * 60 + 10) <= vn_min <= (23 * 60 + 40)      # cửa sổ chốt ~23h15
        # Đã có bản chốt CHÍNH THỨC (chụp trong cửa sổ) của HÔM NAY chưa? (min >= 23:10)
        has_window_chot = bool(chot and chot.get("ngay") == today
                               and (chot.get("min") or 0) >= (23 * 60 + 10))
        if force_chot or (in_window and not has_window_chot):
            _bc_label = now_vn.strftime("%H:%M · %d/%m")
            report_buucuc.save_chot(rows, collectable, outdir, today, _bc_label, vn_min)
        elif chot:
            _bc_rows, _bc_coll, _bc_label = chot["rows"], chot.get("coll"), chot.get("label")
        bcm = report_buucuc.embed(_bc_rows, _bc_coll, label=_bc_label)
    except Exception as e:
        logger.warning("Nhúng Bảng điều khiển Bưu cục lỗi (bỏ qua): %s", str(e)[:150])
    h = gen_html(rows, giao_120h, nv_xuly, nvm, collectable, trend, g120_trend, cx_trend, pt_trend, nvdat_tr, bcm,
                 fc_uplift=fc_uplift, pace=pace, cmp_y=cmp_y, bc_avg=bc_avg, lgt_w=lgt_w,
                 nv_avg=nv_avg, noon12_trend=noon12_trend, am_ctrend=am_ctrend)
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

    # Trang BẢNG ĐIỀU KHIỂN BƯU CỤC riêng — DÙNG CÙNG BẢN CHỐT 23h15 như ô nhúng.
    try:
        import report_buucuc
        with open(os.path.join(outdir, "buucuc.html"), "w", encoding="utf-8") as f:
            f.write(report_buucuc.gen_html(_bc_rows, _bc_coll, label=_bc_label))
    except Exception as e:
        logger.warning("Tạo buucuc.html lỗi (bỏ qua): %s", str(e)[:150])

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

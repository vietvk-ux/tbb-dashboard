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


def _region_snapshot(rows, giao_120h=None):
    """Snapshot toàn bộ chỉ số DẢI của vùng (để lưu theo giờ → so cùng giờ hôm qua).
    Khớp đúng cách tính trong gen_html (R + on_road/late/nv_low/vpct)."""
    tot = gtc = vngh = vgtc = cod = ltc = ltb = ontrip = chua_gan = 0
    ot_tot = ot_done = late = nv_low = 0
    for r in rows:
        tot += r.get("total", 0); gtc += r.get("gtc", 0); ontrip += r.get("ontrip", 0)
        vngh += r.get("vngh", 0); vgtc += r.get("vngh_gtc", 0); cod += r.get("cod_gtb", 0)
        ltc += r.get("ltc", 0); ltb += r.get("ltb", 0); chua_gan += r.get("backlog", 0)
        for d in r.get("drivers", []):
            ot_tot += d.get("ot_tot", 0); ot_done += d.get("ot_done", 0)
            st = d.get("st")
            if st is not None and (st.hour * 60 + st.minute) > 570:
                late += 1
            if d.get("total", 0) >= 20:
                p = _pct(d.get("gtc", 0), d["total"])
                if p is not None and p < 50:
                    nv_low += 1
    return {"total": tot, "ontrip": ontrip, "on_road": max(ot_tot - ot_done, 0), "gtc": gtc,
            "late": late, "vngh": vngh, "vngh_gtc": vgtc, "vpct": _pct(vgtc, vngh),
            "cod_gtb": cod, "ltc": ltc, "ltb": ltb, "nv_low": nv_low, "g120": giao_120h,
            "chua_gan": chua_gan}


def _bc_snapshot(rows):
    """Snapshot [gtc,total] TỪNG BƯU CỤC (để so %GTC cùng giờ hôm qua). AM/Tỉnh = tổng BC con."""
    return {r["name"]: [r.get("gtc", 0), r.get("total", 0)]
            for r in rows if r.get("total", 0) > 0}


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
                       json={"snap": None, "bcsnap": None}, headers=hdr, timeout=20)
        # 2) Xoá dòng quá cũ (có filter ngay → PostgREST cho phép)
        requests.delete("%s/rest/v1/bao_cao_gio?ngay=lt.%s" % (url, cut_del), headers=hdr, timeout=20)
    except Exception:
        pass


def _store_hourly_pct(pct, gtc=None, total=None, g120=None, snap=None, bcsnap=None):
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
        rich = dict(base, gtc=gtc, total=total, phut=now.minute, g120=g120, snap=snap, bcsnap=bcsnap)
        hdr = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
        u = "%s/rest/v1/bao_cao_gio?on_conflict=ngay,gio" % url
        r = requests.post(u, json=[rich], headers=hdr, timeout=20)
        # Cột mới chưa có (chưa migrate) → PostgREST 400 → vẫn ghi được pct cho dự báo.
        if r.status_code >= 400:
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
    gid = "sg%d" % _spark._i
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
        # TÊN NV tô màu theo %GTC (giống bưu cục): đỏ<60 · vàng<70 · xanh≥70
        nmc = "nmc " + _cls(pc2)
        P.append("<tr%s><td class='nv'>%s<span class='%s'>%s</span>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                 "<td><span class='pill sm %s'>%s%%</span></td><td>%s</td></tr>"
                 % (attr, cx, nmc, _esc(d["name"]), lb, _n(d["total"]), _n(d["gtc"]),
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
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
             "<span class='pill %s'>%s%%</span></div>" % (cls, cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
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


async def fetch_lgt_weight(session, hub_ids, token):
    """KHỐI LƯỢNG (kg thực, gram) TỒN ĐỌNG Lấy/Giao/Trả toàn vùng — 1 CALL get-general-info
    gộp hết hub_ids (≤200). Trả bản gộp 'ALL' (nhiều hub) hoặc cộng các entry. Giao = DELIVER +
    DELIVER_PRIORITY (ưu tiên giao). Trả về gram: {lay, giao, tra}. None nếu lỗi."""
    try:
        d = await _post(session, "/core/oss/v1/report/get-general-info",
                        {"hub_ids": [str(h) for h in hub_ids], "view_mode": "WARD",
                         "order_type": "ALL"}, hub_ids[0] if hub_ids else "1", token)
        data = d.get("data") or []
        alle = [e for e in data if str(e.get("hub_id") or "") == "ALL"]
        use = alle if alle else data            # nhiều hub → chỉ entry ALL; 1 hub → entry đó
        agg = {"PICK": 0, "DELIVER": 0, "DELIVER_PRIORITY": 0, "RETURN": 0}
        for e in use:
            for gi in (e.get("general_infos") or []):
                ot = gi.get("order_type")
                if ot in agg:
                    agg[ot] += gi.get("total_weight") or 0
        return {"lay": agg["PICK"], "giao": agg["DELIVER"] + agg["DELIVER_PRIORITY"],
                "tra": agg["RETURN"]}
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
        # Khối lượng tồn Lấy/Giao/Trả toàn vùng — 1 call gộp (hero).
        lgt_w = await fetch_lgt_weight(session, [str(h["locationCode"]) for h in hubs], token)
        return rows, giao_120h, lgt_w


def gen_html(rows, giao_120h=None, nv_xuly=None, nvm=None, collectable=None, trend=None,
             g120_trend=None, cx_trend=None, pt_trend=None, nvdat_trend=None, bcm=None,
             fc_uplift=None, pace=None, cmp_y=None, bc_avg=None, lgt_w=None):
    now = datetime.now(VN)
    # SO CÙNG GIỜ HÔM QUA / TB 7 NGÀY chỉ hiện từ ~10h sáng (ENV CMP_MIN_HOUR): trước đó %GTC
    # luỹ kế biến động mạnh do 'sóng gán đơn' đầu ngày (mẫu số nhảy vọt) → so sánh dễ hiểu nhầm.
    # Tắt sớm = dòng ⏱, chip dải .sd, mũi tên thẻ .ga đều ẩn tới khi đủ giờ.
    if now.hour < float(os.environ.get("CMP_MIN_HOUR", "10")):
        cmp_y = None
        bc_avg = None
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
    P.append("<div class='hpct'>%s<span>%%</span></div>"
             % (reg_pct if reg_pct is not None else "—"))
    P.append(_bar(reg_pct, _cls(reg_pct), target=70))
    P.append("<div class='hsub'><b class='hn'>%s</b> / <b class='hn'>%s</b> đơn giao thành công · "
             "LTC <b class='hn'>%s</b> · cần giao <b class='hn'>%s</b></div>"
             % (_n(R["gtc"]), _n(R["total"]), _n(R["ltc"]), _n(can_giao)))
    # ⚖️ Khối lượng TỒN ĐỌNG Lấy/Giao/Trả toàn vùng (kg thực) — ngay dưới subtext %GTC (08/10)
    if lgt_w:
        P.append("<div class='hlgt'><span class='hvi'>⚖️</span> Tồn kho <i>(kg thực)</i> · "
                 "📥 Lấy <b>%s</b> · 🚚 Giao <b>%s</b> · ↩️ Trả <b>%s</b></div>"
                 % (_kgfmt(lgt_w.get("lay", 0) / 1000.0), _kgfmt(lgt_w.get("giao", 0) / 1000.0),
                    _kgfmt(lgt_w.get("tra", 0) / 1000.0)))
    # DÒNG CẢNH BÁO RỦI RO VẬN HÀNH — CHỈ hiện khi có rủi ro (tắc gán / backlog tăng bất thường).
    # (Bỏ dòng đánh giá thường ngày '🟡 CẦN CHÚ Ý · còn X điểm' 08/10 — trùng số %GTC + bar + chips.)
    if _oprisk:
        P.append("<div class='hverd bad'><span class='hvi'>⚠️</span> "
                 "<b>RỦI RO VẬN HÀNH</b> · %s</div>" % " · ".join(_oprisk))

    # ===== 🎯 NHỊP ĐỘ CÁN ĐÍCH — tốc độ thực + "cần X đơn/giờ để chạm 70%" + dự phóng chốt =====
    # Cùng base với %GTC Hero (R["total"]). Chỉ hiện trong giờ vận hành (9h30 → giờ giao cao
    # điểm kết thúc, mặc định 22h; ENV PACE_CUTOFF_HOUR). Đòn bẩy điều hành số 1: biết nhịp
    # thực, theo đà này sẽ chốt bao nhiêu %, và cần đẩy mạnh cỡ nào để chạm mục tiêu.
    _cutoff_h = float(os.environ.get("PACE_CUTOFF_HOUR", "22"))
    _now_t = now.hour + now.minute / 60.0
    if R["total"] > 0 and reg_pct is not None and 9.5 <= _now_t < _cutoff_h:
        _need = int(round(0.70 * R["total"])) - R["gtc"]
        _hleft = max(_cutoff_h - _now_t, 0.1)
        if _need <= 0:
            P.append("<div class='hpace good'><span class='hvi'>🎯</span> "
                     "<b>ĐÃ CHẠM MỐC 70%</b> · giữ nhịp tới hết ngày</div>")
        else:
            _rneed = _need / _hleft
            # Sát giờ cutoff (<45') → "cần X/giờ" nổ số (chia khoảng rất nhỏ) → vô nghĩa, bỏ vế đó.
            _show_need = _hleft >= 0.75
            _need_cl = (" · cần <b>%s/giờ</b> để đạt 70%%" % _n(int(round(_rneed)))) if _show_need else ""
            # Dự phóng chốt ngày = %GTC hiện tại + độ bứt tốc LỊCH SỬ (median eod−giờ này),
            # chính xác hơn nhiều so với kéo dài tuyến tính tốc độ (giao giảm dần cuối ngày).
            _proj = None
            if fc_uplift is not None and reg_pct is not None:
                _proj = min(100, max(reg_pct, int(round(reg_pct + fc_uplift))))
            if pace is not None and pace > 0:
                # Màu theo DỰ PHÓNG chốt (nếu có) — thước đo thực tế nhất.
                if _proj is not None:
                    if _proj >= 70:
                        pk, pic, pst = "good", "✅", "ĐÚNG ĐÀ ĐẠT MỤC TIÊU"
                    elif yp is not None and _proj >= yp:
                        pk, pic, pst = "warn", "🎯", "TRÊN NHỊP HÔM QUA"
                    else:
                        pk, pic, pst = "bad", "🔴", "DƯỚI ĐÀ MỤC TIÊU"
                    _body = ("thực <b>%s đơn/giờ</b> · theo đà chốt <b>~%d%%</b>%s"
                             % (_n(int(round(pace))), _proj, _need_cl))
                else:
                    pk, pic, pst = "warn", "🎯", "NHỊP ĐỘ GIAO"
                    _tail = (_need_cl + (" <i>(còn %s đơn · %.1fh)</i>" % (_n(_need), _hleft)
                                         if _show_need else " <i>(còn %s đơn tới 70%%)</i>" % _n(_need)))
                    _body = "thực <b>%s đơn/giờ</b>%s" % (_n(int(round(pace))), _tail)
            else:
                pk, pic, pst = "warn", "🎯", "NHỊP CẦN ĐẠT"
                if _show_need:
                    _body = ("cần <b>%s đơn/giờ</b> <i>(còn %s đơn · %.1fh tới %dh)</i> để chạm 70%% "
                             "· đang đo tốc độ thực…" % (_n(int(round(_rneed))), _n(_need),
                                                         _hleft, int(_cutoff_h)))
                else:
                    _body = ("còn <b>%s đơn</b> tới mốc 70%% · đang đo tốc độ thực…" % _n(_need))
            P.append("<div class='hpace %s'><span class='hvi'>%s</span> <b>%s</b> · %s</div>"
                     % (pk, pic, pst, _body))

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
        # %GTC — màu theo tốt/xấu (cao hơn hôm qua = tốt)
        if "pct" in cmp_y:
            d, s = _dpp(reg_pct, cmp_y["pct"])
            c = "up" if d > 0 else ("dn" if d < 0 else "fl")
            parts.append("<b>%%GTC</b> %d%% <span class='cq %s'>%s đ</span> <i>(hôm qua %s%%)</i>"
                         % (reg_pct, c, s, ("%g" % cmp_y["pct"]).replace(".", ",")))
        # Đã gán (total) — nhiều hơn = trung tính (volume)
        if "total" in cmp_y:
            d, s = _dpc(R["total"], cmp_y["total"])
            parts.append("<b>Đã gán</b> %s <span class='cq nt'>%s</span>" % (_n(R["total"]), s))
        # GTC (đơn giao TC) — nhiều hơn = tốt
        if "gtc" in cmp_y:
            d, s = _dpc(R["gtc"], cmp_y["gtc"])
            c = "up" if d > 0 else ("dn" if d < 0 else "fl")
            parts.append("<b>GTC</b> %s <span class='cq %s'>%s</span>" % (_n(R["gtc"]), c, s))
        # Backlog 120h — nhiều hơn = XẤU (đảo màu)
        if "g120" in cmp_y and giao_120h is not None:
            d, s = _dpc(giao_120h, cmp_y["g120"])
            c = "dn" if d > 0 else ("up" if d < 0 else "fl")
            parts.append("<b>Backlog</b> %s <span class='cq %s'>%s</span>" % (_n(giao_120h), c, s))
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
                    z = _yam_h.setdefault(_a, [0, 0]); z[0] += _gt[0]; z[1] += _gt[1]
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
                    _dg, _sg = _dpc(_v["total"], _yv[1])
                    _dv, _sv = _dpc(_v["gtc"], _yv[0])
                    _R2.append("<tr><td class='aml'>%s</td><td><b>%s%%</b> %s</td>"
                               "<td>%s %s</td><td>%s %s</td></tr>"
                               % (_esc(_amn), ("%g" % _tp).replace(".", ","),
                                  _cqd(_dp, _sp, True, suffix="đ"),
                                  _n(_v["total"]), _cqd(_dg, _sg, neutral=True),
                                  _n(_v["gtc"]), _cqd(_dv, _sv, True)))
                _R2.append("</tbody></table>")
                _am_tbl = "".join(_R2)

        if parts:
            _sumline = ("<span class='hvi'>⏱</span> <b>SO CÙNG GIỜ HÔM QUA · mốc %dh</b> · %s%s"
                        % (_ch, " · ".join(parts), _miss))
            if _am_tbl:
                P.append("<details class='hcmp cx'><summary>%s<span class='hcv'>▾</span></summary>"
                         "<div class='amwrap'><div class='amnote'>Chi tiết từng AM · so cùng giờ "
                         "hôm qua · %%GTC tụt nhiều nhất lên đầu</div>%s</div></details>"
                         % (_sumline, _am_tbl))
            else:
                P.append("<div class='hcmp'>%s</div>" % _sumline)

    if trend and len(trend) >= 2:
        pcts = [t["pct"] for t in trend]
        P.append("<div class='sparkwrap'>%s</div>" % _spark(pcts, "#fbbf24", w=280, h=50, target=70))
    if ref:
        # (Bỏ chip 'Còn X điểm tới 70%' 08/10 — trùng thanh bar + mục tiêu; giữ 3 mốc tham chiếu.)
        P.append("<div class='href'>"
                 "<span class='rc'>🎯 Mục tiêu <b>70%%</b></span>"
                 "<span class='rc'>Hôm qua <b>%d%%</b></span>"
                 "<span class='rc'>TB %d ngày <b>%d%%</b></span></div>"
                 % (ref["yp"], ref["ndays"], ref["avg7p"]))
    P.append("</section>")

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
        if abs(d) > 999:            # chặn trần an toàn, tránh số delta phi lý
            d = 999 if d > 0 else -999
        up = d > 0
        cls = "up" if (up == up_good) else "dn"   # thay đổi TỐT = xanh · XẤU = đỏ
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

    # Chỗ đặt sẵn cho khối ĐIỂM NÓNG CHÚ Ý — đẩy LÊN TRÊN Tổng Quan Vận Hành (07/10).
    # Phần tính toán vẫn ở dưới (cần am_pcts…); tại đó ghi vào P[_diag_slot] thay vì append cuối.
    _diag_slot = len(P)
    P.append("")
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
                        # Đã gán theo xã (gộp từ drivers' wards[xã][0]) + chưa gán theo xã (backlog_wards)
                        w_dg = {}
                        for d in r.get("drivers", []):
                            for ward, gv in d.get("wards", {}).items():
                                if gv and gv[0] > 0:
                                    w_dg[ward] = w_dg.get(ward, 0) + gv[0]
                        w_cg = {}
                        for ward, cnt in r.get("backlog_wards", []):
                            w_cg[ward] = w_cg.get(ward, 0) + cnt
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
            "<details class='diag'><summary>⚡ <b>Điểm Nóng Chú Ý</b> · %s<span class='dcv'>▾</span></summary>"
            "<div class='ddtl'><div class='dnote'>Top 5 cần chú ý mỗi mục · "
            "%%GTC luỹ kế trong ngày (sáng còn thấp là bình thường)</div>%s</div></details>"
            % (" · ".join(diag), "".join(grps)))
    else:
        P[_diag_slot] = "<div class='diag'>⚡ <b>Điểm Nóng Chú Ý</b> · %s</div>" % " · ".join(diag)

    # ===== Dải chỉ số · Bento (Mẫu 3) · màu theo từng chỉ số =====
    vpct = _pct(R["vngh_gtc"], R["vngh"])
    _cgo = ("onclick=\"var d=document.getElementById('cgd');if(d){d.open=true;"
            "d.scrollIntoView({behavior:'smooth',block:'start'});}\"")
    # Kỷ luật ngữ nghĩa: NEU = ô đếm số (tối trung tính) · màu chỉ dành cho ô cảnh báo
    NEU, AMBER, RED, GREEN = "128,140,174", "247,185,85", "242,88,95", "47,208,122"
    xp_rgb = RED if late_cnt else NEU                       # xuất phát muộn: đỏ khi >0
    tt_rgb = {"good": GREEN, "warn": AMBER, "bad": RED}.get(_cls(vpct), NEU) if vpct is not None else NEU
    #      icon, giá trị, nhãn, màu rgb, extra, neu, key(snap), raw(số nay), up_good
    kpis = [
        ("📥", _n(R["total"]),     "Đã gán",          NEU,   "", True,  "total",    R["total"],    None),
        ("🏃", _n(R["ontrip"]),    "Đang chạy",       NEU,   "", True,  "ontrip",   R["ontrip"],   None),
        ("🚛", _n(on_road),        "Còn phải giao",   NEU,   "", True,  "on_road",  on_road,       False),
        ("✅", _n(R["gtc"]),       "GTC nay",         NEU,   "", True,  "gtc",      R["gtc"],      True),
        ("🕘", _n(late_cnt),       "XP muộn &gt;9h30",xp_rgb,"", not late_cnt, "late", late_cnt,   False),
        ("🛍️", _n(R["vngh"]),     "TikTok gán",      NEU,   "", True,  "vngh",     R["vngh"],     None),
        ("🛍️", _n(R["vngh_gtc"]), "TikTok GTC",      NEU,   "", True,  "vngh_gtc", R["vngh_gtc"], True),
        ("🛍️", (("%d%%" % vpct) if vpct is not None else "—"), "%GTC TikTok", tt_rgb, "", vpct is None, "vpct", vpct, True),
        ("💰", _codm(R["cod_gtb"]),"COD GTB",         AMBER, "", False, "cod_gtb",  R["cod_gtb"],  False),
        ("🛒", _n(R["ltc"]),       "LTC",             NEU,   "", True,  "ltc",      R["ltc"],      True),
        ("📦", _n(R["ltb"]),       "LTB",  (RED if R["ltb"] else NEU), "", not R["ltb"], "ltb", R["ltb"], False),
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
        sd = _sd(raw, ysnap.get(key), up_good, is_pct=(key == "vpct")) if key else ""
        P.append("<div class='%s' style='--h:%s'%s><div class='sv'>%s</div>"
                 "<div class='sl'>%s %s</div>%s</div>" % (cls, rgb, oc, val, ic, lab, sd))
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
        return "<span class='ga %s' title='so TB 7 ngày cùng giờ'>%s%sđ</span>" % (
            "up" if up else "dn", "▲" if up else "▼", ("%.1f" % abs(d)).replace(".", ","))

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
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "%s<span class='pill %s'>%s%%</span></div>"
                 % (cls, cls, _esc(amn), _gtc_arrow(v["gtc"], v["total"], _y_am.get(amn)),
                    cls, pc if pc is not None else "—"))
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
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "%s<span class='pill %s'>%s%%</span></div>"
                 % (cls, cls, _esc(PROV_NAME.get(pv, pv)), _gtc_arrow(v["gtc"], v["total"], _y_prov.get(pv)),
                    cls, pc if pc is not None else "—"))
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
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn %s'>%s</span>"
                 "%s<span class='pill %s'>%s%%</span></div>"
                 % (cls, cls, _esc(r["name"]), _gtc_arrow(r["gtc"], r["total"], _ybc.get(r["name"])),
                    cls, pc if pc is not None else "—"))
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
             "<span class='up' style='color:var(--good);font-weight:800'>▲</span>/<span class='dn' style='color:var(--bad);font-weight:800'>▼ %</span> cạnh số ở lưới = thay đổi <b>ngày chốt hôm qua so hôm kia</b> · <span style='color:var(--good)'>xanh = tốt lên</span> · <span style='color:var(--bad)'>đỏ = xấu đi</span> (tồn/NV cần xử lý tăng là xấu; sản lượng/GTC/NV đạt tăng là tốt)<br>"
             "📥 <b>Đã gán</b> = đơn đã xếp vào chuyến hôm nay · ⏳ <b>Chưa gán</b> = đơn tồn ở kho chưa xếp chuyến<br>"
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
.hsub b.hn{color:#18c07a;font-weight:800;font-size:13.5px}
.hlgt{margin-top:8px;font-size:11.5px;font-weight:600;color:var(--mut);line-height:1.5;
 padding:7px 11px;border-radius:11px;border:1px solid var(--line);background:rgba(255,255,255,.035);
 font-variant-numeric:tabular-nums}
.hlgt .hvi{font-size:13px}
.hlgt i{font-style:normal;color:var(--mut);font-size:10px}
.hlgt b{font-family:Sora,sans-serif;color:var(--txt);font-weight:800;font-size:12.5px;margin-left:1px}
.hsub .ld{color:var(--txt);font-weight:700}
.href{display:flex;flex-wrap:wrap;gap:6px 7px;margin-top:12px}
.rc{font-size:11px;color:var(--mut);background:rgba(255,255,255,.05);border:1px solid var(--line);
 border-radius:99px;padding:3px 10px;font-variant-numeric:tabular-nums;white-space:nowrap}
.rc b{color:var(--txt);font-weight:800}
.sparkwrap{margin-top:10px}
.spklbl{font-size:9.5px;color:var(--mut);font-weight:600;letter-spacing:.02em;margin-bottom:3px}
svg.spk{display:block}
/* DẢI MỎNG ĐÁNH GIÁ trong Hero (thay dòng Xu hướng) */
.hverd{margin-top:11px;font-size:11.5px;font-weight:600;color:var(--mut);line-height:1.4;
 padding:7px 11px;border-radius:11px;border:1px solid var(--line);background:rgba(255,255,255,.035)}
.hverd .hvi{font-size:13px}
.hverd b{font-family:Sora,sans-serif;font-size:12px;letter-spacing:.01em;color:var(--txt)}
.hverd.good{border-color:rgba(23,201,131,.42);background:rgba(23,201,131,.09)}.hverd.good b{color:var(--good)}
.hverd.warn{border-color:rgba(245,170,23,.42);background:rgba(245,170,23,.09)}.hverd.warn b{color:var(--warn)}
.hverd.bad{border-color:rgba(245,69,92,.42);background:rgba(245,69,92,.09)}.hverd.bad b{color:var(--bad)}
.hverd b.rk{color:var(--bad)!important;background:rgba(245,69,92,.16);padding:1px 7px;border-radius:7px;
 font-size:11px;white-space:nowrap}
/* 🎯 NHỊP ĐỘ CÁN ĐÍCH — dòng điều hành (cần X đơn/giờ) */
.hpace{margin-top:8px;font-size:11.5px;font-weight:600;color:var(--mut);line-height:1.45;
 padding:8px 11px;border-radius:11px;border:1px solid var(--line);background:rgba(255,255,255,.035)}
.hpace .hvi{font-size:13px}
.hpace b{font-family:Sora,sans-serif;font-size:12px;letter-spacing:.01em;color:var(--txt)}
.hpace i{font-style:normal;color:var(--mut);font-weight:500}
.hpace.good{border-color:rgba(23,201,131,.45);background:rgba(23,201,131,.10)}.hpace.good b{color:var(--good)}
.hpace.warn{border-color:rgba(245,170,23,.45);background:rgba(245,170,23,.10)}.hpace.warn b{color:var(--warn)}
.hpace.bad{border-color:rgba(245,69,92,.45);background:rgba(245,69,92,.10)}.hpace.bad b{color:var(--bad)}
/* ⏱ SO CÙNG GIỜ HÔM QUA — dòng đánh giá vùng theo mốc giờ */
.hcmp{margin-top:8px;font-size:11px;font-weight:600;color:var(--mut);line-height:1.6;
 padding:8px 11px;border-radius:11px;border:1px solid var(--line);background:rgba(255,255,255,.035)}
.hcmp .hvi{font-size:13px}
.hcmp b{font-family:Sora,sans-serif;font-size:11px;letter-spacing:.01em;color:var(--txt)}
.hcmp i{font-style:normal;color:var(--mut);font-weight:400;font-size:10px}
.hcmp .cq{display:inline-block;padding:0 6px;border-radius:7px;font-weight:800;font-size:10.5px;
 font-variant-numeric:tabular-nums;margin-left:1px}
.hcmp .cq.up{color:var(--good);background:rgba(23,201,131,.14)}
.hcmp .cq.dn{color:var(--bad);background:rgba(245,69,92,.14)}
.hcmp .cq.fl{color:var(--mut);background:rgba(255,255,255,.06)}
.hcmp .cq.nt{color:var(--txt);background:rgba(255,255,255,.08)}
/* ⏱ bấm xổ ra bảng chi tiết SO CÙNG GIỜ HÔM QUA theo từng AM */
details.hcmp.cx>summary{cursor:pointer;list-style:none;display:block;position:relative;padding-right:18px}
details.hcmp.cx>summary::-webkit-details-marker{display:none}
details.hcmp.cx .hcv{position:absolute;right:9px;top:8px;color:var(--mut);font-size:11px;transition:transform .2s}
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
.sectitle{font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);margin:14px 3px 8px}
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
.gt .gtv{font-family:Sora,sans-serif;font-weight:800;font-size:24px;letter-spacing:-.01em;line-height:1;
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
/* 🏤 Bảng tổng quát Bưu cục — ô NỔI BẬT, to & cân bằng */
.bcfeat{margin:6px 0 16px;border-width:1.5px;border-color:rgba(var(--h),.7);
 background:linear-gradient(120deg,rgba(var(--h),.30),#0d1526 70%);
 box-shadow:0 10px 34px rgba(var(--h),.20),inset 0 1px 0 rgba(255,255,255,.05)}
.bcfeat>summary{padding:18px 18px;gap:16px}
.bcfeat .mic{width:58px;height:58px;border-radius:17px;font-size:30px;background:rgba(var(--h),.32);border-color:rgba(var(--h),.6)}
.bcfeat .mn{font-size:21px}
.bcfeat .ms{font-size:12.5px;margin-top:4px}
.bcfeat .ms b{color:#9ec9f0;font-weight:800}
.bcfeat .mbig{font-size:38px;text-shadow:0 0 18px rgba(var(--h),.4)}
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
.sl{color:var(--mut);font-size:10.5px;margin-top:3px;white-space:nowrap}
/* chip ▲/▼ so cùng giờ hôm qua trên mỗi ô dải chỉ số */
.sd{margin-top:4px;font-size:9.5px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:-.02em;
 display:inline-block;padding:1px 5px;border-radius:7px;line-height:1.35}
.sd.up{color:var(--good);background:rgba(23,201,131,.14)}
.sd.dn{color:var(--bad);background:rgba(245,69,92,.14)}
.sd.nt{color:var(--mut);background:rgba(255,255,255,.07)}
.sd.fl{color:var(--mut);background:rgba(255,255,255,.05)}
/* mũi tên %GTC so cùng giờ hôm qua trên thẻ AM / Tỉnh / Bưu cục (▲ xanh tốt hơn · ▼ đỏ kém) */
.ga{flex:none;margin-right:7px;font-size:10px;font-weight:800;font-variant-numeric:tabular-nums;
 letter-spacing:-.02em;padding:1px 6px;border-radius:7px;line-height:1.4}
.ga.up{color:var(--good);background:rgba(23,201,131,.15)}
.ga.dn{color:var(--bad);background:rgba(245,69,92,.15)}
.ga.fl{color:var(--mut);background:rgba(255,255,255,.07)}

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
        _snap_now = _region_snapshot(rows, giao_120h)
        _bcsnap_now = _bc_snapshot(rows)
        _store_hourly_pct(_rp_now, gtc=_gtc_now, total=_tot_now, g120=giao_120h,
                          snap=_snap_now, bcsnap=_bcsnap_now)
        fc_uplift = _fetch_hour_uplift(datetime.now(VN).hour)
        # NHỊP ĐỘ: đọc SAU khi lưu (bản vừa lưu bị loại vì cách <30'); cần mốc giờ trước.
        pace = _fetch_today_rate(_gtc_now)
        # SO CÙNG GIỜ HÔM QUA: đọc bao_cao_gio hôm qua tại giờ này (cho dòng ⏱ + chip dải).
        cmp_y = _fetch_hour_vs_yesterday(datetime.now(VN).hour)
        # Mũi tên BC/AM/Tỉnh: so TRUNG BÌNH cùng giờ 7 NGÀY gần nhất (tổng quan chính xác hơn).
        bc_avg = _fetch_hour_bc_avg(datetime.now(VN).hour)
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
                 fc_uplift=fc_uplift, pace=pace, cmp_y=cmp_y, bc_avg=bc_avg, lgt_w=lgt_w)
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

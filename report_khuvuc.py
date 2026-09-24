"""TRANG 9 — BẢN ĐỒ KHU VỰC (khuvuc.html).

Phân tích GTC theo khu vực (xã/phường + toạ độ) từng ngày, giữ 30 ngày gần nhất,
kèm bản đồ nhiệt và biểu đồ so sánh theo TUẦN / THÁNG.

Lưu trữ: file JSON trong repo `khuvuc_data/YYYY-MM-DD.json` (1 file/ngày, prune >30).
Không dùng Supabase (payload toạ độ nặng — tránh ăn quota). 30 file ~2-5MB.

Hai chế độ (đối số dòng lệnh):
  collect   — gom dữ liệu 1 ngày rồi ghi khuvuc_data/<ngày>.json.
              Dùng lại payload của report.fetch_report (đã bóc item + geo) → KHÔNG
              tốn call API thêm khi gọi từ report_db_sync. Chạy độc lập cũng được.
  render    — đọc 30 file JSON gần nhất → dựng docs/<slug>/khuvuc.html.

Env: NHANH_TOKEN (collect độc lập), DASH_SLUG. EOD_DATE=YYYY-MM-DD (collect 1 ngày).
"""
from __future__ import annotations
import os, sys, json, glob, html, math, asyncio, logging
import collections
from datetime import datetime, timedelta, timezone, date

try:
    from am_map import AM_OF
except Exception:
    AM_OF = {}

VN = timezone(timedelta(hours=7))
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "khuvuc_data")
KEEP_DAYS = 30
CELL = 0.02  # ~2km
logger = logging.getLogger("khuvuc")


# ══════════════════ COLLECT ══════════════════
def build_day_payload(report_payload):
    """Từ payload của report.fetch_report (trips có items + geo) → dict lưu 1 ngày."""
    trips = [t for t in report_payload["trips"] if "error" not in t]
    # dedup theo (bưu cục, mã đơn): giữ lần thành công nếu có
    od = {}   # (bc, code) -> {ward,dist,city,lat,lng,succ,nv}
    for t in trips:
        bc = t.get("bc") or "?"
        nv = t.get("driver_name") or "—"
        for r in t.get("items", []):
            if r.get("type") != "DELIVER":
                continue
            code = r.get("code")
            if not code:
                continue
            succ = bool(r.get("succ"))
            key = (bc, code)
            prev = od.get(key)
            if prev is None:
                od[key] = {"ward": r.get("ward"), "dist": r.get("dist"), "city": r.get("city"),
                           "lat": r.get("lat"), "lng": r.get("lng"), "succ": succ, "nv": nv}
            elif succ and not prev["succ"]:
                prev.update({"succ": True, "nv": nv, "ward": r.get("ward"), "dist": r.get("dist"),
                             "city": r.get("city"), "lat": r.get("lat"), "lng": r.get("lng")})

    cells = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])   # (gy,gx)->[n,g,Σlat,Σlng]
    wards = collections.defaultdict(lambda: [0, 0])             # (dist,ward)->[n,g]
    nvw = collections.defaultdict(lambda: [0, 0])               # (bc,nv,dist,ward)->[n,g]
    provs = collections.defaultdict(lambda: [0, 0, 0.0, 0.0, 0])  # city->[n,g,Σlat,Σlng,nCoord]
    nv_dist = collections.defaultdict(collections.Counter)      # (bc,nv)->Counter(dist)
    tot = gtc = 0
    for (bc, code), o in od.items():
        ward = o["ward"] or "?"; dist = o["dist"] or "?"; city = o["city"] or "?"
        s = 1 if o["succ"] else 0
        tot += 1; gtc += s
        wards[(dist, ward)][0] += 1; wards[(dist, ward)][1] += s
        nvw[(bc, o["nv"], dist, ward)][0] += 1; nvw[(bc, o["nv"], dist, ward)][1] += s
        pv = provs[city]; pv[0] += 1; pv[1] += s
        nv_dist[(bc, o["nv"])][dist] += 1
        lat, lng = o["lat"], o["lng"]
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)) \
                and 19.5 < lat < 23.5 and 102.0 < lng < 106.5:
            gy = round(lat / CELL); gx = round(lng / CELL)
            c = cells[(gy, gx)]
            c[0] += 1; c[1] += s; c[2] += lat; c[3] += lng
            pv[2] += lat; pv[3] += lng; pv[4] += 1

    cell_list = [[round(c[2] / c[0], 4), round(c[3] / c[0], 4), c[0], c[1]]
                 for c in cells.values() if c[0] > 0]
    lats = [c[0] for c in cell_list]; lngs = [c[1] for c in cell_list]
    bbox = ({"minLat": min(lats), "maxLat": max(lats), "minLng": min(lngs), "maxLng": max(lngs)}
            if cell_list else None)
    # đơn lạc tuyến: đơn ở huyện khác "địa bàn chính" của NV
    stray = []
    for (bc, nv), dc in nv_dist.items():
        if not dc:
            continue
        main_dist = dc.most_common(1)[0][0]
        out = sum(n for d, n in dc.items() if d != main_dist)
        if out > 0:
            stray.append([bc, nv, main_dist, out, sum(dc.values())])
    stray.sort(key=lambda x: -x[3])

    return {
        "ngay": report_payload["date"].isoformat() if hasattr(report_payload["date"], "isoformat") else str(report_payload["date"]),
        "tot": tot, "gtc": gtc,
        "provs": sorted([[k, v[0], v[1],
                          round(v[2] / v[4], 4) if v[4] else None,
                          round(v[3] / v[4], 4) if v[4] else None]
                         for k, v in provs.items() if k != "?"], key=lambda x: -x[1]),
        "bbox": bbox,
        "cells": cell_list,
        "wards": sorted([[d, w, n, g] for (d, w), (n, g) in wards.items()], key=lambda x: -x[2]),
        "nv": [[bc, nv, d, w, n, g] for (bc, nv, d, w), (n, g) in nvw.items()],
        "stray": stray[:20],
    }


def write_day(day_payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    ngay = day_payload["ngay"]
    path = os.path.join(DATA_DIR, "%s.json" % ngay)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(day_payload, f, ensure_ascii=False, separators=(",", ":"))
    # prune >KEEP_DAYS file cũ nhất
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
    for old in files[:-KEEP_DAYS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def collect_standalone(token, day):
    """Gom 1 ngày chạy độc lập (tốn call API). Dùng khi backfill."""
    import report
    payload = asyncio.run(report.fetch_report(token, day))
    dp = build_day_payload(payload)
    p = write_day(dp)
    logger.info("collect %s → %s (%d đơn, GTC %d)", day, p, dp["tot"], dp["gtc"])
    return dp


# ══════════════════ RENDER ══════════════════
def _load_days(n=KEEP_DAYS):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))[-n:]
    out = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except Exception:
            pass
    return out


def _esc(s): return html.escape(str(s if s is not None else ""))
def _n(x): return f"{int(x):,}".replace(",", ".")
def _pct(a, b): return round(a * 100 / b) if b else 0
def _cls(p): return "bad" if p < 60 else ("warn" if p < 80 else "good")


def _iso_week(ds):
    y, m, d = map(int, ds.split("-"))
    iso = date(y, m, d).isocalendar()
    return "%d-T%02d" % (iso[0], iso[1])


def _svg_bars(series, unit="%", target=None, vfmt=None):
    """series: list [(label, value, cls)] → SVG cột dọc gọn, responsive."""
    if not series:
        return "<div class='none'>Chưa đủ dữ liệu.</div>"
    W, H = 700, 200
    pad_b, pad_t, pad_l = 34, 18, 26
    n = len(series)
    bw = (W - pad_l) / n
    vmax = max((v for _, v, _ in series), default=1) or 1
    vmax = max(vmax, target or 0)
    top = math.ceil(vmax / 20) * 20 if unit == "%" else vmax * 1.15
    top = top or 1
    P = ["<svg viewBox='0 0 %d %d' class='chart' preserveAspectRatio='xMidYMid meet'>" % (W, H)]
    # lưới ngang
    for gy in range(0, 101, 25) if unit == "%" else []:
        y = pad_t + (H - pad_t - pad_b) * (1 - gy / 100)
        P.append("<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' class='grid'/>" % (pad_l, y, W, y))
        P.append("<text x='%d' y='%.1f' class='gl'>%d</text>" % (pad_l - 4, y + 3, gy))
    if target:
        yt = pad_t + (H - pad_t - pad_b) * (1 - target / top)
        P.append("<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' class='tgt'/>" % (pad_l, yt, W, yt))
    barw = min(bw * 0.68, 80)   # chặn bề rộng cột để 1-2 cột không bị kéo dài cả biểu đồ
    for i, (lab, val, cls) in enumerate(series):
        cx = pad_l + i * bw + bw / 2
        h = (H - pad_t - pad_b) * (val / top)
        y = (H - pad_b) - h
        P.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' rx='3' class='b %s'/>"
                 % (cx - barw / 2, y, barw, max(h, 1), cls))
        vtxt = vfmt(val) if vfmt else ("%s%s" % (val, unit if unit == "%" else ""))
        P.append("<text x='%.1f' y='%.1f' class='bv'>%s</text>"
                 % (cx, y - 4, vtxt))
        P.append("<text x='%.1f' y='%d' class='bx'>%s</text>" % (cx, H - 12, _esc(lab)))
    P.append("</svg>")
    return "".join(P)


def build_html(days):
    if not days:
        return ("<div style='padding:40px;text-align:center;color:#8792ad;font-family:sans-serif'>"
                "Chưa có dữ liệu khu vực. Trang sẽ có số sau lần chốt cuối ngày đầu tiên.</div>")
    latest = days[-1]

    # ----- SỐ ĐƠN theo ngày (14 ngày) · màu cột theo %GTC ngày đó -----
    dord_series = [(d["ngay"][8:] + "/" + d["ngay"][5:7], d["tot"], _cls(_pct(d["gtc"], d["tot"])))
                   for d in days][-14:]
    # ----- TOP HUYỆN/TP theo số đơn (ngày mới nhất) -----
    dist = collections.defaultdict(lambda: [0, 0])
    for dd, w, n, g in latest["wards"]:
        dist[dd][0] += n; dist[dd][1] += g
    top_dist = sorted(dist.items(), key=lambda x: -x[1][0])[:12]
    dmax = top_dist[0][1][0] if top_dist else 1

    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta http-equiv='refresh' content='900'>")
    P.append("<title>Bản đồ khu vực · TBB</title>")
    P.append(_CSS)
    P.append("<div class='wrap'>")
    P.append("<header class='top'><div class='brand'>🗺 BẢN ĐỒ KHU VỰC</div>"
             "<div class='ts'>%s · %d ngày</div></header>" % (_fmt(latest["ngay"]), len(days)))
    P.append("<div class='note'>Phân tích GTC theo <b>xã/phường</b> &amp; toạ độ giao hàng · gom mức xã trở lên "
             "(không lộ địa chỉ/GPS từng khách) · lưu 30 ngày · nguồn nhanh.ghn.vn.</div>")

    # dải chỉ số ngày mới nhất
    lp = _pct(latest["gtc"], latest["tot"])
    P.append("<section class='strip'>")
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>📦 Đơn giao</div></div>" % _n(latest["tot"]))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>✅ GTC</div></div>" % _n(latest["gtc"]))
    P.append("<div class='st'><div class='sv %s'>%d%%</div><div class='sl'>🎯 %%GTC</div></div>" % (_cls(lp), lp))
    P.append("<div class='st'><div class='sv'>%d</div><div class='sl'>🏘 Xã/phường</div></div>" % len(latest["wards"]))
    P.append("</section>")

    # ----- BẢN ĐỒ NHIỆT -----
    P.append("<div class='sec'>🔥 Bản đồ nhiệt · ngày %s</div>" % _fmt(latest["ngay"]))
    P.append("<div class='tabs'><div class='tab on' id='tA' onclick='setMode(0)'>🌡 Mật độ đơn</div>"
             "<div class='tab' id='tB' onclick='setMode(1)'>🎯 %GTC khu vực</div></div>")
    P.append("<div class='mapwrap' id='mw'><canvas id='cv'></canvas></div>")
    P.append("<div class='leg' id='legA'>Ít&nbsp;<span class='bar dens'></span>&nbsp;Nhiều đơn</div>")
    P.append("<div class='leg' id='legB' style='display:none'>🔴 %GTC thấp&nbsp;<span class='bar gtc'></span>"
             "&nbsp;🟢 cao&nbsp;·&nbsp;chấm to = nhiều đơn</div>")

    # ----- SỐ ĐƠN GIAO VỀ THEO NGÀY -----
    P.append("<div class='sec'>📦 Số đơn giao về theo ngày (14 ngày) · màu cột theo %GTC</div>")
    P.append("<div class='card'>%s</div>" % _svg_bars(dord_series, unit="đơn", vfmt=_n))

    # ----- TOP HUYỆN/TP THEO SỐ ĐƠN -----
    P.append("<div class='sec'>🏙 Top 12 Huyện/Thành phố có đơn nhiều nhất · ngày %s</div>" % _fmt(latest["ngay"]))
    P.append("<div class='tw'><table class='t'><thead><tr><th class='rk'>#</th><th class='l'>Huyện/TP</th>"
             "<th>Đơn</th><th>GTC</th><th>%GTC</th></tr></thead><tbody>")
    for i, (dd, (n, g)) in enumerate(top_dist, 1):
        p = _pct(g, n); wpc = round(n * 100 / dmax) if dmax else 0
        P.append("<tr><td class='rk'>%d</td>"
                 "<td class='l'><div class='dbar' style='width:%d%%'></div><span class='dnm'>%s</span></td>"
                 "<td><b>%s</b></td><td>%s</td><td><span class='pill %s'>%d%%</span></td></tr>"
                 % (i, wpc, _esc(dd), _n(n), _n(g), _cls(p), p))
    P.append("</tbody></table></div>")

    # ----- XÃ KHÓ GIAO -----
    P.append("<div class='sec'>🔴 Xã/phường khó giao nhất · %GTC thấp → cao (≥20 đơn)</div>")
    hard = sorted([w for w in latest["wards"] if w[2] >= 20], key=lambda w: _pct(w[3], w[2]))[:15]
    P.append("<div class='tw'><table class='t'><thead><tr><th class='l'>Huyện</th><th class='l'>Xã/Phường</th><th>Đơn</th><th>GTC</th><th>%GTC</th></tr></thead><tbody>")
    for dist, ward, n, g in hard:
        p = _pct(g, n)
        P.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td>%d</td><td>%d</td>"
                 "<td><span class='pill %s'>%d%%</span></td></tr>" % (_esc(dist), _esc(ward), n, g, _cls(p), p))
    P.append("</tbody></table></div>")

    # ----- TOP 20 XÃ ĐƠN NHIỀU NHẤT -----
    P.append("<div class='sec'>📦 Top 20 xã/phường có đơn giao về nhiều nhất vùng</div>")
    topw = sorted(latest["wards"], key=lambda w: -w[2])[:20]
    P.append("<div class='tw'><table class='t'><thead><tr><th class='rk'>#</th><th class='l'>Huyện</th><th class='l'>Xã/Phường</th><th>Đơn</th><th>GTC</th><th>%GTC</th></tr></thead><tbody>")
    for i, (dist, ward, n, g) in enumerate(topw, 1):
        p = _pct(g, n)
        P.append("<tr><td class='rk'>%d</td><td class='l'>%s</td><td class='l'>%s</td><td><b>%d</b></td><td>%d</td>"
                 "<td><span class='pill %s'>%d%%</span></td></tr>" % (i, _esc(dist), _esc(ward), n, g, _cls(p), p))
    P.append("</tbody></table></div>")

    # ----- DRILL AM → BƯU CỤC → NV × XÃ -----
    P.append("<div class='sec'>👤 GTC theo AM → Bưu cục → Nhân viên (theo xã/phường) · bấm mở</div>")
    by_bc = collections.defaultdict(list)
    for bc, nv, dist, ward, n, g in latest["nv"]:
        by_bc[bc].append((nv, dist, ward, n, g))

    def _bc_pct(bc):
        rr = by_bc[bc]
        return _pct(sum(r[4] for r in rr), sum(r[3] for r in rr))
    # gom bưu cục theo AM
    am_bcs = collections.defaultdict(list)
    for bc in by_bc:
        am_bcs[AM_OF.get(bc) or "(chưa phân AM)"].append(bc)

    def _am_ng(am):
        t = sum(r[3] for bc in am_bcs[am] for r in by_bc[bc])
        g = sum(r[4] for bc in am_bcs[am] for r in by_bc[bc])
        return t, g

    def _am_pct(am):
        t, g = _am_ng(am)
        return _pct(g, t) if t else 999
    for am in sorted(am_bcs, key=_am_pct):   # AM %GTC THẤP → CAO (kém lên đầu)
        at, ag = _am_ng(am); ap = _pct(ag, at)
        nnv = len(set(r[0] for bc in am_bcs[am] for r in by_bc[bc]))
        P.append("<details class='bc %s'><summary><div class='bch'><span class='dot %s'></span>"
                 "<span class='bcn'>%s</span><span class='pill %s'>%d%%</span></div>"
                 "<div class='pmeta'>🏤 %d BC · 👤 %d NV · 📦 %s · ✅ %s</div></summary><div class='dtl'>"
                 % (_cls(ap), _cls(ap), _esc(am), _cls(ap), ap if ap is not None else 0,
                    len(am_bcs[am]), nnv, _n(at), _n(ag)))
        for bc in sorted(am_bcs[am], key=_bc_pct):   # bưu cục %GTC THẤP → CAO
            rows = by_bc[bc]
            bt = sum(r[3] for r in rows); bg = sum(r[4] for r in rows)
            bp = _pct(bg, bt)
            nvset = sorted(set(r[0] for r in rows))
            P.append("<details class='bc sub %s'><summary><div class='bch'><span class='dot %s'></span>"
                     "<span class='bcn'>%s</span><span class='pill %s'>%d%%</span></div>"
                     "<div class='pmeta'>👤 %d NV · 📦 %s · ✅ %s</div></summary><div class='dtl'>"
                     % (_cls(bp), _cls(bp), _esc(bc), _cls(bp), bp, len(nvset), _n(bt), _n(bg)))

            def nv_pct(nv, rows=rows):
                r = [x for x in rows if x[0] == nv]
                return _pct(sum(x[4] for x in r), sum(x[3] for x in r))
            for nv in sorted(nvset, key=nv_pct):
                nr = [x for x in rows if x[0] == nv]
                nt = sum(x[3] for x in nr); ng = sum(x[4] for x in nr); npc = _pct(ng, nt)
                P.append("<details class='bc sub %s'><summary><div class='bch'><span class='dot %s'></span>"
                         "<span class='bcn'>%s</span><span class='pill %s'>%d%%</span></div>"
                         "<div class='pmeta'>📦 %d · ✅ %d</div></summary><div class='dtl'>"
                         % (_cls(npc), _cls(npc), _esc(nv), _cls(npc), npc, nt, ng))
                P.append("<table class='drv'><thead><tr><th class='l'>Huyện</th><th class='l'>Xã/Phường</th><th>Đơn</th><th>GTC</th><th>%GTC</th></tr></thead><tbody>")
                for nv2, dist, ward, n, g in sorted(nr, key=lambda x: -x[3]):
                    p = _pct(g, n)
                    P.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td>%d</td><td>%d</td>"
                             "<td><span class='pill sm %s'>%d%%</span></td></tr>" % (_esc(dist), _esc(ward), n, g, _cls(p), p))
                P.append("</tbody></table></div></details>")
            P.append("</div></details>")
        P.append("</div></details>")

    P.append("<a class='eod' href='nhanvien.html'><span>⚡ Năng suất nhân viên</span><span class='arw'>← trang trước</span></a>")
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span><span class='arw'>TBB</span></a>")
    P.append("<div class='foot'>%GTC = giao thành công / tổng đơn giao (gộp theo mã đơn) · địa bàn chính = huyện nhiều đơn nhất của NV<br>"
             "dữ liệu chốt cuối ngày · lưu 30 ngày gần nhất</div>")

    # data cho canvas heatmap (ngày mới nhất)
    hd = {"cells": [[c[0], c[1], c[2], c[3]] for c in latest["cells"]],
          "bbox": latest["bbox"] or {"minLat": 20, "maxLat": 23, "minLng": 102, "maxLng": 106},
          "provs": [{"name": p[0], "n": p[1], "lat": p[3], "lng": p[4]}
                    for p in latest["provs"][:8] if len(p) > 4 and p[3] is not None and p[1] >= 200]}
    P.append("<script>var HD=%s;</script>" % json.dumps(hd, ensure_ascii=False, separators=(",", ":")))
    P.append(_JS)
    P.append("</div></body></html>")
    return "\n".join(P)


def _fmt(ds):
    return "%s/%s/%s" % (ds[8:], ds[5:7], ds[:4])


# đặt centroid tỉnh từ bbox toàn vùng để dán nhãn (không cần toạ độ tỉnh chính xác)
_JS = r"""<script>
var cv=document.getElementById('cv'),mw=document.getElementById('mw'),mode=0;
var W=700,H=560;cv.width=W;cv.height=H;
var pts=HD.cells.map(function(c){return {lat:c[0],lng:c[1],n:c[2],g:c[3]};});
var b=HD.bbox,px=(b.maxLng-b.minLng)*0.06||0.1,py=(b.maxLat-b.minLat)*0.06||0.1;
var mnL=b.minLng-px,mxL=b.maxLng+px,mnA=b.minLat-py,mxA=b.maxLat+py;
function X(l){return (l-mnL)/(mxL-mnL)*W;}
function Y(a){return H-(a-mnA)/(mxA-mnA)*H;}
var maxN=1;pts.forEach(function(p){if(p.n>maxN)maxN=p.n;});
function heat(t){var s=[[16,32,58],[43,108,255],[255,242,168],[255,90,60]];t=Math.max(0,Math.min(1,t));
 var x=t*(s.length-1),i=Math.floor(x),f=x-i;if(i>=s.length-1){i=s.length-2;f=1;}var a=s[i],c=s[i+1];
 return 'rgb('+Math.round(a[0]+(c[0]-a[0])*f)+','+Math.round(a[1]+(c[1]-a[1])*f)+','+Math.round(a[2]+(c[2]-a[2])*f)+')';}
function gtcC(p){var r=p.g/p.n,R,G;if(r<0.5){R=239;G=Math.round(68+90*(r/0.5));}else{R=Math.round(239-205*((r-0.5)/0.5));G=Math.round(158+39*((r-0.5)/0.5));}return 'rgb('+R+','+G+',60)';}
function draw(){var ctx=cv.getContext('2d');ctx.clearRect(0,0,W,H);ctx.fillStyle='#0d1120';ctx.fillRect(0,0,W,H);
 if(mode==0){ctx.globalCompositeOperation='lighter';pts.forEach(function(p){var t=Math.sqrt(p.n/maxN),rad=8+t*32;
  var g=ctx.createRadialGradient(X(p.lng),Y(p.lat),0,X(p.lng),Y(p.lat),rad);g.addColorStop(0,heat(t));g.addColorStop(1,'rgba(13,17,32,0)');
  ctx.fillStyle=g;ctx.globalAlpha=.55;ctx.beginPath();ctx.arc(X(p.lng),Y(p.lat),rad,0,7);ctx.fill();});
  ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';}
 else{pts.slice().sort(function(a,b){return a.n-b.n;}).forEach(function(p){var rad=3+Math.sqrt(p.n/maxN)*15;
  ctx.fillStyle=gtcC(p);ctx.globalAlpha=.8;ctx.beginPath();ctx.arc(X(p.lng),Y(p.lat),rad,0,7);ctx.fill();});ctx.globalAlpha=1;}
 document.querySelectorAll('.lbl').forEach(function(e){e.remove();});
 var sc=mw.clientWidth/W;
 HD.provs.forEach(function(pv){if(pv.lat==null)return;var el=document.createElement('div');el.className='lbl';
  el.style.left=(X(pv.lng)*sc)+'px';el.style.top=(Y(pv.lat)*sc)+'px';el.textContent=pv.name.replace('Thành phố','TP').replace('Tỉnh ','');mw.appendChild(el);});}
function setMode(m){mode=m;document.getElementById('tA').classList.toggle('on',m==0);
 document.getElementById('tB').classList.toggle('on',m==1);
 document.getElementById('legA').style.display=m==0?'':'none';document.getElementById('legB').style.display=m==1?'':'none';draw();}
window.addEventListener('resize',draw);draw();
</script>"""


_CSS = """<style>
:root{--bg:#0a0d18;--card:#131829;--line:#1f2740;--txt:#e7ecf7;--mut:#8792ad;
--good:#22c55e;--warn:#f59e0b;--bad:#ef4444;--accent:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:12px}
.top{display:flex;justify-content:space-between;align-items:center;padding:6px 2px 10px}
.brand{font-weight:800;font-size:16px;letter-spacing:.02em}.ts{color:var(--mut);font-size:12px}
.note{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 12px;font-size:12px;color:var(--mut);margin-bottom:12px}
.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:6px}
.st{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 5px;text-align:center}
.sv{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}.sl{font-size:10px;color:var(--mut);margin-top:2px}
.sec{font-weight:700;font-size:13px;margin:18px 2px 8px;color:#c7d0e6}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.tabs{display:flex;gap:6px;margin-bottom:8px}
.tab{flex:1;text-align:center;padding:8px;border-radius:10px;background:var(--card);border:1px solid var(--line);font-size:12.5px;font-weight:600;cursor:pointer;color:var(--mut)}
.tab.on{background:rgba(59,130,246,.18);color:#dbe7ff;border-color:var(--accent)}
.mapwrap{position:relative;background:#0d1120;border:1px solid var(--line);border-radius:14px;overflow:hidden}
canvas{display:block;width:100%;height:auto}
.lbl{position:absolute;transform:translate(-50%,-50%);font-size:10px;font-weight:700;color:#cdd8f0;text-shadow:0 1px 3px #000,0 0 6px #000;pointer-events:none;white-space:nowrap}
.leg{display:flex;align-items:center;gap:8px;justify-content:center;margin:8px 0 2px;font-size:11px;color:var(--mut);flex-wrap:wrap}
.bar{height:10px;width:150px;border-radius:6px}
.bar.dens{background:linear-gradient(90deg,#10203a,#2b6cff,#fff2a8,#ff5a3c)}
.bar.gtc{background:linear-gradient(90deg,#ef4444,#f59e0b,#22c55e)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 6px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:rgba(255,255,255,.06);stroke-width:1}
.chart .tgt{stroke:var(--good);stroke-width:1;stroke-dasharray:4 3;opacity:.6}
.chart .gl{fill:var(--mut);font-size:9px;text-anchor:end}
.chart .b{fill:var(--accent)}
.chart .b.good{fill:var(--good)}.chart .b.warn{fill:var(--warn)}.chart .b.bad{fill:var(--bad)}
.chart .bv{fill:var(--txt);font-size:11px;font-weight:700;text-anchor:middle}
.chart .bx{fill:var(--mut);font-size:9.5px;text-anchor:middle}
table.t,table.drv{width:100%;border-collapse:collapse;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
table.t th,table.t td,table.drv th,table.drv td{padding:7px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
table.t th,table.drv th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase;letter-spacing:.02em}
table.t td.rk,table.t th.rk{text-align:center;color:var(--mut);width:22px;padding-left:2px;padding-right:2px}
table.t td.l{position:relative;overflow:hidden;text-align:left}
.dbar{position:absolute;left:0;top:3px;bottom:3px;background:rgba(247,185,85,.16);border-radius:0 4px 4px 0;z-index:0}
.dnm{position:relative;z-index:1}
td.l,th.l{text-align:left}td.l{max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-weight:700;font-size:11px}
.pill.sm{font-size:10px;padding:1px 6px}
.pill.good{background:rgba(34,197,94,.15);color:var(--good)}
.pill.warn{background:rgba(245,158,11,.15);color:var(--warn)}
.pill.bad{background:rgba(239,68,68,.15);color:var(--bad)}
.gtb{color:var(--bad)}
details.bc{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:12px;margin:7px 0;overflow:hidden}
details.bc.good{border-left-color:var(--good)}details.bc.warn{border-left-color:var(--warn)}details.bc.bad{border-left-color:var(--bad)}
details.bc summary{padding:10px 12px;cursor:pointer;list-style:none}details.bc summary::-webkit-details-marker{display:none}
.bch{display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%;flex:none}
.dot.good{background:var(--good)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.bcn{font-weight:700;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pmeta{font-size:11.5px;color:var(--mut);margin-top:5px}
.dtl{padding:2px 8px 10px}
details.bc.sub{margin:6px 0;border-radius:10px;background:rgba(255,255,255,.02)}
.eod{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:9px 0;text-decoration:none;color:var(--txt);font-weight:600}
.eod .arw{color:var(--mut);font-size:12px;font-weight:500}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:16px 0 10px}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:12px}
/* ===== Tối ưu iPhone / màn hình hẹp ===== */
@media (max-width:430px){
  .wrap{padding:10px 10px calc(14px + env(safe-area-inset-bottom))}
  .brand{font-size:15px}.note{font-size:11.5px;padding:8px 10px}
  .strip{gap:6px}.st{padding:9px 3px}.sv{font-size:16px}.sl{font-size:9.5px}
  .sec{font-size:12.5px;margin:16px 2px 7px}
  .tab{font-size:12px;padding:7px 4px}
  table.t th,table.t td,table.drv th,table.drv td{padding:5px 4px;font-size:11px}
  table.t th,table.drv th{font-size:9px}
  td.l,th.l{max-width:88px}
  .pill{font-size:10.5px;padding:2px 6px}.pill.sm{font-size:9.5px}
  .dtl{overflow-x:auto;-webkit-overflow-scrolling:touch;padding:2px 6px 9px}
  details.bc summary{padding:9px 10px}.pmeta{font-size:11px}
  .bcn{font-size:13px}
  .eod{padding:11px 12px;font-size:14px}
}
body{background:radial-gradient(130% 100% at 50% -10%,rgba(244,114,182,.10),transparent 65%),#26121d !important;background-attachment:fixed}
.top{background:linear-gradient(180deg,#26121d 62%,rgba(38,18,29,0)) !important}
</style></head><body>"""


def render(outdir):
    days = _load_days()
    html_out = build_html(days)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "khuvuc.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    logger.info("render khuvuc.html ← %d ngày", len(days))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    mode = sys.argv[1] if len(sys.argv) > 1 else "render"
    if mode == "collect":
        token = os.environ.get("NHANH_TOKEN", "").strip()
        if not token:
            raise SystemExit("Thiếu NHANH_TOKEN")
        s = os.environ.get("EOD_DATE", "").strip()
        if s:
            day = datetime.strptime(s, "%Y-%m-%d").date()
        else:
            now = datetime.now(VN)
            day = now.date() if now.hour >= 22 else (now.date() - timedelta(days=1))
        collect_standalone(token, day)
    else:
        slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
        render(os.path.join("docs", slug))


if __name__ == "__main__":
    main()

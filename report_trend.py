"""
Trang XU HƯỚNG (trend.html) — đọc số liệu lịch sử từ Supabase và vẽ biểu đồ.

- Cần SUPABASE_URL + SUPABASE_SERVICE_KEY (hoặc SUPABASE_ANON_KEY). Thiếu → tạo
  trang "chưa cấu hình" để link không 404.
- Biểu đồ SVG tự chứa (không thư viện ngoài). Cùng phong cách app-style.
- Ghi docs/<slug>/trend.html.
"""
from __future__ import annotations
import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import requests

try:
    from am_map import AM_OF
except Exception:
    AM_OF = {}

logger = logging.getLogger("trend")
VN = timezone(timedelta(hours=7))
PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}

# 3 KHO CHUYỂN TIẾP (transit) — tồn đọng luân chuyển (live từ nhanh.ghn.vn)
TRANSIT_HUBS = [("20791000", "Yên Bái"), ("20818000", "Lào Cai"), ("21321000", "Sơn La")]


async def _fetch_transit_async(token):
    import report_backlog_web as BL
    from report import _post
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    giao_t = [("TRANSPORT_DELIVERY", "", "")]
    tra_t = [("TRANSPORT_RETURN", "", "")]
    async with aiohttp.ClientSession(timeout=timeout) as s:
        out = []
        for code, name in TRANSIT_HUBS:
            try:
                d = await _post(s, BL.EP_TR, {"hub_ids": [code]}, code, token)
                p = BL.parse_hub((d.get("data") or [{}])[0])
            except Exception:
                p = {}
            g = (p.get("TRANSPORT_DELIVERY") or {"total": 0})["total"]
            r = (p.get("TRANSPORT_RETURN") or {"total": 0})["total"]
            gg = BL.sec_groups(p, giao_t)
            rg = BL.sec_groups(p, tra_t)
            gbk = (p.get("TRANSPORT_DELIVERY") or {}).get("buckets") or {}
            rbk = (p.get("TRANSPORT_RETURN") or {}).get("buckets") or {}
            out.append({"name": name, "giao": g, "tra": r,
                        "over120": gg[">120h"] + rg[">120h"],
                        "giao_g": gg, "tra_g": rg,
                        "giao_bk": gbk, "tra_bk": rbk})
        return out


def fetch_transit():
    """Tồn đọng luân chuyển 3 kho chuyển tiếp (live). Thiếu NHANH_TOKEN → None."""
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        return None
    try:
        return asyncio.run(_fetch_transit_async(token))
    except Exception as e:
        logger.warning("Tồn đọng kho chuyển tiếp lỗi: %s", str(e)[:150])
        return None


def _esc(s):
    return html.escape(str(s))


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _cls(p):
    if p is None:
        return "na"
    return "bad" if p < 60 else ("warn" if p < 80 else "good")


def _get(url, key, path, tries=3):
    ep = "%s/rest/v1/%s" % (url.rstrip("/"), path)
    h = {"apikey": key, "Authorization": "Bearer " + key}
    last = None
    for i in range(tries):
        try:
            r = requests.get(ep, headers=h, timeout=(15, 90))  # (connect, read)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))   # backoff 2s, 4s
    raise last


def _get_safe(url, key, path):
    """Như _get nhưng lỗi (vd view chưa tạo) → trả [] thay vì vỡ trang."""
    try:
        return _get(url, key, path)
    except Exception:
        return []


def _get_all(url, key, path, page=1000, order="id.asc"):
    """Lấy HẾT dòng qua phân trang (Supabase chặn 1000 dòng/request).
    BẮT BUỘC có ORDER BY ổn định (mặc định id) — nếu không, giữa các trang
    limit/offset Postgres có thể trả trùng/sót dòng → tổng hợp sai."""
    out, offset = [], 0
    sep = "&" if "?" in path else "?"
    ordq = ("order=%s&" % order) if order else ""
    while True:
        chunk = _get(url, key, "%s%s%slimit=%d&offset=%d" % (path, sep, ordq, page, offset))
        out.extend(chunk)
        if len(chunk) < page:
            return out
        offset += page


def fetch_trend(days=90):
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    since = (datetime.now(VN).date() - timedelta(days=days)).isoformat()
    vung = _get(url, key, "bao_cao_vung?ngay=gte.%s&order=ngay.asc&select=*" % since)
    # %GTC 30 ngày theo bưu cục (tốt/kém) — dùng cho bảng. limit cao tránh cắt dòng.
    since30 = (datetime.now(VN).date() - timedelta(days=30)).isoformat()
    bc = _get_all(url, key, "bao_cao_buu_cuc?ngay=gte.%s&select=buu_cuc,tinh,gtc,gtb,don_giao" % since30)
    # Nhân viên 30 ngày (để xếp COD GTB / đơn GTB cao nhất)
    nv30 = _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&select=driver_id,ten_nv,buu_cuc,cod_gtb,gtb,don_giao" % since30)
    # GTC theo NGÀY 30 ngày (so đơn GTC ngày gần nhất vs TB của chính NV)
    nv_gtc = _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&select=ngay,driver_id,ten_nv,buu_cuc,gtc" % since30)
    # Tồn đọng theo NGÀY (gộp toàn vùng) — cho biểu đồ xu hướng.
    #  gt120_giao = đơn GIAO tồn >120h (khách chờ lâu)
    #  red_tra_lc = đơn ĐỎ Trả+Luân chuyển (Trả>120h + LC giao>48h + LC trả>48h) — cột g_red
    td_raw = _get_all(url, key, "bao_cao_ton_dong?ngay=gte.%s&select=ngay,order_type,g_gt120,g_red" % since)
    # đơn đỏ tách riêng từng loại (ngưỡng đúng): Trả>120h · LC giao>48h · LC trả>48h
    RED_FIELD = {"RETURN": "red_return", "TRANSPORT_DELIVERY": "red_lcgiao",
                 "TRANSPORT_RETURN": "red_lctra"}
    tdd = {}
    for r in td_raw:
        a = tdd.setdefault(r["ngay"], {"ngay": r["ngay"], "gt120_giao": 0, "has_red": False,
                                       "red_return": 0, "red_lcgiao": 0, "red_lctra": 0})
        ot = r.get("order_type")
        if ot == "DELIVER":
            a["gt120_giao"] += r.get("g_gt120") or 0
        if r.get("g_red") is not None:   # ngày đã có cột g_red (sau migration)
            a["has_red"] = True
        if ot in RED_FIELD:
            a[RED_FIELD[ot]] += r.get("g_red") or 0
    tondong = sorted(tdd.values(), key=lambda x: x["ngay"])
    return {
        "vung": vung, "bc30": bc, "nv30": nv30, "nv_gtc": nv_gtc, "tondong": tondong,
        "nangsuat": _get_safe(url, key, "v_nv_nangsuat?order=nang_suat.desc&limit=20"),
    }


# ---------- Vẽ SVG ----------

def _line(rows, field, y0, y1, accent, height=150, pct=False):
    pts = [(r["ngay"], r.get(field)) for r in rows]
    vals = [v for _, v in pts if v is not None]
    if len(vals) < 1:
        return "<div class='none'>Chưa đủ dữ liệu để vẽ.</div>"
    W, H = 320.0, float(height)
    pl, pr, pt, pb = 6, 6, 10, 16
    n = len(pts)
    span = (y1 - y0) or 1

    def X(i):
        return pl + (i * (W - pl - pr) / (n - 1)) if n > 1 else W / 2

    def Y(v):
        return pt + (1 - (v - y0) / span) * (H - pt - pb)

    # lưới ngang 3 mức
    grid = []
    labs = []
    for f in (0.0, 0.5, 1.0):
        yy = pt + f * (H - pt - pb)
        val = y1 - f * span
        grid.append("<line x1='%.1f' y1='%.1f' x2='%.1f' y2='%.1f' class='gl'/>" % (pl, yy, W - pr, yy))
        labs.append("<text x='%.1f' y='%.1f' class='gt'>%s</text>" % (pl, yy - 2, (("%.0f%%" % val) if pct else _n(val))))
    # đường + vùng tô
    dpts = [(X(i), Y(v)) for i, (_, v) in enumerate(pts) if v is not None]
    line = " ".join("%.1f,%.1f" % p for p in dpts)
    area = "M%.1f,%.1f " % (dpts[0][0], H - pb) + " ".join("L%.1f,%.1f" % p for p in dpts) + " L%.1f,%.1f Z" % (dpts[-1][0], H - pb)
    last = dpts[-1]
    lastv = vals[-1]
    lastlab = ("%.1f%%" % lastv) if pct else _n(lastv)
    # nhãn ngày: đầu / giữa / cuối
    xlabs = []
    for i in (0, n // 2, n - 1):
        dd = pts[i][0][8:10] + "/" + pts[i][0][5:7]
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        xlabs.append("<text x='%.1f' y='%.1f' class='xt' text-anchor='%s'>%s</text>" % (X(i), H - 3, anchor, dd))
    return ("<svg viewBox='0 0 320 %d' class='chart'>" % int(H)
            + "".join(grid)
            + "<path d='%s' class='ar %s'/>" % (area, accent)
            + "<polyline points='%s' class='ln %s'/>" % (line, accent)
            + "<circle cx='%.1f' cy='%.1f' r='3' class='dot %s'/>" % (last[0], last[1], accent)
            + "<text x='%.1f' y='%.1f' class='lv %s' text-anchor='end'>%s</text>" % (W - pr, Y(lastv) - 6, accent, lastlab)
            + "".join(labs) + "".join(xlabs) + "</svg>")


def _bars(rows, days, height=150):
    rows = rows[-days:]
    if not rows:
        return "<div class='none'>Chưa đủ dữ liệu.</div>"
    W, H = 320.0, float(height)
    pl, pr, pt, pb = 6, 6, 10, 16
    n = len(rows)
    ymax = max((r.get("don_giao") or 0) for r in rows) or 1
    gw = (W - pl - pr) / n
    bw = gw * 0.62
    bars = []
    for i, r in enumerate(rows):
        x = pl + i * gw + (gw - bw) / 2
        dg = r.get("don_giao") or 0
        gt = r.get("gtc") or 0
        hd = (dg / ymax) * (H - pt - pb)
        hg = (gt / ymax) * (H - pt - pb)
        bars.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' rx='1.5' class='bg2'/>"
                    % (x, H - pb - hd, bw, hd))
        bars.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' rx='1.5' class='bg-good'/>"
                    % (x, H - pb - hg, bw, hg))
    xlabs = []
    for i in (0, n // 2, n - 1):
        dd = rows[i]["ngay"][8:10] + "/" + rows[i]["ngay"][5:7]
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        xlabs.append("<text x='%.1f' y='%.1f' class='xt' text-anchor='%s'>%s</text>"
                     % (pl + i * gw + gw / 2, H - 3, anchor, dd))
    return "<svg viewBox='0 0 320 %d' class='chart'>%s%s</svg>" % (int(H), "".join(bars), "".join(xlabs))


def _dcell(delta):
    """Ô Δ so với kỳ trước — có kỳ trước mới hiện tăng/giảm, chưa có → dấu –."""
    if delta is None:
        return "<td class='dmut'>–</td>"
    if delta > 0:
        return "<td class='up'>▲ %.1f</td>" % delta
    if delta < 0:
        return "<td class='down'>▼ %.1f</td>" % abs(delta)
    return "<td class='dmut'>0</td>"


def _rank_table(rows):
    trs = []
    for i, r in enumerate(rows, 1):
        pc = r.get("pct_cur")
        trs.append("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                   "<td>%s</td><td><span class='pill sm %s'>%s%%</span></td>%s</tr>"
                   % (i, _esc(r.get("ten_nv")), _esc(r.get("buu_cuc")), _n(r.get("dg_cur")),
                      _cls(pc), pc if pc is not None else "—", _dcell(r.get("delta"))))
    return ("<table class='t'><thead><tr><th class='rk'>#</th><th>Nhân viên</th><th>Đơn</th><th>%GTC</th><th>Δ kỳ trước</th></tr></thead>"
            "<tbody>" + "".join(trs) + "</tbody></table>")


def _rank_card(label, note, top_rows, bot_rows):
    if not top_rows and not bot_rows:
        inner = "<div class='none'>Chưa đủ dữ liệu — cần %s (số sẽ hiện khi tích lũy đủ).</div>" % note
    else:
        inner = ""
        if top_rows:
            inner += "<div class='mh up'>🏆 %GTC cao nhất</div>" + _rank_table(top_rows)
        if bot_rows:
            inner += "<div class='mh down'>🔻 %GTC thấp nhất</div>" + _rank_table(bot_rows)
    return "<div class='sec'>%s</div><section class='card'>%s</section>" % (label, inner)


def _ns_drill(rows):
    """Năng suất TB 30 ngày (đơn GTC / ngày làm) theo AM → Bưu cục → Nhân viên,
    xếp THẤP → CAO, màu đỏ/vàng/xanh theo nhóm 3 (tỉ lệ) toàn vùng."""
    label = "⚡ Năng suất TB 30 ngày · AM → Bưu cục → Nhân viên (thấp → cao)"
    if not rows:
        return ("<div class='sec' style='color:var(--good)'>%s</div>"
                "<section class='card'><div class='none'>Chưa đủ dữ liệu.</div></section>" % label)
    nv = {}
    for r in rows:
        bc = r.get("buu_cuc") or "?"
        did = str(r.get("driver_id") or "") or (r.get("ten_nv") or "")
        d = nv.setdefault((did, bc), {"ten": r.get("ten_nv") or "—", "bc": bc, "g": 0, "days": 0})
        if r.get("ten_nv"): d["ten"] = r["ten_nv"]
        d["g"] += r.get("gtc") or 0
        d["days"] += 1
    nvs = [{**d, "avg": d["g"] / d["days"]} for d in nv.values() if d["days"] > 0]
    if not nvs:
        return ("<div class='sec' style='color:var(--good)'>%s</div>"
                "<section class='card'><div class='none'>Chưa đủ dữ liệu.</div></section>" % label)
    # nhóm 3 theo TB/ngày toàn vùng
    avs = sorted(x["avg"] for x in nvs)
    n = len(avs); LO = avs[n // 3]; HI = avs[2 * n // 3]
    def cls(a): return "bad" if a < LO else ("warn" if a < HI else "good")
    def wavg(items):
        g = sum(i["g"] for i in items); dd = sum(i["days"] for i in items)
        return g / dd if dd else 0
    ambc = {}
    for x in nvs:
        am = AM_OF.get(x["bc"]) or "(chưa gán AM)"
        ambc.setdefault(am, {}).setdefault(x["bc"], []).append(x)

    reg_avg = wavg(nvs)   # năng suất bình quân toàn vùng (Σ GTC ÷ Σ ngày làm)
    P = ["<div class='sec' style='color:var(--good)'>%s</div>" % label,
         "<div class='regavg'><span>🌐 Năng suất bình quân toàn vùng</span>"
         "<span class='pill %s'>%d đơn/ngày</span></div>" % (cls(reg_avg), round(reg_avg)),
         "<div class='dnote'>TB/ngày = đơn GTC ÷ số ngày làm (30 ngày) · 🔴 thấp → 🟡 → 🟢 cao · xếp thấp trước</div>"]
    for am in sorted(ambc, key=lambda a: wavg([x for bl in ambc[a].values() for x in bl])):
        am_items = [x for bl in ambc[am].values() for x in bl]
        aa = wavg(am_items); ac = cls(aa)
        P.append("<details class='amx %s'><summary>"
                 "<span class='dot %s'></span><span class='dn'>%s</span>"
                 "<span class='dmeta'>%d BC · %d NV</span><span class='pill sm %s'>%d</span>"
                 "</summary><div class='dbody'>" % (ac, ac, _esc(am), len(ambc[am]), len(am_items), ac, round(aa)))
        for bc in sorted(ambc[am], key=lambda b: wavg(ambc[am][b])):
            items = ambc[am][bc]; ba = wavg(items); bc_c = cls(ba)
            P.append("<details class='bcx %s'><summary>"
                     "<span class='dot %s'></span><span class='dn'>%s</span>"
                     "<span class='dmeta'>%d NV</span><span class='pill sm %s'>%d</span>"
                     "</summary><div class='dbody'>" % (bc_c, bc_c, _esc(bc), len(items), bc_c, round(ba)))
            for x in sorted(items, key=lambda i: i["avg"]):
                c = cls(x["avg"])
                P.append("<div class='nvr'><span class='dn'>%s</span>"
                         "<span class='dmeta'>%d ngày · %s đơn</span>"
                         "<span class='pill sm %s'>%d</span></div>"
                         % (_esc(x["ten"]), x["days"], _n(x["g"]), c, round(x["avg"])))
            P.append("</div></details>")
        P.append("</div></details>")
    return P[0] + "<section class='card drillcard'>" + "".join(P[1:]) + "</section>"


def _ns_yesterday_bc(rows):
    """GTC HÔM QUA theo bưu cục (xếp cao→thấp), bấm mở ra nhân viên; mỗi cấp so
    với TB 30 ngày (▲ trên / ▼ dưới mức thường). Màu xanh=trên TB, đỏ=dưới TB."""
    if not rows:
        return ("<div class='sec' style='color:var(--good)'>🏤 GTC hôm qua theo bưu cục</div>"
                "<section class='card'><div class='none'>Chưa đủ dữ liệu.</div></section>")
    latest = max(r["ngay"] for r in rows)
    dm = "%s/%s" % (latest[8:10], latest[5:7])
    label = "🏤 GTC hôm qua (%s) theo bưu cục · cao → thấp · so TB 30 ngày" % dm
    nv = {}; bc_days = {}
    for r in rows:
        b = r.get("buu_cuc") or "?"
        bc_days.setdefault(b, set()).add(r["ngay"])
        did = str(r.get("driver_id") or "") or (r.get("ten_nv") or "")
        d = nv.setdefault((did, b), {"ten": r.get("ten_nv") or "—", "bc": b, "g": 0, "days": 0, "hq": 0})
        if r.get("ten_nv"): d["ten"] = r["ten_nv"]
        g = r.get("gtc") or 0
        d["g"] += g; d["days"] += 1
        if r["ngay"] == latest:
            d["hq"] = g
    bc = {}
    for x in nv.values():
        d = bc.setdefault(x["bc"], {"hq": 0, "g": 0, "nv": []})
        d["hq"] += x["hq"]; d["g"] += x["g"]; d["nv"].append(x)

    def ud(cur, avg): return "good" if cur >= avg else "bad"
    def dtxt(cur, avg):
        diff = round(cur - avg)
        return ("<span class='up'>▲%d</span>" % diff) if diff >= 0 else ("<span class='down'>▼%d</span>" % abs(diff))
    # Tổng GTC hôm qua toàn vùng = Σ tất cả bưu cục · so TB ngày (Σ GTC 30 ngày ÷ số ngày)
    reg_hq = sum(d["hq"] for d in bc.values())
    reg_days = len(set(r["ngay"] for r in rows)) or 1
    reg_avgd = sum(r.get("gtc") or 0 for r in rows) / reg_days
    rc = ud(reg_hq, reg_avgd)
    P = ["<div class='sec' style='color:var(--good)'>%s</div>" % label,
         "<div class='regavg'><span>🌐 Tổng GTC hôm qua toàn vùng · TB %d/ngày · %s</span>"
         "<span class='pill %s'>%s</span></div>" % (round(reg_avgd), dtxt(reg_hq, reg_avgd), rc, _n(reg_hq)),
         "<div class='dnote'>Số lớn = GTC HÔM QUA · TB = bình quân 30 ngày · ▲ trên / ▼ dưới mức thường · 🟢 trên TB · 🔴 dưới TB</div>"]
    for b in sorted(bc, key=lambda k: -bc[k]["hq"]):
        d = bc[b]; nd = len(bc_days.get(b, [])) or 1; avgd = d["g"] / nd
        c = ud(d["hq"], avgd)
        P.append("<details class='bcx %s'><summary><span class='dot %s'></span>"
                 "<span class='dn'>%s</span><span class='dmeta'>TB %d · %s</span>"
                 "<span class='pill sm %s'>%s</span></summary><div class='dbody'>"
                 % (c, c, _esc(b), round(avgd), dtxt(d["hq"], avgd), c, _n(d["hq"])))
        for x in sorted(d["nv"], key=lambda i: -i["hq"]):
            avg = x["g"] / x["days"] if x["days"] else 0
            cc = ud(x["hq"], avg)
            P.append("<div class='nvr'><span class='dn'>%s</span>"
                     "<span class='dmeta'>TB %d · %s</span>"
                     "<span class='pill sm %s'>%s</span></div>"
                     % (_esc(x["ten"]), round(avg), dtxt(x["hq"], avg), cc, _n(x["hq"])))
        P.append("</div></details>")
    return P[0] + "<section class='card drillcard'>" + "".join(P[1:]) + "</section>"


def _ns_today_card(rows, min_today=30, min_prior=3, topn=15):
    """So NĂNG SUẤT: đơn GTC NGÀY GẦN NHẤT của NV vs TB đơn GTC/ngày của CHÍNH NV
    (các ngày trước trong 30 ngày). Chỉ NV có GTC ngày gần nhất > min_today.
    → top bứt phá (cao hơn ngày thường nhất) + top sa sút (thấp hơn nhất)."""
    label = "⚡ Năng suất GTC — ngày gần nhất vs TB 30 ngày"
    if not rows:
        return ("<div class='sec' style='color:var(--good)'>%s</div>"
                "<section class='card'><div class='none'>Chưa đủ dữ liệu.</div></section>" % label)
    latest = max(r["ngay"] for r in rows)
    drv = {}
    for r in rows:
        did = str(r.get("driver_id") or "") or ("%s|%s" % (r.get("ten_nv") or "", r.get("buu_cuc") or ""))
        d = drv.setdefault(did, {"ten": r.get("ten_nv"), "bc": r.get("buu_cuc"),
                                 "today": None, "prior": []})
        if r.get("ten_nv"): d["ten"] = r["ten_nv"]
        if r.get("buu_cuc"): d["bc"] = r["buu_cuc"]
        g = r.get("gtc") or 0
        if r["ngay"] == latest:
            d["today"] = g
        else:
            d["prior"].append(g)
    cand = []
    for d in drv.values():
        if d["today"] is None or d["today"] <= min_today:
            continue
        if len(d["prior"]) < min_prior:
            continue
        avg = sum(d["prior"]) / len(d["prior"])
        if avg <= 0:
            continue
        cand.append({**d, "avg": avg, "diff": d["today"] - avg})
    dm = "%s/%s" % (latest[8:10], latest[5:7])
    lbl = "%s <span style='font-weight:500;color:var(--mut)'>(ngày %s · NV có GTC hôm nay &gt; %d đơn)</span>" % (label, dm, min_today)
    if not cand:
        return ("<div class='sec' style='color:var(--good)'>%s</div>"
                "<section class='card'><div class='none'>Chưa có NV nào đạt ngưỡng (cần ≥%d ngày lịch sử + GTC hôm nay &gt; %d).</div></section>"
                % (lbl, min_prior, min_today))

    def _tbl(items):
        trs = []
        for i, a in enumerate(items, 1):
            dv = round(a["diff"])
            cls = "up" if dv >= 0 else "down"
            dcell = ("+%s" % _n(dv)) if dv >= 0 else ("−%s" % _n(abs(dv)))
            trs.append("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                       "<td>%s</td><td>%s</td><td class='%s'>%s</td></tr>"
                       % (i, _esc(a["ten"]), _esc(a["bc"]), _n(a["today"]),
                          _n(round(a["avg"])), cls, dcell))
        return ("<table class='t'><thead><tr><th class='rk'>#</th><th>Nhân viên</th>"
                "<th>GTC nay</th><th>TB/ngày</th><th>Nay−TB</th></tr></thead><tbody>"
                + "".join(trs) + "</tbody></table>")

    best = sorted(cand, key=lambda x: -x["diff"])[:topn]
    worst = sorted(cand, key=lambda x: x["diff"])[:topn]
    inner = ("<div class='mh up'>🏆 Bứt phá — cao hơn ngày thường nhất</div>" + _tbl(best)
             + "<div class='mh down'>🔻 Sa sút — thấp hơn ngày thường nhất</div>" + _tbl(worst))
    return "<div class='sec' style='color:var(--good)'>%s</div><section class='card'>%s</section>" % (lbl, inner)


def _ns_card(rows):
    if not rows:
        inner = "<div class='none'>Chưa đủ dữ liệu — chạy view v_nv_nangsuat rồi đợi có dữ liệu.</div>"
    else:
        trs = []
        for i, r in enumerate(rows, 1):
            trs.append("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                       "<td>%s</td><td>%s</td><td class='ns'>%s</td></tr>"
                       % (i, _esc(r.get("ten_nv")), _esc(r.get("buu_cuc")),
                          _n(r.get("so_don_gtc")), _n(r.get("so_ngay_lam")), r.get("nang_suat")))
        inner = ("<table class='t'><thead><tr><th class='rk'>#</th><th>Nhân viên</th><th>GTC</th><th>Ngày</th>"
                 "<th>NS/ngày</th></tr></thead><tbody>" + "".join(trs) + "</tbody></table>")
    return ("<div class='sec' style='color:var(--good)'>⚡ Năng suất GTC nhân viên · GTC/ngày làm (30 ngày)</div>"
            "<section class='card'>%s</section>" % inner)


def _cod_card(nv30):
    """Top 10 nhân viên COD GTB / đơn GTB cao nhất (gộp 30 ngày). Tiền kẹt / đơn hỏng."""
    if not nv30:
        inner = "<div class='none'>Chưa đủ dữ liệu — cần bảng nhân viên tích lũy (số hiện khi có dữ liệu 30 ngày).</div>"
    else:
        agg = {}
        for r in nv30:
            did = str(r.get("driver_id") or "") or ("%s|%s" % (r.get("ten_nv") or "", r.get("buu_cuc") or ""))
            a = agg.setdefault(did, {"ten": r.get("ten_nv"), "bc": r.get("buu_cuc"), "cod": 0.0, "gtb": 0})
            a["cod"] += r.get("cod_gtb") or 0
            a["gtb"] += r.get("gtb") or 0
            if r.get("ten_nv"):
                a["ten"] = r["ten_nv"]
            if r.get("buu_cuc"):
                a["bc"] = r["buu_cuc"]
        rows = [a for a in agg.values() if a["gtb"] > 0]
        rows.sort(key=lambda x: -(x["cod"] / x["gtb"]))
        top = rows[:10]
        trs = []
        for i, a in enumerate(top, 1):
            per = a["cod"] / a["gtb"] / 1e6           # triệu / đơn GTB
            trs.append("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                       "<td>%s</td><td>%s</td><td class='cod'>%s</td></tr>"
                       % (i, _esc(a["ten"]), _esc(a["bc"]), _n(a["gtb"]),
                          ("%.1f" % (a["cod"] / 1e6)).replace(".", ","),
                          ("%.2f" % per).replace(".", ",")))
        inner = ("<table class='t'><thead><tr><th class='rk'>#</th><th>Nhân viên</th>"
                 "<th>GTB</th><th>COD GTB tr</th><th>tr/đơn</th></tr></thead><tbody>"
                 + "".join(trs) + "</tbody></table>")
    return ("<div class='sec' style='color:var(--bad)'>💰 Top 10 nhân viên COD GTB / đơn cao nhất (30 ngày)</div>"
            "<section class='card'>%s</section>" % inner)


def _transit_card(transit):
    if not transit:
        return ""
    P = ["<div class='sec'>Tổng tồn theo kho</div>",
         "<section class='card'><table class='t'><thead><tr><th>Kho chuyển tiếp</th>"
         "<th>🚚 Giao</th><th>↩️ Trả</th><th>⚠️&gt;120h</th></tr></thead><tbody>"]
    for w in transit:
        ov = w["over120"]
        ovc = ("<td class='down'>%s</td>" % _n(ov)) if ov else "<td class='dmut'>0</td>"
        P.append("<tr><td class='nv'>%s</td><td class='vol'>%s</td><td>%s</td>%s</tr>"
                 % (_esc(w["name"]), _n(w["giao"]), _n(w["tra"]), ovc))
    P.append("</tbody></table></section>")
    return "".join(P)


def _grp_chips(groups):
    """4 cột khung giờ: nhãn ở trên, số đơn ở dưới (<24h · 24–72h · 72–120h · >120h)."""
    order = [("<24h", "g"), ("24–72h", "w"), ("72–120h", "o"), (">120h", "b")]
    cells = ["<div class='gcol %s'><div class='gl'>%s</div><div class='gv'>%s</div></div>"
             % (c, lb, _n(groups.get(lb, 0))) for lb, c in order]
    return "<div class='gcols'>" + "".join(cells) + "</div>"


# 10 mốc giờ chi tiết (key API, nhãn, màu theo mức tuổi)
_BUCKETS = [
    ("0_6", "0–6h", "g"), ("6_12", "6–12h", "g"), ("12_24", "12–24h", "g"),
    ("24_36", "24–36h", "w"), ("36_48", "36–48h", "w"), ("48_72", "48–72h", "w"),
    ("72_96", "72–96h", "o"), ("96_120", "96–120h", "o"),
    ("120_192", "120–192h", "b"), ("192", "192h+", "b"),
]


def _bucket_chips(buckets):
    """10 chip số đơn từng mốc giờ chi tiết."""
    buckets = buckets or {}
    parts = ["<span class='gchip %s'>%s <b>%s</b></span>" % (c, lb, _n(buckets.get(k, 0)))
             for k, lb, c in _BUCKETS]
    return "<div class='gchips'>" + "".join(parts) + "</div>"


# ---------- Trang ----------

def gen_html(data):
    now = datetime.now(VN)
    P = [_HEAD, "<div class='wrap'>",
         "<header class='top'><div class='brand'>📈 XU HƯỚNG TBB</div>"
         "<div class='ts'>cập nhật %s</div></header>" % now.strftime("%H:%M %d/%m")]

    if not data:
        P.append("<div class='empty'>⚙️ Chưa cấu hình database (Supabase).<br>"
                 "Thêm secret <b>SUPABASE_URL</b> + <b>SUPABASE_SERVICE_KEY</b> rồi đợi có dữ liệu.</div>")
        P.append("</div></body></html>")
        return "\n".join(P)

    vung = data["vung"]
    if not vung:
        P.append("<div class='empty'>⏳ Database đã kết nối nhưng <b>chưa có dữ liệu</b>.<br>"
                 "Số liệu được lưu mỗi tối 23h — quay lại sau vài ngày để xem xu hướng.</div>")
        P.append("</div></body></html>")
        return "\n".join(P)

    last = vung[-1]
    prev = vung[-2] if len(vung) > 1 else None
    pct = last.get("pct_gtc")
    cls = _cls(pct)
    # Δ so ngày trước
    delta = ""
    if prev and prev.get("pct_gtc") is not None and pct is not None:
        d = round(pct - prev["pct_gtc"], 1)
        if d > 0:
            delta = "<span class='up'>▲ %.1f%%</span>" % d
        elif d < 0:
            delta = "<span class='down'>▼ %.1f%%</span>" % abs(d)
        else:
            delta = "<span class='flat'>▬ 0</span>"

    # Hero
    dlab = last["ngay"][8:10] + "/" + last["ngay"][5:7]
    P.append("<section class='hero %s'>" % cls)
    P.append("<div class='hlbl'>🎯 %%GTC MỚI NHẤT · %s</div>" % dlab)
    P.append("<div class='hpct'>%s<span>%%</span> %s</div>" % (pct if pct is not None else "—", delta))
    P.append("<div class='hsub'>%s đơn giao · %s GTC · tồn %s · %d ngày dữ liệu</div>"
             % (_n(last.get("don_giao")), _n(last.get("gtc")), _n(last.get("chua_gan")), len(vung)))
    P.append("</section>")

    # Biểu đồ %GTC
    pcts = [v.get("pct_gtc") for v in vung if v.get("pct_gtc") is not None]
    lo = max(0, (min(pcts) // 5) * 5 - 5) if pcts else 0
    hi = min(100, (max(pcts) // 5) * 5 + 10) if pcts else 100
    P.append("<div class='sec'>📉 %GTC toàn vùng theo ngày</div>")
    P.append("<section class='card'>%s</section>" % _line(vung, "pct_gtc", lo, hi, cls, 165, pct=True))

    # Đơn giao & GTC
    P.append("<div class='sec'>📦 Đơn giao &amp; ✅ GTC (21 ngày gần nhất)</div>")
    P.append("<section class='card'>%s"
             "<div class='lg'><span class='k bg2'></span>Đơn giao <span class='k bg-good'></span>GTC</div>"
             "</section>" % _bars(vung, 21))

    # Tồn chưa gán
    cg = [v.get("chua_gan") for v in vung if v.get("chua_gan") is not None]
    if any(cg):
        P.append("<div class='sec'>⏳ Tồn chưa gán giao theo ngày</div>")
        P.append("<section class='card'>%s</section>"
                 % _line(vung, "chua_gan", 0, (max(cg) // 100 + 1) * 100, "warn", 140))

    # Xu hướng TỒN ĐỌNG (Lấy·Giao·Trả·Luân chuyển) theo ngày — từ bao_cao_ton_dong
    td = data.get("tondong") or []
    if td:
        g120 = [v["gt120_giao"] for v in td]
        last_td = td[-1]
        P.append("<div class='sec' style='color:var(--bad)'>🔴 Đơn GIAO quá hạn &gt;120h theo ngày</div>")
        P.append("<section class='card'><div class='note' style='margin:0 0 6px'>"
                 "Đơn giao khách chờ &gt;5 ngày · hôm nay: <b style='color:var(--bad)'>%s</b> đơn</div>%s</section>"
                 % (_n(last_td["gt120_giao"]),
                    _line(td, "gt120_giao", 0, (max(g120) // 100 + 1) * 100 if g120 else 100, "bad", 150)))
        # Đơn đỏ tách RIÊNG 3 loại (ngưỡng đúng). Chỉ vẽ ngày ĐÃ có g_red (sau migration).
        td_red = [v for v in td if v.get("has_red")]
        if td_red:
            _COL = {"bad": "var(--bad)", "orng": "#ff9d5c", "warn": "var(--warn)"}
            for title, field, accent in (
                    ("↩️ Đơn TRẢ quá hạn &gt;120h theo ngày", "red_return", "bad"),
                    ("🚚 LC GIAO quá hạn &gt;48h theo ngày", "red_lcgiao", "orng"),
                    ("↩️ LC TRẢ quá hạn &gt;48h theo ngày", "red_lctra", "warn")):
                vals = [v[field] for v in td_red]
                P.append("<div class='sec' style='color:%s'>%s</div>" % (_COL[accent], title))
                P.append("<section class='card'><div class='note' style='margin:0 0 6px'>"
                         "Hôm nay: <b style='color:%s'>%s</b> đơn</div>%s</section>"
                         % (_COL[accent], _n(td_red[-1][field]),
                            _line(td_red, field, 0, (max(vals) // 100 + 1) * 100 if vals else 100, accent, 150)))

    # Bảng bưu cục %GTC TB 7 ngày
    bc30 = data.get("bc30") or []
    if bc30:
        agg = {}
        for r in bc30:
            a = agg.setdefault(r["buu_cuc"], {"bc": r["buu_cuc"], "tinh": r.get("tinh"), "sc": 0, "gtb": 0, "dg": 0})
            a["sc"] += r.get("gtc") or 0        # đơn giao thành công
            a["gtb"] += r.get("gtb") or 0
            a["dg"] += r.get("don_giao") or 0   # tổng đơn
        rows = [{"bc": a["bc"], "tinh": a["tinh"],
                 "gtc": round(a["sc"] * 100.0 / a["dg"], 1) if a["dg"] else None,
                 "gtb": a["gtb"], "dg": a["dg"]} for a in agg.values()]
        valid = [r for r in rows if r["gtc"] is not None]
        # 🏆 10 BC %GTC CAO NHẤT (xanh)
        best = sorted(valid, key=lambda x: -x["gtc"])[:10]
        P.append("<div class='sec' style='color:var(--good)'>🏆 10 bưu cục %GTC cao nhất (30 ngày)</div>")
        P.append("<section class='card'><table class='t'><thead><tr><th class='rk'>#</th><th>Bưu cục</th><th>Đơn</th><th>%GTC TB</th></tr></thead><tbody>")
        for i, r in enumerate(best, 1):
            P.append("<tr><td class='rk'>%d</td><td class='nv'>%s</td><td>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (i, _esc(r["bc"]), _n(r["dg"]), _cls(r["gtc"]), r["gtc"]))
        P.append("</tbody></table></section>")
        # 🔻 10 BC %GTC THẤP NHẤT (đỏ)
        worst = sorted(valid, key=lambda x: x["gtc"])[:10]
        P.append("<div class='sec' style='color:var(--bad)'>🔻 10 bưu cục %GTC thấp nhất (30 ngày)</div>")
        P.append("<section class='card'><table class='t'><thead><tr><th class='rk'>#</th><th>Bưu cục</th><th>GTB</th><th>%GTC TB</th></tr></thead><tbody>")
        for i, r in enumerate(worst, 1):
            P.append("<tr><td class='rk'>%d</td><td class='nv'>%s</td><td>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (i, _esc(r["bc"]), _n(r["gtb"]), _cls(r["gtc"]), r["gtc"]))
        P.append("</tbody></table></section>")
        # 📦 10 BC SỐ ĐƠN cao nhất (sản lượng)
        topvol = sorted(valid, key=lambda x: -x["dg"])[:10]
        P.append("<div class='sec' style='color:#8ea2ff'>📦 10 bưu cục số đơn cao nhất (30 ngày)</div>")
        P.append("<section class='card'><table class='t'><thead><tr><th class='rk'>#</th><th>Bưu cục</th><th>Đơn</th><th>%GTC TB</th></tr></thead><tbody>")
        for i, r in enumerate(topvol, 1):
            P.append("<tr><td class='rk'>%d</td><td class='nv'>%s</td><td class='vol'>%s</td><td><span class='pill sm %s'>%s%%</span></td></tr>"
                     % (i, _esc(r["bc"]), _n(r["dg"]), _cls(r["gtc"]), r["gtc"]))
        P.append("</tbody></table></section>")

    # (Bảng nhân viên chuyển sang trang riêng "Năng suất nhân viên")
    P.append("<a class='eod' href='nhanvien.html'><span>⚡ Năng suất &amp; xếp hạng nhân viên</span>"
             "<span class='arw'>chi tiết →</span></a>")
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>Số liệu lịch sử lưu tại Supabase · cập nhật mỗi tối 23h · Vùng Tây Bắc Bộ</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


_NV_BG = ("<style>body{background:"
          "radial-gradient(135% 80% at 0% 0%,rgba(245,158,11,.13),transparent 55%),"
          "radial-gradient(135% 80% at 100% 12%,rgba(239,68,68,.10),transparent 55%),"
          "#0a0d18 !important;background-attachment:fixed}"
          ".top{background:linear-gradient(180deg,rgba(20,15,6,.92) 60%,rgba(20,15,6,0)) !important}</style>")


def gen_nhanvien_html(data):
    now = datetime.now(VN)
    P = [_HEAD.replace("<title>Xu hướng TBB</title>", "<title>Năng suất Nhân viên · TBB</title>") + _NV_BG,
         "<div class='wrap'>",
         "<header class='top'><div class='brand'>⚡ NĂNG SUẤT NHÂN VIÊN</div>"
         "<div class='ts' style='white-space:nowrap'>cập nhật %s</div></header>" % now.strftime("%H:%M %d/%m")]
    if not data:
        P.append("<div class='empty'>⚙️ Chưa cấu hình database (Supabase).<br>"
                 "Thêm secret rồi đợi có dữ liệu.</div>")
        P.append("</div></body></html>")
        return "\n".join(P)
    # ĐẦU TRANG: 2 báo cáo drill năng suất theo AM/bưu cục/nhân viên
    # 1) Năng suất TB 30 ngày theo AM → Bưu cục → Nhân viên (thấp→cao, màu đỏ/vàng/xanh)
    P.append(_ns_drill(data.get("nv_gtc") or []))
    # 2) GTC hôm qua theo bưu cục (cao→thấp) · bấm ra NV · so TB 30 ngày
    P.append(_ns_yesterday_bc(data.get("nv_gtc") or []))
    # Năng suất GTC = đơn GTC / số ngày làm việc (danh sách xếp hạng)
    P.append(_ns_card(data.get("nangsuat") or []))
    # Top 10 COD GTB / đơn GTB cao nhất (30 ngày) — tiền thu hộ kẹt/đơn hỏng
    P.append(_cod_card(data.get("nv30") or []))
    P.append("<a class='eod' href='trend.html'><span>📈 Xu hướng theo ngày</span>"
             "<span class='arw'>biểu đồ %GTC →</span></a>")
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>NS = đơn GTC / số ngày làm việc · số liệu lưu tại Supabase · cập nhật mỗi tối 23h</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


def gen_transit_html(transit):
    now = datetime.now(VN)
    P = [_HEAD.replace("<title>Xu hướng TBB</title>", "<title>Kho Chuyển Tiếp · TBB</title>"),
         "<div class='wrap'>",
         "<header class='top'><div class='brand'>📦 KHO CHUYỂN TIẾP</div>"
         "<div class='ts' style='white-space:nowrap'>cập nhật %s</div></header>" % now.strftime("%H:%M %d/%m")]
    if not transit:
        P.append("<div class='empty'>Chưa lấy được số tồn đọng luân chuyển "
                 "(thiếu token hoặc lỗi API). Thử lại sau ít phút.</div>")
        P.append("</div></body></html>")
        return "\n".join(P)
    tg = sum(w["giao"] for w in transit)
    tt = sum(w["tra"] for w in transit)
    tov = sum(w["over120"] for w in transit)
    # Hero tổng
    P.append("<section class='hero warn'>")
    P.append("<div class='hlbl'>📦 TỒN ĐỌNG LUÂN CHUYỂN · %d KHO CHUYỂN TIẾP</div>" % len(transit))
    P.append("<div class='hpct' style='font-size:46px'>%s<span> đơn</span></div>" % _n(tg + tt))
    P.append("<div class='hsub'>🚚 %s giao · ↩️ %s trả%s</div>"
             % (_n(tg), _n(tt), (" · ⚠️ %s quá 120h" % _n(tov)) if tov else ""))
    P.append("</section>")
    # Bảng tổng 3 kho
    P.append(_transit_card(transit))
    # Chi tiết khung giờ từng kho (đơn LC giao)
    for w in transit:
        P.append("<div class='sec'>🏭 Kho Chuyển Tiếp %s · phân bố tồn theo mốc giờ</div>" % _esc(w["name"]))
        P.append("<section class='card'>"
                 "<div class='note' style='margin:0 0 6px'>🚚 LC giao (%s đơn)</div>%s"
                 "<div class='note' style='margin:8px 0 6px'>↩️ LC trả (%s đơn)</div>%s"
                 "</section>"
                 % (_n(w["giao"]), _grp_chips(w.get("giao_g") or {}),
                    _n(w["tra"]), _grp_chips(w.get("tra_g") or {})))
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>Tồn đọng luân chuyển 3 kho chuyển tiếp · số LIVE từ nhanh.ghn.vn · tự cập nhật ~15'</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


def _preserve_or(outdir, fn, fallback_html):
    """Supabase lỗi → GIỮ trang LIVE đang tốt (không đè trắng); nếu live cũng hỏng → ghi fallback.
    Trả True nếu giữ được bản live tốt."""
    base = "https://vietvk-ux.github.io/tbb-dashboard/%s/" % os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    try:
        r = requests.get(base + fn, timeout=30)
        bad = ("Chưa cấu hình" in r.text) or ("chưa có dữ liệu" in r.text.lower())
        if r.status_code == 200 and not bad and len(r.text) > 800:
            with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
                f.write(r.text)
            return True
    except Exception:
        pass
    with open(os.path.join(outdir, fn), "w", encoding="utf-8") as f:
        f.write(fallback_html)
    return False


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    try:
        data = fetch_trend()
    except Exception as e:
        logger.warning("Không đọc được Supabase: %s", str(e)[:200])
        data = None
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    # Kho chuyển tiếp dùng dữ liệu LIVE nhanh.ghn.vn (độc lập Supabase) — luôn tạo.
    transit = fetch_transit()
    with open(os.path.join(outdir, "khochuyentiep.html"), "w", encoding="utf-8") as f:
        f.write(gen_transit_html(transit))

    if data is None:
        # Supabase timeout/lỗi → KHÔNG đè trắng: giữ trang cũ đang LIVE (trend/nhanvien/xephang).
        k1 = _preserve_or(outdir, "trend.html", gen_html(None))
        k2 = _preserve_or(outdir, "nhanvien.html", gen_nhanvien_html(None))
        try:
            import report_xephang
            ph = report_xephang.gen_html(None)
            _preserve_or(outdir, "xephang.html", ph)
            _preserve_or(outdir, "xephang_prev.html", ph)
        except Exception:
            pass
        logger.warning("Supabase None — GIỮ trang cũ (trend giữ=%s · nhanvien giữ=%s).", k1, k2)
        return

    with open(os.path.join(outdir, "trend.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(data))
    with open(os.path.join(outdir, "nhanvien.html"), "w", encoding="utf-8") as f:
        f.write(gen_nhanvien_html(data))
    # Trang xếp hạng tổng hợp (scorecard AM→BC→NV) — tháng này + tháng trước
    try:
        import report_xephang
        report_xephang.write_pages(outdir)
    except Exception as e:
        logger.warning("Tạo xephang.html lỗi — GIỮ trang cũ: %s", str(e)[:150])
        try:
            import report_xephang
            ph = report_xephang.gen_html(None)
            _preserve_or(outdir, "xephang.html", ph)
            _preserve_or(outdir, "xephang_prev.html", ph)
        except Exception:
            pass
    logger.info("Đã tạo trend + nhanvien + khochuyentiep.html (%d ngày · transit %s kho).",
                len(data["vung"]) if data and data.get("vung") else 0,
                len(transit) if transit else 0)


_HEAD = """<!doctype html><html lang='vi'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='robots' content='noindex,nofollow'>
<meta http-equiv='refresh' content='900'>
<meta name='theme-color' content='#0a0d18'>
<title>Xu hướng TBB</title>
<style>
:root{--card:#161b2d;--line:#272d45;--mut:#8b92ab;--txt:#eef0f7;--good:#2fd07a;--warn:#f7b955;--bad:#f2585f;--ink:#0a0d18}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(180deg,#0b0f1c,#0a0d18 240px,#0a0d18);color:var(--txt);-webkit-font-smoothing:antialiased;font-size:15px;line-height:1.35}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right));padding-bottom:calc(30px + env(safe-area-inset-bottom))}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0))}
.brand{font-weight:800;letter-spacing:.04em;font-size:15px}
.ts{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
.hero{border-radius:20px;padding:18px;margin:4px 0 6px;background:radial-gradient(120% 90% at 100% 0,rgba(255,255,255,.05),transparent),var(--card);border:1px solid var(--line)}
.hero.good{box-shadow:0 10px 30px -12px rgba(47,208,122,.35)}.hero.warn{box-shadow:0 10px 30px -12px rgba(247,185,85,.32)}.hero.bad{box-shadow:0 10px 30px -12px rgba(242,88,95,.32)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.hpct{font-size:52px;font-weight:850;line-height:1;margin:8px 0 4px;font-variant-numeric:tabular-nums}
.hero.good .hpct{color:var(--good)}.hero.warn .hpct{color:var(--warn)}.hero.bad .hpct{color:var(--bad)}
.hpct span{font-size:22px;font-weight:700;opacity:.6}
.hpct .up{font-size:16px;color:var(--good);margin-left:8px;vertical-align:middle}
.hpct .down{font-size:16px;color:var(--bad);margin-left:8px;vertical-align:middle}
.hpct .flat{font-size:16px;color:var(--mut);margin-left:8px;vertical-align:middle}
.hsub{color:var(--mut);font-size:12.5px;margin-top:8px;font-variant-numeric:tabular-nums}
.sec{font-size:12px;font-weight:700;letter-spacing:.05em;color:#b9c0da;text-transform:uppercase;margin:18px 4px 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px 12px 6px}
.chart{width:100%;height:auto;display:block;overflow:visible}
.gl{stroke:rgba(255,255,255,.06);stroke-width:1}
.gt{fill:var(--mut);font-size:8px}
.xt{fill:var(--mut);font-size:8px}
.ln{fill:none;stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.ln.good{stroke:var(--good)}.ln.warn{stroke:var(--warn)}.ln.bad{stroke:var(--bad)}.ln.orng{stroke:#ff9d5c}
.ar{opacity:.16}.ar.good{fill:var(--good)}.ar.warn{fill:var(--warn)}.ar.bad{fill:var(--bad)}.ar.orng{fill:#ff9d5c}
.dot.good{fill:var(--good)}.dot.warn{fill:var(--warn)}.dot.bad{fill:var(--bad)}.dot.orng{fill:#ff9d5c}
.lv{font-size:10px;font-weight:800}.lv.good{fill:var(--good)}.lv.warn{fill:var(--warn)}.lv.bad{fill:var(--bad)}.lv.orng{fill:#ff9d5c}
.bg2{fill:#3a4470}.bg-good{fill:var(--good)}
.lg{display:flex;gap:14px;align-items:center;color:var(--mut);font-size:11.5px;padding:6px 2px 4px}
.lg .k{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.t{width:100%;border-collapse:collapse;font-size:13px}
.t th,.t td{padding:8px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
.t th{color:var(--mut);font-weight:600;font-size:10.5px;text-transform:uppercase;border-bottom:1px solid var(--line)}
.t th:first-child,.t td:first-child{text-align:left}
.t tbody tr:last-child td{border-bottom:none}
.nv{font-weight:600;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.nv .sc{color:var(--mut);font-size:11px;font-weight:500;overflow:hidden;text-overflow:ellipsis}
.t td.rk,.t th.rk{color:var(--mut);font-weight:700;text-align:left;width:20px;padding-right:2px}
.t td.nv{text-align:left}
.mh{font-size:11.5px;font-weight:700;letter-spacing:.03em;padding:10px 2px 2px}
.mh.up{color:var(--good)}.mh.down{color:var(--bad)}
td.up{color:var(--good);font-weight:800}td.down{color:var(--bad);font-weight:800}
td.dmut{color:var(--mut)}
td.ns{font-weight:800;color:var(--good)}
td.vol{font-weight:800;color:#aeb6e0}
td.cod{font-weight:800;color:var(--bad)}
.gchips{display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 6px}
.gchip{font-size:12px;padding:5px 9px;border-radius:9px;background:rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
.gchip b{font-weight:800;margin-left:2px}
.gchip.g{color:var(--good)}.gchip.w{color:var(--warn)}.gchip.o{color:#ff9d5c}.gchip.b{color:var(--bad)}
.gcols{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:2px 0 2px}
.gcol{text-align:center;padding:8px 4px;border-radius:10px;background:rgba(255,255,255,.05)}
.gcol .gl{font-size:11px;opacity:.85}
.gcol .gv{font-size:17px;font-weight:800;margin-top:3px;font-variant-numeric:tabular-nums}
.gcol.g{color:var(--good)}.gcol.w{color:var(--warn)}.gcol.o{color:#ff9d5c}.gcol.b{color:var(--bad)}
.pill{display:inline-flex;align-items:center;justify-content:center;min-width:46px;padding:3px 8px;border-radius:99px;font-weight:800;font-size:12px;color:var(--ink);font-variant-numeric:tabular-nums}
.pill.sm{min-width:42px;font-size:11.5px;padding:2px 7px}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.bad{background:var(--bad)}.pill.na{background:#3a4160;color:var(--mut)}
.eod{display:flex;align-items:center;justify-content:space-between;text-decoration:none;color:var(--txt);background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;padding:14px 16px;margin:16px 0 8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.empty{color:var(--mut);text-align:center;padding:40px 16px;font-size:14px;line-height:1.7;background:var(--card);border:1px solid var(--line);border-radius:16px;margin-top:16px}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:22px 0 4px}
.none{color:var(--mut);font-size:12.5px;padding:16px 4px}
/* Drill năng suất AM→BC→NV */
.drillcard{padding:8px}
.dnote{font-size:11.5px;color:var(--mut);margin:2px 2px 8px;line-height:1.5}
.regavg{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;margin:2px 0 6px;border-radius:12px;background:rgba(255,255,255,.05);border:1px solid var(--line);font-weight:800;font-size:14px}
details.amx{border:1px solid var(--line);border-left:3px solid var(--line);border-radius:12px;margin:7px 0;overflow:hidden;background:var(--card)}
details.amx.good{border-left-color:var(--good)}details.amx.warn{border-left-color:var(--warn)}details.amx.bad{border-left-color:var(--bad)}
details.bcx{border:1px solid var(--line);border-left:2px solid var(--line);border-radius:10px;margin:6px 0;overflow:hidden;background:rgba(255,255,255,.02)}
details.bcx.good{border-left-color:var(--good)}details.bcx.warn{border-left-color:var(--warn)}details.bcx.bad{border-left-color:var(--bad)}
.amx>summary,.bcx>summary{display:flex;align-items:center;gap:9px;padding:10px 12px;cursor:pointer;list-style:none}
.amx>summary::-webkit-details-marker,.bcx>summary::-webkit-details-marker{display:none}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.dot.good{background:var(--good)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.dn{font-weight:700;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.dmeta{color:var(--mut);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.dmeta .up,.regavg .up{color:var(--good);font-weight:800}.dmeta .down,.regavg .down{color:var(--bad);font-weight:800}
.dbody{padding:2px 10px 9px}
.nvr{display:flex;align-items:center;gap:9px;padding:7px 2px;border-top:1px solid rgba(255,255,255,.05)}
.nvr:first-child{border-top:none}
.nvr .dn{font-weight:600;font-size:13px}
</style></head><body>"""


if __name__ == "__main__":
    main()

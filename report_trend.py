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
from datetime import datetime, timedelta, timezone

import aiohttp
import requests

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
            out.append({"name": name, "giao": g, "tra": r,
                        "over120": gg[">120h"] + rg[">120h"],
                        "giao_g": gg, "tra_g": rg})
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


def _get(url, key, path):
    ep = "%s/rest/v1/%s" % (url.rstrip("/"), path)
    h = {"apikey": key, "Authorization": "Bearer " + key}
    r = requests.get(ep, headers=h, timeout=60)
    r.raise_for_status()
    return r.json()


def _get_safe(url, key, path):
    """Như _get nhưng lỗi (vd view chưa tạo) → trả [] thay vì vỡ trang."""
    try:
        return _get(url, key, path)
    except Exception:
        return []


def _get_all(url, key, path, page=1000):
    """Lấy HẾT dòng qua phân trang (Supabase chặn 1000 dòng/request)."""
    out, offset = [], 0
    sep = "&" if "?" in path else "?"
    while True:
        chunk = _get(url, key, "%s%slimit=%d&offset=%d" % (path, sep, page, offset))
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
    # Xếp hạng %GTC nhân viên tuần/tháng (view v_nv_tuan, v_nv_thang; chưa tạo → [])
    return {
        "vung": vung, "bc30": bc,
        "tuan_top": _get_safe(url, key, "v_nv_tuan?order=pct_cur.desc,dg_cur.desc&limit=8"),
        "tuan_bot": _get_safe(url, key, "v_nv_tuan?order=pct_cur.asc,dg_cur.desc&limit=8"),
        "thang_top": _get_safe(url, key, "v_nv_thang?order=pct_cur.desc,dg_cur.desc&limit=8"),
        "thang_bot": _get_safe(url, key, "v_nv_thang?order=pct_cur.asc,dg_cur.desc&limit=8"),
        "nangsuat": _get_safe(url, key, "v_nv_nangsuat?order=nang_suat.desc&limit=15"),
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
    """4 chip phân bố khung giờ: <24h · 24–72h · 72–120h · >120h."""
    order = [("<24h", "g"), ("24–72h", "w"), ("72–120h", "o"), (">120h", "b")]
    parts = ["<span class='gchip %s'>%s <b>%s</b></span>" % (c, lb, _n(groups.get(lb, 0)))
             for lb, c in order]
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


def gen_nhanvien_html(data):
    now = datetime.now(VN)
    P = [_HEAD, "<div class='wrap'>",
         "<header class='top'><div class='brand'>⚡ NĂNG SUẤT NHÂN VIÊN</div>"
         "<div class='ts' style='white-space:nowrap'>cập nhật %s</div></header>" % now.strftime("%H:%M %d/%m")]
    if not data:
        P.append("<div class='empty'>⚙️ Chưa cấu hình database (Supabase).<br>"
                 "Thêm secret rồi đợi có dữ liệu.</div>")
        P.append("</div></body></html>")
        return "\n".join(P)
    # Năng suất GTC = đơn GTC / số ngày làm việc (mục chính)
    P.append(_ns_card(data.get("nangsuat") or []))
    # Xếp hạng %GTC nhân viên tuần / tháng
    P.append(_rank_card("🏅 Xếp hạng %GTC nhân viên · TUẦN (7 ngày gần nhất)", "≥ 1 tuần dữ liệu",
                        data.get("tuan_top") or [], data.get("tuan_bot") or []))
    P.append(_rank_card("🏅 Xếp hạng %GTC nhân viên · THÁNG (30 ngày gần nhất)", "≥ 1 tháng dữ liệu",
                        data.get("thang_top") or [], data.get("thang_bot") or []))
    P.append("<a class='eod' href='trend.html'><span>📈 Xu hướng theo ngày</span>"
             "<span class='arw'>biểu đồ %GTC →</span></a>")
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>NS = đơn GTC / số ngày làm việc · số liệu lưu tại Supabase · cập nhật mỗi tối 23h</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


def gen_transit_html(transit):
    now = datetime.now(VN)
    P = [_HEAD, "<div class='wrap'>",
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
        P.append("<div class='sec'>🏭 Kho Chuyển Tiếp %s · phân bố tồn (LC giao)</div>" % _esc(w["name"]))
        P.append("<section class='card'>%s"
                 "<div class='note'>🚚 giao <b>%s</b> · ↩️ trả <b>%s</b></div></section>"
                 % (_grp_chips(w.get("giao_g") or {}), _n(w["giao"]), _n(w["tra"])))
    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>Tồn đọng luân chuyển 3 kho chuyển tiếp · số LIVE từ nhanh.ghn.vn · tự cập nhật ~15'</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


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
    transit = fetch_transit()
    with open(os.path.join(outdir, "trend.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(data))
    with open(os.path.join(outdir, "nhanvien.html"), "w", encoding="utf-8") as f:
        f.write(gen_nhanvien_html(data))
    with open(os.path.join(outdir, "khochuyentiep.html"), "w", encoding="utf-8") as f:
        f.write(gen_transit_html(transit))
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
.wrap{max-width:640px;margin:0 auto;padding:0 14px 30px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right))}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;padding:12px 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0))}
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
.ln.good{stroke:var(--good)}.ln.warn{stroke:var(--warn)}.ln.bad{stroke:var(--bad)}
.ar{opacity:.16}.ar.good{fill:var(--good)}.ar.warn{fill:var(--warn)}.ar.bad{fill:var(--bad)}
.dot.good{fill:var(--good)}.dot.warn{fill:var(--warn)}.dot.bad{fill:var(--bad)}
.lv{font-size:10px;font-weight:800}.lv.good{fill:var(--good)}.lv.warn{fill:var(--warn)}.lv.bad{fill:var(--bad)}
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
.gchips{display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 6px}
.gchip{font-size:12px;padding:5px 9px;border-radius:9px;background:rgba(255,255,255,.05);font-variant-numeric:tabular-nums}
.gchip b{font-weight:800;margin-left:2px}
.gchip.g{color:var(--good)}.gchip.w{color:var(--warn)}.gchip.o{color:#ff9d5c}.gchip.b{color:var(--bad)}
.pill{display:inline-flex;align-items:center;justify-content:center;min-width:46px;padding:3px 8px;border-radius:99px;font-weight:800;font-size:12px;color:var(--ink);font-variant-numeric:tabular-nums}
.pill.sm{min-width:42px;font-size:11.5px;padding:2px 7px}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.bad{background:var(--bad)}.pill.na{background:#3a4160;color:var(--mut)}
.eod{display:flex;align-items:center;justify-content:space-between;text-decoration:none;color:var(--txt);background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;padding:14px 16px;margin:16px 0 8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.empty{color:var(--mut);text-align:center;padding:40px 16px;font-size:14px;line-height:1.7;background:var(--card);border:1px solid var(--line);border-radius:16px;margin-top:16px}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:22px 0 4px}
.none{color:var(--mut);font-size:12.5px;padding:16px 4px}
</style></head><body>"""


if __name__ == "__main__":
    main()

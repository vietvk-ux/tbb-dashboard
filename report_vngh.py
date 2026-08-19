"""
TRANG ĐƠN TIKTOK SHOP (mã VNGH) — tiến độ giao theo TỪNG BƯU CỤC (gần realtime).
Mỗi bưu cục: tổng đơn VNGH đã gán · đã giao (GTC) · còn lại · %GTC. Refresh ~15'.

Dùng lại `rows` từ report_live.fetch_live (mỗi row đã có 'vngh' + 'vngh_gtc') → KHÔNG
fetch lại. report_live.main() gọi gen_html() và ghi docs/<slug>/vngh.html.
"""
from __future__ import annotations
from datetime import datetime

from report_live import _n, _esc, _cls, _bar, VN, PROV_NAME, _CSS


def gen_html(rows):
    now = datetime.now(VN)
    R_t = sum(r.get("vngh", 0) for r in rows)
    R_g = sum(r.get("vngh_gtc", 0) for r in rows)
    pct = round(R_g / R_t * 100, 1) if R_t else None
    cls = _cls(pct)
    nbc = sum(1 for r in rows if r.get("vngh", 0) > 0)

    prov = {}
    for r in rows:
        if r.get("vngh", 0) <= 0:
            continue
        p = prov.setdefault(r["prov"], {"t": 0, "g": 0, "bc": 0})
        p["t"] += r["vngh"]
        p["g"] += r["vngh_gtc"]
        p["bc"] += 1

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<meta http-equiv='refresh' content='300'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Đơn TikTok · TBB</title>", _CSS, "<div class='wrap'>"]
    P.append("<header class='top'><div class='brand'>🛍️ ĐƠN TIKTOK</div>"
             "<div class='ts'>%s · %s</div></header>" % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    # Hero — %GTC đơn VNGH toàn vùng
    P.append("<section class='hero %s'>" % cls)
    P.append("<div class='hlbl'>🎯 %GTC ĐƠN TIKTOK TOÀN VÙNG</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>" % (pct if pct is not None else "—"))
    P.append(_bar(pct, cls))
    P.append("<div class='hsub'>%s / %s đơn TikTok đã giao · còn lại <b>%s</b> · %d bưu cục</div>"
             % (_n(R_g), _n(R_t), _n(R_t - R_g), nbc))
    P.append("</section>")

    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC toàn vùng →</span></a>")

    # Theo tỉnh
    P.append("<div class='sec'>🗺 Theo tỉnh · %GTC thấp → cao</div>")
    P.append("<section class='provs'>")
    for pv, v in sorted(prov.items(), key=lambda kv: (kv[1]["g"] / kv[1]["t"] * 100 if kv[1]["t"] else 999)):
        pc = round(v["g"] / v["t"] * 100, 1) if v["t"] else None
        c = _cls(pc)
        P.append("<div class='prow %s'>" % c)
        P.append("<div class='pl'><span class='dot %s'></span><b>%s</b></div>" % (c, _esc(PROV_NAME.get(pv, pv))))
        P.append("<span class='pill %s'>%s%%</span>" % (c, pc if pc is not None else "—"))
        P.append(_bar(pc, c))
        P.append("<div class='pmeta'>🏤 %d BC · 🛍️ %s đơn TikTok · ✅ %s · <span class='gtb'>còn %s</span></div>"
                 % (v["bc"], _n(v["t"]), _n(v["g"]), _n(v["t"] - v["g"])))
        P.append("</div>")
    P.append("</section>")

    # Bưu cục — xếp theo SỐ ĐƠN CHƯA GIAO nhiều → ít; bấm để xem chi tiết nhân viên
    P.append("<div class='sec'>🏤 Bưu cục · còn chưa giao nhiều → ít</div>")
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    vbcs = [r for r in rows if r.get("vngh", 0) > 0]
    for r in sorted(vbcs, key=lambda x: -(x["vngh"] - x["vngh_gtc"])):
        t, g = r["vngh"], r["vngh_gtc"]
        rem = t - g
        pc = round(g / t * 100, 1) if t else None
        c = _cls(pc)
        vdrv = [d for d in r.get("drivers", []) if d.get("vngh", 0) > 0]
        keys = _esc((r["name"] + " " + " ".join(d["name"] for d in vdrv)).lower())
        P.append("<details class='bc %s' data-k=\"%s\">" % (c, keys))
        P.append("<summary>")
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s%%</span></div>" % (c, _esc(r["name"]), c, pc if pc is not None else "—"))
        P.append(_bar(pc, c))
        P.append("<div class='bcm'><span>🛍️ %s</span><span>✅ %s</span><span class='gtb'>còn %s</span></div>"
                 % (_n(t), _n(g), _n(rem)))
        P.append("</summary>")
        P.append("<div class='dtl'>")
        if vdrv:
            P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>TikTok</th><th>Giao</th>"
                     "<th>Còn</th><th>%GTC</th></tr></thead><tbody>")
            for d in sorted(vdrv, key=lambda x: -(x["vngh"] - x["vngh_gtc"])):
                dt, dg = d["vngh"], d["vngh_gtc"]
                dpc = round(dg / dt * 100, 1) if dt else None
                dc = _n(dt - dg)
                dc = ("<b class='gtb'>%s</b>" % dc) if (dt - dg) > 0 else "0"
                P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                         "<td><span class='pill sm %s'>%s%%</span></td></tr>"
                         % (_esc(d["name"]), _n(dt), _n(dg), dc, _cls(dpc), dpc if dpc is not None else "—"))
            P.append("</tbody></table>")
        else:
            P.append("<div class='none'>Không có nhân viên TikTok.</div>")
        P.append("</div></details>")

    P.append("<div class='foot'>Đơn TikTok Shop = mã bắt đầu <b>VNGH</b> · %GTC = đã giao / tổng đơn TikTok đã gán<br>"
             "gộp theo mã đơn · số LIVE gồm cả chuyến đã kết thúc trong ngày · nguồn nhanh.ghn.vn</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc').forEach(function(e){var s=(!q||e.dataset.k.indexOf(q)>=0);"
             "e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=n?'none':'block';}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)

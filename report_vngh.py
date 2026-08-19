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
         "<title>Đơn TikTok VNGH · TBB</title>", _CSS, "<div class='wrap'>"]
    P.append("<header class='top'><div class='brand'>🛍️ ĐƠN TIKTOK · VNGH</div>"
             "<div class='ts'>%s · %s</div></header>" % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    # Hero — %GTC đơn VNGH toàn vùng
    P.append("<section class='hero %s'>" % cls)
    P.append("<div class='hlbl'>🎯 %GTC ĐƠN VNGH (TIKTOK) TOÀN VÙNG</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>" % (pct if pct is not None else "—"))
    P.append(_bar(pct, cls))
    P.append("<div class='hsub'>%s / %s đơn VNGH đã giao · còn lại <b>%s</b> · %d bưu cục</div>"
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
        P.append("<div class='pmeta'>🏤 %d BC · 🛍️ %s đơn VNGH · ✅ %s · <span class='gtb'>còn %s</span></div>"
                 % (v["bc"], _n(v["t"]), _n(v["g"]), _n(v["t"] - v["g"])))
        P.append("</div>")
    P.append("</section>")

    # Bưu cục — còn lại nhiều → ít (ưu tiên đốc thúc)
    P.append("<div class='sec'>🏤 Bưu cục · còn lại nhiều → ít</div>")
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục...' oninput='filt()'></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    P.append("<section class='provs'>")
    vbcs = [r for r in rows if r.get("vngh", 0) > 0]
    for r in sorted(vbcs, key=lambda x: (-(x["vngh"] - x["vngh_gtc"]),
                                         x["vngh_gtc"] / x["vngh"] if x["vngh"] else 1)):
        t, g = r["vngh"], r["vngh_gtc"]
        pc = round(g / t * 100, 1) if t else None
        c = _cls(pc)
        P.append("<div class='prow vbc %s' data-k=\"%s\">" % (c, _esc(r["name"].lower())))
        P.append("<div class='pl'><span class='dot %s'></span><b>%s</b></div>" % (c, _esc(r["name"])))
        P.append("<span class='pill %s'>%s%%</span>" % (c, pc if pc is not None else "—"))
        P.append(_bar(pc, c))
        P.append("<div class='pmeta'>🛍️ %s đơn VNGH · ✅ giao %s · <span class='gtb'>còn %s</span></div>"
                 % (_n(t), _n(g), _n(t - g)))
        P.append("</div>")
    P.append("</section>")

    P.append("<div class='foot'>Đơn TikTok Shop = mã bắt đầu <b>VNGH</b> · %GTC = đã giao / tổng đơn VNGH đã gán<br>"
             "gộp theo mã đơn · số LIVE gồm cả chuyến đã kết thúc trong ngày · nguồn nhanh.ghn.vn</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.vbc').forEach(function(e){var s=(!q||e.dataset.k.indexOf(q)>=0);"
             "e.style.display=s?'':'none';if(s)n++;});"
             "document.getElementById('empty').style.display=n?'none':'block';}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)

"""
Trang TỒN ĐỌNG (mobile) toàn Vùng TBB — 61 bưu cục. Gồm 2 báo cáo:
  1) Lấy · Giao · Trả  — POST /core/oss/v1/report/get-general-info · order_type=ALL, view_mode=WARD
     (trang /lastmile/report/backlog-lgt, view "Trạng thái đơn" = mọi đơn còn tồn chưa xử lý tại BC)
  2) Luân chuyển       — POST /core/oss/v1/report/get-backlog-transport-info · body {hub_ids}
     (trang /lastmile/report/backlog-rotation, tổng mọi trạng thái đóng kiện)
Cả 2 endpoint trả cùng shape general_infos[]{order_type,total_order,order_inventories[]{duration}}.

Số LIVE tại thời điểm chạy. Refresh 30' qua workflow + trang tự reload 5'.
Env: NHANH_TOKEN. Xuất: docs/<slug>/backlog.html (self-contained).
"""
from __future__ import annotations
import asyncio, html, json, logging, os
from datetime import datetime, timezone, timedelta

import aiohttp
from report import _post, _get_hubs, TokenExpiredError, CONCURRENCY
from am_map import AM_OF

logger = logging.getLogger("backlog-web")
VN = timezone(timedelta(hours=7))
EP_LGT = "/core/oss/v1/report/get-general-info"
EP_TR = "/core/oss/v1/report/get-backlog-transport-info"

PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}

# Lấy-Giao-Trả: 4 loại
LGT_TYPES = [
    ("PICK", "📥 Lấy", "Lấy"),
    ("DELIVER", "📦 Giao", "Giao"),
    ("DELIVER_PRIORITY", "⚡ Ưu tiên", "Ưu tiên"),
    ("RETURN", "🔄 Trả", "Trả"),
]
# Luân chuyển: 2 loại
TR_TYPES = [
    ("TRANSPORT_DELIVERY", "🚚 LC giao", "LC giao"),
    ("TRANSPORT_RETURN", "↩️ LC trả", "LC trả"),
]

# Gộp 10 khung giờ nguồn thành 4 nhóm ưu tiên (mobile-friendly)
GROUPS = [
    ("<24h",    ["0_6", "6_12", "12_24"],    "good"),
    ("24–72h",  ["24_36", "36_48", "48_72"], "warn"),
    ("72–120h", ["72_96", "96_120"],         "orng"),
    (">120h",   ["120_192", "192"],          "bad"),
]
GROUP_LABELS = [g[0] for g in GROUPS]


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


def _prov(name):
    return name[name.find("(") + 1:name.find(")")] if "(" in name else "?"


def parse_hub(hub_data):
    """{order_type: {'total': N, 'buckets': {duration: count}}}"""
    out = {}
    for info in (hub_data or {}).get("general_infos", []):
        ot = info["order_type"]
        buckets = {inv["duration"]: inv.get("total_order", 0)
                   for inv in info.get("order_inventories", [])}
        out[ot] = {"total": info.get("total_order", 0), "buckets": buckets}
    return out


def sec_total(parsed, types):
    return sum(parsed.get(ot, {}).get("total", 0) for ot, _, _ in types)


def sec_groups(parsed, types):
    """Tổng theo 4 nhóm khung giờ, chỉ gộp các loại trong `types`."""
    g = {name: 0 for name, _, _ in GROUPS}
    for ot, _, _ in types:
        v = parsed.get(ot)
        if not v:
            continue
        for name, durs, _ in GROUPS:
            g[name] += sum(v["buckets"].get(d, 0) for d in durs)
    return g


def type_group_counts(parsed, otype):
    v = parsed.get(otype)
    if not v:
        return [0, 0, 0, 0]
    return [sum(v["buckets"].get(d, 0) for d in durs) for _, durs, _ in GROUPS]


# ===== "ĐƠN ĐỎ" quá hạn — KPI vùng (khớp sheet 4.backlog) =====
GT_120 = ["120_192", "192"]                                       # > 120h
GT_36 = ["36_48", "48_72", "72_96", "96_120", "120_192", "192"]   # > 36h
GT_48 = ["48_72", "72_96", "96_120", "120_192", "192"]            # > 48h
# order_type -> (dataset key, bộ khung được tính đỏ)
RED_BUCKETS = {
    "DELIVER": ("lgt", GT_120),            # Giao > 120h
    "RETURN": ("lgt", GT_120),             # Trả > 120h
    "TRANSPORT_DELIVERY": ("tr", GT_36),   # LC giao > 36h
    "TRANSPORT_RETURN": ("tr", GT_48),     # LC trả > 48h
}
# thứ tự hiển thị + nhãn ngắn
RED_LABELS = [
    ("DELIVER", "Giao>120h", "Giao>120"),
    ("RETURN", "Trả>120h", "Trả>120"),
    ("TRANSPORT_DELIVERY", "LC giao>36h", "LCg>36"),
    ("TRANSPORT_RETURN", "LC trả>48h", "LCt>48"),
]


def _red_type(parsed, otype):
    """Số đơn đỏ (quá hạn) của 1 loại trong parsed đã cho."""
    info = RED_BUCKETS.get(otype)
    v = parsed.get(otype)
    if not info or not v:
        return 0
    return sum(v["buckets"].get(d, 0) for d in info[1])


def red_of(e):
    """Dict {order_type: n_đỏ, total: tổng đỏ} cho 1 bưu cục."""
    r = {}
    for ot, key in ((ot, RED_BUCKETS[ot][0]) for ot, _, _ in RED_LABELS):
        r[ot] = _red_type(e[key], ot)
    r["total"] = sum(r[ot] for ot, _, _ in RED_LABELS)
    return r


async def _hub_fetch(session, token, hub, sem):
    """Lấy cả 2 báo cáo cho 1 hub. Trả entry {name, prov, lgt, tr}."""
    code = str(hub["locationCode"])
    async with sem:
        lgt, tr = {}, {}
        try:
            d = await _post(session, EP_LGT,
                            {"hub_ids": [code], "view_mode": "WARD", "order_type": "ALL"},
                            code, token)
            data = d.get("data") or []
            if data:
                lgt = parse_hub(data[0])
        except TokenExpiredError:
            raise
        except Exception as e:
            logger.warning("Hub %s LGT lỗi: %s", hub.get("locationName"), str(e)[:90])
        try:
            d = await _post(session, EP_TR, {"hub_ids": [code]}, code, token)
            data = d.get("data") or []
            if data:
                tr = parse_hub(data[0])
        except TokenExpiredError:
            raise
        except Exception as e:
            logger.warning("Hub %s LC lỗi: %s", hub.get("locationName"), str(e)[:90])
    return {"name": hub["locationName"], "prov": _prov(hub["locationName"]), "lgt": lgt, "tr": tr}


async def fetch_all(token):
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)
        logger.info("Hubs TBB: %d", len(hubs))
        sem = asyncio.Semaphore(CONCURRENCY)
        res = await asyncio.gather(*[_hub_fetch(session, token, h, sem) for h in hubs])
        return list(res), len(hubs)


_CSS = """<style>
:root{--bg:#0a0d18;--card:#161a2b;--tx:#eef0f7;--mut:#9aa2bd;--line:#252b42;
--good:#22c55e;--warn:#f59e0b;--orng:#fb923c;--bad:#ef4444;--acc:#38bdf8}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--tx);-webkit-text-size-adjust:100%}
.wrap{max-width:820px;margin:0 auto;padding:0 12px 40px;padding-left:max(12px,env(safe-area-inset-left));padding-right:max(12px,env(safe-area-inset-right));padding-bottom:calc(40px + env(safe-area-inset-bottom))}
.top{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;
padding:calc(12px + env(safe-area-inset-top)) 4px 12px;background:linear-gradient(180deg,var(--bg) 70%,transparent);backdrop-filter:blur(6px)}
.brand{font-weight:800;letter-spacing:.02em;font-size:15px}
.brand .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--bad);margin-right:7px;animation:pl 1.6s infinite}
@keyframes pl{0%,100%{opacity:1}50%{opacity:.25}}
.ts{color:var(--mut);font-size:12px;text-align:right}
.tabs{display:flex;gap:8px;margin:2px 0 10px}
.tabs a{flex:1;text-align:center;padding:9px;border-radius:11px;background:var(--card);border:1px solid var(--line);
color:var(--tx);text-decoration:none;font-weight:700;font-size:13px}
.tabs a.on{border-color:var(--acc);color:var(--acc)}
.hero{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;text-align:center;margin:4px 0 12px}
.hero.tr{border-color:rgba(56,189,248,.4)}
.hlbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.hbig{font-size:42px;font-weight:800;line-height:1.05;margin:4px 0 2px}
.hsub{color:var(--mut);font-size:12.5px}
.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.st{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:11px 6px;text-align:center}
.st .sv{font-size:20px;font-weight:800}.st .sl{color:var(--mut);font-size:11px;margin-top:2px}
.st.good{border-color:rgba(34,197,94,.4)}.st.warn{border-color:rgba(245,158,11,.4)}
.st.orng{border-color:rgba(251,146,60,.45)}.st.bad{border-color:rgba(239,68,68,.5);background:rgba(239,68,68,.08)}
.sv.good{color:var(--good)}.sv.warn{color:var(--warn)}.sv.orng{color:var(--orng)}.sv.bad{color:var(--bad)}
.types{display:grid;gap:8px;margin-bottom:14px}
.types.n4{grid-template-columns:repeat(4,1fr)}.types.n2{grid-template-columns:repeat(2,1fr)}
.ty{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:10px 6px;text-align:center}
.ty .v{font-size:19px;font-weight:800}.ty .l{color:var(--mut);font-size:11.5px;margin-top:2px}
.sec{font-size:16px;font-weight:800;color:#eaf6ff;margin:26px 2px 4px;letter-spacing:.01em;padding-top:14px;border-top:1px solid var(--line)}
.sec.first{border-top:none;padding-top:0;margin-top:8px}
.secsub{color:var(--mut);font-size:12px;margin:0 2px 10px}
.subh{font-size:13px;font-weight:700;color:#cbd0ea;margin:16px 2px 8px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px}
.scroll table{min-width:520px}
th,td{padding:7px 5px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:11px}
.pill{display:inline-block;min-width:34px;padding:2px 7px;border-radius:20px;font-weight:800;font-size:12px;color:#0a0d18}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.orng{background:var(--orng)}.pill.bad{background:var(--bad)}
.pill.mut{background:#39405c;color:var(--tx)}.pill.acc{background:var(--acc)}
.search{width:100%;padding:11px 12px;border-radius:11px;border:1px solid var(--line);background:var(--card);color:var(--tx);font-size:14px;margin:2px 0 8px}
details.bc{background:var(--card);border:1px solid var(--line);border-radius:13px;margin:8px 0;overflow:hidden}
details.bc[data-u="1"]{border-color:rgba(239,68,68,.45)}
summary{padding:12px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:10px}
summary::-webkit-details-marker{display:none}
.bcn{font-weight:700;font-size:14px}.bcm{color:var(--mut);font-size:12px;margin-top:2px}
.bcr{display:flex;align-items:center;gap:8px;flex-shrink:0}
.tot{font-size:19px;font-weight:800}
details[open] summary{border-bottom:1px solid var(--line)}
.dtl{padding:4px 10px 10px}
.dtl .cap{font-size:12px;font-weight:700;color:#cbd0ea;margin:10px 2px 2px}
.eod{display:flex;justify-content:space-between;align-items:center;gap:8px;background:var(--card);
border:1px solid var(--line);border-radius:13px;padding:13px;margin-bottom:12px;color:var(--tx);text-decoration:none;font-weight:600}
.eod .arw{color:var(--mut);font-size:12.5px;font-weight:500}
.foot{color:var(--mut);font-size:11px;text-align:center;margin:22px 0 8px;line-height:1.6}
.muted{color:var(--mut)}
</style>"""


def render_summary(entries, key, types, hero_lbl, tr=False):
    """Khối tổng quan cho 1 báo cáo: hero + strip 4 nhóm + theo loại + theo tỉnh + top >120h."""
    P = []
    # region aggregates
    grand = sum(sec_total(e[key], types) for e in entries)
    rgroups = {name: 0 for name, _, _ in GROUPS}
    rtypes = {ot: 0 for ot, _, _ in types}
    for e in entries:
        g = sec_groups(e[key], types)
        for name in rgroups:
            rgroups[name] += g[name]
        for ot, _, _ in types:
            rtypes[ot] += e[key].get(ot, {}).get("total", 0)
    over120 = rgroups[">120h"]

    P.append("<section class='hero%s'>" % (" tr" if tr else ""))
    P.append("<div class='hlbl'>%s</div>" % _esc(hero_lbl))
    P.append("<div class='hbig'>%s</div>" % _n(grand))
    P.append("<div class='hsub'>đơn tồn · 🔴 &gt;120h: <b style='color:var(--bad)'>%s</b></div>" % _n(over120))
    P.append("</section>")

    P.append("<section class='strip'>")
    for name, _, cls in GROUPS:
        P.append("<div class='st %s'><div class='sv %s'>%s</div><div class='sl'>%s</div></div>"
                 % (cls, cls, _n(rgroups[name]), _esc(name)))
    P.append("</section>")

    P.append("<section class='types n%d'>" % len(types))
    for ot, lbl, _ in types:
        P.append("<div class='ty'><div class='v'>%s</div><div class='l'>%s</div></div>"
                 % (_n(rtypes[ot]), _esc(lbl)))
    P.append("</section>")

    # theo tỉnh
    provs = {}
    for e in entries:
        tot = sec_total(e[key], types)
        if tot <= 0:
            continue
        o120 = sec_groups(e[key], types)[">120h"]
        p = provs.setdefault(e["prov"], {"total": 0, "over120": 0, "bc": 0})
        p["total"] += tot; p["over120"] += o120; p["bc"] += 1
    P.append("<div class='subh'>🗺 Theo tỉnh · tồn nhiều → ít</div>")
    P.append("<table><tr><th>Tỉnh</th><th>BC</th><th>Tổng tồn</th><th>&gt;120h</th></tr>")
    for pv, v in sorted(provs.items(), key=lambda kv: -kv[1]["total"]):
        ucls = "bad" if v["over120"] > 0 else "mut"
        P.append("<tr><td>%s</td><td>%d</td><td><b>%s</b></td><td><span class='pill %s'>%s</span></td></tr>"
                 % (_esc(PROV_NAME.get(pv, pv)), v["bc"], _n(v["total"]), ucls, _n(v["over120"])))
    P.append("</table>")

    # top 5 BC >120h
    ub = []
    for e in entries:
        o120 = sec_groups(e[key], types)[">120h"]
        if o120 > 0:
            ub.append((e["name"], o120, sec_total(e[key], types)))
    ub.sort(key=lambda x: -x[1])
    if ub:
        P.append("<div class='subh'>🔴 Top bưu cục tồn &gt;120h (ưu tiên xử lý)</div>")
        P.append("<table><tr><th>Bưu cục</th><th>&gt;120h</th><th>Tổng</th></tr>")
        for name, o120, tot in ub[:5]:
            P.append("<tr><td>%s</td><td><span class='pill bad'>%s</span></td><td>%s</td></tr>"
                     % (_esc(name), _n(o120), _n(tot)))
        P.append("</table>")
    return P


def render_detail_table(parsed, types):
    """Bảng chi tiết: loại × 4 nhóm khung giờ + cột 🔴 Đỏ (quá hạn, chính xác theo bucket) + Tổng."""
    P = ["<div class='scroll'><table><tr><th>Loại</th>"]
    for gl in GROUP_LABELS:
        P.append("<th>%s</th>" % _esc(gl))
    P.append("<th>🔴 Backlog</th><th>Tổng</th></tr>")
    for ot, _, short in types:
        tot = parsed.get(ot, {}).get("total", 0)
        if tot <= 0:
            continue
        tg = type_group_counts(parsed, ot)
        P.append("<tr><td>%s</td>" % _esc(short))
        for val in tg:
            P.append("<td>%s</td>" % (_n(val) if val else "<span class='muted'>–</span>"))
        # cột đỏ: chính xác theo ngưỡng của từng loại
        if ot in RED_BUCKETS:
            red = _red_type(parsed, ot)
            rc = ("<span class='pill bad'>%s</span>" % _n(red)) if red > 0 else "<span class='muted'>0</span>"
        else:
            rc = "<span class='muted'>·</span>"   # Lấy / Ưu tiên: không có ngưỡng đỏ
        P.append("<td>%s</td>" % rc)
        P.append("<td><b>%s</b></td></tr>" % _n(tot))
    P.append("</table></div>")
    return "".join(P)


def render_red_section(entries):
    """Khối 🚨 Đơn đỏ toàn vùng: hero tổng + 4 KPI + top BC."""
    tot = {ot: 0 for ot, _, _ in RED_LABELS}
    grand = 0
    for e in entries:
        r = red_of(e)
        for ot, _, _ in RED_LABELS:
            tot[ot] += r[ot]
        grand += r["total"]
    P = ["<section class='hero' style='border-color:rgba(239,68,68,.55);background:rgba(239,68,68,.09)'>"]
    P.append("<div class='hlbl'>🚨 Tổng đơn backlog quá hạn toàn vùng</div>")
    P.append("<div class='hbig' style='color:var(--bad)'>%s</div>" % _n(grand))
    P.append("<div class='hsub'>Giao&gt;120h · Trả&gt;120h · LC giao&gt;36h · LC trả&gt;48h</div>")
    P.append("</section>")
    P.append("<section class='strip'>")
    for ot, lbl, _ in RED_LABELS:
        P.append("<div class='st bad'><div class='sv bad'>%s</div><div class='sl'>%s</div></div>"
                 % (_n(tot[ot]), _esc(lbl)))
    P.append("</section>")
    rows = [(e["name"], red_of(e)) for e in entries]
    rows = [x for x in rows if x[1]["total"] > 0]
    rows.sort(key=lambda x: -x[1]["total"])
    if rows:
        P.append("<div class='subh'>🔴 Top bưu cục đơn backlog nhiều nhất</div>")
        P.append("<div class='scroll'><table><tr><th>Bưu cục</th><th>Giao&gt;120</th><th>Trả&gt;120</th>"
                 "<th>LCg&gt;36</th><th>LCt&gt;48</th><th>Tổng backlog</th></tr>")
        for name, r in rows[:10]:
            P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td><span class='pill bad'>%s</span></td></tr>"
                     % (_esc(name), _n(r["DELIVER"]), _n(r["RETURN"]),
                        _n(r["TRANSPORT_DELIVERY"]), _n(r["TRANSPORT_RETURN"]), _n(r["total"])))
        P.append("</table></div>")

    # Đơn backlog theo AM (cao → thấp · bấm mở xem bưu cục)
    am, am_bcs = {}, {}
    for e in entries:
        amn = AM_OF.get(e["name"])
        if not amn:
            continue
        r = red_of(e)
        a = am.get(amn)
        if a is None:
            a = {ot: 0 for ot, _, _ in RED_LABELS}
            a["total"] = 0
            a["bc"] = 0
            am[amn] = a
        for ot, _, _ in RED_LABELS:
            a[ot] += r[ot]
        a["total"] += r["total"]
        a["bc"] += 1
        am_bcs.setdefault(amn, []).append((e["name"], r))
    am_rows = sorted(am.items(), key=lambda kv: -kv[1]["total"])
    if am_rows:
        P.append("<div class='subh'>🧑‍💼 Đơn backlog theo AM · cao → thấp · bấm xem bưu cục</div>")
        for amn, a in am_rows:
            P.append("<details class='bc' data-u='%s'><summary>" % ("1" if a["total"] > 0 else "0"))
            P.append("<div><div class='bcn'>%s</div><div class='bcm'>🏤 %d BC · Giao&gt;120 %s · Trả %s · LCg %s · LCt %s</div></div>"
                     % (_esc(amn), a["bc"], _n(a["DELIVER"]), _n(a["RETURN"]),
                        _n(a["TRANSPORT_DELIVERY"]), _n(a["TRANSPORT_RETURN"])))
            P.append("<div class='bcr'><span class='pill bad'>%s</span></div></summary>" % _n(a["total"]))
            P.append("<div class='dtl'><div class='scroll'><table><tr><th>Bưu cục</th><th>Giao&gt;120</th>"
                     "<th>Trả&gt;120</th><th>LCg&gt;36</th><th>LCt&gt;48</th><th>Tổng</th></tr>")
            for name, r in sorted(am_bcs[amn], key=lambda x: -x[1]["total"]):
                red = ("<span class='pill bad'>%s</span>" % _n(r["total"])) if r["total"] > 0 else "<span class='muted'>0</span>"
                P.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (_esc(name), _n(r["DELIVER"]), _n(r["RETURN"]),
                            _n(r["TRANSPORT_DELIVERY"]), _n(r["TRANSPORT_RETURN"]), red))
            P.append("</table></div></div></details>")
    return P


def build_html(entries, hub_count):
    now = datetime.now(VN)
    active = [e for e in entries if sec_total(e["lgt"], LGT_TYPES) > 0 or sec_total(e["tr"], TR_TYPES) > 0]

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<meta http-equiv='refresh' content='300'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Tồn đọng TBB · %s</title>" % now.strftime("%H:%M"),
         _CSS, "<div class='wrap'>"]

    P.append("<header class='top'><div class='brand'><span class='dot'></span>TỒN ĐỌNG · TBB</div>"
             "<div class='ts'>%s<br>%d/%d bưu cục</div></header>"
             % (now.strftime("%H:%M · %d/%m/%Y"), len(active), hub_count))

    # tab nhảy nhanh
    P.append("<div class='tabs'><a class='on' href='#do'>🚨 Đơn backlog</a>"
             "<a href='#lgt'>📦 Lấy·Giao·Trả</a>"
             "<a href='#luanchuyen'>🔁 Luân chuyển</a></div>")

    # ===== 🚨 ĐƠN ĐỎ QUÁ HẠN (ưu tiên) =====
    P.append("<div class='sec first' id='do'>🚨 Đơn backlog — quá hạn cần xử lý</div>")
    P.append("<div class='secsub'>Giao&gt;120h · Trả&gt;120h · LC giao&gt;36h · LC trả&gt;48h · danh sách BC dưới sắp theo tổng backlog</div>")
    P += render_red_section(entries)

    # ===== BÁO CÁO 1: LẤY-GIAO-TRẢ =====
    P.append("<div class='sec' id='lgt'>📦 Tồn Lấy · Giao · Trả</div>")
    P.append("<div class='secsub'>Đơn còn tồn tại bưu cục, chưa xử lý (mọi trạng thái)</div>")
    P += render_summary(entries, "lgt", LGT_TYPES, "📦 Tổng tồn Lấy · Giao · Trả toàn vùng")

    # ===== BÁO CÁO 2: LUÂN CHUYỂN =====
    P.append("<div class='sec' id='luanchuyen'>🔁 Tồn đọng luân chuyển</div>")
    P.append("<div class='secsub'>Đơn luân chuyển giao / trả tồn tại kho (mọi trạng thái đóng kiện)</div>")
    P += render_summary(entries, "tr", TR_TYPES, "🔁 Tổng tồn luân chuyển toàn vùng", tr=True)

    # ===== DANH SÁCH BƯU CỤC (sắp theo TỔNG ĐƠN ĐỎ) =====
    P.append("<div class='sec' id='bc'>🏤 Tất cả bưu cục (%d)</div>" % len(active))
    P.append("<div class='secsub'>Sắp theo TỔNG ĐƠN BACKLOG nhiều → ít · bấm để xem chi tiết theo khung giờ</div>")
    P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục / tỉnh...' oninput='filt()'>")

    def bc_sort_key(e):
        red = red_of(e)["total"]
        t = sec_total(e["lgt"], LGT_TYPES) + sec_total(e["tr"], TR_TYPES)
        return (-red, -t)

    for e in sorted(active, key=bc_sort_key):
        lgt_tot = sec_total(e["lgt"], LGT_TYPES)
        tr_tot = sec_total(e["tr"], TR_TYPES)
        r = red_of(e)
        u = "1" if r["total"] > 0 else "0"
        key = _esc((e["name"] + " " + PROV_NAME.get(e["prov"], e["prov"])).lower())
        # meta = các chỉ số đỏ khác 0
        parts = ["%s %s" % (short, _n(r[ot])) for ot, _, short in RED_LABELS if r[ot] > 0]
        meta = " · ".join(parts) if parts else "không có đơn backlog"
        badge = ("<span class='pill bad'>%s</span>" % _n(r["total"])) if r["total"] > 0 \
            else "<span class='pill mut'>0</span>"
        P.append("<details class='bc' data-u='%s' data-k=\"%s\">" % (u, key))
        P.append("<summary><div><div class='bcn'>%s</div><div class='bcm'>🔴 %s</div></div>"
                 "<div class='bcr'>%s</div></summary>"
                 % (_esc(e["name"]), meta, badge))
        P.append("<div class='dtl'>")
        P.append("<div class='cap'>Tổng tồn: 📦 Lấy·Giao·Trả %s · 🔁 Luân chuyển %s</div>"
                 % (_n(lgt_tot), _n(tr_tot)))
        if lgt_tot > 0:
            P.append("<div class='cap'>📦 Lấy · Giao · Trả</div>")
            P.append(render_detail_table(e["lgt"], LGT_TYPES))
        if tr_tot > 0:
            P.append("<div class='cap'>🔁 Luân chuyển</div>")
            P.append(render_detail_table(e["tr"], TR_TYPES))
        P.append("</div></details>")

    P.append("<div class='foot'>Nguồn: nhanh.ghn.vn · (1) Đơn tồn tại bưu cục chưa xử lý (mọi trạng thái) · "
             "(2) Tồn đọng luân chuyển giao/trả<br>"
             "Số cập nhật lúc chạy · trang tự làm mới mỗi 5 phút · dữ liệu làm mới ~30 phút/lần</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim();"
             "document.querySelectorAll('details.bc').forEach(function(e){"
             "e.style.display=(!q||e.dataset.k.indexOf(q)>=0)?'':'none';});}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


def backlog_rows(entries):
    """entries (fetch_all) → dòng cho bảng bao_cao_ton_dong (chỉ loại có tồn > 0)."""
    rows = []
    for e in entries:
        for key, types in (("lgt", LGT_TYPES), ("tr", TR_TYPES)):
            parsed = e.get(key) or {}
            for ot, _, _ in types:
                v = parsed.get(ot)
                if not v or v.get("total", 0) <= 0:
                    continue
                g = type_group_counts(parsed, ot)   # [<24h, 24–72h, 72–120h, >120h]
                rows.append({"buu_cuc": e["name"], "tinh": e.get("prov"), "order_type": ot,
                             "total": v.get("total", 0), "g_lt24": g[0], "g_24_72": g[1],
                             "g_72_120": g[2], "g_gt120": g[3],
                             # đơn ĐỎ theo ngưỡng đúng từng loại (Giao/Trả>120h, LCg>36h, LCt>48h)
                             "g_red": _red_type(parsed, ot)})
    return rows


def build_backlog_fallback(agg, backlog, day):
    """Trang backlog DỰ PHÒNG từ Supabase. Nếu đã có bảng bao_cao_ton_dong (chốt tối) →
    hiện tồn Lấy·Giao·Trả·Luân chuyển + >120h. Luôn kèm 'chưa gán giao'. Banner cảnh báo."""
    import snapshot as SNAP
    now = datetime.now(VN)
    dm = "%s/%s" % (day[8:10], day[5:7])
    td = SNAP.load_ton_dong(day)
    labels = {ot: short for grp in (LGT_TYPES, TR_TYPES) for ot, _, short in grp}
    type_order = [ot for grp in (LGT_TYPES, TR_TYPES) for ot, _, _ in grp]
    total_cg = sum(v.get("deliver", 0) for v in backlog.values())

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<meta http-equiv='refresh' content='300'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Tồn đọng TBB · dự phòng</title>",
         _CSS, "<div class='wrap'>"]
    P.append("<header class='top'><div class='brand'><span class='dot'></span>TỒN ĐỌNG · TBB</div>"
             "<div class='ts'>%s<br>dự phòng</div></header>" % now.strftime("%H:%M · %d/%m/%Y"))
    if td:
        P.append(SNAP.banner_html(day, "Hiện tồn Lấy·Giao·Trả·Luân chuyển đã chốt tối (không real-time)."))
    else:
        P.append(SNAP.banner_html(day, "Số tồn theo khung giờ chưa lưu — chỉ hiện <b>chưa gán giao</b>. "
                                       "(Chạy migration + đợi 1 tối để có đủ.)"))

    # ===== Tồn Lấy·Giao·Trả·Luân chuyển (nếu đã lưu) =====
    if td:
        by_type = {ot: {"total": 0, "gt120": 0} for ot in type_order}
        prov_t, bc_t = {}, {}
        for r in td:
            ot = r["order_type"]
            t = r.get("total") or 0
            o120 = r.get("g_gt120") or 0
            if ot in by_type:
                by_type[ot]["total"] += t
                by_type[ot]["gt120"] += o120
            pv = r.get("tinh") or "?"
            p = prov_t.setdefault(pv, {"total": 0, "gt120": 0, "bc": set()})
            p["total"] += t
            p["gt120"] += o120
            p["bc"].add(r["buu_cuc"])
            b = bc_t.setdefault(r["buu_cuc"], {"tinh": pv, "total": 0, "gt120": 0})
            b["total"] += t
            b["gt120"] += o120
        grand_t = sum(x["total"] for x in by_type.values())
        grand_120 = sum(x["gt120"] for x in by_type.values())
        P.append("<section class='hero'><div class='hlbl'>📦 Tổng tồn Lấy·Giao·Trả·Luân chuyển · chốt %s</div>"
                 "<div class='hbig'>%s</div><div class='hsub'>đơn tồn · 🔴 &gt;120h: "
                 "<b style='color:var(--bad)'>%s</b></div></section>" % (dm, _n(grand_t), _n(grand_120)))
        P.append("<section class='types n4'>")
        for ot in type_order:
            P.append("<div class='ty'><div class='v'>%s</div><div class='l'>%s</div></div>"
                     % (_n(by_type[ot]["total"]), _esc(labels[ot])))
        P.append("</section>")
        P.append("<div class='subh'>🗺 Theo tỉnh · tồn nhiều → ít</div>")
        P.append("<table><tr><th>Tỉnh</th><th>BC</th><th>Tổng tồn</th><th>&gt;120h</th></tr>")
        for pv, v in sorted(prov_t.items(), key=lambda kv: -kv[1]["total"]):
            ucls = "bad" if v["gt120"] > 0 else "mut"
            P.append("<tr><td>%s</td><td>%d</td><td><b>%s</b></td><td><span class='pill %s'>%s</span></td></tr>"
                     % (_esc(PROV_NAME.get(pv, pv)), len(v["bc"]), _n(v["total"]), ucls, _n(v["gt120"])))
        P.append("</table>")
        P.append("<div class='subh'>🏤 Bưu cục · tồn nhiều → ít · 🔎 tìm nhanh</div>")
        P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục...' oninput='filt()'>")
        P.append("<div class='scroll'><table id='bctb'><tr><th>Bưu cục</th><th>Tỉnh</th>"
                 "<th>Tồn</th><th>&gt;120h</th><th>Chưa gán</th></tr>")
        for name, b in sorted(bc_t.items(), key=lambda kv: -kv[1]["total"]):
            cg = backlog.get(name, {}).get("deliver", 0)
            pvn = PROV_NAME.get(b["tinh"], b["tinh"])
            key = _esc((name + " " + pvn).lower())
            o120 = ("<span class='pill bad'>%s</span>" % _n(b["gt120"])) if b["gt120"] else "<span class='muted'>0</span>"
            P.append("<tr data-k=\"%s\"><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td>"
                     "<td><span class='pill mut'>%s</span></td></tr>"
                     % (key, _esc(name), _esc(pvn), _n(b["total"]), o120, _n(cg)))
        P.append("</table></div>")

    # ===== Chưa gán giao (luôn có) =====
    P.append("<div class='sec' id='cg'>⏳ Chưa gán giao (chờ xếp chuyến)</div>")
    prov_cg = {}
    for b in agg["bcs"]:
        cg = backlog.get(b["bc"], {}).get("deliver", 0)
        p = prov_cg.setdefault(b["prov"], {"cg": 0, "bc": 0})
        p["cg"] += cg
        p["bc"] += 1
    P.append("<section class='hero'><div class='hlbl'>⏳ Chưa gán giao toàn vùng · chốt %s</div>"
             "<div class='hbig'>%s</div><div class='hsub'>đơn chờ xếp chuyến</div></section>"
             % (dm, _n(total_cg)))
    P.append("<table><tr><th>Tỉnh</th><th>BC</th><th>Chưa gán giao</th></tr>")
    for pv, v in sorted(prov_cg.items(), key=lambda kv: -kv[1]["cg"]):
        P.append("<tr><td>%s</td><td>%d</td><td><b>%s</b></td></tr>"
                 % (_esc(PROV_NAME.get(pv, pv)), v["bc"], _n(v["cg"])))
    P.append("</table>")

    P.append("<div class='foot'>Bản dự phòng từ Supabase (chốt %s) · số tồn real-time tạm dừng do token nhanh.ghn.vn</div>" % dm)
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim();"
             "document.querySelectorAll('#bctb tr[data-k]').forEach(function(e){"
             "e.style.display=(!q||e.dataset.k.indexOf(q)>=0)?'':'none';});}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


def _write_backlog_fallback(err):
    import snapshot as SNAP
    snap = SNAP.load_snapshot()
    if not snap:
        return False
    agg, backlog, day = snap
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "backlog.html"), "w", encoding="utf-8") as f:
        f.write(build_backlog_fallback(agg, backlog, day))
    logger.warning("ĐÃ GHI backlog.html DỰ PHÒNG từ Supabase ngày %s · lỗi: %s", day, str(err)[:120])
    return True


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    try:
        entries, hub_count = asyncio.run(fetch_all(token))
    except Exception as e:
        # Token hết hạn / API lỗi → backlog.html dự phòng (chưa gán giao từ Supabase) + banner.
        if _write_backlog_fallback(e):
            return
        raise SystemExit("Fetch tồn đọng lỗi và không có snapshot dự phòng: %s" % e)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    html_out = build_html(entries, hub_count)
    with open(os.path.join(outdir, "backlog.html"), "w", encoding="utf-8") as f:
        f.write(html_out)

    # JSON cho BOT đọc trực tiếp (khớp trang backlog)
    payload = {
        "generated": datetime.now(VN).strftime("%H:%M · %d/%m/%Y"),
        "bcs": [{"name": e["name"], "prov": e["prov"], "lgt": e["lgt"], "tr": e["tr"]}
                for e in entries],
    }
    with open(os.path.join(outdir, "backlog.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    lgt = sum(sec_total(e["lgt"], LGT_TYPES) for e in entries)
    tr = sum(sec_total(e["tr"], TR_TYPES) for e in entries)
    logger.info("Xong · LGT %d đơn · Luân chuyển %d đơn · %d bytes", lgt, tr, len(html_out))


if __name__ == "__main__":
    main()

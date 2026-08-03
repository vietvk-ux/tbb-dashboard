"""
Trang TỒN ĐỌNG Lấy–Giao–Trả (mobile) toàn Vùng TBB — 61 bưu cục.
Nguồn: POST /core/oss/v1/report/get-general-info · order_type=DAILY_TRIP_NONE
(đúng dữ liệu trang nhanh.ghn.vn /lastmile/report/backlog-lgt, view "Chưa có chuyến đi trong ngày").

Số LIVE tại thời điểm chạy. Refresh 30' qua workflow + trang tự reload 5'.
Env: NHANH_TOKEN. Xuất: docs/<slug>/backlog.html (self-contained).
"""
from __future__ import annotations
import asyncio, html, logging, os
from datetime import datetime, timezone, timedelta

import aiohttp
from report import _post, _get_hubs, TokenExpiredError, CONCURRENCY

logger = logging.getLogger("backlog-web")
VN = timezone(timedelta(hours=7))
ENDPOINT = "/core/oss/v1/report/get-general-info"

PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}

# Thứ tự & nhãn loại đơn
OTYPES = [
    ("PICK", "📥 Lấy"),
    ("DELIVER", "📦 Giao"),
    ("DELIVER_PRIORITY", "⚡ Ưu tiên"),
    ("RETURN", "🔄 Trả"),
]
OTYPE_SHORT = {"PICK": "Lấy", "DELIVER": "Giao", "DELIVER_PRIORITY": "Ưu tiên", "RETURN": "Trả"}

# Gộp 10 khung giờ nguồn thành 4 nhóm ưu tiên (mobile-friendly)
GROUPS = [
    ("<24h",    ["0_6", "6_12", "12_24"],   "good"),
    ("24–72h",  ["24_36", "36_48", "48_72"], "warn"),
    ("72–120h", ["72_96", "96_120"],        "orng"),
    (">120h",   ["120_192", "192"],         "bad"),
]
GROUP_LABELS = [g[0] for g in GROUPS]


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


def _prov(name):
    return name[name.find("(") + 1:name.find(")")] if "(" in name else "?"


def _pct(part, total):
    return round(part * 100.0 / total) if total else 0


async def _hub_backlog(session, token, hub, sem):
    async with sem:
        try:
            body = {"hub_ids": [str(hub["locationCode"])], "view_mode": "WARD",
                    "order_type": "DAILY_TRIP_NONE"}
            d = await _post(session, ENDPOINT, body, hub["locationCode"], token)
            data = d.get("data") or []
            if data:
                e = data[0]
                e["hub_name"] = hub["locationName"]
                return e
        except TokenExpiredError:
            raise
        except Exception as e:
            logger.warning("Hub %s lỗi, bỏ qua: %s", hub.get("locationName"), str(e)[:100])
        return None


def parse_hub(hub_data):
    """{order_type: {'total': N, 'buckets': {duration: count}}}"""
    out = {}
    for info in hub_data.get("general_infos", []):
        ot = info["order_type"]
        buckets = {inv["duration"]: inv.get("total_order", 0)
                   for inv in info.get("order_inventories", [])}
        out[ot] = {"total": info.get("total_order", 0), "buckets": buckets}
    return out


def group_counts(parsed):
    """Tổng theo 4 nhóm khung giờ (gộp mọi loại đơn)."""
    g = {name: 0 for name, _, _ in GROUPS}
    for v in parsed.values():
        for name, durs, _ in GROUPS:
            g[name] += sum(v["buckets"].get(d, 0) for d in durs)
    return g


def type_group_counts(parsed, otype):
    v = parsed.get(otype)
    if not v:
        return [0, 0, 0, 0]
    return [sum(v["buckets"].get(d, 0) for d in durs) for _, durs, _ in GROUPS]


async def fetch_all(token):
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)
        logger.info("Hubs TBB: %d", len(hubs))
        sem = asyncio.Semaphore(CONCURRENCY)
        res = await asyncio.gather(*[_hub_backlog(session, token, h, sem) for h in hubs])
        return [r for r in res if r], len(hubs)


_CSS = """<style>
:root{--bg:#0a0d18;--card:#161a2b;--tx:#eef0f7;--mut:#9aa2bd;--line:#252b42;
--good:#22c55e;--warn:#f59e0b;--orng:#fb923c;--bad:#ef4444}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--tx);-webkit-text-size-adjust:100%}
.wrap{max-width:820px;margin:0 auto;padding:0 12px 40px}
.top{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;align-items:center;
padding:12px 4px;background:linear-gradient(180deg,var(--bg) 70%,transparent);backdrop-filter:blur(6px)}
.brand{font-weight:800;letter-spacing:.02em;font-size:15px}
.brand .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--bad);margin-right:7px;animation:pl 1.6s infinite}
@keyframes pl{0%,100%{opacity:1}50%{opacity:.25}}
.ts{color:var(--mut);font-size:12px;text-align:right}
.hero{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;text-align:center;margin:4px 0 12px}
.hlbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.hbig{font-size:44px;font-weight:800;line-height:1.05;margin:4px 0 2px}
.hsub{color:var(--mut);font-size:12.5px}
.strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.st{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:11px 6px;text-align:center}
.st .sv{font-size:20px;font-weight:800}.st .sl{color:var(--mut);font-size:11px;margin-top:2px}
.st.good{border-color:rgba(34,197,94,.4)}.st.warn{border-color:rgba(245,158,11,.4)}
.st.orng{border-color:rgba(251,146,60,.45)}.st.bad{border-color:rgba(239,68,68,.5);background:rgba(239,68,68,.08)}
.sv.good{color:var(--good)}.sv.warn{color:var(--warn)}.sv.orng{color:var(--orng)}.sv.bad{color:var(--bad)}
.types{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}
.ty{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:10px 6px;text-align:center}
.ty .v{font-size:19px;font-weight:800}.ty .l{color:var(--mut);font-size:11.5px;margin-top:2px}
.sec{font-size:13px;font-weight:700;color:#cbd0ea;margin:16px 2px 8px;letter-spacing:.02em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 5px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:11px}
.pill{display:inline-block;min-width:34px;padding:2px 7px;border-radius:20px;font-weight:800;font-size:12px;color:#0a0d18}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.orng{background:var(--orng)}.pill.bad{background:var(--bad)}
.pill.mut{background:#39405c;color:var(--tx)}
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
.eod{display:flex;justify-content:space-between;align-items:center;gap:8px;background:var(--card);
border:1px solid var(--line);border-radius:13px;padding:13px;margin-bottom:12px;color:var(--tx);text-decoration:none;font-weight:600}
.eod .arw{color:var(--mut);font-size:12.5px;font-weight:500}
.foot{color:var(--mut);font-size:11px;text-align:center;margin:22px 0 8px;line-height:1.6}
.muted{color:var(--mut)}
</style>"""


def build_html(entries, hub_count):
    now = datetime.now(VN)

    # Aggregate per BC
    bcs = []
    region_types = {ot: 0 for ot, _ in OTYPES}
    region_groups = {name: 0 for name, _, _ in GROUPS}
    grand = 0
    for e in entries:
        parsed = parse_hub(e)
        total = sum(v["total"] for v in parsed.values())
        if total <= 0:
            continue
        g = group_counts(parsed)
        over120 = g[">120h"]
        for ot, _ in OTYPES:
            region_types[ot] += parsed.get(ot, {}).get("total", 0)
        for name in region_groups:
            region_groups[name] += g[name]
        grand += total
        bcs.append({
            "name": e.get("hub_name") or e.get("hub_id"),
            "prov": _prov(e.get("hub_name") or ""),
            "total": total, "over120": over120,
            "groups": g, "parsed": parsed,
        })

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<meta http-equiv='refresh' content='300'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Tồn đọng TBB · %s</title>" % now.strftime("%H:%M"),
         _CSS, "<div class='wrap'>"]

    P.append("<header class='top'><div class='brand'><span class='dot'></span>TỒN ĐỌNG · TBB</div>"
             "<div class='ts'>%s<br>%s</div></header>"
             % (now.strftime("%H:%M · %d/%m/%Y"), "%d/%d bưu cục" % (len(bcs), hub_count)))

    # Hero — tổng tồn
    over120 = region_groups[">120h"]
    P.append("<section class='hero'>")
    P.append("<div class='hlbl'>📦 Tổng tồn Lấy · Giao · Trả toàn vùng</div>")
    P.append("<div class='hbig'>%s</div>" % _n(grand))
    P.append("<div class='hsub'>đơn chưa có chuyến đi trong ngày · 🔴 &gt;120h: <b style='color:var(--bad)'>%s</b></div>" % _n(over120))
    P.append("</section>")

    # Strip theo nhóm khung giờ
    P.append("<section class='strip'>")
    for name, _, cls in GROUPS:
        n = region_groups[name]
        P.append("<div class='st %s'><div class='sv %s'>%s</div><div class='sl'>%s</div></div>"
                 % (cls, cls, _n(n), _esc(name)))
    P.append("</section>")

    # Theo loại đơn
    P.append("<section class='types'>")
    for ot, lbl in OTYPES:
        P.append("<div class='ty'><div class='v'>%s</div><div class='l'>%s</div></div>"
                 % (_n(region_types[ot]), lbl))
    P.append("</section>")

    # Theo tỉnh
    provs = {}
    for b in bcs:
        p = provs.setdefault(b["prov"], {"total": 0, "over120": 0, "bc": 0})
        p["total"] += b["total"]; p["over120"] += b["over120"]; p["bc"] += 1
    P.append("<div class='sec'>🗺 Theo tỉnh · tồn nhiều → ít</div>")
    P.append("<table><tr><th>Tỉnh</th><th>BC</th><th>Tổng tồn</th><th>&gt;120h</th></tr>")
    for pv, v in sorted(provs.items(), key=lambda kv: -kv[1]["total"]):
        ucls = "bad" if v["over120"] > 0 else "mut"
        P.append("<tr><td>%s</td><td>%d</td><td><b>%s</b></td><td><span class='pill %s'>%s</span></td></tr>"
                 % (_esc(PROV_NAME.get(pv, pv)), v["bc"], _n(v["total"]), ucls, _n(v["over120"])))
    P.append("</table>")

    # Top 5 BC tồn >120h (nếu có)
    urgent = sorted([b for b in bcs if b["over120"] > 0], key=lambda x: -x["over120"])[:5]
    if urgent:
        P.append("<div class='sec'>🔴 Top bưu cục tồn &gt;120h (ưu tiên xử lý)</div>")
        P.append("<table><tr><th>Bưu cục</th><th>&gt;120h</th><th>Tổng</th></tr>")
        for b in urgent:
            P.append("<tr><td>%s</td><td><span class='pill bad'>%s</span></td><td>%s</td></tr>"
                     % (_esc(b["name"]), _n(b["over120"]), _n(b["total"])))
        P.append("</table>")

    # Danh sách BC — sort >120h desc, rồi tổng desc
    P.append("<div class='sec'>🏤 Tất cả bưu cục (%d) · tồn nhiều → ít</div>" % len(bcs))
    P.append("<input class='search' id='q' placeholder='🔎 Tìm bưu cục / tỉnh...' oninput='filt()'>")
    for b in sorted(bcs, key=lambda x: (-x["over120"], -x["total"])):
        u = "1" if b["over120"] > 0 else "0"
        key = _esc((b["name"] + " " + PROV_NAME.get(b["prov"], b["prov"])).lower())
        # summary badges: nhóm khung giờ dạng chip nhỏ trong meta
        gchips = " · ".join("%s %s" % (_esc(nm), _n(b["groups"][nm])) for nm, _, _ in GROUPS if b["groups"][nm] > 0)
        ubadge = ("<span class='pill bad'>%s</span>" % _n(b["over120"])) if b["over120"] > 0 else ""
        P.append("<details class='bc' data-u='%s' data-k=\"%s\">" % (u, key))
        P.append("<summary><div><div class='bcn'>%s</div><div class='bcm'>%s</div></div>"
                 "<div class='bcr'>%s<span class='tot'>%s</span></div></summary>"
                 % (_esc(b["name"]), gchips or "—", ubadge, _n(b["total"])))
        # bảng chi tiết: loại × 4 nhóm khung giờ + tổng
        P.append("<div class='dtl'><table><tr><th>Loại</th>")
        for gl in GROUP_LABELS:
            P.append("<th>%s</th>" % _esc(gl))
        P.append("<th>Tổng</th></tr>")
        for ot, lbl in OTYPES:
            tot = b["parsed"].get(ot, {}).get("total", 0)
            if tot <= 0:
                continue
            tg = type_group_counts(b["parsed"], ot)
            P.append("<tr><td>%s</td>" % OTYPE_SHORT[ot])
            for i, (nm, _, cls) in enumerate(GROUPS):
                val = tg[i]
                cell = ("<span class='pill %s'>%s</span>" % (cls, _n(val))) if (val and nm == ">120h") else (_n(val) if val else "<span class='muted'>–</span>")
                P.append("<td>%s</td>" % cell)
            P.append("<td><b>%s</b></td></tr>" % _n(tot))
        P.append("</table></div></details>")

    P.append("<div class='foot'>Nguồn: nhanh.ghn.vn · Tồn \"chưa có chuyến đi trong ngày\" (Lấy/Giao/Ưu tiên/Trả)<br>"
             "Số cập nhật lúc chạy · trang tự làm mới mỗi 5 phút · dữ liệu làm mới ~30 phút/lần</div>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim();"
             "document.querySelectorAll('details.bc').forEach(function(e){"
             "e.style.display=(!q||e.dataset.k.indexOf(q)>=0)?'':'none';});}</script>")
    P.append("</div></body></html>")
    return "\n".join(P)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    token = os.environ.get("NHANH_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu NHANH_TOKEN")
    try:
        entries, hub_count = asyncio.run(fetch_all(token))
    except TokenExpiredError as e:
        raise SystemExit("Token hết hạn: %s" % e)
    slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
    outdir = os.path.join("docs", slug)
    os.makedirs(outdir, exist_ok=True)
    html_out = build_html(entries, hub_count)
    with open(os.path.join(outdir, "backlog.html"), "w", encoding="utf-8") as f:
        f.write(html_out)
    logger.info("Xong · %d BC có tồn · %d bytes", len(entries), len(html_out))


if __name__ == "__main__":
    main()

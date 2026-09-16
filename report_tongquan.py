"""TRANG TỔNG QUAN (tongquan.html) — gom số chính cần theo dõi từ báo cáo trực tiếp
+ dữ liệu 30 ngày, vào 1 màn hình. Tái dùng `rows` của report_live.fetch_live (0 call
API thêm) + đọc khuvuc_data/ cho xu hướng & điểm nóng khu vực.

Gọi từ report_live.main: build_html(rows) → docs/<slug>/tongquan.html.
"""
from __future__ import annotations
import html, os
from datetime import datetime, timezone, timedelta

from am_map import AM_OF

VN = timezone(timedelta(hours=7))
LATE_H = int(os.environ.get("EOD_LATE_START_HOUR", "9") or "9")


def _n(x): return "{:,}".format(int(x or 0)).replace(",", ".")
def _esc(s): return html.escape(str(s if s is not None else ""))
def _pct(a, b): return round(a * 100 / b) if b else None
def _cls(p): return "bad" if (p is None or p < 60) else ("warn" if p < 80 else "good")
def _codm(v):
    v = v or 0
    return "0" if v < 1e5 else ("%.1f" % (v / 1e6)).replace(".", ",")


def _load_days(n=14):
    try:
        import report_khuvuc
        return report_khuvuc._load_days(n)
    except Exception:
        return []


def build_html(rows):
    now = datetime.now(VN)
    # ---- Gộp vùng ----
    def S(k): return sum(r.get(k, 0) for r in rows)
    total, gtc, backlog = S("total"), S("gtc"), S("backlog")
    ontrip, fin, ltc = S("ontrip"), S("fin"), S("ltc")
    vngh, vngh_gtc, cod_gtb = S("vngh"), S("vngh_gtc"), S("cod_gtb")
    on_road = sum(d.get("ot_tot", 0) - d.get("ot_done", 0) for r in rows for d in r.get("drivers", []))
    late_nv = sum(1 for r in rows for d in r.get("drivers", [])
                  if d.get("st") is not None and d["st"].hour >= LATE_H)
    reg_pct = _pct(gtc, total)
    vpct = _pct(vngh_gtc, vngh)

    # ---- AM / bưu cục ----
    am = {}
    for r in rows:
        a = AM_OF.get(r["name"])
        if not a:
            continue
        d = am.setdefault(a, {"gtc": 0, "total": 0, "cod": 0})
        d["gtc"] += r.get("gtc", 0); d["total"] += r.get("total", 0); d["cod"] += r.get("cod_gtb", 0)
    am_worst = min([(a, _pct(v["gtc"], v["total"])) for a, v in am.items() if v["total"] >= 50],
                   key=lambda x: x[1], default=None)
    am_cod = max([(a, v["cod"]) for a, v in am.items()], key=lambda x: x[1], default=None)
    bc_worst = min([(r["name"], _pct(r["gtc"], r["total"])) for r in rows if r.get("total", 0) >= 50],
                   key=lambda x: x[1], default=None)
    bc_cod = max([(r["name"], r.get("cod_gtb", 0)) for r in rows], key=lambda x: x[1], default=None)

    # ---- Xu hướng + điểm nóng khu vực (khuvuc_data) ----
    days = _load_days(14)
    day_series = [(d["ngay"][8:] + "/" + d["ngay"][5:7], _pct(d["gtc"], d["tot"])) for d in days]
    hard_ward = None
    if days:
        latest = days[-1]
        cand = [w for w in latest.get("wards", []) if w[2] >= 20]
        if cand:
            w = min(cand, key=lambda w: _pct(w[3], w[2]))
            hard_ward = (w[1], w[0], _pct(w[3], w[2]))   # ward, dist, pct

    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta http-equiv='refresh' content='300'>")
    P.append("<meta name='theme-color' content='#0a0d18'>")
    P.append("<title>Tổng quan TBB · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    P.append("<div class='wrap'>")
    P.append("<header class='top'><div class='brand'><span class='live'></span>TỔNG QUAN VÙNG TBB</div>"
             "<div class='ts'>%s · %s</div></header>" % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    # Hero %GTC
    P.append("<section class='hero %s'>" % _cls(reg_pct))
    P.append("<div class='hlbl'>🎯 %GTC TOÀN VÙNG HÔM NAY</div>")
    P.append("<div class='hpct'>%s<span>%%</span></div>" % (reg_pct if reg_pct is not None else "—"))
    P.append(_bar(reg_pct))
    P.append("<div class='hsub'>%s / %s đơn giao thành công · cần giao %s</div>"
             % (_n(gtc), _n(total), _n(total + backlog)))
    P.append("</section>")

    # KPI band
    kpis = [
        ("📥", _n(total), "Đã gán", ""),
        ("⏳", _n(backlog), "Chưa gán", "warn"),
        ("🏃", _n(ontrip), "Đang chạy", ""),
        ("📦", _n(on_road), "Còn phải giao", "warn"),
        ("✅", _n(gtc), "GTC nay", "good"),
        ("💰", _codm(cod_gtb) + "tr", "COD GTB kẹt", "bad"),
        ("🛒", _n(ltc), "LTC", "good"),
        ("🛍️", (str(vpct) + "%") if vpct is not None else "—", "%GTC TikTok", _cls(vpct)),
    ]
    P.append("<section class='grid'>")
    for ic, val, lab, cl in kpis:
        P.append("<div class='k'><div class='kv %s'>%s</div><div class='kl'>%s %s</div></div>"
                 % (cl, val, ic, lab))
    P.append("</section>")

    # Xu hướng %GTC
    if day_series:
        P.append("<div class='sec'>📈 %GTC theo ngày (14 ngày gần nhất)</div>")
        P.append("<div class='card'>%s</div>" % _spark(day_series))

    # Điểm nóng
    P.append("<div class='sec'>⚠️ Điểm nóng cần chú ý</div>")
    P.append("<section class='hot'>")
    if am_worst:
        P.append(_hot("🧑‍💼", "AM %GTC thấp nhất", am_worst[0], "%d%%" % am_worst[1], _cls(am_worst[1])))
    if bc_worst:
        P.append(_hot("🏤", "Bưu cục %GTC thấp nhất", _short(bc_worst[0]), "%d%%" % bc_worst[1], _cls(bc_worst[1])))
    if bc_cod:
        P.append(_hot("💰", "Bưu cục COD GTB cao nhất", _short(bc_cod[0]), _codm(bc_cod[1]) + "tr", "bad"))
    P.append(_hot("🕘", "NV xuất phát muộn (≥%dh)" % LATE_H, "toàn vùng", str(late_nv) + " NV", "bad" if late_nv else "good"))
    if hard_ward:
        P.append(_hot("🗺", "Xã khó giao nhất (hôm qua)", "%s · %s" % (hard_ward[0], hard_ward[1]), "%d%%" % hard_ward[2], _cls(hard_ward[2])))
    P.append("</section>")

    # Truy cập nhanh
    P.append("<div class='sec'>🔗 Xem chi tiết</div>")
    links = [
        ("index.html", "🟢 Trực tiếp", "%GTC · gán · COD theo NV/BC/AM"),
        ("eod.html", "📊 Cuối ngày", "chốt %GTC · Top 10 BC COD GTB"),
        ("backlog.html", "📦 Tồn đọng", "Lấy · Giao · Trả theo giờ"),
        ("chuyendi.html", "🚚 Hiệu suất chuyến đi", "đơn/giờ · giờ ra hàng"),
        ("nhanvien.html", "⚡ Năng suất NV", "xếp hạng GTC/ngày"),
        ("khuvuc.html", "🗺 Bản đồ khu vực", "GTC theo xã · bản đồ nhiệt"),
        ("xephang.html", "🏆 Xếp hạng tổng hợp", "điểm AM · BC · NV"),
        ("khochuyentiep.html", "📦 Kho chuyển tiếp", "tồn luân chuyển"),
    ]
    for href, name, desc in links:
        P.append("<a class='eod' href='%s'><span>%s</span><span class='arw'>%s →</span></a>"
                 % (href, name, desc))

    P.append("<div class='foot'>Tổng hợp real-time từ báo cáo trực tiếp + dữ liệu 30 ngày · "
             "COD GTB = tiền thu hộ kẹt trên đơn giao hỏng (triệu) · nguồn nhanh.ghn.vn</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


def _short(bc):
    return bc  # giữ nguyên "(TỈNH) Tên" cho rõ


def _bar(p):
    p = p if p is not None else 0
    cls = _cls(p)
    return "<div class='bar'><div class='fill %s' style='width:%d%%'></div></div>" % (cls, min(p, 100))


def _hot(ic, lab, name, val, cls):
    return ("<div class='ht %s'><div class='hi'>%s</div><div class='hm'>"
            "<div class='hl'>%s</div><div class='hn'>%s</div></div>"
            "<div class='hv %s'>%s</div></div>" % (cls, ic, _esc(lab), _esc(name), cls, _esc(val)))


def _spark(series):
    W, H, pb, pt, pl = 700, 170, 30, 16, 24
    n = len(series)
    bw = (W - pl) / n
    vals = [v for _, v in series if v is not None] or [0]
    top = 100
    P = ["<svg viewBox='0 0 %d %d' class='chart' preserveAspectRatio='xMidYMid meet'>" % (W, H)]
    for gy in (0, 25, 50, 75, 100):
        y = pt + (H - pt - pb) * (1 - gy / 100)
        P.append("<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' class='grid'/>" % (pl, y, W, y))
        P.append("<text x='%d' y='%.1f' class='gl'>%d</text>" % (pl - 4, y + 3, gy))
    yt = pt + (H - pt - pb) * (1 - 80 / top)
    P.append("<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' class='tgt'/>" % (pl, yt, W, yt))
    barw = min(bw * 0.66, 44)
    for i, (lab, v) in enumerate(series):
        if v is None:
            continue
        cx = pl + i * bw + bw / 2
        h = (H - pt - pb) * (v / top)
        y = (H - pb) - h
        cls = _cls(v)
        P.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' rx='3' class='b %s'/>" % (cx - barw / 2, y, barw, max(h, 1), cls))
        P.append("<text x='%.1f' y='%.1f' class='bv'>%d</text>" % (cx, y - 4, v))
        P.append("<text x='%.1f' y='%d' class='bx'>%s</text>" % (cx, H - 10, _esc(lab)))
    P.append("</svg>")
    return "".join(P)


_CSS = """<style>
:root{--bg:#0a0d18;--card:#131829;--line:#1f2740;--txt:#e7ecf7;--mut:#8792ad;
--good:#22c55e;--warn:#f59e0b;--bad:#ef4444;--accent:#3b82f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:10px 12px calc(16px + env(safe-area-inset-bottom))}
.top{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;
 padding:calc(10px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 72%,rgba(10,13,24,0))}
.brand{font-weight:800;font-size:15.5px;letter-spacing:.02em;display:flex;align-items:center;gap:7px}
.live{width:8px;height:8px;border-radius:50%;background:var(--good);box-shadow:0 0 0 0 rgba(34,197,94,.6);animation:pl 2s infinite}
@keyframes pl{0%{box-shadow:0 0 0 0 rgba(34,197,94,.5)}70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
.ts{color:var(--mut);font-size:12px}
.hero{border-radius:16px;padding:16px;text-align:center;margin-bottom:12px;border:1px solid var(--line);
 background:radial-gradient(120% 100% at 50% 0,rgba(59,130,246,.12),transparent),var(--card)}
.hero.good{background:radial-gradient(120% 100% at 50% 0,rgba(34,197,94,.14),transparent),var(--card)}
.hero.warn{background:radial-gradient(120% 100% at 50% 0,rgba(245,158,11,.14),transparent),var(--card)}
.hero.bad{background:radial-gradient(120% 100% at 50% 0,rgba(239,68,68,.14),transparent),var(--card)}
.hlbl{color:var(--mut);font-size:12px;font-weight:600;letter-spacing:.04em}
.hpct{font-size:52px;font-weight:800;line-height:1.05;font-variant-numeric:tabular-nums}
.hpct span{font-size:24px;color:var(--mut)}
.hsub{color:var(--mut);font-size:12.5px;margin-top:8px;font-variant-numeric:tabular-nums}
.bar{height:8px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden;margin:10px auto 0;max-width:420px}
.fill{height:100%;border-radius:5px}.fill.good{background:var(--good)}.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:6px}
.k{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 5px;text-align:center}
.kv{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}
.kv.good{color:var(--good)}.kv.warn{color:var(--warn)}.kv.bad{color:var(--bad)}
.kl{font-size:10px;color:var(--mut);margin-top:3px}
.sec{font-weight:700;font-size:13px;margin:18px 2px 8px;color:#c7d0e6}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 6px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:rgba(255,255,255,.06);stroke-width:1}
.chart .tgt{stroke:var(--good);stroke-width:1;stroke-dasharray:4 3;opacity:.6}
.chart .gl{fill:var(--mut);font-size:9px;text-anchor:end}
.chart .b{fill:var(--accent)}.chart .b.good{fill:var(--good)}.chart .b.warn{fill:var(--warn)}.chart .b.bad{fill:var(--bad)}
.chart .bv{fill:var(--txt);font-size:10px;font-weight:700;text-anchor:middle}
.chart .bx{fill:var(--mut);font-size:9px;text-anchor:middle}
.hot{display:flex;flex-direction:column;gap:8px}
.ht{display:flex;align-items:center;gap:11px;background:var(--card);border:1px solid var(--line);
 border-left:3px solid var(--line);border-radius:12px;padding:11px 13px}
.ht.good{border-left-color:var(--good)}.ht.warn{border-left-color:var(--warn)}.ht.bad{border-left-color:var(--bad)}
.hi{font-size:20px;flex:none}
.hm{flex:1;min-width:0}
.hl{font-size:11px;color:var(--mut)}
.hn{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hv{font-weight:800;font-size:17px;font-variant-numeric:tabular-nums;flex:none}
.hv.good{color:var(--good)}.hv.warn{color:var(--warn)}.hv.bad{color:var(--bad)}
.eod{display:flex;justify-content:space-between;align-items:center;gap:10px;background:var(--card);
 border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0;text-decoration:none;color:var(--txt);font-weight:600}
.eod .arw{color:var(--mut);font-size:11.5px;font-weight:500;text-align:right}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:16px 0 8px}
@media (max-width:430px){
  .grid{grid-template-columns:repeat(2,1fr)}
  .hpct{font-size:46px}.kv{font-size:17px}
  .eod{font-size:13.5px;padding:11px 12px}.eod .arw{font-size:11px}
}
</style></head><body>"""

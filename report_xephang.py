"""
TRANG XẾP HẠNG TỔNG HỢP (scorecard) — đánh giá AM → Bưu cục → Nhân viên.
Điểm tổng hợp 0-100 từ ma trận chỉ số (Supabase 7 ngày). Xếp TỆ→TỐT mỗi cấp;
màu theo NHÓM 3 (tỉ lệ): 1/3 cuối 🔴 · 1/3 giữa 🟡 · 1/3 đầu 🟢.

Trọng số "Cân bằng vận hành": %GTC 35 · Năng suất 20 · Kỷ luật 20 · Tồn đỏ 15 · COD 10.
Cấp NV không có Tồn đỏ → chuẩn hoá lại 4 chỉ số còn lại.

Đọc Supabase (như report_trend). report_trend.main() gọi gen_html() → docs/<slug>/xephang.html.
"""
from __future__ import annotations
import html
import os
from datetime import datetime, timedelta, timezone

from report_trend import _get_all
from am_map import AM_OF

VN = timezone(timedelta(hours=7))
PROV_NAME = {"LCA": "Lào Cai", "YBA": "Yên Bái", "SLA": "Sơn La",
             "DBI": "Điện Biên", "LCH": "Lai Châu"}

# Trọng số (BC/AM có đủ 5; NV bỏ 'red' rồi chuẩn hoá lại)
W = {"gtc": .35, "ns": .20, "kyluat": .10, "red": .20, "cod": .15}
WINDOW_DAYS = 30              # cửa sổ đánh giá (ngày gần nhất)
NS_HI, NS_LO = 120, 30        # năng suất GTC/ngày làm: ≥120→100, ≤30→0
COD_CAP = 2_000_000            # COD kẹt/đơn ≥2 triệu → 0 điểm
RED_CAP = 0.5                  # đơn đỏ / (đơn giao TB ngày) ≥0.5 → 0 điểm


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s))


def _clamp(v):
    return max(0.0, min(100.0, v))


def _sub(a, red=None, days=WINDOW_DAYS):
    """Chấm điểm con 0-100 từ số liệu gộp. a: don,gtc,gtb,cod,wd,xp,ontime."""
    don, gtc, gtb, cod, wd = a["don"], a["gtc"], a["gtb"], a["cod"], a["wd"]
    xp, ontime = a["xp"], a["ontime"]
    s = {}
    s["gtc"] = _clamp(gtc / don * 100) if don else None
    ns = (gtc / wd) if wd else None
    s["ns"] = _clamp((ns - NS_LO) / (NS_HI - NS_LO) * 100) if ns is not None else None
    s["kyluat"] = _clamp(ontime / xp * 100) if xp else None
    codper = (cod / gtb) if gtb else 0
    s["cod"] = _clamp(100 - min(codper / COD_CAP, 1) * 100)
    if red is not None:
        daily = don / max(days, 1)          # đơn giao TB ngày (theo số ngày dữ liệu thật)
        ratio = (red / daily) if daily else 0
        s["red"] = _clamp(100 - min(ratio / RED_CAP, 1) * 100)
    return s, ns, codper


def _composite(sub, has_red):
    w = dict(W)
    if not has_red:
        w.pop("red")
    num = den = 0.0
    for k, wt in w.items():
        v = sub.get(k)
        if v is None:
            continue
        num += v * wt; den += wt
    return round(num / den, 1) if den else None


def _tcolor(idx, n):
    """idx: vị trí trong danh sách xếp TĂNG dần (0 = tệ nhất). Nhóm 3 theo tỉ lệ."""
    if n <= 1:
        return "warn"
    if n == 2:
        return "bad" if idx == 0 else "good"
    frac = idx / (n - 1)
    return "bad" if frac < 1 / 3 else ("warn" if frac < 2 / 3 else "good")


def fetch():
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    since = (datetime.now(VN).date() - timedelta(days=WINDOW_DAYS - 1)).isoformat()
    nv = _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&select=ngay,driver_id,buu_cuc,tinh,"
                  "ten_nv,don_giao,gtc,gtb,cod_gtb,gio_xuat_phat,xuat_phat_muon" % since)
    td_all = _get_all(url, key, "bao_cao_ton_dong?ngay=gte.%s&select=ngay,buu_cuc,order_type,g_red" % since)
    latest = max((r["ngay"] for r in td_all), default=None)
    red_by_bc = {}
    for r in td_all:
        if r["ngay"] != latest:
            continue
        red_by_bc[r["buu_cuc"]] = red_by_bc.get(r["buu_cuc"], 0) + (r.get("g_red") or 0)
    return {"nv": nv, "red_by_bc": red_by_bc, "since": since, "td_day": latest}


def _agg0(tinh=None, bc=None):
    return {"tinh": tinh, "bc": bc, "don": 0, "gtc": 0, "gtb": 0, "cod": 0.0,
            "wd": 0, "xp": 0, "ontime": 0, "days": set()}


def build(data):
    """→ danh sách AM (mỗi AM có bcs, mỗi bc có nvs), đã chấm điểm + màu nhóm-3."""
    red_by_bc = data["red_by_bc"]
    ndays = len({r["ngay"] for r in data["nv"]}) or WINDOW_DAYS  # số ngày dữ liệu thật
    # 1) Gộp theo NHÂN VIÊN (cửa sổ đánh giá)
    nvagg = {}
    for r in data["nv"]:
        k = (r.get("driver_id") or "", r.get("buu_cuc") or "")
        a = nvagg.get(k)
        if a is None:
            a = nvagg[k] = {"ten": "", "bc": r.get("buu_cuc"), "tinh": r.get("tinh"),
                            "don": 0, "gtc": 0, "gtb": 0, "cod": 0.0, "days": set(),
                            "xp": 0, "ontime": 0}
        a["don"] += r.get("don_giao") or 0; a["gtc"] += r.get("gtc") or 0
        a["gtb"] += r.get("gtb") or 0; a["cod"] += r.get("cod_gtb") or 0
        if (r.get("don_giao") or 0) > 0:
            a["days"].add(r["ngay"])
        if r.get("gio_xuat_phat"):
            a["xp"] += 1
            if not r.get("xuat_phat_muon"):
                a["ontime"] += 1
        if r.get("ten_nv"):
            a["ten"] = r["ten_nv"]
    for a in nvagg.values():
        a["wd"] = len(a["days"])

    # 2) Gộp BƯU CỤC (từ NV) + 3) Gộp AM (từ BC)
    bcagg = {}
    for k, a in nvagg.items():
        if a["don"] <= 0:
            continue
        bc = a["bc"]
        b = bcagg.get(bc)
        if b is None:
            b = bcagg[bc] = {"bc": bc, "tinh": a["tinh"], "don": 0, "gtc": 0, "gtb": 0,
                             "cod": 0.0, "wd": 0, "xp": 0, "ontime": 0, "nvs": []}
        for f in ("don", "gtc", "gtb", "cod", "wd", "xp", "ontime"):
            b[f] += a[f]
        sub, ns, codper = _sub(a, red=None)
        b["nvs"].append({"ten": a["ten"], "sub": sub, "ns": ns, "codper": codper,
                         "don": a["don"], "comp": _composite(sub, False),
                         "kyluat_xp": a["xp"], "kyluat_ok": a["ontime"]})

    amagg = {}
    for bc, b in bcagg.items():
        am = AM_OF.get(bc)
        if not am:
            continue
        x = amagg.get(am)
        if x is None:
            x = amagg[am] = {"am": am, "don": 0, "gtc": 0, "gtb": 0, "cod": 0.0,
                             "wd": 0, "xp": 0, "ontime": 0, "red": 0, "bcs": []}
        for f in ("don", "gtc", "gtb", "cod", "wd", "xp", "ontime"):
            x[f] += b[f]
        x["red"] += red_by_bc.get(bc, 0)
        sub, ns, codper = _sub(b, red=red_by_bc.get(bc, 0), days=ndays)
        b["sub"] = sub; b["ns"] = ns; b["codper"] = codper
        b["red"] = red_by_bc.get(bc, 0)
        b["comp"] = _composite(sub, True)
        x["bcs"].append(b)

    ams = []
    for am, x in amagg.items():
        sub, ns, codper = _sub(x, red=x["red"], days=ndays)
        x["sub"] = sub; x["ns"] = ns; x["codper"] = codper
        x["comp"] = _composite(sub, True)
        # màu nhóm-3 cho BC trong AM
        bcs = sorted([b for b in x["bcs"] if b["comp"] is not None], key=lambda b: b["comp"])
        for i, b in enumerate(bcs):
            b["color"] = _tcolor(i, len(bcs))
            nvs = sorted([v for v in b["nvs"] if v["comp"] is not None], key=lambda v: v["comp"])
            for j, v in enumerate(nvs):
                v["color"] = _tcolor(j, len(nvs))
            b["nvs_sorted"] = nvs
        x["bcs_sorted"] = bcs
        ams.append(x)
    ams = sorted([a for a in ams if a["comp"] is not None], key=lambda a: a["comp"])
    for i, a in enumerate(ams):
        a["color"] = _tcolor(i, len(ams))
    return ams


# ---------- Render ----------
_HEAD = """<!doctype html><html lang='vi'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name='robots' content='noindex,nofollow'>
<meta http-equiv='refresh' content='900'>
<meta name='theme-color' content='#0a0d18'>
<title>Xếp hạng tổng hợp · TBB</title>
<style>
:root{--bg:#0a0d18;--card:#161b2d;--card2:#1b2136;--line:#272d45;--mut:#8b92ab;--txt:#eef0f7;
--good:#2fd07a;--warn:#f7b955;--bad:#f2585f;--ink:#0a0d18}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:linear-gradient(180deg,#0b0f1c,#0a0d18 240px,#0a0d18);color:var(--txt);font-size:15px;line-height:1.35}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 40px;padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right))}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0))}
.brand{font-weight:800;letter-spacing:.04em;font-size:15px}
.ts{color:var(--mut);font-size:12px}
.hero{border-radius:18px;padding:16px;margin:4px 0 12px;background:radial-gradient(120% 90% at 100% 0,rgba(255,255,255,.05),transparent),var(--card);border:1px solid var(--line)}
.hlbl{color:var(--mut);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.htitle{font-size:20px;font-weight:850;margin:6px 0 4px}
.hsub{color:var(--mut);font-size:12.5px;margin-top:6px}
.leg{display:flex;gap:12px;margin-top:10px;font-size:11.5px;color:var(--mut);flex-wrap:wrap}
.leg b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:middle}
.sec{font-size:12px;font-weight:700;letter-spacing:.05em;color:#b9c0da;text-transform:uppercase;margin:16px 4px 8px}
details{border:1px solid var(--line);border-left:4px solid var(--line);border-radius:13px;margin:8px 0;overflow:hidden;background:var(--card)}
details.good{border-left-color:var(--good)}details.warn{border-left-color:var(--warn)}details.bad{border-left-color:var(--bad)}
summary{list-style:none;cursor:pointer;padding:11px 13px;display:flex;align-items:center;gap:10px}
summary::-webkit-details-marker{display:none}
details[open]{background:var(--card2)}
.rk{color:var(--mut);font-weight:700;font-size:12px;min-width:20px;text-align:center}
.nm{font-weight:700;font-size:15px;flex:1;min-width:0}
.nm .s2{display:block;color:var(--mut);font-size:11px;font-weight:500}
.score{min-width:52px;text-align:center;border-radius:10px;padding:5px 8px;font-weight:850;font-size:16px;font-variant-numeric:tabular-nums}
.score.good{background:rgba(47,208,122,.16);color:var(--good)}
.score.warn{background:rgba(247,185,85,.16);color:var(--warn)}
.score.bad{background:rgba(242,88,95,.16);color:var(--bad)}
.body{padding:0 10px 10px}
.mx{display:flex;flex-wrap:wrap;gap:4px 8px;color:var(--mut);font-size:11px;padding:2px 4px 8px;font-variant-numeric:tabular-nums}
.mx b{color:var(--txt);font-weight:700}
.sub2{margin:6px 0;border-left-width:3px;border-radius:10px}
.sub2 summary{padding:9px 11px}
.sub2 .nm{font-size:14px}
.sub3{margin:5px 0;border-left-width:3px;border-radius:9px;background:rgba(255,255,255,.02)}
.sub3 .row{display:flex;align-items:center;gap:9px;padding:8px 11px;border-top:1px solid rgba(255,255,255,.05)}
.sub3 .row:first-child{border-top:none}
.sub3 .nm{font-size:13.5px}
.sc-sm{min-width:42px;text-align:center;border-radius:8px;padding:3px 6px;font-weight:800;font-size:13px}
.sc-sm.good{background:rgba(47,208,122,.16);color:var(--good)}
.sc-sm.warn{background:rgba(247,185,85,.16);color:var(--warn)}
.sc-sm.bad{background:rgba(242,88,95,.16);color:var(--bad)}
.mini{display:block;margin-top:2px;color:var(--mut);font-size:10.5px;font-weight:500}
.eod{display:flex;align-items:center;justify-content:space-between;text-decoration:none;color:var(--txt);background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;padding:14px 16px;margin:14px 0 8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:20px 0 4px}
.empty{color:var(--mut);text-align:center;padding:30px 20px;font-size:13px}
</style></head><body>"""


def _mx(sub, ns, codper, red=None, don=None):
    def g(k):
        v = sub.get(k)
        return "—" if v is None else "%.0f" % v
    parts = ["🎯 %%GTC <b>%s</b>" % g("gtc"),
             "⚡ NS <b>%s</b>" % (("%.0f" % ns) if ns is not None else "—"),
             "🕗 Kỷ luật <b>%s</b>" % g("kyluat")]
    if red is not None:
        parts.append("🔴 đỏ <b>%s</b>" % _n(red))
    parts.append("💰 COD/đơn <b>%s</b>" % (("%.1ftr" % (codper / 1e6)).replace(".", ",") if codper else "0"))
    return "<div class='mx'>" + " · ".join(parts) + "</div>"


def gen_html(data):
    now = datetime.now(VN)
    P = [_HEAD, "<div class='wrap'>",
         "<header class='top'><div class='brand'>🏆 XẾP HẠNG TỔNG HỢP</div>"
         "<div class='ts'>cập nhật %s</div></header>" % now.strftime("%H:%M %d/%m")]
    ams = build(data) if data else []
    if not ams:
        P.append("<div class='empty'>⚙️ Chưa đủ dữ liệu Supabase để xếp hạng.<br>"
                 "Số liệu lưu mỗi tối — quay lại sau vài ngày.</div></div></body></html>")
        return "\n".join(P)

    n_bc = sum(len(a["bcs_sorted"]) for a in ams)
    n_nv = sum(len(b["nvs_sorted"]) for a in ams for b in a["bcs_sorted"])
    earliest = min((r["ngay"] for r in data["nv"]), default=data.get("since") or "")
    dstr = earliest[5:].replace("-", "/")
    P.append("<section class='hero'>")
    P.append("<div class='hlbl'>THẺ ĐIỂM ĐÁNH GIÁ · 30 NGÀY GẦN NHẤT (từ %s)</div>" % dstr)
    P.append("<div class='htitle'>%d AM · %d bưu cục · %d nhân viên</div>" % (len(ams), n_bc, n_nv))
    P.append("<div class='hsub'>Điểm tổng hợp 0–100 · %GTC 35 · Năng suất 20 · Tồn đỏ 20 · COD 15 · Kỷ luật 10</div>")
    P.append("<div class='leg'><span><b style='background:var(--bad)'></b>Tệ nhất (1/3 cuối)</span>"
             "<span><b style='background:var(--warn)'></b>Chấp nhận (giữa)</span>"
             "<span><b style='background:var(--good)'></b>Tốt (1/3 đầu)</span></div>")
    P.append("</section>")

    P.append("<div class='sec'>🧑‍💼 Xếp hạng AM (tệ → tốt) · bấm mở xem bưu cục → nhân viên</div>")
    for i, a in enumerate(ams, 1):
        P.append("<details class='%s'><summary>"
                 "<span class='rk'>%d</span>"
                 "<span class='nm'>%s<span class='s2'>%d bưu cục · %s đơn/30ngày</span></span>"
                 "<span class='score %s'>%s</span></summary>"
                 % (a["color"], i, _esc(a["am"]), len(a["bcs_sorted"]), _n(a["don"]),
                    a["color"], a["comp"]))
        P.append("<div class='body'>")
        P.append(_mx(a["sub"], a["ns"], a["codper"], red=a["red"]))
        # BC trong AM (tệ→tốt)
        for j, b in enumerate(a["bcs_sorted"], 1):
            prov = PROV_NAME.get(b.get("tinh"), b.get("tinh") or "")
            P.append("<details class='sub2 %s'><summary>"
                     "<span class='rk'>%d</span>"
                     "<span class='nm'>%s<span class='s2'>%s · %d NV · %s đơn</span></span>"
                     "<span class='score %s'>%s</span></summary>"
                     % (b["color"], j, _esc(b["bc"]), _esc(prov), len(b["nvs_sorted"]),
                        _n(b["don"]), b["color"], b["comp"]))
            P.append("<div class='body'>")
            P.append(_mx(b["sub"], b["ns"], b["codper"], red=b.get("red")))
            # NV trong BC (tệ→tốt)
            P.append("<div class='sub3 %s' style='border-left-color:var(--line)'>" % "")
            for v in b["nvs_sorted"]:
                kl = ("%.0f%%" % v["sub"]["kyluat"]) if v["sub"]["kyluat"] is not None else "—"
                gtc = ("%.0f" % v["sub"]["gtc"]) if v["sub"]["gtc"] is not None else "—"
                ns = ("%.0f" % v["ns"]) if v["ns"] is not None else "—"
                P.append("<div class='row'><span class='nm'>%s"
                         "<span class='mini'>GTC %s · NS %s · KL %s</span></span>"
                         "<span class='sc-sm %s'>%s</span></div>"
                         % (_esc(v["ten"]), gtc, ns, kl, v["color"], v["comp"]))
            P.append("</div></div></details>")
        P.append("</div></details>")

    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>Điểm tổng hợp = %GTC·35 + Năng suất·20 + Tồn đỏ·20 + COD·15 + Kỷ luật·10 "
             "(NV: bỏ Tồn đỏ, chuẩn hoá lại 4). Màu theo NHÓM 3 (tỉ lệ) trong từng cấp.<br>"
             "Năng suất = GTC/ngày làm · Kỷ luật = %% ngày xuất phát &lt;9h · nguồn Supabase 30 ngày</div>")
    P.append("</div></body></html>")
    return "\n".join(P)

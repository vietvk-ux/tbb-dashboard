"""Trang QUẢN LÝ NHÂN VIÊN (nvxuly.html) — cho việc KIỂM SOÁT/XỬ LÝ nhân sự.
Khác các trang NV khác (theo ngày/xếp hạng): trang này nhìn 14 NGÀY để phân biệt
"yếu 1 hôm" vs "yếu DAI DẲNG" → phục vụ ra quyết định (khiển trách/đào tạo/điều chuyển).

Gồm:
  1) 📋 NV cần xử lý — %GTC dưới chuẩn nhiều ngày (streak đỏ), sắp nặng→nhẹ.
  2) 👤 Tra cứu hồ sơ bất kỳ NV (ô tìm) → thẻ 14 ngày (streak + chất lượng·năng suất·kỷ luật·COD).

Đọc bao_cao_nhan_vien 14 ngày (Supabase). report_trend.main() gọi generate(outdir).
"""
from __future__ import annotations
import os
import html
import json
import logging
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger("nvxuly")
VN = timezone(timedelta(hours=7))
WINDOW = 14           # số ngày nhìn lại
MIN_DON_DAY = 10      # ngày "hoạt động" = giao ≥10 đơn (bỏ ngày lẻ)
MIN_ACTIVE = 5        # đủ mẫu để kết luận dai dẳng
YEU = 50             # %GTC < 50 = ngày yếu / NV yếu


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _pct(g, t):
    return round(g * 100 / t) if t else None


def _codm(v):
    v = v or 0
    if v < 1e5:
        return "0"
    if v >= 1e9:
        return ("%.2f tỷ" % (v / 1e9)).replace(".", ",")
    return ("%.1ftr" % (v / 1e6)).replace(".", ",")


def _cls(p):
    if p is None:
        return "na"
    return "bad" if p < YEU else ("warn" if p < 70 else "good")


def _get_all(url, key, path, page=1000):
    rows, off = [], 0
    h = {"apikey": key, "Authorization": "Bearer " + key}
    while True:
        r = requests.get("%s/rest/v1/%s&limit=%d&offset=%d" % (url, path, page, off),
                         headers=h, timeout=45)
        if not r.ok:
            raise RuntimeError("Supabase %d: %s" % (r.status_code, r.text[:150]))
        c = r.json()
        rows += c
        if len(c) < page:
            break
        off += page
    return rows


def fetch():
    """bao_cao_nhan_vien WINDOW ngày gần nhất (đến hôm qua). None nếu thiếu creds/lỗi."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    today = datetime.now(VN).date()
    since = (today - timedelta(days=WINDOW)).isoformat()
    try:
        return _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&ngay=lt.%s"
                        "&select=ngay,driver_id,buu_cuc,tinh,ten_nv,don_giao,gtc,cod_gtb,xuat_phat_muon"
                        "&order=id.asc" % (since, today.isoformat()))
    except Exception as e:
        logger.warning("Fetch NV lỗi: %s", str(e)[:150])
        return None


def build(rows):
    """Gộp theo NHÂN VIÊN (driver_id + bưu cục) → list dict đã tính chỉ số 14 ngày."""
    nv = {}
    for r in rows:
        k = (r.get("driver_id") or "", r.get("buu_cuc") or "")
        a = nv.get(k)
        if a is None:
            a = nv[k] = {"ten": r.get("ten_nv") or "?", "bc": r.get("buu_cuc") or "?",
                         "tinh": r.get("tinh"), "days": {}}
        if r.get("ten_nv"):
            a["ten"] = r["ten_nv"]
        a["days"][r.get("ngay")] = {
            "don": r.get("don_giao") or 0, "gtc": r.get("gtc") or 0,
            "cod": r.get("cod_gtb") or 0, "muon": bool(r.get("xuat_phat_muon")),
        }
    out = []
    for (did, bc), a in nv.items():
        ds = sorted(a["days"].items())                      # [(ngay, {..})]
        act = [d for _, d in ds if d["don"] >= MIN_DON_DAY]  # ngày hoạt động
        if not act:
            continue
        sd = sum(d["don"] for d in act)
        sg = sum(d["gtc"] for d in act)
        avg = _pct(sg, sd)
        yeu = sum(1 for d in act if d["don"] and _pct(d["gtc"], d["don"]) < YEU)
        muon = sum(1 for _, d in ds if d["muon"])
        cod = sum(d["cod"] for _, d in ds)
        # chuỗi ngày (mọi ngày có dữ liệu) cho streak
        series = [{"ng": ng[5:], "don": d["don"], "pc": (_pct(d["gtc"], d["don"]) if d["don"] else None),
                   "muon": d["muon"]} for ng, d in ds]
        out.append({"id": did, "ten": a["ten"], "bc": bc, "tinh": a["tinh"],
                    "avg": avg, "act": len(act), "yeu": yeu, "muon": muon,
                    "cod": cod, "don": sd, "gtc": sg, "series": series})
    return out


def _streak(series):
    """Dải ô 1 ngày/ô, màu theo %GTC ngày đó (đỏ<50·vàng<70·xanh≥70·xám ít đơn)."""
    cells = []
    for s in series:
        pc = s["pc"]
        cl = "z" if (s["don"] < MIN_DON_DAY or pc is None) else _cls(pc)
        tip = "%s: %s%% (%s đơn)%s" % (s["ng"], pc if pc is not None else "—", _n(s["don"]),
                                       " · muộn" if s["muon"] else "")
        cells.append("<span class='cel %s' title='%s'></span>" % (cl, _esc(tip)))
    return "<div class='streak'>%s</div>" % "".join(cells)


def _card(x, rank=None):
    """1 thẻ NV: tên·bưu cục · avg%GTC · streak 14 ngày · meta (ngày yếu/muộn/COD)."""
    cl = _cls(x["avg"])
    rk = ("<span class='rk'>%d</span>" % rank) if rank else ""
    P = ["<details class='nv %s'><summary>" % cl]
    P.append("<div class='hd'>%s<div class='nm'>%s<div class='sc'>%s</div></div>"
             "<span class='pill %s'>%s%%</span></div>" % (rk, _esc(x["ten"]), _esc(x["bc"]), cl,
                                                          x["avg"] if x["avg"] is not None else "—"))
    P.append(_streak(x["series"]))
    P.append("<div class='mt'><span class='b'>🔴 %d/%d ngày yếu</span>"
             "<span>🕘 muộn %d</span><span>📦 %s đơn</span><span class='cd'>💰 %s</span></div>"
             % (x["yeu"], x["act"], x["muon"], _n(x["don"]), _codm(x["cod"])))
    P.append("</summary><div class='dtl'>")
    # bảng ngày
    P.append("<table class='t'><thead><tr><th class='l'>Ngày</th><th>Đơn</th><th>%GTC</th>"
             "<th>Muộn</th></tr></thead><tbody>")
    for s in reversed(x["series"]):
        pc = s["pc"]
        pcell = ("<span class='pill sm %s'>%s%%</span>" % (_cls(pc), pc)) if pc is not None else "—"
        mu = "🕘" if s["muon"] else "·"
        P.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (s["ng"], _n(s["don"]), pcell, mu))
    P.append("</tbody></table></div></details>")
    return "".join(P)


def gen_html(rows):
    now = datetime.now(VN)
    data = build(rows) if rows else []
    ndays = max((x["act"] for x in data), default=0)
    # NV cần xử lý: đủ mẫu + %GTC TB dưới chuẩn (yếu dai dẳng)
    flagged = sorted([x for x in data if x["act"] >= MIN_ACTIVE and x["avg"] is not None and x["avg"] < YEU],
                     key=lambda x: (x["avg"], -x["yeu"]))
    # dữ liệu tra cứu (mọi NV đủ mẫu) cho JS search
    idx = [{"ten": x["ten"], "bc": x["bc"], "avg": x["avg"], "yeu": x["yeu"], "act": x["act"],
            "muon": x["muon"], "don": x["don"], "cod": x["cod"], "series": x["series"]}
           for x in sorted(data, key=lambda x: (x["avg"] if x["avg"] is not None else 999))
           if x["act"] >= 3]

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'><meta http-equiv='refresh' content='900'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Quản lý Nhân viên · TBB</title>", _CSS, "<div class='wrap'>"]
    P.append("<header class='top'><div class='brand'>👤 QUẢN LÝ NHÂN VIÊN</div>"
             "<div class='ts'>%s · %d ngày</div></header>" % (now.strftime("%H:%M · %d/%m"), WINDOW))
    P.append("<div class='note'>Nhìn <b>%d ngày</b> gần nhất để tách <b>yếu dai dẳng</b> khỏi \"xui 1 hôm\". "
             "Streak: mỗi ô = 1 ngày (🟥&lt;50%% · 🟨50-70%% · 🟩≥70%% · ⬛ ít đơn). "
             "Ngày hoạt động = giao ≥%d đơn.</div>" % (WINDOW, MIN_DON_DAY))

    # ---- Tra cứu ----
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tra cứu hồ sơ nhân viên (gõ tên / bưu cục)...' "
             "oninput='doSearch()' autocomplete='off'></div>")
    P.append("<div id='res' class='res'></div>")

    # ---- NV cần xử lý ----
    P.append("<div class='sec' style='color:var(--bad)'>📋 NV cần xử lý · %%GTC dưới %d%% dai dẳng "
             "(≥%d ngày hoạt động) · %d NV</div>" % (YEU, MIN_ACTIVE, len(flagged)))
    if flagged:
        for i, x in enumerate(flagged[:60], 1):
            P.append(_card(x, rank=i))
    else:
        P.append("<div class='none'>Không có NV nào yếu dai dẳng. 👍</div>")

    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>⚡ Năng suất →</span></a>")
    P.append("<div class='foot'>NV cần xử lý = %%GTC TB &lt;%d%% qua ≥%d ngày hoạt động (giao ≥%d đơn/ngày) — "
             "yếu LIÊN TỤC, không phải nhiễu 1 hôm. Dữ liệu chốt cuối ngày (Supabase), tự cập nhật.</div>"
             % (YEU, MIN_ACTIVE, MIN_DON_DAY))
    P.append("</div>")
    # dữ liệu + JS tra cứu
    P.append("<script>var NV=%s;\n%s</script>" % (json.dumps(idx, ensure_ascii=False), _JS))
    P.append("</body></html>")
    return "".join(P)


def generate(outdir):
    """Sinh nvxuly.html. Trả True nếu ghi được; None nếu thiếu dữ liệu (caller giữ trang cũ)."""
    rows = fetch()
    if rows is None:
        return None
    with open(os.path.join(outdir, "nvxuly.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(rows))
    logger.info("nvxuly.html: %d dòng NV·ngày", len(rows))
    return True


_CSS = """<style>
:root{--bg:#0a0d18;--card:#161b2d;--card2:#1b2136;--line:#272d45;--mut:#8b92ab;--txt:#eef0f7;
 --good:#2fd07a;--warn:#f7b955;--bad:#f2585f}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 background:radial-gradient(900px 500px at 10% -5%,#241a06 0%,transparent 55%),#0a0d18;
 background-attachment:fixed;color:var(--txt);font-size:15px;line-height:1.4;-webkit-font-smoothing:antialiased}
.wrap{max-width:640px;margin:0 auto;padding:0 14px 40px;
 padding-left:max(14px,env(safe-area-inset-left));padding-right:max(14px,env(safe-area-inset-right))}
.top{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;
 padding:calc(12px + env(safe-area-inset-top)) 2px 10px;background:linear-gradient(180deg,#0a0d18 70%,rgba(10,13,24,0))}
.brand{font-weight:800;letter-spacing:.04em;font-size:15px}
.ts{color:var(--mut);font-size:12px}
.note{color:var(--mut);font-size:12px;line-height:1.6;margin:2px 2px 12px}
.note b{color:var(--txt)}
.sbar{position:sticky;top:44px;z-index:10;padding:4px 0 10px;background:linear-gradient(180deg,#0a0d18 82%,rgba(10,13,24,0))}
.search{width:100%;padding:12px 14px;border-radius:13px;border:1px solid var(--line);background:var(--card);
 color:var(--txt);font-size:15px;outline:none}
.search:focus{border-color:#4a5384}
.res:not(:empty){margin-bottom:14px}
.sec{font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;margin:16px 4px 10px}
.none{color:var(--mut);text-align:center;padding:22px;font-size:13px}
details.nv{position:relative;overflow:hidden;border-radius:14px;margin:8px 0;
 background:radial-gradient(120% 90% at 0% 0%,rgba(255,255,255,.04),var(--card) 70%);border:1px solid var(--line)}
details.nv.bad{border-color:rgba(242,88,95,.4);background:radial-gradient(120% 90% at 0% 0%,rgba(242,88,95,.12),var(--card) 72%)}
details.nv.warn{border-color:rgba(247,185,85,.32)}
details.nv summary{padding:11px 13px;cursor:pointer;list-style:none;display:flex;flex-direction:column;gap:8px}
details.nv summary::-webkit-details-marker{display:none}
.hd{display:flex;align-items:center;gap:10px}
.rk{width:22px;height:22px;border-radius:7px;background:rgba(242,88,95,.2);color:var(--bad);
 font-weight:800;font-size:12px;display:grid;place-items:center;flex:none}
.nm{flex:1;min-width:0;font-weight:700;font-size:14.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nm .sc{font-size:10.5px;color:var(--mut);font-weight:400;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-flex;align-items:center;justify-content:center;min-width:46px;padding:3px 10px;border-radius:999px;
 font-weight:800;font-size:13px;color:#0a0d18;flex:none;font-variant-numeric:tabular-nums}
.pill.sm{min-width:40px;font-size:11.5px;padding:2px 7px}
.pill.good{background:var(--good)}.pill.warn{background:var(--warn)}.pill.bad{background:var(--bad)}.pill.na{background:#3a4160;color:var(--mut)}
.streak{display:flex;gap:3px;flex-wrap:nowrap;overflow:hidden}
.cel{flex:1;height:16px;border-radius:3px;min-width:6px}
.cel.good{background:var(--good)}.cel.warn{background:var(--warn)}.cel.bad{background:var(--bad)}.cel.z{background:#2a3050}
.mt{display:flex;flex-wrap:wrap;gap:5px 12px;color:var(--mut);font-size:11px;font-variant-numeric:tabular-nums}
.mt .b{color:var(--bad);font-weight:700}.mt .cd{color:var(--warn)}
.dtl{padding:0 12px 12px}
table.t{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
table.t th,table.t td{padding:6px 4px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05)}
table.t th{color:var(--mut);font-weight:600;font-size:9.5px;text-transform:uppercase}
table.t th.l,table.t td.l{text-align:left}
table.t tbody tr:last-child td{border-bottom:none}
.eod{display:flex;align-items:center;justify-content:space-between;gap:8px;text-decoration:none;color:var(--txt);
 background:linear-gradient(135deg,#20264a,#191f38);border:1px solid #313a63;border-radius:14px;
 padding:14px 16px;margin:16px 0 8px;font-weight:700;font-size:14px}
.eod .arw{color:#aeb6e0;font-size:12px;font-weight:600}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:8px 0}
</style>"""

_JS = """
function _cls(p){return p==null?'na':(p<50?'bad':(p<70?'warn':'good'));}
function _codm(v){v=v||0;if(v<1e5)return'0';if(v>=1e9)return(v/1e9).toFixed(2).replace('.',',')+' tỷ';return(v/1e6).toFixed(1).replace('.',',')+'tr';}
function _card(x){
  var cl=_cls(x.avg);
  var cells=x.series.map(function(s){var c=(s.don<10||s.pc==null)?'z':_cls(s.pc);
    var t=s.ng+': '+(s.pc==null?'—':s.pc+'%')+' ('+s.don+' đơn)'+(s.muon?' · muộn':'');
    return "<span class='cel "+c+"' title='"+t+"'></span>";}).join('');
  var rows=x.series.slice().reverse().map(function(s){
    var p=s.pc==null?'—':"<span class='pill sm "+_cls(s.pc)+"'>"+s.pc+"%</span>";
    return "<tr><td class='l'>"+s.ng+"</td><td>"+s.don.toLocaleString('vi')+"</td><td>"+p+"</td><td>"+(s.muon?'🕘':'·')+"</td></tr>";}).join('');
  return "<details class='nv "+cl+"' open><summary><div class='hd'><div class='nm'>"+x.ten+
    "<div class='sc'>"+x.bc+"</div></div><span class='pill "+cl+"'>"+(x.avg==null?'—':x.avg+'%')+
    "</span></div><div class='streak'>"+cells+"</div><div class='mt'><span class='b'>🔴 "+x.yeu+"/"+x.act+
    " ngày yếu</span><span>🕘 muộn "+x.muon+"</span><span>📦 "+x.don.toLocaleString('vi')+" đơn</span><span class='cd'>💰 "+
    _codm(x.cod)+"</span></div></summary><div class='dtl'><table class='t'><thead><tr><th class='l'>Ngày</th><th>Đơn</th><th>%GTC</th><th>Muộn</th></tr></thead><tbody>"+
    rows+"</tbody></table></div></details>";
}
function _norm(s){return (s||'').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/đ/g,'d');}
function doSearch(){
  var q=_norm(document.getElementById('q').value.trim());var box=document.getElementById('res');
  if(q.length<2){box.innerHTML='';return;}
  var hit=NV.filter(function(x){return _norm(x.ten).indexOf(q)>=0||_norm(x.bc).indexOf(q)>=0;}).slice(0,15);
  box.innerHTML=hit.length?("<div class='sec'>🔎 "+hit.length+" kết quả</div>"+hit.map(_card).join('')):"<div class='none'>Không tìm thấy NV.</div>";
}
"""

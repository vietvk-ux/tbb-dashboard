"""KHỐI QUẢN LÝ NHÂN VIÊN nhúng vào TRANG TRỰC TIẾP (ô bento "NV cần xử lý").
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
YEU = 40             # %GTC < 40 = ngày yếu / NV yếu (mốc nhóm yếu NHẤT, đổi 50→40 ngày 30/09)
EMBED_MAX = 25        # số NV tối đa hiển thị trong ô bento trang trực tiếp


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


def nvdat_trend(days=14, thresh=50, min_don=20):
    """Số NV ĐẠT %GTC ≥thresh theo TỪNG NGÀY chốt (NV giao ≥min_don đơn/ngày). Trả list
    cũ→mới (days ngày gần nhất); None nếu lỗi. Cho sparkline ô 'NV đạt ≥50%'."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        from collections import defaultdict
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days + 1)).isoformat()
        rows = _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&ngay=lt.%s"
                        "&select=ngay,don_giao,gtc&order=id.asc" % (since, today.isoformat()))
        if not rows:
            return None
        cnt = defaultdict(int)
        for r in rows:
            don = r.get("don_giao") or 0
            if don >= min_don:
                p = _pct(r.get("gtc") or 0, don)
                if p is not None and p >= thresh:
                    cnt[r.get("ngay")] += 1
        vals = [cnt[d] for d in sorted(cnt)][-days:]
        return vals if len(vals) >= 2 else None
    except Exception as e:
        logger.warning("nvdat_trend lỗi: %s", str(e)[:120])
        return None


def canxuly_trend(days=8):
    """Số NV cần xử lý (dai dẳng) theo TỪNG NGÀY chốt — rolling WINDOW ngày, %GTC TB<YEU
    qua ≥MIN_ACTIVE ngày hoạt động. Trả list số cũ→mới (days ngày gần nhất); None nếu lỗi.
    Dùng cho sparkline ô 'NV cần xử lý' trang trực tiếp."""
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not (url and key):
        return None
    try:
        from collections import defaultdict
        today = datetime.now(VN).date()
        since = (today - timedelta(days=days + WINDOW + 1)).isoformat()
        rows = _get_all(url, key, "bao_cao_nhan_vien?ngay=gte.%s&ngay=lt.%s"
                        "&select=ngay,driver_id,buu_cuc,don_giao,gtc&order=id.asc"
                        % (since, today.isoformat()))
        if not rows:
            return None
        per = defaultdict(dict)   # (driver,bc) -> {ngay: (don,gtc)}
        dates = set()
        for r in rows:
            k = (r.get("driver_id") or "", r.get("buu_cuc") or "")
            per[k][r.get("ngay")] = (r.get("don_giao") or 0, r.get("gtc") or 0)
            dates.add(r.get("ngay"))
        targets = sorted(dates)[-days:]
        series = []
        for D in targets:
            lo = (datetime.fromisoformat(D).date() - timedelta(days=WINDOW - 1)).isoformat()
            cnt = 0
            for dd in per.values():
                act = [(don, g) for ng, (don, g) in dd.items() if lo <= ng <= D and don >= MIN_DON_DAY]
                if len(act) < MIN_ACTIVE:
                    continue
                sd = sum(a[0] for a in act); sg = sum(a[1] for a in act)
                avg = _pct(sg, sd)
                if avg is not None and avg < YEU:
                    cnt += 1
            series.append(cnt)
        return series if len(series) >= 2 else None
    except Exception as e:
        logger.warning("canxuly_trend lỗi: %s", str(e)[:120])
        return None


# ============================================================================
#  NHÚNG vào TRANG TRỰC TIẾP (index/live) — toàn bộ nội dung trang này gói gọn
#  trong 1 ô bento. Class riêng .nvmgr/.q* để KHÔNG đụng CSS/JS trang trực tiếp;
#  tái dùng biến màu (--mut/--line/--card2/--bad…) + lớp .pill/.streak/.cel của
#  trang trực tiếp. embed(rows) → {"n", "html", "css", "js"} cho report_live.
# ============================================================================
def _embed_card(x, rank=None):
    cl = _cls(x["avg"])
    rk = ("<span class='qrk'>%d</span>" % rank) if rank else ""
    cells = "".join("<span class='cel %s'></span>"
                    % ("z" if (s["don"] < MIN_DON_DAY or s["pc"] is None) else _cls(s["pc"]))
                    for s in x["series"])
    P = ["<details class='qcard %s'><summary>" % cl]
    P.append("<div class='qhd'>%s<div class='qnm'>%s<i>%s</i></div>"
             "<span class='pill sm %s'>%s%%</span></div>"
             % (rk, _esc(x["ten"]), _esc(x["bc"]), cl,
                x["avg"] if x["avg"] is not None else "—"))
    P.append("<div class='streak'>%s</div>" % cells)
    P.append("<div class='qmt'><span class='w'>🔴 %d/%d ngày yếu</span>"
             "<span>🕘 muộn %d</span><span>📦 %s đơn</span><span class='cd'>💰 %s</span></div>"
             % (x["yeu"], x["act"], x["muon"], _n(x["don"]), _codm(x["cod"])))
    P.append("</summary><div class='qdtl'><table class='qt'><thead><tr><th class='l'>Ngày</th>"
             "<th>Đơn</th><th>%GTC</th><th>Muộn</th></tr></thead><tbody>")
    for s in reversed(x["series"]):
        pc = s["pc"]
        pcell = ("<span class='pill sm %s'>%s%%</span>" % (_cls(pc), pc)) if pc is not None else "—"
        P.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                 % (s["ng"], _n(s["don"]), pcell, "🕘" if s["muon"] else "·"))
    P.append("</tbody></table></div></details>")
    return "".join(P)


def embed(rows):
    """Trả về khối nhúng (ô tra cứu + TOÀN BỘ danh sách NV cần xử lý + hồ sơ 14 ngày)."""
    data = build(rows) if rows else []
    flagged = sorted([x for x in data if x["act"] >= MIN_ACTIVE and x["avg"] is not None and x["avg"] < YEU],
                     key=lambda x: (x["avg"], -x["yeu"]))
    idx = [{"ten": x["ten"], "bc": x["bc"], "avg": x["avg"], "yeu": x["yeu"], "act": x["act"],
            "muon": x["muon"], "don": x["don"], "cod": x["cod"], "series": x["series"]}
           for x in sorted(data, key=lambda x: (x["avg"] if x["avg"] is not None else 999))
           if x["act"] >= 3]
    H = ["<div class='nvmgr'>"]
    H.append("<input class='qsearch' id='nvmq' placeholder='🔎 Tra cứu hồ sơ nhân viên (tên / bưu cục)…' "
             "oninput='nvmSearch()' autocomplete='off' onclick='event.stopPropagation()'>")
    H.append("<div id='nvmres' class='qres'></div>")
    shown = flagged[:EMBED_MAX]
    lbl = ("📋 %d NV cần xử lý · hiển thị %d nặng nhất" % (len(flagged), len(shown))) \
        if len(flagged) > EMBED_MAX else \
        ("📋 %d NV cần xử lý · %%GTC dưới %d%% dai dẳng (≥%d/%d ngày hoạt động)"
         % (len(flagged), YEU, MIN_ACTIVE, WINDOW))
    H.append("<div class='qsec'>%s</div>" % lbl)
    if flagged:
        for i, x in enumerate(shown, 1):
            H.append(_embed_card(x, rank=i))
        if len(flagged) > EMBED_MAX:
            H.append("<div class='qmore off'>+ %d NV nữa dưới mục tiêu — tìm theo tên ở ô trên</div>" % (len(flagged) - EMBED_MAX))
    else:
        H.append("<div class='qnone'>Không có NV nào yếu dai dẳng. 👍</div>")
    H.append("<div class='qfoot'>Nhìn %d ngày · ngày hoạt động = giao ≥%d đơn · dữ liệu chốt cuối ngày (Supabase). "
             "Streak: 🟥&lt;40%% · 🟨40-70%% · 🟩≥70%% · ⬛ ít đơn.</div>" % (WINDOW, MIN_DON_DAY))
    H.append("</div>")
    js = "var NVM=%s;\n%s" % (json.dumps(idx, ensure_ascii=False), _EMBED_JS)
    return {"n": len(flagged), "html": "".join(H), "css": _EMBED_CSS, "js": js}


# ---- CSS nhúng vào TRANG TRỰC TIẾP (scope .nvmgr, dùng biến màu của trang đó) ----
_EMBED_CSS = """
.nvmgr{margin-top:2px}
.nvmgr .qsearch{width:100%;padding:11px 13px;border-radius:12px;border:1px solid var(--line);
 background:var(--bg2);color:var(--txt);font-size:14px;outline:none;margin:2px 0 10px;-webkit-appearance:none}
.nvmgr .qsearch:focus{border-color:#4a5384}
.nvmgr .qres:not(:empty){margin-bottom:12px}
.nvmgr .qsec{font-size:11px;font-weight:800;letter-spacing:.03em;text-transform:uppercase;color:var(--mut);margin:8px 2px 8px}
.nvmgr .qcard{border-radius:13px;margin:7px 0;overflow:hidden;background:var(--card2);border:1px solid var(--line)}
.nvmgr .qcard.bad{border-color:rgba(242,88,95,.42)}
.nvmgr .qcard.warn{border-color:rgba(247,185,85,.34)}
.nvmgr .qcard>summary{padding:10px 12px;cursor:pointer;list-style:none;display:flex;flex-direction:column;gap:7px}
.nvmgr .qcard>summary::-webkit-details-marker{display:none}
.nvmgr .qhd{display:flex;align-items:center;gap:9px}
.nvmgr .qrk{width:21px;height:21px;border-radius:6px;background:rgba(242,88,95,.2);color:var(--bad);
 font-weight:800;font-size:11px;display:grid;place-items:center;flex:none}
.nvmgr .qnm{flex:1;min-width:0;font-weight:700;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nvmgr .qnm i{font-style:normal;color:var(--mut);font-weight:400;font-size:11px;margin-left:7px}
.nvmgr .qmt{display:flex;flex-wrap:wrap;gap:4px 12px;color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums}
.nvmgr .qmt .w{color:var(--bad);font-weight:700}.nvmgr .qmt .cd{color:var(--warn)}
.nvmgr .qdtl{padding:0 12px 11px}
.nvmgr table.qt{width:100%;border-collapse:collapse;font-size:11.5px;font-variant-numeric:tabular-nums}
.nvmgr table.qt th,.nvmgr table.qt td{padding:5px 4px;text-align:right;border-bottom:1px solid rgba(255,255,255,.05)}
.nvmgr table.qt th{color:var(--mut);font-weight:600;font-size:9px;text-transform:uppercase}
.nvmgr table.qt th.l,.nvmgr table.qt td.l{text-align:left}
.nvmgr table.qt tbody tr:last-child td{border-bottom:none}
.nvmgr .qnone{color:var(--mut);text-align:center;padding:16px;font-size:12.5px}
.nvmgr .qmore{display:block;text-align:center;font-size:11.5px;font-weight:600;color:var(--mut);
 padding:10px;margin:8px 0 2px;border:1px dashed var(--line);border-radius:12px}
.nvmgr .qfoot{color:#6d7492;font-size:10px;line-height:1.6;margin:10px 2px 2px}
"""

# ---- JS nhúng (namespaced nvm*) — ô tra cứu bất kỳ NV trên trang trực tiếp ----
_EMBED_JS = """
function _nvmcls(p){return p==null?'na':(p<40?'bad':(p<70?'warn':'good'));}
function _nvmcodm(v){v=v||0;if(v<1e5)return'0';if(v>=1e9)return(v/1e9).toFixed(2).replace('.',',')+' tỷ';return(v/1e6).toFixed(1).replace('.',',')+'tr';}
function _nvmcard(x){
  var cl=_nvmcls(x.avg);
  var cells=x.series.map(function(s){var c=(s.don<10||s.pc==null)?'z':_nvmcls(s.pc);
    return "<span class='cel "+c+"'></span>";}).join('');
  var rows=x.series.slice().reverse().map(function(s){
    var p=s.pc==null?'—':"<span class='pill sm "+_nvmcls(s.pc)+"'>"+s.pc+"%</span>";
    return "<tr><td class='l'>"+s.ng+"</td><td>"+s.don.toLocaleString('vi')+"</td><td>"+p+"</td><td>"+(s.muon?'🕘':'·')+"</td></tr>";}).join('');
  return "<details class='qcard "+cl+"' open><summary><div class='qhd'><div class='qnm'>"+x.ten+
    "<i>"+x.bc+"</i></div><span class='pill sm "+cl+"'>"+(x.avg==null?'—':x.avg+'%')+
    "</span></div><div class='streak'>"+cells+"</div><div class='qmt'><span class='w'>🔴 "+x.yeu+"/"+x.act+
    " ngày yếu</span><span>🕘 muộn "+x.muon+"</span><span>📦 "+x.don.toLocaleString('vi')+" đơn</span><span class='cd'>💰 "+
    _nvmcodm(x.cod)+"</span></div></summary><div class='qdtl'><table class='qt'><thead><tr><th class='l'>Ngày</th><th>Đơn</th><th>%GTC</th><th>Muộn</th></tr></thead><tbody>"+
    rows+"</tbody></table></div></details>";
}
function _nvmnorm(s){return (s||'').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').replace(/đ/g,'d');}
function nvmSearch(){
  var el=document.getElementById('nvmq');if(!el)return;
  var q=_nvmnorm(el.value.trim());var box=document.getElementById('nvmres');
  if(q.length<2){box.innerHTML='';return;}
  var hit=NVM.filter(function(x){return _nvmnorm(x.ten).indexOf(q)>=0||_nvmnorm(x.bc).indexOf(q)>=0;}).slice(0,15);
  box.innerHTML=hit.length?("<div class='qsec'>🔎 "+hit.length+" kết quả</div>"+hit.map(_nvmcard).join('')):"<div class='qnone'>Không tìm thấy NV.</div>";
}
"""

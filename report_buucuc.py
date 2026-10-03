# -*- coding: utf-8 -*-
"""Trang BẢNG ĐIỀU KHIỂN BƯU CỤC (buucuc.html) — gom TẤT CẢ chỉ số của từng bưu cục
vào 1 scorecard, điều hướng AM → Bưu cục. Dùng lại rows=fetch_live + collectable (phiếu
thu treo) ĐÃ fetch ở report_live.main → 0 call API thêm. Tái dùng helper report_live.
"""
from datetime import datetime, timezone, timedelta

from am_map import AM_OF
from report_live import (_CSS, _n, _esc, _codm, _kgfmt, _pct, _cls, _bar, _tt_chip,
                         _drv_table, _late_cnt, _low_cnt, PROV_NAME)

VN = timezone(timedelta(hours=7))   # dùng stdlib (requirements KHÔNG có pytz) — khớp report_live


def _late_badge(n):
    return ("<span class='lbc' title='%d NV xuất phát sau 9h30'>🕘 %s</span>" % (n, _n(n))) if n else ""


def _low_badge(n):
    return ("<span class='lwc' title='%d NV %%GTC &lt;50%% (≥20 đơn)'>📉 %s</span>" % (n, _n(n))) if n else ""


def _tile(label, value, cls=""):
    return ("<div class='sc'><div class='scl'>%s</div>"
            "<div class='scv %s'>%s</div></div>" % (label, cls, value))


def _grp(title, tiles):
    return ("<div class='scgrp'><div class='scgt'>%s</div>"
            "<div class='scgrid'>%s</div></div>" % (title, "".join(tiles)))


def _on_road(r):
    return max(sum(d.get("ot_tot", 0) - d.get("ot_done", 0) for d in r.get("drivers", [])), 0)


def _bc_scorecard(r, collectable, dkattr="data-k"):
    """1 bưu cục = 1 scorecard đầy đủ (dạng <details> bấm mở).
    dkattr: tên attribute khoá tìm kiếm (data-k ở trang riêng, data-kb khi NHÚNG để
    không đụng ô tìm kiếm chính của trang trực tiếp)."""
    pc = _pct(r["gtc"], r["att"])
    cls = _cls(pc)
    nv = len(r.get("drivers", []))
    late = _late_cnt(r)
    low = _low_cnt(r)
    ttp = _pct(r.get("vngh_gtc", 0), r.get("vngh", 0))
    # phiếu thu treo của bưu cục
    pts = (collectable or {}).get(r["name"]) or []
    pt_nv = len(pts)
    pt_amt = sum(u.get("amount", 0) for u in pts)
    kg_dg = (r.get("weight_g", 0) or 0) / 1000.0
    kg_cg = (r.get("backlog_weight_g", 0) or 0) / 1000.0

    keys = (r["name"] + " " + " ".join(d["name"] for d in r.get("drivers", []))).lower()
    P = ["<details class='bc %s' %s=\"%s\"><summary>" % (cls, dkattr, _esc(keys))]
    P.append("<div class='bch'><span class='dot %s'></span>"
             "<span class='bcn %s'>%s</span><span class='pill %s'>%s%%</span></div>"
             % (cls, cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
    P.append(_bar(pc, cls))
    P.append("<div class='bcm'><span>📥 %s</span><span class='w'>⏳ %s</span>"
             "<span>✅ %s</span><span>👤 %s NV</span>%s%s</div>"
             % (_n(r["total"]), _n(r["backlog"]), _n(r["gtc"]), _n(nv),
                _late_badge(late), _low_badge(low)))
    P.append("</summary><div class='dtl scbody'>")

    # 📦 GIAO HÔM NAY
    P.append(_grp("📦 Giao hôm nay", [
        _tile("Đã gán", _n(r["total"])),
        _tile("Giao TC", _n(r["gtc"]), "good"),
        _tile("%GTC", ("%s%%" % pc) if pc is not None else "—", cls),
        _tile("LTC", _n(r.get("ltc", 0))),
        _tile("LTB", _n(r.get("ltb", 0)), "bad" if r.get("ltb", 0) else ""),
        _tile("Đang chạy", _n(r["ontrip"])),
        _tile("Xong chuyến", _n(r["fin"])),
        _tile("Còn phải giao", _n(_on_road(r)), "warn" if _on_road(r) else ""),
        _tile("Số kiện", _n(r.get("kien", 0))),
    ]))
    # ⏳ TỒN ĐỌNG
    P.append(_grp("⏳ Tồn đọng", [
        _tile("Chưa gán giao", _n(r["backlog"]), "warn" if r["backlog"] else ""),
        _tile("Tồn Lấy", _n(r.get("ton_lay", 0)), "warn" if r.get("ton_lay", 0) else ""),
        _tile("Tồn Trả", _n(r.get("ton_tra", 0)), "warn" if r.get("ton_tra", 0) else ""),
        _tile("🔴 Backlog 120h", _n(r.get("giao120h", 0)), "bad" if r.get("giao120h", 0) else ""),
    ]))
    # ⚖️ KHỐI LƯỢNG
    P.append(_grp("⚖️ Khối lượng (kg thực)", [
        _tile("Đã gán giao", _kgfmt(kg_dg) if kg_dg else "—"),
        _tile("Chưa gán giao", _kgfmt(kg_cg) if kg_cg else "—", "warn" if kg_cg else ""),
    ]))
    # 🛍️ TIKTOK
    if r.get("vngh", 0):
        P.append(_grp("🛍️ TikTok Shop", [
            _tile("Đơn gán", _n(r.get("vngh", 0))),
            _tile("Giao TC", _n(r.get("vngh_gtc", 0)), "good"),
            _tile("%GTC", ("%s%%" % ttp) if ttp is not None else "—", _cls(ttp)),
        ]))
    # 💰 TIỀN
    P.append(_grp("💰 Tiền", [
        _tile("COD GTB kẹt", _codm(r.get("cod_gtb", 0)), "bad" if r.get("cod_gtb", 0) else ""),
        _tile("Phiếu thu treo", ("%s NV" % _n(pt_nv)) if pt_nv else "—", "warn" if pt_nv else ""),
        _tile("Tiền treo", _codm(pt_amt) if pt_amt else "—", "warn" if pt_amt else ""),
    ]))
    # 👤 NHÂN SỰ
    P.append(_grp("👤 Nhân sự", [
        _tile("Số NV", _n(nv)),
        _tile("🕘 XP muộn &gt;9h30", _n(late), "warn" if late else ""),
        _tile("📉 NV yếu &lt;50%", _n(low), "bad" if low else ""),
    ]))

    # Bảng NV + %GTC theo xã (bấm NV mở)
    P.append("<div class='scgt' style='margin-top:10px'>📋 Chi tiết nhân viên</div>")
    P.append(_drv_table(r.get("drivers", [])))
    P.append("</div></details>")
    return "".join(P)


def _am_blocks(rows, collectable, dkattr="data-k", am_open=True):
    """Dựng các khối AM → bưu cục (scorecard). Trả chuỗi HTML. Dùng chung trang riêng +
    nhúng trên trang trực tiếp (dkattr='data-kb', am_open=False để gọn)."""
    am_rows = {}
    for r in rows:
        amn = AM_OF.get(r["name"]) or "(chưa phân AM)"
        am_rows.setdefault(amn, []).append(r)

    def am_pct(bcs):
        return _pct(sum(x["gtc"] for x in bcs), sum(x["att"] for x in bcs))

    P = []
    for amn, bcs in sorted(am_rows.items(), key=lambda kv: (am_pct(kv[1]) if any(x["att"] for x in kv[1]) else 999)):
        apc = am_pct(bcs)
        acls = _cls(apc)
        a_tot = sum(x["total"] for x in bcs)
        a_gtc = sum(x["gtc"] for x in bcs)
        a_bl = sum(x["backlog"] for x in bcs)
        a_late = sum(_late_cnt(x) for x in bcs)
        a_low = sum(_low_cnt(x) for x in bcs)
        a_nv = sum(len(x.get("drivers", [])) for x in bcs)
        P.append("<details class='bc am %s'%s><summary>" % (acls, " open" if am_open else ""))
        P.append("<div class='bch'><span class='dot %s'></span>"
                 "<span class='bcn %s'>🧑‍💼 %s</span><span class='pill %s'>%s%%</span></div>"
                 % (acls, acls, _esc(amn), acls, apc if apc is not None else "—"))
        P.append(_bar(apc, acls))
        P.append("<div class='pmeta'>🏤 %d BC·👤 %s NV·📥 %s·<span class='w'>⏳ %s</span>·✅ %s%s%s</div>"
                 % (len(bcs), _n(a_nv), _n(a_tot), _n(a_bl), _n(a_gtc),
                    ("·" + _late_badge(a_late)) if a_late else "",
                    ("·" + _low_badge(a_low)) if a_low else ""))
        P.append("</summary><div class='dtl'>")
        for r in sorted(bcs, key=lambda x: (_pct(x["gtc"], x["att"]) if x["att"] else 999)):
            P.append(_bc_scorecard(r, collectable, dkattr))
        P.append("</div></details>")
    return "".join(P)


def embed(rows, collectable=None):
    """Khối NHÚNG cho trang trực tiếp (giống report_nvxuly.embed). Trả {n, html, css}.
    html = ô tìm kiếm riêng (id qb/filtb, data-kb — không đụng tìm kiếm chính) + AM→BC."""
    n = len([r for r in rows if r.get("total") or r.get("backlog") or r.get("ontrip")])
    P = ["<div id='bcmwrap'>"]
    P.append("<div class='sbar'><input class='search' id='qb' placeholder='🔎 Tìm bưu cục / nhân viên trong vùng...' oninput='filtb()'></div>")
    P.append("<div id='emptyb' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")
    P.append(_am_blocks(rows, collectable, dkattr="data-kb", am_open=False))
    P.append("</div>")
    P.append("<script>function filtb(){var q=document.getElementById('qb').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('#bcmwrap .bc[data-kb]').forEach(function(e){var k=e.getAttribute('data-kb')||'';"
             "var s=(!q||k.indexOf(q)>=0);e.style.display=s?'':'none';if(s)n++;"
             "if(s&&q){var p=e.closest('details.am');if(p)p.open=true;e.open=true;}});"
             "document.getElementById('emptyb').style.display=(q&&!n)?'block':'none';}</script>")
    return {"n": n, "html": "".join(P), "css": _EXTRA_CSS_INNER}


def gen_html(rows, collectable=None):
    now = datetime.now(VN)
    P = []
    P.append("<!doctype html><html lang='vi'><head><meta charset='utf-8'>")
    P.append("<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>")
    P.append("<meta name='robots' content='noindex,nofollow'>")
    P.append("<meta http-equiv='refresh' content='300'>")
    P.append("<meta name='theme-color' content='#080a16'>")
    P.append("<link rel='preconnect' href='https://fonts.googleapis.com'>")
    P.append("<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>")
    P.append("<link rel='stylesheet' href='https://fonts.googleapis.com/css2?"
             "family=Sora:wght@600;700;800&family=Manrope:wght@400;500;600;700;800&display=swap'>")
    P.append("<title>Bảng điều khiển Bưu cục · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    P.append(_EXTRA_CSS)
    P.append("<div class='wrap'>")
    P.append("<header class='top'><div class='brand'><span class='live'></span>"
             "BẢNG ĐIỀU KHIỂN BƯU CỤC</div><div class='ts'>%s · %s</div></header>"
             % (now.strftime("%H:%M"), now.strftime("%d/%m")))
    P.append("<a class='backlnk' href='index.html'>← Trang trực tiếp</a>")
    P.append("<div class='sec'>🧑‍💼 Theo AM · %GTC thấp → cao · bấm mở bưu cục → mở scorecard</div>")
    P.append("<div class='sbar'><input class='search' id='q' placeholder='🔎 Tìm bưu cục / nhân viên...' oninput='filt()'></div>")
    P.append("<div id='empty' class='empty' style='display:none'>Không tìm thấy bưu cục nào.</div>")

    P.append(_am_blocks(rows, collectable, dkattr="data-k", am_open=True))

    P.append("<div class='foot'><b>📖 Bảng điều khiển bưu cục</b><br>"
             "Gom TẤT CẢ chỉ số live của từng bưu cục (giao·tồn·khối lượng·TikTok·tiền·nhân sự) vào 1 scorecard. "
             "Màu tên/%%GTC: <span style='color:var(--bad)'>đỏ &lt;60%</span>·<span style='color:var(--warn)'>vàng &lt;70%</span>·<span style='color:var(--good)'>xanh ≥70%</span>. "
             "🕘 NV xuất phát sau 9h30 · 📉 NV %GTC &lt;50% (≥20 đơn). "
             "<span style='opacity:.7'>%GTC luỹ kế trong ngày — sáng còn thấp, đúng dần về chiều/tối · nguồn nhanh.ghn.vn</span></div>")
    P.append("</div>")
    P.append("<script>function tgw(tr){tr.classList.toggle('op');"
             "var s=tr.nextElementSibling;if(s&&s.classList.contains('wsub'))s.classList.toggle('show');}</script>")
    P.append("<script>function filt(){var q=document.getElementById('q').value.toLowerCase().trim(),n=0;"
             "document.querySelectorAll('.bc[data-k]').forEach(function(e){var k=e.dataset.k||'';"
             "var s=(!q||k.indexOf(q)>=0);e.style.display=s?'':'none';if(s)n++;"
             "if(s&&q){var p=e.closest('details.am');if(p)p.open=true;e.open=true;}});"
             "document.getElementById('empty').style.display=(q&&!n)?'block':'none';}</script>")
    P.append("<button id='rf' class='fab' onclick='rf()' aria-label='Làm mới'><span class='rfi'>⟳</span></button>")
    P.append("<script>function rf(){var b=document.getElementById('rf');"
             "b.classList.add('spin');location.replace(location.pathname+'?t='+Date.now());}</script>")
    P.append("</body></html>")
    return "\n".join(P)


_EXTRA_CSS_INNER = """
.backlnk{display:inline-block;margin:0 0 10px;color:var(--mut);text-decoration:none;font-weight:700;font-size:13px}
.backlnk:hover{color:var(--txt)}
details.bc.am>summary{padding:12px 14px}
.scbody{padding-top:4px}
.scgrp{margin:10px 0 4px}
.scgt{font-size:12px;font-weight:800;color:var(--mut);letter-spacing:.02em;margin:0 0 6px;text-transform:uppercase}
.scgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(104px,1fr));gap:7px}
.sc{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.1);border-radius:11px;padding:8px 10px}
.scl{font-size:11px;color:var(--mut);font-weight:600;margin-bottom:3px;line-height:1.25}
.scv{font-size:17px;font-weight:800;color:var(--txt);font-family:Sora,sans-serif}
.scv.good{color:var(--good)}.scv.warn{color:var(--warn)}.scv.bad{color:var(--bad)}.scv.na{color:var(--mut)}
@media(max-width:430px){.scgrid{grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:6px}.scv{font-size:15px}}
"""
_EXTRA_CSS = "<style>" + _EXTRA_CSS_INNER + "</style>"

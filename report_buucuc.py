# -*- coding: utf-8 -*-
"""Trang BẢNG ĐIỀU KHIỂN BƯU CỤC (buucuc.html) — gom TẤT CẢ chỉ số của từng bưu cục
vào 1 scorecard, điều hướng AM → Bưu cục. Dùng lại rows=fetch_live + collectable (phiếu
thu treo) ĐÃ fetch ở report_live.main → 0 call API thêm. Tái dùng helper report_live.
"""
import os
import json
import logging
from datetime import datetime, timezone, timedelta

from am_map import AM_OF
from report_live import (_CSS, _n, _esc, _codm, _kgfmt, _pct, _cls, _bar, _tt_chip,
                         _drv_table, _late_cnt, _low_cnt, _spark, PROV_NAME)

VN = timezone(timedelta(hours=7))   # dùng stdlib (requirements KHÔNG có pytz) — khớp report_live
logger = logging.getLogger("buucuc")

CHOT_NAME = "buucuc_chot.json"      # file chốt (ngày) trên Pages — giữ qua các slot như eod.html


# ===== SNAPSHOT "CHỐT SỐ 23h15" — đóng băng số liệu cuối ngày =====
_ROW_KEYS = ("name", "prov", "total", "gtc", "att", "backlog", "ontrip", "fin", "ltc", "ltb",
             "vngh", "vngh_gtc", "cod_gtb", "kien", "kien_gtc", "giao120h",
             "weight_g", "backlog_weight_g", "ton_lay", "ton_tra")


def _slim_drv(d):
    st = d.get("st")
    return {"did": d.get("did"), "name": d.get("name"), "total": d.get("total", 0),
            "gtc": d.get("gtc", 0), "att": d.get("att", 0), "ltc": d.get("ltc", 0),
            "vngh": d.get("vngh", 0), "vngh_gtc": d.get("vngh_gtc", 0),
            "wards": d.get("wards", {}), "ot_tot": d.get("ot_tot", 0), "ot_done": d.get("ot_done", 0),
            "stmin": (st.hour * 60 + st.minute) if st else None}


def _slim_row(r):
    o = {k: r.get(k, 0) for k in _ROW_KEYS}
    o["drivers"] = [_slim_drv(d) for d in r.get("drivers", [])]
    return o


def _unslim_drv(d):
    m = d.get("stmin")
    d["st"] = datetime(2000, 1, 1, m // 60, m % 60) if m is not None else None
    return d


def _unslim_row(r):
    r["drivers"] = [_unslim_drv(d) for d in r.get("drivers", [])]
    return r


def save_chot(rows, collectable, outdir, ngay, label, cmin=None):
    """Ghi snapshot CHỐT ra outdir/buucuc_chot.json (sẽ deploy lên Pages).
    cmin = phút trong ngày lúc chụp (để phân biệt chốt trong cửa sổ 23h15 vs chụp giữa ngày)."""
    data = {"ngay": ngay, "label": label, "min": cmin,
            "rows": [_slim_row(r) for r in rows], "coll": collectable or {}}
    with open(os.path.join(outdir, CHOT_NAME), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info("Đã ghi chốt bưu cục %s (%d BC).", label, len(rows))


def load_chot_http(slug, timeout=15):
    """Đọc bản chốt ĐÃ DEPLOY trên Pages (giữ qua các slot). None nếu chưa có/lỗi."""
    try:
        import requests
        url = "https://vietvk-ux.github.io/tbb-dashboard/%s/%s?t=%d" % (
            slug, CHOT_NAME, int(datetime.now().timestamp()))
        r = requests.get(url, timeout=timeout)
        if not r.ok:
            return None
        d = r.json()
        d["rows"] = [_unslim_row(x) for x in d.get("rows", [])]
        return d
    except Exception:
        return None


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


_BC_TREND_CACHE = {}        # cache theo tiến trình (report chạy 1 lần/lượt) → fetch DB đúng 1 lần


def bc_pct_trend(days=14):
    """%GTC CHỐT NGÀY của TỪNG bưu cục trong `days` ngày gần nhất — nguồn **bao_cao_buu_cuc**
    (Σgtc/Σdon_giao close ngày) để KHỚP số chốt toàn dashboard (bao_cao_vung/AM/tỉnh). (Trước
    dùng bao_cao_gio EOD snapshot ~23h → lệch tới >10đ ở BC có chuyến đóng muộn.)
    Trả (dates, by_bc): dates=list ISO cũ→mới (GỒM hôm nay — điểm cuối sẽ =live override trong
    `_bc_spark`); by_bc[bc]={ngay: %GTC}. Thiếu creds/lỗi → (None, {})."""
    if "v" in _BC_TREND_CACHE:
        return _BC_TREND_CACHE["v"]
    out = (None, {})
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if url and key:
        try:
            import requests
            today = datetime.now(VN).date()
            since = (today - timedelta(days=days + 4)).isoformat()
            h = {"apikey": key, "Authorization": "Bearer " + key}
            byday, off = {}, 0                  # ngay -> {bc: %GTC}
            while True:
                r = requests.get("%s/rest/v1/bao_cao_buu_cuc?select=ngay,buu_cuc,don_giao,gtc"
                                 "&ngay=gte.%s&order=id.asc&limit=1000&offset=%d" % (url, since, off),
                                 headers=h, timeout=25)
                if not r.ok:
                    break
                c = r.json()
                for row in c:
                    dg, gt = row.get("don_giao") or 0, row.get("gtc") or 0
                    if dg:
                        byday.setdefault(row["ngay"], {})[row["buu_cuc"]] = round(gt / dg * 100, 1)
                if len(c) < 1000:
                    break
                off += 1000
            # dates = các ngày CHỐT (bao_cao_buu_cuc, tới hôm qua) + HÔM NAY (điểm cuối = live override)
            tod = today.isoformat()
            dates = [d for d in sorted(byday) if d < tod][-(days - 1):]
            dates = (dates + [tod])[-days:]
            by_bc = {}
            for ng in dates:
                for bc, pc in (byday.get(ng) or {}).items():
                    by_bc.setdefault(bc, {})[ng] = pc
            if dates:
                out = (dates, by_bc)
        except Exception as e:
            logger.warning("Lấy xu hướng %%GTC bưu cục lỗi (bỏ qua): %s", str(e)[:120])
    _BC_TREND_CACHE["v"] = out
    return out


def _bc_spark(bc_name, live_pc, trend):
    """Đường %GTC 14 ngày của 1 bưu cục. Điểm cuối (hôm nay) ghi đè bằng %GTC live để khớp
    đúng số lớn trên thẻ. '' nếu không đủ dữ liệu (<2 điểm)."""
    if not trend:
        return ""
    dates, by_bc = trend
    if not dates:
        return ""
    series = [by_bc.get(bc_name, {}).get(d) for d in dates]
    today = datetime.now(VN).date().isoformat()
    if dates[-1] == today and live_pc is not None:
        series[-1] = live_pc
    # Cắt các ngày KHUYẾT ở đầu chuỗi (BC mới có dữ liệu ~12/10) để đường trải đều hết bề ngang.
    first = next((i for i, v in enumerate(series) if v is not None), None)
    if first is None:
        return ""
    series = series[first:]
    ndays = len(series)
    if len([v for v in series if v is not None]) < 2:
        return ""
    cur = next((v for v in reversed(series) if v is not None), None)
    col = "#17c983" if (cur or 0) >= 70 else "#fbbf24" if (cur or 0) >= 50 else "#ff6274"
    sp = _spark(series, col, w=280, h=42, target=70)
    lo = min(v for v in series if v is not None)
    hi = max(v for v in series if v is not None)
    return ("<div class='scgrp bcspk'><div class='scgt'>📈 %%GTC %d ngày gần nhất "
            "<span class='spkrng'>thấp %.0f%% · cao %.0f%% · vạch mục tiêu 70%%</span></div>"
            "<div class='spkwrap'>%s</div></div>" % (ndays, lo, hi, sp))


def _bc_scorecard(r, collectable, dkattr="data-k", trend=None, pos=None):
    """1 bưu cục = 1 scorecard đầy đủ (dạng <details> bấm mở).
    dkattr: tên attribute khoá tìm kiếm (data-k ở trang riêng, data-kb khi NHÚNG để
    không đụng ô tìm kiếm chính của trang trực tiếp).
    pos: vị trí 0→1 trong AM (0=tệ nhất) → nền TỐI DẦN xuống dưới, bớt chói, số liệu rõ hơn."""
    pc = _pct(r["gtc"], r["total"])     # %GTC = GTC / đã gán (khớp toàn dashboard)
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
    _st = (" style='--pos:%.3f'" % max(0.0, min(1.0, pos))) if pos is not None else ""
    P = ["<details class='bc scfade %s' %s=\"%s\"%s><summary>" % (cls, dkattr, _esc(keys), _st)]
    P.append("<div class='bch'><span class='dot %s'></span>"
             "<span class='bcn %s'>%s</span><span class='pill %s'>%s%%</span></div>"
             % (cls, cls, _esc(r["name"]), cls, pc if pc is not None else "—"))
    P.append(_bar(pc, cls))
    P.append("<div class='bcm'><span>📥 %s</span><span class='w'>⏳ %s</span>"
             "<span>✅ %s</span><span>👤 %s NV</span>%s%s</div>"
             % (_n(r["total"]), _n(r["backlog"]), _n(r["gtc"]), _n(nv),
                _late_badge(late), _low_badge(low)))
    P.append("</summary><div class='dtl scbody'>")

    # 📈 ĐƯỜNG %GTC 14 NGÀY (chốt cuối ngày; điểm cuối = hôm nay live)
    P.append(_bc_spark(r["name"], pc, trend))

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


def _am_blocks(rows, collectable, dkattr="data-k", am_open=True, trend=None):
    """Dựng các khối AM → bưu cục (scorecard). Trả chuỗi HTML. Dùng chung trang riêng +
    nhúng trên trang trực tiếp (dkattr='data-kb', am_open=False để gọn)."""
    am_rows = {}
    for r in rows:
        amn = AM_OF.get(r["name"])
        if not amn:          # bỏ BC chưa phân AM (điểm giao phụ) theo yêu cầu
            continue
        am_rows.setdefault(amn, []).append(r)

    def am_pct(bcs):
        return _pct(sum(x["gtc"] for x in bcs), sum(x["total"] for x in bcs))

    P = []
    for amn, bcs in sorted(am_rows.items(), key=lambda kv: (am_pct(kv[1]) if any(x["total"] for x in kv[1]) else 999)):
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
        _sbcs = sorted(bcs, key=lambda x: (_pct(x["gtc"], x["total"]) if x["total"] else 999))
        _den = max(len(_sbcs) - 1, 1)
        for _i, r in enumerate(_sbcs):
            P.append(_bc_scorecard(r, collectable, dkattr, trend, pos=_i / _den))
        P.append("</div></details>")
    return "".join(P)


def embed(rows, collectable=None, label=None):
    """Khối NHÚNG cho trang trực tiếp (giống report_nvxuly.embed). Trả {n, html, css, label}.
    html = (banner chốt) + ô tìm kiếm riêng (id qb/filtb, data-kb) + AM→BC."""
    n = len([r for r in rows if AM_OF.get(r["name"])
             and (r.get("total") or r.get("backlog") or r.get("ontrip"))])
    P = ["<div id='bcmwrap'>"]
    if label:
        P.append("<div class='chotbn'>📌 Số CHỐT cuối ngày · <b>%s</b> · giữ nguyên cả ngày hôm sau</div>" % _esc(label))
    P.append(_am_blocks(rows, collectable, dkattr="data-kb", am_open=False, trend=bc_pct_trend()))
    P.append("</div>")
    return {"n": n, "html": "".join(P), "css": _EXTRA_CSS_INNER, "label": label}


def gen_html(rows, collectable=None, label=None):
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
             "family=Geist:wght@400;500;600;700;800;900&family=Geist+Mono:wght@500;600;700;800&display=swap'>")
    P.append("<title>Bảng tổng quát Bưu cục · %s</title>" % now.strftime("%H:%M"))
    P.append(_CSS)
    P.append(_EXTRA_CSS)
    P.append("<div class='wrap'>")
    P.append("<header class='top'><div class='brand'><span class='live'></span>"
             "BẢNG TỔNG QUÁT BƯU CỤC</div><div class='ts'>%s · %s</div></header>"
             % (now.strftime("%H:%M"), now.strftime("%d/%m")))
    P.append("<a class='backlnk' href='index.html'>← Trang trực tiếp</a>")
    if label:
        P.append("<div class='chotbn'>📌 Số CHỐT cuối ngày · <b>%s</b> · giữ nguyên cả ngày hôm sau</div>" % _esc(label))
    P.append("<div class='sec'>🧑‍💼 Theo AM · %GTC thấp → cao · bấm mở bưu cục → mở scorecard</div>")

    P.append(_am_blocks(rows, collectable, dkattr="data-k", am_open=True, trend=bc_pct_trend()))

    P.append("<div class='foot'><b>📖 Bảng điều khiển bưu cục</b><br>"
             "Gom TẤT CẢ chỉ số live của từng bưu cục (giao·tồn·khối lượng·TikTok·tiền·nhân sự) vào 1 scorecard. "
             "Màu tên/%%GTC: <span style='color:var(--bad)'>đỏ &lt;60%</span>·<span style='color:var(--warn)'>vàng &lt;70%</span>·<span style='color:var(--good)'>xanh ≥70%</span>. "
             "🕘 NV xuất phát sau 9h30 · 📉 NV %GTC &lt;50% (≥20 đơn). "
             "<span style='opacity:.7'>%GTC luỹ kế trong ngày — sáng còn thấp, đúng dần về chiều/tối · nguồn nhanh.ghn.vn</span></div>")
    P.append("</div>")
    P.append("<script>function tgw(tr){tr.classList.toggle('op');"
             "var s=tr.nextElementSibling;if(s&&s.classList.contains('wsub'))s.classList.toggle('show');}</script>")
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
.scv{font-size:17px;font-weight:700;color:var(--txt);font-family:'Geist Mono','Geist',monospace;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.scv.good{color:var(--good)}.scv.warn{color:var(--warn)}.scv.bad{color:var(--bad)}.scv.na{color:var(--mut)}
@media(max-width:430px){.scgrid{grid-template-columns:repeat(auto-fit,minmax(92px,1fr));gap:6px}.scv{font-size:15px}}
.chotbn{margin:2px 0 10px;padding:9px 12px;border-radius:11px;font-size:12px;font-weight:600;color:#cfe0ff;
 background:rgba(99,179,237,.14);border:1px solid rgba(99,179,237,.4)}
.chotbn b{color:#fff;font-weight:800}
.bcspk{margin:8px 0 10px}
.bcspk .scgt{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:5px}
.spkrng{font-size:10px;font-weight:600;color:var(--mut);text-transform:none;letter-spacing:0;opacity:.85}
.spkwrap{background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.1);border-radius:11px;
 padding:8px 10px 4px}
.spkwrap svg.spk{width:100%;height:42px}
/* Scorecard bưu cục: nền SÁNG GIẢM DẦN theo vị trí (--pos 0→1) + BỚT CHÓI khi mở → số liệu rõ hơn.
   (tệ nhất ở trên = sáng nổi; tốt hơn ở dưới = tối dần, dịu mắt) */
.bc.scfade{--pos:0;border-color:rgba(var(--h),calc(.30 - .15*var(--pos)));
 background:linear-gradient(0deg,rgba(5,8,15,calc(.42*var(--pos))),rgba(5,8,15,calc(.42*var(--pos)))),
  radial-gradient(120% 90% at 0% 0%,rgba(var(--h),calc(.12 - .08*var(--pos))),var(--card) 70%)}
.bc.scfade[open]{
 background:linear-gradient(0deg,rgba(5,8,15,calc(.34*var(--pos))),rgba(5,8,15,calc(.34*var(--pos)))),
  radial-gradient(120% 90% at 0% 0%,rgba(var(--h),.10),var(--card2) 78%)}
/* Tương phản số liệu: nền ô tile dịu (không chói), chữ/số sáng rõ */
.bc.scfade .sc{background:rgba(255,255,255,.045);border-color:rgba(255,255,255,.12)}
.bc.scfade .scl{color:#aab4d4}
.bc.scfade .scv{color:#fff}
.bc.scfade .scv.good{color:var(--good)}.bc.scfade .scv.warn{color:var(--warn)}.bc.scfade .scv.bad{color:var(--bad)}.bc.scfade .scv.na{color:var(--mut)}
.bc.scfade .bcm{color:#b7c0de}
.bc.scfade .scgt{color:#c4ccec}
"""
_EXTRA_CSS = "<style>" + _EXTRA_CSS_INNER + "</style>"

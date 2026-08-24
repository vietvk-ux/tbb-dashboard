"""
TRANG HIỆU SUẤT CHUYẾN ĐI NHÂN VIÊN (mobile) — gần realtime, refresh ~15'.
Dùng lại `rows` từ report_live.fetch_live (mỗi driver đã có st/en/scan/ot) → KHÔNG
fetch lại. report_live.main() gọi gen_html() và ghi docs/<slug>/chuyendi.html.

Chỉ số: giờ xuất phát (chuyến bắt đầu hôm nay) → giờ đóng chuyến muộn nhất, thời
lượng, đơn/giờ (GTC ÷ giờ làm), đơn/chuyến, % đã scan, tiến độ chuyến đang chạy.
"""
from __future__ import annotations
from datetime import datetime

from report_live import _n, _esc, VN, PROV_NAME, _CSS
from am_map import AM_OF

# Ngưỡng cảnh báo (đơn/giờ tính trên cả span mở→đóng chuyến nên trung vị vùng ~4;
# đặt ngưỡng theo phân phối thật để không gắn cờ cả vùng).
DPH_MIN = 2.5      # đơn/giờ dưới mức này = chậm (đáy phân phối)
LATE_H = 9         # xuất phát từ 9h = muộn
SCAN_MIN = 40      # % đã scan rất thấp mới cảnh báo (vùng dùng scan không đều)
MIN_ORDERS = 10    # tối thiểu đơn để vào bảng hiệu suất (tránh nhiễu mẫu nhỏ)

# CSS bổ sung (report_live._CSS thiếu các class này)
_CSS2 = """<style>
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:4px 12px 8px;margin-bottom:4px;overflow-x:auto}
.note{color:var(--mut);font-size:11.5px;margin:8px 2px;line-height:1.5}
table.drv td.rk,table.drv th.rk{color:var(--mut);width:18px;text-align:center}
table.drv th.lft{text-align:left}
table.drv td.nv{text-align:left;max-width:none;white-space:normal;line-height:1.25;font-weight:700}
td.nv .sc{color:var(--mut);font-size:10.5px;font-weight:400}
.flags{color:var(--bad);font-size:10px;font-weight:700;margin-top:2px}
table.drv td.win{font-size:11px;color:var(--mut);white-space:nowrap}
.bc summary .amn{font-weight:800;font-size:14.5px}
.bc summary .ammet{color:var(--mut);font-size:11.5px;font-weight:500}
</style>"""


def _dph_cls(v):
    if v is None:
        return "na"
    return "good" if v >= 6 else ("warn" if v >= 3 else "bad")


def _is_done_eff(m):
    """NV đã đóng hết chuyến trong ngày → đơn/giờ trọn vẹn, đủ điều kiện xếp hạng."""
    return (m["dph"] is not None and m["ot_tot"] == 0 and m["span_h"]
            and m["span_h"] >= 2 and m["total"] >= MIN_ORDERS)


def _metrics(d, bc, prov):
    """Tính chỉ số phái sinh cho 1 nhân viên."""
    st, en = d.get("st"), d.get("en")
    span_h = (en - st).total_seconds() / 3600 if (st and en and en > st) else None
    gtc = d.get("gtc", 0)
    dph = round(gtc / span_h, 1) if (span_h and span_h > 0) else None
    start_h = st.hour + st.minute / 60 if st else None
    scan_tot = d.get("scan_tot", 0)
    scan_pct = round(d.get("scan_ok", 0) * 100 / scan_tot) if scan_tot else None
    chuyen = d.get("chuyen", 0)
    dpc = round(d.get("total", 0) / chuyen) if chuyen else 0
    late = start_h is not None and start_h >= LATE_H
    return {
        "name": d.get("name"), "bc": bc, "prov": prov, "chuyen": chuyen,
        "total": d.get("total", 0), "gtc": gtc, "dpc": dpc, "dph": dph,
        "start": st.strftime("%H:%M") if st else None,
        "end": en.strftime("%H:%M") if en else None,
        "start_h": start_h, "span_h": span_h, "scan_pct": scan_pct, "late": late,
        "ot_done": d.get("ot_done", 0), "ot_tot": d.get("ot_tot", 0),
    }


def _flags(m):
    fl = []
    if m["late"]:
        fl.append("muộn")
    if m["dph"] is not None and m["dph"] < DPH_MIN:
        fl.append("chậm")
    if m["scan_pct"] is not None and m["scan_pct"] < SCAN_MIN:
        fl.append("scan")
    return fl


def _row(i, m, show_scan=True, show_flags=True):
    fl = _flags(m) if show_flags else []
    flg = ("<div class='flags'>⚠ %s</div>" % " · ".join(fl)) if fl else ""
    dph = ("<span class='pill sm %s'>%s</span>"
           % (_dph_cls(m["dph"]), str(m["dph"]).replace(".", ",") if m["dph"] is not None else "—"))
    win = ("%s→%s" % (m["start"] or "—", m["end"] or "—"))
    scan = ""
    if show_scan:
        sc = m["scan_pct"]
        scls = "na" if sc is None else ("good" if sc >= 70 else ("warn" if sc >= SCAN_MIN else "bad"))
        scan = "<td class='%s'>%s</td>" % (scls, ("%d%%" % sc) if sc is not None else "—")
    return ("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td class='win'>%s</td>%s</tr>"
            % (i, _esc(m["name"]), _esc(m["bc"]), flg, _n(m["chuyen"]), _n(m["dpc"]),
               dph, win, scan))


def gen_html(rows):
    now = datetime.now(VN)
    # Làm phẳng danh sách nhân viên + tính chỉ số
    drv = []
    for r in rows:
        bc = r["name"]; prov = r.get("prov")
        for d in r.get("drivers", []):
            drv.append(_metrics(d, bc, prov))

    # Đơn/giờ chỉ có nghĩa khi NV ĐÃ ĐÓNG HẾT chuyến trong ngày (ot_tot==0): lúc đó
    # gtc & cửa sổ giờ mới trọn vẹn. NV còn chạy → xuống mục "đang chạy". Cửa sổ ≥2h
    # để loại chuyến lẻ buổi sáng gây đơn/giờ ảo.
    eff = [m for m in drv if _is_done_eff(m)]
    timed = [m for m in drv if m["start_h"] is not None]
    # Cần chú ý = NV đã đóng chuyến nhưng đơn/giờ ĐÁY (chậm). Muộn/scan chỉ là badge.
    can_chu_y = sorted([m for m in eff if m["dph"] < DPH_MIN], key=lambda x: x["dph"])
    hi = sorted([m for m in eff if m["total"] >= 20], key=lambda x: -x["dph"])
    dang_chay = sorted([m for m in drv if m["ot_tot"] > 0], key=lambda x: -x["ot_tot"])

    # Chỉ số vùng
    chuyen_tot = sum(m["chuyen"] for m in drv)
    active = sum(1 for m in drv if m["total"] > 0 or m["chuyen"] > 0)
    sum_gtc = sum(m["gtc"] for m in eff); sum_h = sum(m["span_h"] for m in eff)
    dph_vung = round(sum_gtc / sum_h, 1) if sum_h else None
    scan_ok = sum(r_d.get("scan_ok", 0) for r in rows for r_d in r.get("drivers", []))
    scan_tot = sum(r_d.get("scan_tot", 0) for r in rows for r_d in r.get("drivers", []))
    scan_vung = round(scan_ok * 100 / scan_tot) if scan_tot else None
    avg_h = sum(m["start_h"] for m in timed) / len(timed) if timed else None
    xp_tb = ("%02d:%02d" % (int(avg_h), round((avg_h - int(avg_h)) * 60))) if avg_h is not None else "—"

    P = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>",
         "<meta name='robots' content='noindex,nofollow'>",
         "<meta http-equiv='refresh' content='300'>",
         "<meta name='theme-color' content='#0a0d18'>",
         "<title>Hiệu suất chuyến đi · TBB</title>", _CSS, _CSS2, "<div class='wrap'>"]
    P.append("<header class='top'><div class='brand'><span class='live'></span>⚡ HIỆU SUẤT CHUYẾN ĐI</div>"
             "<div class='ts'>%s · %s</div></header>" % (now.strftime("%H:%M"), now.strftime("%d/%m")))

    # Hero
    P.append("<section class='hero'>")
    P.append("<div class='hlbl'>🚚 CHUYẾN ĐI TOÀN VÙNG TÂY BẮC BỘ · HÔM NAY</div>")
    P.append("<div class='hpct'>%s<span> chuyến</span></div>" % _n(chuyen_tot))
    P.append("<div class='hsub'>%d NV đang giao · <b class='good'>%s</b> đơn/giờ TB · giờ XP TB <b>%s</b></div>"
             % (active, str(dph_vung).replace(".", ",") if dph_vung is not None else "—", xp_tb))
    P.append("</section>")

    # Dải chỉ số
    P.append("<section class='strip'>")
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>🚚 Chuyến</div></div>" % _n(chuyen_tot))
    P.append("<div class='st'><div class='sv good'>%s</div><div class='sl'>⚡ Đơn/giờ TB</div></div>"
             % (str(dph_vung).replace(".", ",") if dph_vung is not None else "—"))
    P.append("<div class='st'><div class='sv'>%s</div><div class='sl'>🕗 Giờ XP TB</div></div>" % xp_tb)
    P.append("<div class='st'><div class='sv warn'>%s</div><div class='sl'>📷 Đã scan</div></div>"
             % (("%d%%" % scan_vung) if scan_vung is not None else "—"))
    P.append("<div class='st'><div class='sv bad'>%d</div><div class='sl'>🐢 Cần chú ý</div></div>" % len(can_chu_y))
    P.append("<div class='st'><div class='sv'>%d</div><div class='sl'>🏃 Đang chạy</div></div>" % len(dang_chay))
    P.append("</section>")

    thead = ("<table class='drv'><thead><tr><th class='rk'>#</th><th class='lft'>Nhân viên · Bưu cục</th>"
             "<th>Ch</th><th>Đơn/ch</th><th>Đơn/giờ</th><th>XP→Đóng</th><th>Scan</th></tr></thead><tbody>")

    # 🔴 NV cần chú ý
    P.append("<div class='sec' style='color:var(--bad)'>🔴 NV cần chú ý — hiệu suất chuyến đi thấp</div>")
    P.append("<section class='card'>")
    P.append("<div class='note'>NV <b>đã đóng chuyến</b>, xếp theo <b>đơn/giờ thấp nhất</b> (&lt; %s). "
             "Badge ⚠ nếu kèm <b>xuất phát muộn ≥%dh</b> / <b>scan &lt;%d%%</b>.</div>"
             % (str(DPH_MIN).replace(".", ","), LATE_H, SCAN_MIN))
    if can_chu_y:
        P.append(thead)
        for i, m in enumerate(can_chu_y[:25], 1):
            P.append(_row(i, m))
        P.append("</tbody></table>")
    else:
        P.append("<div class='none'>✅ Không có NV nào dính ngưỡng cảnh báo.</div>")
    P.append("</section>")

    # 🟢 Hiệu suất cao
    P.append("<div class='sec' style='color:var(--good)'>🟢 Hiệu suất cao — đơn/giờ tốt nhất</div>")
    P.append("<section class='card'>")
    if hi:
        P.append(thead)
        for i, m in enumerate(hi[:15], 1):
            P.append(_row(i, m, show_flags=False))
        P.append("</tbody></table>")
    else:
        P.append("<div class='none'>Chưa đủ dữ liệu chuyến kết thúc.</div>")
    P.append("</section>")

    # 🧑‍💼 Theo AM
    am = {}
    for m in drv:
        a = AM_OF.get(m["bc"])
        if not a:
            continue
        x = am.setdefault(a, {"nv": 0, "gtc": 0, "h": 0.0, "late": 0, "drv": []})
        if m["total"] > 0 or m["chuyen"] > 0:
            x["nv"] += 1
        if _is_done_eff(m):
            x["gtc"] += m["gtc"]; x["h"] += m["span_h"]
        if m["late"]:
            x["late"] += 1
        x["drv"].append(m)
    if am:
        P.append("<div class='sec'>🧑‍💼 Theo AM — bấm mở xem nhân viên</div>")
        P.append("<section class='card' style='padding:2px 10px'>")
        am_list = sorted(am.items(), key=lambda kv: (kv[1]["gtc"] / kv[1]["h"]) if kv[1]["h"] else 999)
        for a, x in am_list:
            dpha = round(x["gtc"] / x["h"], 1) if x["h"] else None
            P.append("<details class='bc'><summary>"
                     "<span class='amn'>%s</span><span class='ammet'>%d NV · %s đơn/giờ · %d muộn ▾</span>"
                     "</summary><div class='dtl'>" % (
                         _esc(a), x["nv"],
                         str(dpha).replace(".", ",") if dpha is not None else "—", x["late"]))
            ds = sorted(x["drv"], key=lambda m: (0, m["dph"]) if _is_done_eff(m) else (1, 999))
            P.append("<table class='drv'><thead><tr><th>Nhân viên</th><th>Đơn</th><th>Đơn/giờ</th>"
                     "<th>XP</th><th>Scan</th></tr></thead><tbody>")
            for m in ds:
                sc = m["scan_pct"]
                if _is_done_eff(m):
                    dcell = "<span class='pill sm %s'>%s</span>" % (_dph_cls(m["dph"]), str(m["dph"]).replace(".", ","))
                elif m["ot_tot"] > 0:
                    dcell = "<span class='pill sm na'>🏃</span>"
                else:
                    dcell = "—"
                P.append("<tr><td class='nv'>%s</td><td>%s</td><td>%s</td>"
                         "<td class='%s'>%s</td><td>%s</td></tr>"
                         % (_esc(m["name"]), _n(m["total"]), dcell,
                            "bad" if m["late"] else "", m["start"] or "—",
                            ("%d%%" % sc) if sc is not None else "—"))
            P.append("</tbody></table></div></details>")
        P.append("</section>")

    # 🏃 NV đang chạy
    P.append("<div class='sec' style='color:var(--warn)'>🏃 NV còn chuyến ĐANG CHẠY (chưa đóng)</div>")
    P.append("<section class='card'>")
    if dang_chay:
        tot_done = sum(m["ot_done"] for m in dang_chay); tot_tot = sum(m["ot_tot"] for m in dang_chay)
        P.append("<div class='note'>%d người · đã giao <b>%s/%s</b> đơn · chưa tính vào đơn/giờ tới khi đóng chuyến.</div>"
                 % (len(dang_chay), _n(tot_done), _n(tot_tot)))
        P.append("<table class='drv'><thead><tr><th class='rk'>#</th><th class='lft'>Nhân viên · Bưu cục</th>"
                 "<th>Đã giao</th><th>Tổng</th><th>Tiến độ</th></tr></thead><tbody>")
        for i, m in enumerate(dang_chay[:25], 1):
            pc = round(m["ot_done"] * 100 / m["ot_tot"]) if m["ot_tot"] else 0
            pcls = "good" if pc >= 70 else ("warn" if pc >= 45 else "bad")
            P.append("<tr><td class='rk'>%d</td><td class='nv'>%s<div class='sc'>%s</div></td>"
                     "<td>%s</td><td>%s</td><td><span class='pill sm %s'>%d%%</span></td></tr>"
                     % (i, _esc(m["name"]), _esc(m["bc"]), _n(m["ot_done"]), _n(m["ot_tot"]), pcls, pc))
        P.append("</tbody></table>")
    else:
        P.append("<div class='none'>Không có chuyến đang chạy.</div>")
    P.append("</section>")

    P.append("<a class='eod' href='index.html'><span>← Về trang trực tiếp</span>"
             "<span class='arw'>%GTC hôm nay →</span></a>")
    P.append("<div class='foot'>Đơn/giờ = đơn giao thành công ÷ (giờ đóng − giờ xuất phát) · "
             "Scan = %% đơn đã quét cầm hàng · giờ XP = chuyến bắt đầu trong ngày sớm nhất<br>"
             "Số LIVE từ nhanh.ghn.vn · tự cập nhật ~15'</div>")
    P.append("</div></body></html>")
    return "\n".join(P)

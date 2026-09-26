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
LATE_MIN = int(os.environ.get("EOD_LATE_START_MIN", "570") or "570")  # muộn = xuất phát SAU 9h30
def _late(st):
    return st is not None and (st.hour * 60 + st.minute) > LATE_MIN


def _n(x): return "{:,}".format(int(x or 0)).replace(",", ".")
def _esc(s): return html.escape(str(s if s is not None else ""))
def _pct(a, b): return round(a * 100 / b) if b else None
def _cls(p): return "bad" if (p is None or p < 60) else ("warn" if p < 70 else "good")
def _codm(v):
    v = v or 0
    return "0" if v < 1e5 else ("%.1f" % (v / 1e6)).replace(".", ",")


def _load_days(n=14):
    try:
        import report_khuvuc
        return report_khuvuc._load_days(n)
    except Exception:
        return []


def _momentum(min_don=100):
    """So %GTC 2 ngày CHỐT gần nhất (bao_cao_buu_cuc) → AM/bưu cục cải thiện & tệ đi nhất.
    Trả {d_cur,d_prev,am[],bc_up[],bc_down[]} hoặc None. So 2 ngày hoàn chỉnh → sạch, không lẫn số live giữa ngày."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
           or os.environ.get("SUPABASE_ANON_KEY", "").strip())
    if not url or not key:
        return None
    try:
        import snapshot as SNAP
        days = SNAP._sb_get(url, key, "bao_cao_vung?select=ngay&order=ngay.desc&limit=2")
        if len(days) < 2:
            return None
        d_cur, d_prev = days[0]["ngay"], days[1]["ngay"]

        def fetch(d):
            rr = SNAP._sb_all(url, key,
                              "bao_cao_buu_cuc?ngay=eq.%s&select=buu_cuc,don_giao,gtc" % d)
            return {r["buu_cuc"]: r for r in rr}
        cur, prev = fetch(d_cur), fetch(d_prev)
        bc = []
        for name, c in cur.items():
            p = prev.get(name)
            if not p or (c.get("don_giao") or 0) < min_don or (p.get("don_giao") or 0) < min_don:
                continue
            pc, pp = _pct(c["gtc"], c["don_giao"]), _pct(p["gtc"], p["don_giao"])
            if pc is None or pp is None:
                continue
            bc.append((name, pp, pc, pc - pp, c["don_giao"]))

        def am_agg(dd):
            a = {}
            for name, r in dd.items():
                am = AM_OF.get(name)
                if not am:
                    continue
                x = a.setdefault(am, [0, 0]); x[0] += r.get("gtc") or 0; x[1] += r.get("don_giao") or 0
            return {k: _pct(v[0], v[1]) for k, v in a.items() if v[1]}
        amc, amp = am_agg(cur), am_agg(prev)
        am = sorted([(k, amp[k], amc[k], amc[k] - amp[k]) for k in amc if k in amp],
                    key=lambda x: x[3])   # tệ đi → cải thiện
        return {"d_cur": d_cur, "d_prev": d_prev, "am": am,
                "bc_down": sorted(bc, key=lambda x: x[3])[:8],
                "bc_up": sorted(bc, key=lambda x: -x[3])[:8]}
    except Exception:
        return None


def build_html(rows):
    now = datetime.now(VN)
    # ---- Gộp vùng ----
    def S(k): return sum(r.get(k, 0) for r in rows)
    total, gtc, backlog = S("total"), S("gtc"), S("backlog")
    ontrip, fin, ltc = S("ontrip"), S("fin"), S("ltc")
    vngh, vngh_gtc, cod_gtb = S("vngh"), S("vngh_gtc"), S("cod_gtb")
    on_road = sum(d.get("ot_tot", 0) - d.get("ot_done", 0) for r in rows for d in r.get("drivers", []))
    late_nv = sum(1 for r in rows for d in r.get("drivers", []) if _late(d.get("st")))
    reg_pct = _pct(gtc, total)
    vpct = _pct(vngh_gtc, vngh)

    # ---- AM / bưu cục ----
    rows_by_name = {r["name"]: r for r in rows}
    am_rows = {}
    am = {}
    for r in rows:
        a = AM_OF.get(r["name"])
        if not a:
            continue
        am_rows.setdefault(a, []).append(r)
        d = am.setdefault(a, {"gtc": 0, "total": 0, "cod": 0})
        d["gtc"] += r.get("gtc", 0); d["total"] += r.get("total", 0); d["cod"] += r.get("cod_gtb", 0)
    am_worst = min([(a, _pct(v["gtc"], v["total"])) for a, v in am.items() if v["total"] >= 50],
                   key=lambda x: x[1], default=None)
    bc_worst = min([(r["name"], _pct(r["gtc"], r["total"])) for r in rows if r.get("total", 0) >= 50],
                   key=lambda x: x[1], default=None)
    bc_cod = max([(r["name"], r.get("cod_gtb", 0)) for r in rows], key=lambda x: x[1], default=None)
    # NV xuất phát muộn (SAU 9h30) — danh sách cụ thể
    late_list = sorted(
        [(d["name"], r["name"], d["st"]) for r in rows for d in r.get("drivers", [])
         if _late(d.get("st"))],
        key=lambda x: -(x[2].hour * 60 + x[2].minute))

    # ---- Điểm nóng bổ sung: NV kém nhất · NV giữ COD · BC còn phải giao · BC chưa gán ----
    all_drv = [(d, r["name"]) for r in rows for d in r.get("drivers", [])]
    # xếp %GTC thấp→cao; cùng %GTC thì NV NHIỀU ĐƠN lên trước (đáng lo hơn khi 0% giữa ngày)
    _nv_qual = sorted([(d, bc) for d, bc in all_drv if d.get("total", 0) >= 30],
                      key=lambda x: (_pct(x[0].get("gtc", 0), x[0].get("total", 0)), -x[0].get("total", 0)))
    nv_worst = _nv_qual[0] if _nv_qual else None
    nv_cod_list = sorted([(d, bc) for d, bc in all_drv if d.get("cod_gtb", 0) >= 1e5],
                         key=lambda x: -x[0].get("cod_gtb", 0))
    nv_cod = nv_cod_list[0] if nv_cod_list else None
    bc_onroad_list = sorted([(r, sum(d.get("ot_tot", 0) - d.get("ot_done", 0) for d in r.get("drivers", [])))
                             for r in rows], key=lambda x: -x[1])
    bc_onroad = bc_onroad_list[0] if bc_onroad_list and bc_onroad_list[0][1] > 0 else None
    bc_backlog_list = sorted([(r["name"], r.get("backlog", 0)) for r in rows if r.get("backlog", 0) > 0],
                             key=lambda x: -x[1])
    bc_backlog = bc_backlog_list[0] if bc_backlog_list else None

    # ---- Xu hướng + điểm nóng khu vực (khuvuc_data) ----
    days = _load_days(14)
    day_series = [(d["ngay"][8:] + "/" + d["ngay"][5:7], _pct(d["gtc"], d["tot"])) for d in days]
    # Toàn bộ xã/tuyến có GTC 0% toàn vùng (hôm qua, ≥5 đơn) — xếp số đơn giảm dần
    zero_wards = []
    if days:
        zero_wards = sorted([w for w in days[-1].get("wards", []) if w[2] >= 5 and w[3] == 0],
                            key=lambda w: -w[2])   # w = [dist, ward, n, g]

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
        ("🚛", _n(on_road), "Còn phải giao", "warn"),
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

    # Điểm nóng — mỗi thẻ bấm mở ra chi tiết NV/bưu cục/tuyến cụ thể
    P.append("<div class='sec'>⚠️ Điểm nóng cần chú ý · bấm mở chi tiết</div>")
    P.append("<section class='hot'>")
    if am_worst:
        # chi tiết: bưu cục của AM đó (bấm mở tiếp ra nhân viên), xếp %GTC thấp→cao
        det = "".join(_bc_block(r) for r in sorted(am_rows.get(am_worst[0], []),
                      key=lambda x: _pct(x["gtc"], x["total"]) if x["total"] else 999))
        P.append(_hot("🧑‍💼", "AM %GTC thấp nhất", am_worst[0], "%d%%" % am_worst[1], _cls(am_worst[1]), det))
    if bc_worst:
        r = rows_by_name.get(bc_worst[0])
        det = _nv_list(r, key=lambda x: _pct(x.get("gtc", 0), x.get("total", 0)) if x.get("total") else 999) if r else ""
        P.append(_hot("🏤", "Bưu cục %GTC thấp nhất", _short(bc_worst[0]), "%d%%" % bc_worst[1], _cls(bc_worst[1]), det))
    if bc_cod:
        r = rows_by_name.get(bc_cod[0])
        det = _nv_list(r, key=lambda x: -x.get("cod_gtb", 0), only_cod=True) if r else ""
        P.append(_hot("💰", "Bưu cục COD GTB cao nhất", _short(bc_cod[0]), _codm(bc_cod[1]) + "tr", "bad", det))
    # 👤 NV %GTC thấp nhất toàn vùng (≥30 đơn) — top 15 kém nhất
    if nv_worst:
        d0, bc0 = nv_worst
        pc0 = _pct(d0.get("gtc", 0), d0.get("total", 0))
        det = ("<div class='dsub'>Top 15 NV %GTC thấp nhất toàn vùng (≥30 đơn) · "
               "giữa ngày 0% thường do chuyến CHƯA đóng — ưu tiên NV nhiều đơn mà giao được ít</div>")
        det += "".join(_nv_hotrow(d, bc, "gtc") for d, bc in _nv_qual[:15])
        P.append(_hot("👤", "NV %GTC thấp nhất (≥30 đơn)",
                      "%s · %s" % (d0.get("name", "—"), _bc_tail(bc0)),
                      "%d%%" % pc0, _cls(pc0), det))
    # 💸 NV giữ COD GTB kẹt nhiều nhất — top 15
    if nv_cod:
        d0, bc0 = nv_cod
        det = "<div class='dsub'>Top 15 NV giữ COD GTB kẹt nhiều nhất (tiền thu hộ trên đơn giao hỏng)</div>"
        det += "".join(_nv_hotrow(d, bc, "cod") for d, bc in nv_cod_list[:15])
        P.append(_hot("💸", "NV COD GTB kẹt cao nhất",
                      "%s · %s" % (d0.get("name", "—"), _bc_tail(bc0)),
                      _codm(d0.get("cod_gtb", 0)) + "tr", "bad", det))
    # 🚛 Bưu cục còn phải giao nhiều nhất (đơn đang trên đường của chuyến đang chạy) — top 10
    if bc_onroad:
        det = "<div class='dsub'>Top 10 bưu cục còn nhiều đơn trên đường nhất (chuyến đang chạy)</div>"
        det += "".join(
            "<div class='dl'><span class='dln'>%s</span><span class='dlm'>🏃 %s chuyến đang chạy</span>"
            "<span class='pill sm warn'>%s</span></div>" % (_esc(r["name"]), _n(r.get("ontrip", 0)), _n(v))
            for r, v in bc_onroad_list[:10] if v > 0)
        P.append(_hot("🚛", "Bưu cục còn phải giao nhiều nhất", _short(bc_onroad[0]["name"]),
                      _n(bc_onroad[1]) + " đơn", "warn", det))
    # ⏳ Bưu cục chưa gán (backlog) cao nhất — top 10
    if bc_backlog:
        det = "<div class='dsub'>Top 10 bưu cục tồn nhiều đơn chưa xếp chuyến · bấm bưu cục xem chi tiết xã/phường</div>"
        ward_of = {r["name"]: r.get("backlog_wards", []) for r in rows}
        _rw = []
        for nm, bl in bc_backlog_list[:10]:
            ws = ward_of.get(nm, [])
            if ws:
                wl = "".join(
                    "<div class='wr'><span class='wn'>%s</span><span class='pill sm warn'>%s</span></div>"
                    % (_esc(wn), _n(wc)) for wn, wc in ws)
                _rw.append(
                    "<details class='bcw'><summary class='dl'><span class='dln'>%s</span>"
                    "<span class='dlm'>chưa gán giao</span><span class='pill sm warn'>%s</span></summary>"
                    "<div class='wl'>%s</div></details>" % (_esc(nm), _n(bl), wl))
            else:
                _rw.append(
                    "<div class='dl'><span class='dln'>%s</span><span class='dlm'>chưa gán giao</span>"
                    "<span class='pill sm warn'>%s</span></div>" % (_esc(nm), _n(bl)))
        det += "".join(_rw)
        P.append(_hot("⏳", "Bưu cục chưa gán giao cao nhất", _short(bc_backlog[0]),
                      _n(bc_backlog[1]) + " đơn", "warn", det))
    P.append("</section>")

    # ===== 📊 BIẾN ĐỘNG %GTC vs HÔM QUA (2 ngày chốt) =====
    mom = _momentum()
    if mom and (mom["bc_down"] or mom["am"]):
        dm = lambda s: s[8:10] + "/" + s[5:7]
        P.append("<div class='sec'>📊 Biến động %%GTC · %s so %s (2 ngày chốt)</div>"
                 % (dm(mom["d_cur"]), dm(mom["d_prev"])))
        P.append("<section class='card'>")
        if mom["am"]:
            P.append("<div class='dsub'>Theo AM (▲ tăng tốt · ▼ giảm — điểm %)</div><div style='display:flex;flex-wrap:wrap;gap:5px;margin:2px 0 4px'>")
            for name, pp, pc, dv in mom["am"]:
                col = "good" if dv >= 0 else "bad"; ar = "▲" if dv >= 0 else "▼"
                P.append("<span style='padding:4px 9px;border-radius:12px;background:rgba(255,255,255,.05);font-size:12px'>"
                         "%s <b style='color:var(--%s)'>%s%d</b></span>" % (_esc(name), col, ar, abs(dv)))
            P.append("</div>")

        def _mrow(t):
            name, pp, pc, dv, don = t
            col = "good" if dv >= 0 else "bad"; ar = "▲" if dv >= 0 else "▼"
            return ("<div class='dl'><span class='dln'>%s</span>"
                    "<span class='dlm'>%d%% → %d%% · 📥%s</span>"
                    "<span class='pill sm %s'>%s%d</span></div>"
                    % (_esc(name), pp, pc, _n(don), col, ar, abs(dv)))
        if mom["bc_down"] and mom["bc_down"][0][3] < 0:
            P.append("<div class='dsub' style='color:var(--bad)'>🔻 Bưu cục tụt %GTC nhiều nhất (≥100 đơn)</div>")
            P += [_mrow(t) for t in mom["bc_down"] if t[3] < 0]
        if mom["bc_up"] and mom["bc_up"][0][3] > 0:
            P.append("<div class='dsub' style='color:var(--good)'>🔺 Bưu cục cải thiện nhất</div>")
            P += [_mrow(t) for t in mom["bc_up"] if t[3] > 0]
        P.append("</section>")

    # ===== BÁO CÁO TỔNG HỢP CHI TIẾT: AM → Bưu cục → Nhân viên =====
    P.append("<div class='sec'>📊 Tổng hợp chi tiết · AM → Bưu cục → Nhân viên · bấm mở</div>")
    P.append(_consolidated(rows))

    P.append("<div class='foot'>Tổng hợp real-time từ báo cáo trực tiếp + dữ liệu 30 ngày · "
             "COD GTB = tiền thu hộ kẹt trên đơn giao hỏng (triệu) · nguồn nhanh.ghn.vn</div>")
    P.append("</div></body></html>")
    return "\n".join(P)


def _short(bc):
    return bc  # giữ nguyên "(TỈNH) Tên" cho rõ


def _bc_tail(bc):
    """Tên bưu cục gọn (bỏ tiền tố '(TỈNH) '). Trả RAW (chưa esc)."""
    return bc.split(") ")[-1] if ") " in bc else bc


def _nv_hotrow(d, bc, mode):
    """1 dòng NV toàn vùng cho chi tiết điểm nóng: tên · bưu cục + số liệu + pill phải."""
    total = d.get("total", 0); gtc = d.get("gtc", 0)
    name = _esc(d.get("name", "—")); bct = _esc(_bc_tail(bc))
    if mode == "cod":
        meta = "%s · 📥%s ❌%s" % (bct, _n(total), _n(total - gtc))
        pill = "<span class='pill sm bad'>%str</span>" % _codm(d.get("cod_gtb", 0))
    else:
        pc = _pct(gtc, total)
        meta = "%s · 📥%s ✅%s" % (bct, _n(total), _n(gtc))
        pill = "<span class='pill sm %s'>%s</span>" % (_cls(pc), ("%d%%" % pc) if pc is not None else "—")
    return ("<div class='dl'><span class='dln'>%s</span>"
            "<span class='dlm'>%s</span>%s</div>" % (name, meta, pill))


def _bar(p):
    p = p if p is not None else 0
    cls = _cls(p)
    return "<div class='bar'><div class='fill %s' style='width:%d%%'></div></div>" % (cls, min(p, 100))


def _hot(ic, lab, name, val, cls, detail=None):
    head = ("<div class='hi'>%s</div><div class='hm'><div class='hl'>%s</div>"
            "<div class='hn'>%s</div></div><div class='hv %s'>%s</div>"
            % (ic, _esc(lab), _esc(name), cls, _esc(val)))
    if not detail:
        return "<div class='ht %s'>%s</div>" % (cls, head)
    return ("<details class='ht %s'><summary>%s<span class='hcar'>▾</span></summary>"
            "<div class='hd'>%s</div></details>" % (cls, head, detail))


def _dl_bc(r):
    """1 dòng bưu cục gọn (dùng trong chi tiết điểm nóng AM)."""
    pc = _pct(r.get("gtc", 0), r.get("total", 0))
    return ("<div class='dl'><span class='dln'>%s</span>"
            "<span class='dlm'>📥%s ✅%s 💰%str</span><span class='pill sm %s'>%s</span></div>"
            % (_esc(r["name"]), _n(r.get("total", 0)), _n(r.get("gtc", 0)),
               _codm(r.get("cod_gtb", 0)), _cls(pc), ("%d%%" % pc) if pc is not None else "—"))


def _nv_list(r, key, only_cod=False):
    """Danh sách NV card của 1 bưu cục (chi tiết điểm nóng bưu cục)."""
    drv = [d for d in r.get("drivers", []) if d.get("total") or d.get("ltc") or d.get("chuyen")]
    if only_cod:
        drv = [d for d in drv if d.get("cod_gtb", 0) >= 1e5]
    if not drv:
        return "<div class='none'>Không có dữ liệu.</div>"
    return "".join(_nv_card(d) for d in sorted(drv, key=key))


def _ward_nv(day, dist, ward):
    """NV đã giao ở xã khó nhất (từ khuvuc_data nv[bc,nv,dist,ward,n,g]) — tuyến cụ thể."""
    rowsw = [x for x in day.get("nv", []) if x[2] == dist and x[3] == ward]
    if not rowsw:
        return "<div class='none'>Không có dữ liệu nhân viên cho xã này.</div>"
    out = []
    for bc, nv, d, w, n, g in sorted(rowsw, key=lambda x: _pct(x[5], x[4]) if x[4] else 999):
        pc = _pct(g, n)
        out.append("<div class='dl'><span class='dln'>%s</span>"
                   "<span class='dlm'>%s · 📦%d ✅%d</span><span class='pill sm %s'>%s</span></div>"
                   % (_esc(nv), _esc(bc.split(") ")[-1]), n, g, _cls(pc), ("%d%%" % pc) if pc is not None else "—"))
    return "".join(out)


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
    yt = pt + (H - pt - pb) * (1 - 70 / top)
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


def _metaline(prefix, m):
    parts = []
    if prefix:
        parts.append("<span class='mi'>%s</span>" % prefix)
    parts.append("<span class='mi'>📥%s</span>" % _n(m["total"]))
    parts.append("<span class='mi w'>⏳%s</span>" % _n(m["backlog"]))
    parts.append("<span class='mi'>🏃%s</span>" % _n(m["ontrip"]))
    parts.append("<span class='mi'>🚛%s</span>" % _n(m["onroad"]))
    parts.append("<span class='mi g'>✅%s</span>" % _n(m["gtc"]))
    parts.append("<span class='mi cod'>💰%str</span>" % _codm(m["cod"]))
    parts.append("<span class='mi ltc'>🛒%s</span>" % _n(m["ltc"]))
    return "<div class='ml'>" + "".join(parts) + "</div>"


def _nv_card(d):
    total = d.get("total", 0); gtc = d.get("gtc", 0); pc = _pct(gtc, total); cls = _cls(pc)
    cod = d.get("cod_gtb", 0); ltc = d.get("ltc", 0); vngh = d.get("vngh", 0); vg = d.get("vngh_gtc", 0)
    st = d.get("st"); ot = d.get("ot_tot", 0); od = d.get("ot_done", 0)
    chips = ["📥%s" % _n(total), "<span class='g'>✅%s</span>" % _n(gtc)]
    if cod >= 1e5:
        chips.append("<span class='cod'>💰%str</span>" % _codm(cod))
    if ltc:
        chips.append("<span class='ltc'>🛒%s</span>" % _n(ltc))
    if vngh:
        chips.append("🛍️%s/%s" % (_n(vg), _n(vngh)))
    if st is not None:
        chips.append("⏰%02d:%02d" % (st.hour, st.minute))
    if ot:
        chips.append("🏃%s/%s" % (_n(od), _n(ot)))
    return ("<div class='nvc %s'><div class='nvh'><span class='nvn'>%s</span>"
            "<span class='pill sm %s'>%s</span></div><div class='chips'>%s</div></div>"
            % (cls, _esc(d.get("name", "—")), cls, ("%d%%" % pc) if pc is not None else "—",
               "".join("<span class='chip'>%s</span>" % c for c in chips)))


def _bcm(r):
    return {"total": r.get("total", 0), "backlog": r.get("backlog", 0),
            "ontrip": r.get("ontrip", 0), "gtc": r.get("gtc", 0),
            "cod": r.get("cod_gtb", 0), "ltc": r.get("ltc", 0), "kien": r.get("kien", 0),
            "onroad": sum(d.get("ot_tot", 0) - d.get("ot_done", 0) for d in r.get("drivers", []))}


def _bc_block(r):
    """1 bưu cục dạng <details> bấm mở ra thẻ nhân viên — dùng ở drill & điểm nóng AM."""
    m = _bcm(r); pc = _pct(m["gtc"], m["total"]); cls = _cls(pc)
    P = ["<details class='bc sub %s'><summary>" % cls]
    P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
             "<span class='pill %s'>%s</span></div>"
             % (cls, _esc(r["name"]), cls, ("%d%%" % pc) if pc is not None else "—"))
    P.append(_metaline("", m))
    P.append("</summary><div class='dtl'>")
    drv = [d for d in r.get("drivers", []) if d.get("total") or d.get("ltc") or d.get("chuyen")]
    if drv:
        for d in sorted(drv, key=lambda x: _pct(x.get("gtc", 0), x.get("total", 0)) if x.get("total") else 999):
            P.append(_nv_card(d))
    else:
        P.append("<div class='none'>Chưa có dữ liệu hôm nay.</div>")
    P.append("</div></details>")
    return "".join(P)


def _consolidated(rows):
    am_rows = {}
    for r in rows:
        a = AM_OF.get(r["name"])
        if a:
            am_rows.setdefault(a, []).append(r)

    def am_pct(a):
        rs = am_rows[a]; t = sum(x.get("total", 0) for x in rs); g = sum(x.get("gtc", 0) for x in rs)
        return _pct(g, t) if t else 999
    P = []
    for a in sorted(am_rows, key=am_pct):
        rs = am_rows[a]
        agg = {k: 0 for k in ("total", "backlog", "ontrip", "gtc", "cod", "ltc", "kien", "onroad")}
        for r in rs:
            for k, v in _bcm(r).items():
                agg[k] += v
        pc = _pct(agg["gtc"], agg["total"]); cls = _cls(pc)
        P.append("<details class='bc %s'><summary>" % cls)
        P.append("<div class='bch'><span class='dot %s'></span><span class='bcn'>%s</span>"
                 "<span class='pill %s'>%s</span></div>"
                 % (cls, _esc(a), cls, ("%d%%" % pc) if pc is not None else "—"))
        P.append(_metaline("🏤%d BC" % len(rs), agg))
        P.append("</summary><div class='dtl'>")
        for r in sorted(rs, key=lambda x: _pct(x.get("gtc", 0), x.get("total", 0)) if x.get("total") else 999):
            P.append(_bc_block(r))
        P.append("</div></details>")
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
details.ht{display:block;padding:0}
details.ht summary{display:flex;align-items:center;gap:11px;padding:11px 13px;cursor:pointer;list-style:none}
details.ht summary::-webkit-details-marker{display:none}
.hcar{color:var(--mut);font-size:11px;flex:none;transition:transform .15s}
details.ht[open] .hcar{transform:rotate(180deg)}
.hd{padding:0 13px 10px}
.hd .dsub{font-size:11px;color:var(--mut);padding:2px 0 4px;line-height:1.5}
.dl{display:flex;align-items:center;gap:8px;padding:7px 2px;border-top:1px solid rgba(255,255,255,.05)}
.dl:first-child{border-top:none}
.dln{font-weight:600;font-size:12.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dlm{font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46%}
details.bcw{border-top:1px solid rgba(255,255,255,.05)}
details.bcw>summary{cursor:pointer;list-style:none}
details.bcw>summary::-webkit-details-marker{display:none}
details.bcw>summary.dl{border-top:none}
details.bcw>summary .dln::before{content:'▸ ';color:var(--mut);font-weight:400}
details.bcw[open]>summary .dln::before{content:'▾ '}
.wl{padding:1px 0 7px 15px}
.wr{display:flex;align-items:center;gap:8px;padding:4px 2px;border-top:1px solid rgba(255,255,255,.04)}
.wn{flex:1;min-width:0;font-size:11.5px;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hd .nvc:first-child{margin-top:2px}
.eod{display:flex;justify-content:space-between;align-items:center;gap:10px;background:var(--card);
 border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:8px 0;text-decoration:none;color:var(--txt);font-weight:600}
.eod .arw{color:var(--mut);font-size:11.5px;font-weight:500;text-align:right}
.foot{color:#6d7492;font-size:11px;text-align:center;line-height:1.7;margin:16px 0 8px}
/* ===== Drill AM → Bưu cục → Nhân viên ===== */
details.bc{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:12px;margin:7px 0;overflow:hidden}
details.bc.good{border-left-color:var(--good)}details.bc.warn{border-left-color:var(--warn)}details.bc.bad{border-left-color:var(--bad)}
details.bc summary{padding:10px 12px;cursor:pointer;list-style:none}
details.bc summary::-webkit-details-marker{display:none}
.bch{display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%;flex:none}
.dot.good{background:var(--good)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.bcn{font-weight:700;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:800;font-size:12px;flex:none;font-variant-numeric:tabular-nums}
.pill.sm{font-size:11px;padding:1px 7px}
.pill.good{background:rgba(34,197,94,.15);color:var(--good)}
.pill.warn{background:rgba(245,158,11,.15);color:var(--warn)}
.pill.bad{background:rgba(239,68,68,.15);color:var(--bad)}
.ml{display:flex;flex-wrap:wrap;gap:3px 9px;font-size:11.5px;color:var(--mut);margin-top:6px;font-variant-numeric:tabular-nums}
.ml .mi{white-space:nowrap}
.ml .w{color:var(--warn)}.ml .g{color:var(--good)}.ml .cod{color:var(--bad);font-weight:700}.ml .ltc{color:var(--good)}
.dtl{padding:2px 8px 9px}
details.bc.sub{margin:6px 0;border-radius:10px;background:rgba(255,255,255,.025)}
.none{color:var(--mut);font-size:12px;padding:6px 4px}
.nvc{background:rgba(255,255,255,.02);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin:6px 0}
.nvh{display:flex;align-items:center;gap:8px}
.nvn{font-weight:600;font-size:13px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.chip{font-size:11px;background:rgba(255,255,255,.05);border-radius:6px;padding:2px 7px;font-variant-numeric:tabular-nums;white-space:nowrap}
.chip .g{color:var(--good)}.chip .cod{color:var(--bad);font-weight:700}.chip .ltc{color:var(--good)}
@media (max-width:430px){
  .grid{grid-template-columns:repeat(2,1fr)}
  .hpct{font-size:46px}.kv{font-size:17px}
  .eod{font-size:13.5px;padding:11px 12px}.eod .arw{font-size:11px}
  .ml{font-size:11px;gap:2px 7px}.chip{font-size:10.5px;padding:2px 6px}.bcn{font-size:13.5px}
  /* Điểm nóng mở ra: thu lề để card lồng rộng, gọn hơn trên iPhone */
  .hd{padding:0 6px 8px}
  .hd .bc.sub{margin:5px 0}
  .hd .dtl{padding:2px 6px 8px}
  .hd details.bc summary{padding:9px 9px}
  .hd .nvc{padding:7px 8px}
  .hd .chips{gap:4px}
}
body{background:radial-gradient(130% 100% at 50% -10%,rgba(59,130,246,.11),transparent 65%),#132449 !important;background-attachment:fixed}
.top{background:linear-gradient(180deg,#132449 62%,rgba(19,36,73,0)) !important}
</style></head><body>"""

"""
BẢN TIN "VIỆC CẦN LÀM HÔM NAY" — gửi GTalk mỗi sáng (07:30 VN).

Đọc số CHỐT TỐI QUA từ Supabase (KHÔNG cần token nhanh.ghn.vn — chạy được cả khi
token hết hạn), tổng hợp thành các việc cần ưu tiên xử lý trong ngày:
  🔴 Bưu cục %GTC thấp cần can thiệp
  ⏳ Đơn quá hạn >120h cần xử lý gấp
  📈 Bưu cục tồn tăng vọt so hôm trước
  💰 Nhân viên COD GTB/đơn cao nhất

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, GTALK_OA_TOKEN, GTALK_CHANNEL_ID.
Tùy chọn: DASH_URL / DASH_SLUG (link trang), MORNING_SEND=0 để chỉ in, không gửi.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from report import send_gtalk

VN = timezone(timedelta(hours=7))
logger = logging.getLogger("morning")

MIN_VOL = 50       # bưu cục phải có ≥ số đơn này mới xét %GTC thấp (tránh nhiễu mẫu nhỏ)
SURGE_MIN = 100    # tồn tăng ≥ số đơn này so hôm trước mới cảnh báo


def _get(url, key, path):
    r = requests.get("%s/rest/v1/%s" % (url.rstrip("/"), path),
                     headers={"apikey": key, "Authorization": "Bearer " + key}, timeout=60)
    r.raise_for_status()
    return r.json()


def _all(url, key, path):
    out, off = [], 0
    sep = "&" if "?" in path else "?"
    while True:
        c = _get(url, key, "%s%sorder=id.asc&limit=1000&offset=%d" % (path, sep, off))
        out += c
        if len(c) < 1000:
            return out
        off += 1000


def _n(x):
    return "{:,}".format(int(x or 0)).replace(",", ".")


def build(url, key, dash_url=""):
    vung = _get(url, key, "bao_cao_vung?order=ngay.desc&limit=2&select=*")
    if not vung:
        return None
    d0 = vung[0]
    d1 = vung[1] if len(vung) > 1 else None
    day = d0["ngay"]
    dm = day[8:10] + "/" + day[5:7]
    bcs = _all(url, key, "bao_cao_buu_cuc?ngay=eq.%s&select=buu_cuc,pct_gtc,don_giao,gtb" % day)
    nv = _all(url, key, "bao_cao_nhan_vien?ngay=eq.%s&select=ten_nv,buu_cuc,cod_gtb,gtb" % day)
    td0 = _all(url, key, "bao_cao_ton_dong?ngay=eq.%s&select=buu_cuc,order_type,total,g_gt120" % day)
    td1 = (_all(url, key, "bao_cao_ton_dong?ngay=eq.%s&select=buu_cuc,total" % d1["ngay"])
           if d1 else [])

    L = ["🔆 **VIỆC CẦN LÀM HÔM NAY · VÙNG TÂY BẮC BỘ**",
         "⏰ Sáng %s · số chốt hôm qua %s" % (datetime.now(VN).strftime("%d/%m/%Y"), dm),
         ""]

    # %GTC vùng + Δ so hôm trước
    pct = d0.get("pct_gtc")
    delta = ""
    if d1 and d1.get("pct_gtc") is not None and pct is not None:
        dd = round(pct - d1["pct_gtc"], 1)
        delta = (" ▲%.1f" % dd) if dd > 0 else ((" ▼%.1f" % abs(dd)) if dd < 0 else " ▬")
    gtb_v = (d0.get("don_giao") or 0) - (d0.get("gtc") or 0)
    L.append("🎯 %%GTC vùng hôm qua: **%s%%**%s · GTB %s đơn · COD GTB %.0f tr" %
             (pct if pct is not None else "—", delta, _n(gtb_v), (d0.get("cod_gtb") or 0) / 1e6))

    # 🔴 Bưu cục cần can thiệp (%GTC thấp, đủ sản lượng)
    worst = sorted([b for b in bcs if b.get("pct_gtc") is not None and (b.get("don_giao") or 0) >= MIN_VOL],
                   key=lambda x: x["pct_gtc"])[:5]
    if worst:
        L += ["", "🔴 **Bưu cục cần can thiệp (%%GTC thấp, ≥%d đơn):**" % MIN_VOL]
        for i, b in enumerate(worst, 1):
            L.append("%d. %s — %s%% · %s đơn (GTB %s)"
                     % (i, b["buu_cuc"], b["pct_gtc"], _n(b["don_giao"]), _n(b["gtb"])))

    # ⏳ Đơn GIAO tồn >120h — khách chờ >5 ngày, xử lý gấp (chỉ loại DELIVER)
    if td0:
        red = {}
        for r in td0:
            if r.get("order_type") != "DELIVER":
                continue
            red[r["buu_cuc"]] = red.get(r["buu_cuc"], 0) + (r.get("g_gt120") or 0)
        tot_red = sum(red.values())
        red = sorted([(k, v) for k, v in red.items() if v > 0], key=lambda x: -x[1])[:5]
        if red:
            L += ["", "⏳ **Đơn GIAO tồn >120h (khách chờ >5 ngày) — %s đơn:**" % _n(tot_red)]
            for i, (k, v) in enumerate(red, 1):
                L.append("%d. %s — %s đơn" % (i, k, _n(v)))

    # 📈 Tồn tăng vọt so hôm trước
    if td0 and td1:
        t0, t1 = {}, {}
        for r in td0:
            t0[r["buu_cuc"]] = t0.get(r["buu_cuc"], 0) + (r.get("total") or 0)
        for r in td1:
            t1[r["buu_cuc"]] = t1.get(r["buu_cuc"], 0) + (r.get("total") or 0)
        surge = [(k, t0[k] - t1.get(k, 0), t0[k]) for k in t0]
        surge = sorted([s for s in surge if s[1] >= SURGE_MIN], key=lambda x: -x[1])[:3]
        if surge:
            L += ["", "📈 **Tồn tăng vọt so hôm trước:**"]
            for i, (k, dlt, tot) in enumerate(surge, 1):
                L.append("%d. %s — +%s đơn (tổng tồn %s)" % (i, k, _n(dlt), _n(tot)))

    # 💰 Nhân viên COD GTB/đơn cao nhất
    dng = [x for x in nv if (x.get("gtb") or 0) > 0]
    dng.sort(key=lambda x: -((x.get("cod_gtb") or 0) / x["gtb"]))
    if dng:
        L += ["", "💰 **NV COD GTB/đơn cao nhất:**"]
        for i, x in enumerate(dng[:3], 1):
            per = (x.get("cod_gtb") or 0) / x["gtb"] / 1e6
            L.append("%d. %s (%s) — %s tr/đơn · %s đơn GTB"
                     % (i, x["ten_nv"], x["buu_cuc"], ("%.2f" % per).replace(".", ","), _n(x["gtb"])))

    if dash_url:
        L += ["", "📱 Chi tiết vùng: " + dash_url]
    L += ["", "_🤖 Việc cần làm sáng · Vùng TBB · %s_" % datetime.now(VN).strftime("%H:%M %d/%m")]
    return "\n".join(L)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not (url and key):
        raise SystemExit("Thiếu SUPABASE_URL/SUPABASE_SERVICE_KEY")
    dash_url = os.environ.get("DASH_URL", "").strip()
    if not dash_url:
        slug = os.environ.get("DASH_SLUG", "9c7e4b21a6f0").strip("/")
        dash_url = "https://vietvk-ux.github.io/tbb-dashboard/%s/" % slug
    text = build(url, key, dash_url)
    if not text:
        logger.warning("Chưa có dữ liệu Supabase — bỏ qua.")
        return
    print(text)
    if os.environ.get("MORNING_SEND", "1").lower() in ("1", "true", "yes"):
        oa = os.environ.get("GTALK_OA_TOKEN", "").strip()
        ch = os.environ.get("GTALK_CHANNEL_ID", "").strip()
        if oa and ch:
            send_gtalk(text, oa, ch)
            logger.info("Đã gửi bản tin sáng vào GTalk.")
        else:
            logger.warning("Thiếu GTALK_OA_TOKEN/CHANNEL_ID — chỉ in, không gửi.")


if __name__ == "__main__":
    main()

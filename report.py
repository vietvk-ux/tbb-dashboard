"""
Runner cho GitHub Actions: fetch báo cáo trip Vùng TBB từ nhanh.ghn.vn,
gửi qua GTalk (nhóm "Vùng TBB Trợ Lý AI - Chat chung").

Chạy: `python report.py` — đọc env vars NHANH_TOKEN, GTALK_OA_TOKEN, GTALK_CHANNEL_ID.
"""
from __future__ import annotations
import asyncio, logging, os, sys, time
from collections import Counter
from datetime import date, datetime, timedelta

# %GTC cuối ngày CHỈ tính chuyến kết thúc TỪ giờ này (VN) trở đi — loại chuyến đóng
# sớm buổi sáng (thường là đuôi hôm trước). Đổi bằng env EOD_TRIP_CUTOFF_HOUR.
EOD_TRIP_CUTOFF_HOUR = int(os.environ.get("EOD_TRIP_CUTOFF_HOUR", "10") or "10")


def _ended_after_cutoff(end_time_utc, cutoff_hour=EOD_TRIP_CUTOFF_HOUR):
    """True nếu chuyến kết thúc lúc >= cutoff_hour giờ VN. endTime là UTC ISO ('...Z').
    Không đọc được giờ → GIỮ (không loại nhầm)."""
    if not end_time_utc:
        return True
    try:
        s = str(end_time_utc).replace("Z", "").split(".")[0]
        vn = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=7)
        return vn.hour >= cutoff_hour
    except Exception:
        return True

import aiohttp
import requests

logger = logging.getLogger("report")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# ==== NHANH.GHN.VN CONFIG ====
BASE = "https://nhanh-api.ghn.vn/api"
TBB_PREFIXES = ("(DBI)", "(LCA)", "(LCH)", "(SLA)", "(YBA)")
DEFAULT_HUB_HEADER = "22751000"
CONCURRENCY = 3
BACKOFF_BASE_S = 2.0
BACKOFF_MULT = 1.5
MAX_RETRIES_429 = 6


class TokenExpiredError(RuntimeError): ...


async def _post(session, path, body, hub_id, token):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-WarehouseId": str(hub_id),
    }
    delay = BACKOFF_BASE_S
    last_status = None
    for attempt in range(MAX_RETRIES_429):
        try:
            async with session.post(f"{BASE}{path}", json=body, headers=headers) as r:
                if r.status == 429:
                    await asyncio.sleep(delay); delay *= BACKOFF_MULT; continue
                if r.status == 401:
                    raise TokenExpiredError("Nhanh API 401 — SESSION token hết hạn")
                # Retry cho 5xx server error và 400 (đôi khi random từ backend GHN)
                if r.status >= 500 or r.status == 400:
                    last_status = r.status
                    await asyncio.sleep(delay); delay *= BACKOFF_MULT; continue
                r.raise_for_status()
                return await r.json()
        except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as e:
            last_status = f"conn_err: {e}"
            await asyncio.sleep(delay); delay *= BACKOFF_MULT
            continue
    raise RuntimeError(f"Retry hết {MAX_RETRIES_429} lần · {path} · last={last_status}")


async def _get_hubs(session, token):
    d = await _post(session, "/hms/metadata/get-locations", {}, DEFAULT_HUB_HEADER, token)
    return [h for h in (d.get("data") or []) if any(p in (h.get("locationName") or "") for p in TBB_PREFIXES)]


async def _finished_trips(session, token, hub_id, hub_name, yyyymmdd, sem):
    async with sem:
        d = await _post(session, "/lastmile/trip/get-trip-list-by-hub", {
            "hub_id": str(hub_id), "status": "FINISHED",
            "offset": 0, "limit": 200, "page": 1, "size": 200, "reverse": 1,
        }, hub_id, token)
    return [{"tripCode": t["tripCode"], "hub_id": hub_id, "bc": hub_name,
             "driver_id": t.get("driverId") or "", "driver_name": t.get("driverName") or "—"}
            for t in (d.get("data") or [])
            if t.get("endDateIndex") == yyyymmdd and _ended_after_cutoff(t.get("endTime"))]


async def _fetch_all_items(session, token, hub_id, trip_code):
    """Lấy TẤT CẢ item của 1 chuyến — PHÂN TRANG vì API get-trip-items cap 1000 item/lần.
    Chuyến >1000 item (auto lấy nhiều, vd 1247 lấy + 141 giao) sẽ MẤT đơn DELIVER nếu chỉ lấy 1 trang."""
    out = []
    for page in range(1, 11):  # tối đa 10 trang (10k item) — đủ an toàn
        d = await _post(session, "/lastmile/trip/get-trip-items",
                        {"tripCode": trip_code, "offset": (page - 1) * 1000,
                         "limit": 1000, "page": page, "size": 1000}, hub_id, token)
        items = d.get("data") or []
        out.extend(items)
        if len(items) < 1000:
            break
    return out


async def _trip_items(session, token, hub_id, trip_code, sem):
    """Trả về danh sách item DELIVER (mức đơn) để aggregate GỘP theo mã đơn.
    1 đơn gán nhiều chuyến (giao hỏng → gán lại) chỉ tính 1 lần ở bước aggregate."""
    async with sem:
        items = await _fetch_all_items(session, token, hub_id, trip_code)
    recs = []
    for x in items:
        typ = x.get("type")
        if typ not in ("DELIVER", "PICK"):   # giữ cả LẤY (PICK) để tính LTC
            continue
        recs.append({
            "type": typ,
            "code": x.get("orderCode") or "",
            "succ": x.get("isSucceeded") is True,
            "att": x.get("isUpdated") is True,
            "cod": float(x.get("collectAmount") or 0),  # COD (dùng cho đơn GTB)
        })
    return recs


async def fetch_report(token, target_date):
    yyyymmdd = target_date.year * 10000 + target_date.month * 100 + target_date.day
    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        hubs = await _get_hubs(session, token)
        logger.info("Fetched %d hubs TBB", len(hubs))
        trip_lists = await asyncio.gather(*[
            _finished_trips(session, token, h["locationCode"], h["locationName"], yyyymmdd, sem)
            for h in hubs
        ])
        all_trips = [t for lst in trip_lists for t in lst]
        logger.info("Found %d FINISHED trips on %s", len(all_trips), yyyymmdd)

        async def per_trip(t):
            try:
                items = await _trip_items(session, token, t["hub_id"], t["tripCode"], sem)
                return {**t, "items": items}
            except TokenExpiredError:
                raise
            except Exception as e:
                logger.warning("Trip %s lỗi: %s", t["tripCode"], e)
                return {**t, "error": str(e)[:120]}

        results = await asyncio.gather(*[per_trip(t) for t in all_trips])
        return {"date": target_date, "trips": results, "hub_count": len(hubs)}


def aggregate(payload):
    ok = [t for t in payload["trips"] if "error" not in t]
    err_count = len(payload["trips"]) - len(ok)

    def pcode_of(bc):
        return bc[bc.find("(")+1:bc.find(")")] if "(" in bc else "?"

    # 1) GỘP theo MÃ ĐƠN trong từng bưu cục: 1 đơn gán nhiều chuyến chỉ tính 1 lần.
    #    Ưu tiên bản ghi: đã giao (4) > đã xử lý (2) > còn lại. GTC = giao xong ở
    #    BẤT KỲ chuyến nào; đơn còn treo credit cho chuyến thắng (đã xử lý gần nhất).
    best = {}
    for t in ok:
        pc = pcode_of(t["bc"])
        for it in t.get("items", []):
            if it.get("type") != "DELIVER":
                continue
            code = it["code"]
            if not code:
                continue
            score = (4 if it["succ"] else 0) + (2 if it["att"] else 0)
            key = (t["bc"], code)
            cur = best.get(key)
            if cur is None or score > cur["score"]:
                best[key] = {"score": score, "bc": t["bc"], "prov": pc,
                             "driver_id": t.get("driver_id", ""), "driver_name": t.get("driver_name", "—"),
                             "succ": it["succ"], "cod": it["cod"], "vngh": code.startswith("VNGH")}

    # 1b) GỘP đơn LẤY (PICK) → LTC (lấy thành công) theo (bưu cục, mã đơn)
    bestp = {}
    for t in ok:
        for it in t.get("items", []):
            if it.get("type") != "PICK" or not it["code"]:
                continue
            key = (t["bc"], it["code"])
            cur = bestp.get(key)
            if cur is None or (it["succ"] and not cur[3]):
                bestp[key] = (t["bc"], pcode_of(t["bc"]),
                              f"{t.get('driver_id','')}|{t['bc']}", it["succ"])
    ltc_bc, ltc_prov, ltc_drv = {}, {}, {}
    for bc, pc, dk, succ in bestp.values():
        if succ:
            ltc_bc[bc] = ltc_bc.get(bc, 0) + 1
            ltc_prov[pc] = ltc_prov.get(pc, 0) + 1
            ltc_drv[dk] = ltc_drv.get(dk, 0) + 1
    grand_ltc = sum(ltc_bc.values())

    # 2) Đếm số chuyến (bc/prov/driver) — chuyến có ≥1 đơn deliver
    prov_trips, bc_trips, drv_trips = {}, {}, {}
    for t in ok:
        pc = pcode_of(t["bc"])
        bc_trips[t["bc"]] = bc_trips.get(t["bc"], 0) + 1
        prov_trips[pc] = prov_trips.get(pc, 0) + 1
        if t.get("items"):
            dkey = f"{t.get('driver_id','')}|{t['bc']}"
            drv_trips[dkey] = drv_trips.get(dkey, 0) + 1

    # 3) Tổng hợp từ đơn đã gộp (unique)
    provs, bcs, drivers = {}, {}, {}
    for rec in best.values():
        pc, bc = rec["prov"], rec["bc"]
        pr = provs.setdefault(pc, {"prov": pc, "total": 0, "success": 0, "bcs": set()})
        pr["total"] += 1; pr["success"] += 1 if rec["succ"] else 0; pr["bcs"].add(bc)
        br = bcs.setdefault(bc, {"bc": bc, "prov": pc, "total": 0, "success": 0})
        br["total"] += 1; br["success"] += 1 if rec["succ"] else 0
        dkey = f"{rec['driver_id']}|{bc}"
        dr = drivers.setdefault(dkey, {"driver_id": rec["driver_id"], "driver_name": rec["driver_name"],
                                       "bc": bc, "prov": pc, "total": 0, "success": 0, "gtb_cod": 0.0})
        dr["total"] += 1
        if rec["succ"]:
            dr["success"] += 1
        else:
            dr["gtb_cod"] += rec["cod"]

    def gtc(v): return round(v["success"]/v["total"]*100, 1) if v["total"] else None
    prov_list = sorted([{"prov": v["prov"], "bc_count": len(v["bcs"]), "trips": prov_trips.get(v["prov"], 0),
                          "total": v["total"], "success": v["success"], "gtc": gtc(v),
                          "ltc": ltc_prov.get(v["prov"], 0)}
                         for v in provs.values()], key=lambda x: -x["total"])
    bc_list = sorted([{**v, "trips": bc_trips.get(v["bc"], 0), "gtc": gtc(v),
                       "ltc": ltc_bc.get(v["bc"], 0)} for v in bcs.values()],
                     key=lambda x: -x["total"])
    driver_list = sorted([{**v, "trips": drv_trips.get(f"{v['driver_id']}|{v['bc']}", 0), "gtc": gtc(v),
                           "ltc": ltc_drv.get(f"{v['driver_id']}|{v['bc']}", 0)}
                          for v in drivers.values() if v["total"] > 0],
                         key=lambda x: (x["gtc"] if x["gtc"] is not None else 999))
    # PHÂN BIỆT TRÙNG TÊN trong cùng bưu cục: thêm đuôi #id (2 NV khác id cùng tên)
    _namebc = Counter((d["driver_name"], d["bc"]) for d in driver_list)
    for d in driver_list:
        if _namebc[(d["driver_name"], d["bc"])] > 1 and d.get("driver_id"):
            d["driver_name"] = "%s #%s" % (d["driver_name"], str(d["driver_id"])[-6:])
    grand_total = sum(p["total"] for p in prov_list)
    grand_success = sum(p["success"] for p in prov_list)
    grand_trips = sum(prov_trips.values())
    grand_gtc = round(grand_success/grand_total*100, 1) if grand_total else None
    # VNGH aggregate (đã gộp mã đơn)
    vngh_total = sum(1 for rec in best.values() if rec["vngh"])
    vngh_success = sum(1 for rec in best.values() if rec["vngh"] and rec["succ"])
    vngh_gtc = round(vngh_success/vngh_total*100, 1) if vngh_total else None
    return {"date": payload["date"], "hub_count": payload["hub_count"], "errors": err_count,
            "grand": {"trips": grand_trips, "total": grand_total, "success": grand_success, "gtc": grand_gtc,
                      "ltc": grand_ltc,
                      "vngh_total": vngh_total, "vngh_success": vngh_success, "vngh_gtc": vngh_gtc},
            "provinces": prov_list, "bcs": bc_list, "drivers": driver_list}


def dedup_orders(payload):
    """Trả về danh sách ĐƠN đã GỘP MÃ (unique theo (bưu cục, mã đơn)) để lưu chi
    tiết vào database. Cùng logic ưu tiên như aggregate(): đã giao(4) > đã xử lý(2)
    > còn lại; đơn gán nhiều chuyến chỉ giữ 1 bản (chuyến thắng)."""
    ok = [t for t in payload["trips"] if "error" not in t]

    def pcode_of(bc):
        return bc[bc.find("(")+1:bc.find(")")] if "(" in bc else "?"

    best = {}
    for t in ok:
        pc = pcode_of(t["bc"])
        for it in t.get("items", []):
            if it.get("type") != "DELIVER":
                continue
            code = it["code"]
            if not code:
                continue
            score = (4 if it["succ"] else 0) + (2 if it["att"] else 0)
            key = (t["bc"], code)
            cur = best.get(key)
            if cur is None or score > cur["score"]:
                best[key] = {"score": score, "bc": t["bc"], "prov": pc, "ma_don": code,
                             "ma_chuyen": t.get("tripCode", ""),
                             "driver_id": t.get("driver_id", ""), "driver_name": t.get("driver_name", "—"),
                             "succ": it["succ"], "att": it["att"], "cod": it["cod"],
                             "vngh": code.startswith("VNGH")}
    return list(best.values())


def format_report(agg, include_cod=False):
    d = agg["date"].strftime("%d/%m/%Y")
    g = agg["grand"]
    lines = [
        f"📦 **BÁO CÁO CHUYẾN ĐI KẾT THÚC · VÙNG TÂY BẮC BỘ · {d}**",
        "",
    ]
    if g["gtc"] is not None:
        lines.append(f"🎯 Tổng vùng: **{g['trips']}** chuyến · **{g['total']:,}** đơn cần giao · **{g['success']:,}** GTC · %GTC **{g['gtc']}%**")
    else:
        lines.append(f"🎯 Tổng vùng: **{g['trips']}** chuyến · **{g['total']:,}** đơn cần giao")
    lines.append("")
    lines.append("**Theo tỉnh:**")
    for p in agg["provinces"]:
        icon = "🔴" if p["gtc"] is not None and p["gtc"] < 60 else ("🟠" if p["gtc"] is not None and p["gtc"] < 80 else "🟢")
        if p["gtc"] is not None:
            lines.append(f"{icon} **{p['prov']}** · {p['bc_count']} BC · {p['trips']} chuyến · {p['total']:,} đơn · GTC {p['success']:,} · **{p['gtc']}%**")
        else:
            lines.append(f"⚪️ **{p['prov']}** · {p['bc_count']} BC · {p['trips']} chuyến · {p['total']:,} đơn")
    lines.append("")
    # Section 1: TOÀN BỘ BC dưới 50% (không cap — theo yêu cầu user)
    critical = sorted([b for b in agg["bcs"] if b["gtc"] is not None and b["gtc"] < 50],
                       key=lambda x: x["gtc"])
    if critical:
        lines.append(f"🔴 **BC %GTC DƯỚI 50% — TOÀN BỘ ({len(critical)} BC)**")
        for i, b in enumerate(critical, 1):
            lines.append(f"{i}. {b['bc']} — **{b['gtc']}%** · {b['total']:,} đơn (GTB {b['total']-b['success']:,})")
        lines.append("")

    if not critical:
        lines.append("✅ Toàn bộ BC đạt %GTC ≥ 50%.")
        lines.append("")

    # ============ CHI TIẾT THEO NHÂN VIÊN ============
    MIN_ORDERS = 20
    drivers = [d for d in agg["drivers"] if d["total"] >= MIN_ORDERS]

    # Section 1: TOP 15 NV %GTC < 50% (giảm từ 30 để giữ 1 tin), sort theo đơn GTB
    critical_nv = sorted([d for d in drivers if d["gtc"] is not None and d["gtc"] < 50],
                         key=lambda x: -(x["total"] - x["success"]))
    if critical_nv:
        cap = min(15, len(critical_nv))
        lines.append(f"👤🔴 **TOP {cap} NV %GTC DƯỚI 50%** _(≥{MIN_ORDERS} đơn, tổng {len(critical_nv)} NV, sort đơn GTB)_")
        for i, d in enumerate(critical_nv[:cap], 1):
            lines.append(f"{i}. {d['driver_name']} · {d['bc']} — **{d['gtc']}%** · {d['total']:,} đơn (GTB {d['total']-d['success']:,})")
        lines.append("")

    # Section 2: Top 5 NV %GTC cao (giảm từ 10)
    good_nv = sorted([d for d in drivers if d["gtc"] is not None and d["gtc"] >= 80],
                     key=lambda x: (-x["gtc"], -x["total"]))
    if good_nv:
        lines.append(f"👤🟢 **TOP 5 NV %GTC CAO NHẤT** _(≥80%, ≥{MIN_ORDERS} đơn)_")
        for i, d in enumerate(good_nv[:5], 1):
            lines.append(f"{i}. {d['driver_name']} · {d['bc']} — **{d['gtc']}%** · {d['total']:,} đơn")
        lines.append("")

    # Section 3: Tổng COD đơn GTB — CHỈ xuất hiện khi include_cod=True (báo cáo 22h)
    if include_cod:
        top_cod = sorted([d for d in agg["drivers"] if d.get("gtb_cod", 0) > 0],
                         key=lambda x: -x["gtb_cod"])[:10]
        if top_cod:
            total_cod = sum(d.get("gtb_cod", 0) for d in agg["drivers"])
            lines.append(f"💰 **TỔNG COD ĐƠN GTB TOÀN VÙNG: {total_cod/1_000_000:.1f} triệu ₫**")
            lines.append(f"Top 10 NV COD GTB cao nhất:")
            for i, d in enumerate(top_cod, 1):
                gtb = d['total'] - d['success']
                lines.append(f"{i}. {d['driver_name']} · {d['bc']} · GTB {gtb} đơn · **{d['gtb_cod']/1_000_000:.1f} triệu ₫**")
            lines.append("")

    if agg["errors"]:
        lines.append(f"_⚠️ {agg['errors']} chuyến không lấy được chi tiết (rate limit hoặc API lỗi)_")
    return "\n".join(lines)


# ==== GTALK SEND ====
def send_gtalk(text, oa_token, channel_id):
    """Gửi text vào GTalk. Chia nhỏ nếu > 4200 ký tự.
    Fan-out sang GTALK_CHANNEL_ID_2 nếu env này set (channel phụ, silent fail)."""
    MAX = 4200
    parts = []
    while len(text) > MAX:
        cut = text.rfind("\n", 0, MAX)
        if cut < 0: cut = MAX
        parts.append(text[:cut]); text = text[cut:]
    parts.append(text)

    channels = [channel_id]
    ch2 = os.environ.get("GTALK_CHANNEL_ID_2", "").strip()
    if ch2 and ch2 != channel_id:
        channels.append(ch2)

    for idx, ch in enumerate(channels):
        is_primary = (idx == 0)
        try:
            for i, p in enumerate(parts):
                body = {
                    "oaToken": oa_token,
                    "channelId": ch,
                    "clientMsgId": str(int(time.time() * 1000) + i),
                    "content": {
                        "text": p + (f"\n_(phần {i+1}/{len(parts)})_" if len(parts) > 1 else ""),
                        "parseMode": "MARKDOWN",
                    },
                }
                r = requests.post("https://mbff.ghn.vn/api/gtalk/send-message", json=body, timeout=20)
                r.raise_for_status()
                d = r.json()
                if d.get("errorCode") != "success":
                    raise RuntimeError(f"GTalk API lỗi: {d}")
                time.sleep(0.3)
        except Exception as e:
            if is_primary:
                raise
            print(f"[WARN] Gửi channel phụ {ch} fail (bỏ qua): {str(e)[:150]}")
            break


def main():
    nhanh_token = os.environ.get("NHANH_TOKEN", "").strip()
    oa_token = os.environ.get("GTALK_OA_TOKEN", "").strip()
    channel_id = os.environ.get("GTALK_CHANNEL_ID", "").strip()
    if not (nhanh_token and oa_token and channel_id):
        raise SystemExit("Thiếu env vars: NHANH_TOKEN / GTALK_OA_TOKEN / GTALK_CHANNEL_ID")

    target_date = date.today()
    logger.info("Bắt đầu fetch báo cáo ngày %s", target_date)
    try:
        payload = asyncio.run(fetch_report(nhanh_token, target_date))
        agg = aggregate(payload)
        include_cod = os.environ.get("INCLUDE_COD_GTB", "").lower() in ("1", "true", "yes")
        msg = format_report(agg, include_cod=include_cod)
        logger.info("Fetch xong · %d chuyến · GTC %s%%", agg["grand"]["trips"], agg["grand"]["gtc"])
        send_gtalk(msg, oa_token, channel_id)
        logger.info("Đã gửi báo cáo vào GTalk channel %s", channel_id)
    except TokenExpiredError as e:
        logger.error("Token hết hạn: %s", e)
        alert = ("⚠️ *Báo cáo trip 23h thất bại* — SESSION token nhanh.ghn.vn hết hạn.\n\n"
                 "*Cách fix:*\n"
                 "1. Mở https://nhanh.ghn.vn/lastmile/trip-list?status=ON_TRIP → đăng nhập\n"
                 "2. DevTools (F12) → Console → gõ: `localStorage.getItem('SESSION')`\n"
                 "3. Copy chuỗi `eyJ...`\n"
                 "4. Vào repo GitHub → Settings → Secrets → sửa `NHANH_TOKEN` → paste giá trị mới")
        try: send_gtalk(alert, oa_token, channel_id)
        except Exception: logger.exception("Không gửi được cảnh báo token expired")
        sys.exit(1)
    except Exception as e:
        logger.exception("Lỗi:")
        try: send_gtalk(f"❌ Báo cáo trip 23h lỗi: {e}", oa_token, channel_id)
        except Exception: pass
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Runner cho GitHub Actions: fetch báo cáo trip Vùng TBB từ nhanh.ghn.vn,
gửi qua GTalk (nhóm "Vùng TBB Trợ Lý AI - Chat chung").

Chạy: `python report.py` — đọc env vars NHANH_TOKEN, GTALK_OA_TOKEN, GTALK_CHANNEL_ID.
"""
from __future__ import annotations
import asyncio, logging, os, sys, time
from datetime import date

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
            for t in (d.get("data") or []) if t.get("endDateIndex") == yyyymmdd]


async def _trip_success(session, token, hub_id, trip_code, sem):
    async with sem:
        d = await _post(session, "/lastmile/trip/get-trip-items", {"tripCode": trip_code}, hub_id, token)
    items = [x for x in (d.get("data") or []) if x.get("type") == "DELIVER"]
    total = len(items)
    success = sum(1 for x in items if x.get("isSucceeded") is True)
    # COD trên đơn GTB (giao thất bại) = tiền kẹt, cần theo dõi
    gtb_cod = sum(float(x.get("collectAmount") or 0) for x in items if x.get("isSucceeded") is not True)
    # VNGH (TikTok Shop) — track riêng để tính %GTC riêng
    vngh_items = [x for x in items if (x.get("orderCode") or "").startswith("VNGH")]
    vngh_total = len(vngh_items)
    vngh_success = sum(1 for x in vngh_items if x.get("isSucceeded") is True)
    return total, success, gtb_cod, vngh_total, vngh_success


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
                total, success, gtb_cod, vngh_t, vngh_s = await _trip_success(session, token, t["hub_id"], t["tripCode"], sem)
                return {**t, "deliver_total": total, "deliver_success": success, "gtb_cod": gtb_cod,
                        "vngh_total": vngh_t, "vngh_success": vngh_s}
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
    provs, bcs, drivers = {}, {}, {}
    for t in ok:
        pcode = t["bc"][t["bc"].find("(")+1:t["bc"].find(")")] if "(" in t["bc"] else "?"
        pr = provs.setdefault(pcode, {"prov": pcode, "trips": 0, "total": 0, "success": 0, "bcs": set()})
        pr["trips"] += 1; pr["total"] += t["deliver_total"]; pr["success"] += t["deliver_success"]
        pr["bcs"].add(t["bc"])
        br = bcs.setdefault(t["bc"], {"bc": t["bc"], "trips": 0, "total": 0, "success": 0})
        br["trips"] += 1; br["total"] += t["deliver_total"]; br["success"] += t["deliver_success"]
        # Driver — key = driver_id + bc. LOẠI chuyến 0 đơn deliver (chuyến khống)
        # để không nhiễu aggregate NV.
        if t["deliver_total"] == 0:
            continue
        dkey = f"{t.get('driver_id','')}|{t['bc']}"
        dr = drivers.setdefault(dkey, {"driver_id": t.get("driver_id",""), "driver_name": t.get("driver_name","—"),
                                        "bc": t["bc"], "prov": pcode,
                                        "trips": 0, "total": 0, "success": 0, "gtb_cod": 0.0})
        dr["trips"] += 1; dr["total"] += t["deliver_total"]; dr["success"] += t["deliver_success"]
        dr["gtb_cod"] += t.get("gtb_cod", 0)
    def gtc(v): return round(v["success"]/v["total"]*100, 1) if v["total"] else None
    prov_list = sorted([{"prov": v["prov"], "bc_count": len(v["bcs"]), "trips": v["trips"],
                          "total": v["total"], "success": v["success"], "gtc": gtc(v)}
                         for v in provs.values()], key=lambda x: -x["total"])
    bc_list = sorted([{**v, "gtc": gtc(v)} for v in bcs.values()], key=lambda x: -x["total"])
    driver_list = sorted([{**v, "gtc": gtc(v)} for v in drivers.values() if v["total"] > 0],
                         key=lambda x: (x["gtc"] if x["gtc"] is not None else 999))
    grand_total = sum(p["total"] for p in prov_list)
    grand_success = sum(p["success"] for p in prov_list)
    grand_trips = sum(p["trips"] for p in prov_list)
    grand_gtc = round(grand_success/grand_total*100, 1) if grand_total else None
    # VNGH aggregate
    vngh_total = sum(t.get("vngh_total", 0) for t in ok)
    vngh_success = sum(t.get("vngh_success", 0) for t in ok)
    vngh_gtc = round(vngh_success/vngh_total*100, 1) if vngh_total else None
    return {"date": payload["date"], "hub_count": payload["hub_count"], "errors": err_count,
            "grand": {"trips": grand_trips, "total": grand_total, "success": grand_success, "gtc": grand_gtc,
                      "vngh_total": vngh_total, "vngh_success": vngh_success, "vngh_gtc": vngh_gtc},
            "provinces": prov_list, "bcs": bc_list, "drivers": driver_list}


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
    """Gửi text vào GTalk. Chia nhỏ nếu > 4200 ký tự."""
    MAX = 4200
    parts = []
    while len(text) > MAX:
        cut = text.rfind("\n", 0, MAX)
        if cut < 0: cut = MAX
        parts.append(text[:cut]); text = text[cut:]
    parts.append(text)
    for i, p in enumerate(parts):
        body = {
            "oaToken": oa_token,
            "channelId": channel_id,
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

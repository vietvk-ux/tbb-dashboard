# HỆ THỐNG BÁO CÁO VẬN HÀNH VÙNG TÂY BẮC BỘ (TBB) — GHN

Tài liệu tổng hợp để **tiếp tục làm việc ở phiên sau / trên máy khác**. Repo: `vietvk-ux/tbb-dashboard` (public). Chủ: Vũ Khắc Việt (vietvk@ghn.vn) — GĐV Vùng TBB.
Cập nhật gần nhất: 25/08/2026.

> Nguyên tắc bảo mật: KHÔNG in/echo/commit giá trị `NHANH_TOKEN`, `SUPABASE_SERVICE_KEY`, `GTALK_OA_TOKEN`, PAT. Đặt qua `gh secret set` / GitHub Actions secrets. Dữ liệu số KHÔNG lưu trong repo — chỉ deploy lên GitHub Pages + Supabase.

---

## 1. TỔNG QUAN

- **Vùng TBB = 5 tỉnh:** Lào Cai (LCA), Yên Bái (YBA), Sơn La (SLA), Điện Biên (DBI), Lai Châu (LCH). ~62 bưu cục.
- Prefix mã bưu cục: `(DBI) `, `(LCA) `, `(LCH) `, `(SLA) `, `(YBA) `.
- Hệ thống gồm: (a) **trang web dashboard** (GitHub Pages, mobile) tự cập nhật; (b) **các bản tin GTalk** tự động; (c) **kho số liệu Supabase** để phân tích lịch sử/xu hướng.

### URL trang web (slug bí mật)
Gốc: `https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/`
**Trang chính + 7 trang phụ:**
- `index.html` / `live.html` — TRANG CHÍNH, GẦN REALTIME (mỗi ~15'). Có 3 chỉ số TikTok (VNGH) toàn vùng ở dải chỉ số.
- `eod.html` — CUỐI NGÀY (chốt ~23:30). Gồm mục "⏰ Kỷ luật ra hàng".
- `backlog.html` — TỒN ĐỌNG (Lấy·Giao·Trả + Luân chuyển + đơn đỏ quá hạn).
- `trend.html` — XU HƯỚNG (đọc từ Supabase).
- `nhanvien.html` — NĂNG SUẤT NV (GTC/ngày làm · COD GTB/đơn · Năng suất Nay−TB).
- `khochuyentiep.html` — kho chuyển tiếp (tồn LC theo mốc giờ).
- `chuyendi.html` — HIỆU SUẤT CHUYẾN ĐI (đơn/giờ · giờ ra hàng · scan · đang chạy). *(Trang 7, thêm 24/08)*
- `xephang.html` — XẾP HẠNG TỔNG HỢP (scorecard AM→BC→NV, điểm 0-100). *(Trang 8, thêm 24/08)*
- ~~`vngh.html`~~ — ĐÃ XOÁ 24/08 (giữ chỉ số TikTok ở trang chính).

---

## 2. NGUỒN DỮ LIỆU — nhanh.ghn.vn (API nội bộ)

- Base: `https://nhanh-api.ghn.vn/api`. Header: `Authorization: Bearer <SESSION JWT>`, `X-WarehouseId: <hub_id>`.
- **Lấy token:** đăng nhập nhanh.ghn.vn → F12 Console → `localStorage.getItem('SESSION')`. TTL ~25–30 ngày. Hết hạn → API trả 400/401. Cập nhật secret `NHANH_TOKEN` (repo bao-cao-trip-tbb & tbb-dashboard).
- Endpoint chính:
  - `/hms/metadata/get-locations` — danh sách hub (lọc theo prefix TBB).
  - `/lastmile/trip/get-trip-list-by-hub` — chuyến theo hub, `status` = `ON_TRIP`/`FINISHED`. Trường dùng: `driverId/driverName`, `startTime/endTime` (UTC), `startDateIndex/endDateIndex` (YYYYMMDD giờ VN), `pickCount/deliverCount/returnCount`, `tripCode`.
  - `/lastmile/trip/get-trip-items` — item của 1 chuyến (PHÂN TRANG 1000/lần). Trường: `type` (DELIVER/PICK/RETURN), `orderCode`, `isSucceeded`, `isUpdated`, `collectAmount` (COD), `failCode/failNote`, `isScanned`, `collectCodFailedAmount`, `receiverContact` (lat/lng, districtName/wardName)…
  - Tồn đọng: `/core/oss/v1/report/...` (get-general-info, get-backlog-transport-info) + `count-orders-to-assign`.

### Quy tắc tính (QUAN TRỌNG — dùng nhất quán mọi báo cáo)
- **Gộp mã đơn:** 1 đơn gán nhiều chuyến chỉ tính 1 lần theo `(bưu cục, orderCode)`. Ưu tiên: đã giao(4) > đã xử lý(2) > còn lại.
- **%GTC** = đơn giao thành công / tổng đơn DELIVER (đã gộp). **LTC** = đơn PICK thành công (đã gộp).
- **Lọc chuyến ≥10h (CHỈ báo cáo CUỐI NGÀY):** `report._finished_trips` giữ MỌI chuyến FINISHED trong ngày, gắn cờ `after_cutoff` (kết thúc ≥10:00 VN, env `EOD_TRIP_CUTOFF_HOUR=10`). CHỈ chuyến `after_cutoff` mới bóc item & tính %GTC (loại "đuôi hôm trước" đóng sớm). Trang trực tiếp KHÔNG lọc.
- **Trùng tên NV:** gộp theo `driverId`; nếu 2 NV cùng tên trong 1 bưu cục → thêm đuôi `#<6 ký tự cuối id>`.
- **COD GTB** = Σ `collectAmount` của đơn DELIVER thất bại theo NV (tiền thu hộ kẹt).
- **Đơn TikTok** = `orderCode` bắt đầu `VNGH`.

### Kỷ luật ra hàng (giờ xuất phát) — thêm 22/08/2026
- Per-NV trong `report.aggregate`: `start`/`end` (HH:MM), `start_h` (giờ thập phân), `span_min` (thời lượng phút), `late` (bool).
- **Giờ xuất phát** chỉ lấy từ chuyến `startDateIndex == hôm nay` (loại chuyến qua đêm → tránh span ảo ~27h). **Kết thúc** = muộn nhất trong ngày.
- Ngưỡng muộn: env `EOD_LATE_START_HOUR` (mặc định **9h** VN).
- Hiển thị: mục "⏰ Kỷ luật ra hàng" trên `eod.html` (giờ XP TB vùng, số NV muộn, bảng NV muộn xếp muộn nhất trước).
- Không tốn thêm call API (chuyến <10h không bóc item, chỉ đọc giờ).

### Trang 7 — HIỆU SUẤT CHUYẾN ĐI (`chuyendi.html`, thêm 24/08) — LIVE ~15'
- `report_chuyendi.py::gen_html(rows)`, dùng lại `report_live.fetch_live` (KHÔNG tốn call API). `fetch_live` thu thêm mỗi NV: `st`/`en` (giờ xuất phát chuyến bắt đầu HÔM NAY / giờ đóng muộn nhất, qua `_vn_time`), `scan_ok`/`scan_tot` (`isScanned`), `ot_done`/`ot_tot` (tiến độ chuyến ĐANG CHẠY).
- **Đơn/giờ = GTC ÷ (giờ đóng − giờ mở), CHỈ xếp NV đã đóng HẾT chuyến (`ot_tot==0` + cửa sổ ≥2h)** → số trọn vẹn; NV còn chạy xuống mục "🏃 đang chạy".
- Ngưỡng (theo phân phối thật, trung vị đơn/giờ vùng ~4): cần chú ý = đơn/giờ **<2.5**; muộn **≥9h**; scan **<40%**. Bố cục: hero + dải 6 chỉ số · 🔴 cần chú ý · 🟢 hiệu suất cao · 🧑‍💼 theo AM (drill NV) · 🏃 đang chạy.

### Trang 8 — XẾP HẠNG TỔNG HỢP (`xephang.html`, thêm 24/08) — Supabase 30 ngày
- `report_xephang.py`, đọc **Supabase 30 ngày gần nhất** (`WINDOW_DAYS=30`; đánh giá ổn định, không live). `report_trend.main()` sinh trang.
- **Điểm tổng hợp 0-100**, scorecard 3 cấp **AM → bưu cục → nhân viên** (`<details>` lồng), xếp TỆ→TỐT mỗi cấp.
- **Trọng số (dict `W`, user chỉnh 25/08):** %GTC **35** · Năng suất (GTC/ngày làm) **20** · Tồn đỏ (Σ`g_red` ngày mới nhất) **20** · COD kẹt/đơn **15** · Kỷ luật (% ngày XP<9h) **10**. Cấp NV bỏ Tồn đỏ → chuẩn hoá lại 4.
- Chuẩn hoá con: NS ≥120→100/≤30→0 (`NS_HI/NS_LO`); COD ≥2tr/đơn→0 (`COD_CAP`); tồn đỏ ratio=đỏ/(đơn giao TB ngày thật)≥0.5→0 (`RED_CAP`). Đổi các hằng số này để tinh chỉnh.
- **Màu theo NHÓM 3 (tỉ lệ)** (`_tcolor`): mỗi cấp xếp tăng dần, 1/3 cuối 🔴 · giữa 🟡 · 1/3 đầu 🟢 — luôn đủ 3 màu.

---

## 3. XẾP HẠNG THEO AM

- **`am_map.py`** — `AM_OF = {tên_bưu_cục: tên_AM}` là NGUỒN DUY NHẤT (54 BC → 7 AM). Sửa 1 file này là áp cho TẤT CẢ báo cáo.
- 7 AM: Nguyễn Công Nam(13), Bùi Văn Đông(5), Hoàng Gia Đạt(7), Đinh Văn Thu(4), Nguyễn Đức Thịnh(9), Điêu Chính Luân(6), Bế Ngọc Chuyển(10). 8 điểm "ĐG" nhỏ chưa gán (thường 0 sản lượng).
- Khi user báo đổi cơ cấu AM → sửa `AM_OF` → kiểm tên khớp hub → commit/push → force deploy → verify `Σ AM = tổng vùng`.
- Mục "🧑‍💼 Theo AM" (bấm mở ra bưu cục, drill tiếp nhân viên) ở: trực tiếp, cuối ngày, tồn đọng (cả 3 phần).
- **Ngưỡng đơn đỏ tồn:** Giao>120h, Trả>120h, **LC giao>48h** (đổi từ 36h ngày 22/08), LC trả>48h.

---

## 4. SUPABASE (kho số liệu lịch sử)

- Postgres đám mây, PostgREST. Secret: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (KHÔNG in ra).
- Ghi bằng `db_sync.py` (`_upsert` merge-duplicates, **chịu lỗi**: cột chưa có → tự bỏ cột rồi thử lại). Sync chính ở `sync-23h.yml` (~23:35 VN), luồng riêng (`report_db_sync.py`), không đụng GitHub Pages.
- **Phân trang PostgREST phải `order=id.asc`** (nếu không sẽ trùng/sót khi >1000 dòng).

### Bảng (schema: `supabase_schema.sql`)
| Bảng | Khóa | Nội dung |
|---|---|---|
| `bao_cao_vung` | ngay | 1 dòng/ngày: đơn, %GTC, GTB, COD, LTC, VNGH, **gio_xuat_phat_tb, so_nv_muon** |
| `bao_cao_buu_cuc` | ngay,buu_cuc | mỗi BC/ngày |
| `bao_cao_nhan_vien` | ngay,buu_cuc,driver_id | mỗi NV/ngày: %GTC, cod_gtb, ltc, **gio_xuat_phat, gio_ket_thuc, thoi_luong_phut, xuat_phat_muon** |
| `bao_cao_ton_dong` | ngay,buu_cuc,order_type | tồn LGT/LC theo 4 nhóm giờ + `g_red` |
| `chi_tiet_don` | ngay,buu_cuc,ma_don | từng đơn (giữ **60 ngày**, env `DB_KEEP_DETAIL_DAYS`) |

- Bảng tổng hợp (vùng/BC/NV/tồn) KHÔNG xóa → giữ nhiều năm. Chỉ `chi_tiet_don` bị dọn theo retention.
- **Migration:** chạy file `.sql` trong Supabase → SQL Editor (ALTER ADD COLUMN IF NOT EXISTS). Đã có: `supabase_migration_gred.sql`, `supabase_migration_ltc_tondong.sql`, `supabase_migration_kyluat.sql`.
- **Backfill an toàn:** dùng partial upsert (chỉ khóa + cột cần) — PostgREST chỉ update cột có trong payload, GIỮ nguyên cột khác (vd không ghi đè `chua_gan` về 0 vì backlog quá khứ không dựng lại được).

### Dung lượng (gói Free 500MB) — theo dõi
- 23/08/2026: 5 bảng, ~655k dòng (chi_tiet_don 644k / 17 ngày), ước tính ~150–180MB (~30–36%). `chi_tiet_don` chiếm ~95%.
- **Rủi ro:** đầy 60 ngày → ~2,3 triệu dòng → có thể chạm/vượt 500MB (~6 tuần). Đã đặt lịch nhắc kiểm tra 20/09/2026. Nếu cần: giảm `DB_KEEP_DETAIL_DAYS` 60→40–45, hoặc nâng Pro.
- SQL xem dung lượng thật: `pg_total_relation_size` trên `pg_stat_user_tables` (SQL Editor).

### Dự phòng khi token nhanh.ghn.vn hết hạn
- `snapshot.py` đọc ngày mới nhất từ Supabase → trang live/eod/backlog rơi về số gần nhất + banner đỏ cảnh báo, thay vì trắng/trống.

---

## 5. SCRIPT ↔ WORKFLOW

| Script | Vai trò | Workflow (giờ VN) |
|---|---|---|
| `report.py` | ENGINE fetch+aggregate; gửi tin trip | (dùng chung) |
| `report_live.py` (+`report_chuyendi.py`) | trang chính + hiệu suất chuyến đi + fallback | `live-30m.yml` (mỗi ~15') |
| `report_dashboard.py` | trang cuối ngày (eod.html) | `live-30m.yml` slot ~23:30 |
| `report_backlog_web.py` | trang tồn đọng | `live-30m.yml` |
| `report_trend.py` (+`report_xephang.py`) | xu hướng + nhân viên + kho chuyển tiếp + **xếp hạng tổng hợp** (từ Supabase) | (deploy cùng live-30m) |
| `report_khochuyentiep.py` | kho chuyển tiếp | `khochuyentiep-8h-16h-22h.yml` |
| `report_db_sync.py` (+`db_sync.py`) | ghi Supabase | `sync-23h.yml` (~23:35) |
| `report_morning.py` | bản tin "việc cần làm hôm nay" | `morning-730.yml` (07:30, kích bởi cron-job.org) |
| `report_overview.py` | tổng quan mỗi 2h | `overview-2h.yml` (9–21h) |
| `report_alert_drop.py` | cảnh báo NV tụt sâu %GTC | (slot EOD) |
| `report_sla_alert.py` | đơn tồn 24–120h cần xử lý | `sla-alert-16h.yml` |
| `report_bc_by_am.py` / `report_bc_hotspot.py` / `report_bc_weekly.py` | BC yếu theo AM / hotspot / tuần | `bc-by-am-mon.yml`, `bc-hotspot-9h.yml`, `bc-weekly-mon.yml` |
| `report_monthly.py` | so sánh MoM | `monthly-day1.yml` |
| `pages.yml` | publish GitHub Pages | (khi push docs) |

- `morning-730.yml` dùng **workflow_dispatch** (GitHub schedule hay bỏ lượt 00:30 UTC) → cron-job.org POST tới `.../actions/workflows/morning-730.yml/dispatches` body `{"ref":"main","inputs":{"send":"1"}}` lúc 07:30 VN. PAT (Actions RW) chỉ nằm ở cron-job.org.
- `live-30m.yml` có `workflow_dispatch` với input `force_eod=1`, `eod_date=YYYY-MM-DD`, `send=0/1` → tạo lại eod.html thủ công cho 1 ngày (KHÔNG spam nhóm khi send=0).

---

## 6. SECRETS CẦN CÓ (GitHub Actions repo tbb-dashboard)
`NHANH_TOKEN` · `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` · `GTALK_OA_TOKEN` · `GTALK_CHANNEL_ID`.
Local (khi chạy tay): đọc từ `tbb-gtalk-bot/.env`. Đặt secret: `gh secret set NHANH_TOKEN` (không lộ giá trị).

---

## 7. QUY TẮC LÀM VIỆC (cho phiên sau)
1. Trước khi trả lời số liệu TBB → luôn fetch dữ liệu mới nhất (không dùng số cũ trong ngữ cảnh).
2. Xong tính năng → verify độc lập (Σ NV = BC = AM = tỉnh = vùng KHỚP; đối chiếu raw vs aggregate vs Supabase).
3. Commit: `git -c commit.gpgsign=false`, kết thúc message bằng `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Đổi UI web xong → push → (force) deploy → verify trên trang thật + chụp màn khổ iPhone.
4. Working copy hay bị xoá (scratchpad) → `gh repo clone vietvk-ux/tbb-dashboard`. cwd reset giữa các lệnh Bash.
5. Nhóm GTalk TBB đã đủ bản tin — KHÔNG tự thêm bot/tin mới nếu user không yêu cầu.

## 8. MỎ DỮ LIỆU CHƯA KHAI THÁC (khảo sát 22/08 từ get-trip-items) — đề xuất tiếp
Đã làm **#2 giờ xuất phát/đơn-giờ (chuyendi.html)** + **#3 isScanned** (cột scan trong chuyendi). Còn: **#1 lý do giao hỏng** `failCode/failNote` (mạnh nhất — biết vì sao GTB; `failNote` có sẵn chữ đọc được: "Không liên lạc được"/"Khách hẹn"/"NV gặp sự cố"…), #4 `collectCodFailedAmount` (COD hỏng đã/chưa thu), #5 năng suất đơn/chuyến, #6 đơn theo huyện/xã (lat/lng).

## 9. NHẬT KÝ THAY ĐỔI GẦN ĐÂY (24–25/08)
- Thêm trang 7 `chuyendi.html` (hiệu suất chuyến đi) + trang 8 `xephang.html` (xếp hạng tổng hợp AM→BC→NV).
- **XOÁ `vngh.html`** + `report_vngh.py` (giữ 3 chỉ số TikTok ở trang chính).
- `nhanvien.html`: bỏ "Xếp hạng %GTC tháng", thay bằng "Năng suất Nay−TB" (đơn GTC ngày gần nhất vs TB chính NV, lọc >30 đơn, top 15 bứt phá/sa sút).
- Supabase: cột kỷ luật ra hàng (`gio_xuat_phat/gio_ket_thuc/thoi_luong_phut/xuat_phat_muon` ở `bao_cao_nhan_vien`; `gio_xuat_phat_tb/so_nv_muon` ở `bao_cao_vung`) — migration `supabase_migration_kyluat.sql` đã chạy, backfill 21–22/08.
- Theo dõi dung lượng Supabase (task nhắc 20/09; giữ retention 60 ngày).

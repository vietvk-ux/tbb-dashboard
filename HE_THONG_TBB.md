# HỆ THỐNG BÁO CÁO VẬN HÀNH VÙNG TÂY BẮC BỘ (TBB)

> Tài liệu tổng hợp toàn bộ hệ thống dashboard giao hàng Vùng TBB (GHN). Cập nhật 2026-08-21.
> Repo: **`vietvk-ux/tbb-dashboard`** (public) · Host: **GitHub Pages** · Slug bí mật: `9c7e4b21a6f0`
> URL gốc: `https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/`

---

## 1) CÁC TRANG BÁO CÁO (thêm tên file sau URL gốc)

| Trang | File | Nội dung | Nguồn | Nhịp |
|---|---|---|---|---|
| Trực tiếp | `index.html` / `live.html` | %GTC realtime, dải chỉ số (gán/chưa gán/chạy/GTC/GTB + 3 chỉ số TikTok), **theo AM**, theo tỉnh, theo bưu cục (drill nhân viên) | Live nhanh.ghn.vn (ON_TRIP+FINISHED) | ~15' |
| Cuối ngày | `eod.html` | %GTC chốt, NV nguy hiểm (COD GTB/đơn), **NV còn chuyến chưa kết thúc**, **theo AM** (+COD), theo tỉnh, bưu cục (drill NV + cột COD) | FINISHED trips (lọc ≥10h) | 23:30 |
| Tồn đọng | `backlog.html` | Lấy·Giao·Trả·Luân chuyển theo khung giờ, đơn đỏ >120h/>36h/>48h | Live | ~15' |
| Xu hướng | `trend.html` | %GTC theo ngày, đơn giao/GTC, tồn chưa gán, **đơn GIAO >120h**, **3 biểu đồ đơn đỏ Trả/LC giao/LC trả**, top/bottom bưu cục 30 ngày | Supabase | ~15' |
| Năng suất NV | `nhanvien.html` | NS GTC/ngày top 20, **COD GTB/đơn top 10 (30 ngày)**, xếp hạng %GTC tháng | Supabase | ~15' |
| Kho chuyển tiếp | `khochuyentiep.html` | Tồn luân chuyển 3 kho | Live | ~15' |
| Đơn TikTok | `vngh.html` | Tiến độ đơn TikTok (mã VNGH) theo bưu cục + **drill nhân viên**, xếp còn-chưa-giao nhiều→ít | Live | ~15' |
| JSON cho bot | `live.json`, `backlog.json`, `dashboard_data.json` | Dữ liệu thô | — | — |

---

## 2) NGUỒN DỮ LIỆU

### nhanh.ghn.vn (nội bộ GHN)
- Host: `https://nhanh-api.ghn.vn/api` · Auth: header `Authorization: Bearer <JWT>` + `X-WarehouseId`.
- **JWT lấy ở:** login nhanh.ghn.vn → F12 → tab **Console** → `localStorage.getItem('SESSION')` → copy chuỗi `eyJ...`.
- Endpoint chính: `get-trip-list-by-hub` (chuyến), `get-trip-items` (đơn trong chuyến), `count-orders-to-assign` (tồn chưa gán), `get-general-info` + `get-backlog-transport-info` (tồn đọng khung giờ), `get-locations` (danh sách hub).
- Item: `type` = DELIVER/PICK/RETURN · `isSucceeded` (GTC/LTC) · `isUpdated` (đã thao tác) · `collectAmount` (COD) · `orderCode` · `failNote`/`failCode` (lý do GTB — CHƯA khai thác).
- Trip: `driverId`, `driverName`, `deliverCount`, `endTime` (UTC), `endDateIndex` (ngày VN), `status`.

### Supabase (Postgres đám mây, gói Free 500MB)
- Project: `kndrnmdfqurrvzhvtvta.supabase.co` · Truy cập REST (PostgREST) bằng service key.
- **5 bảng:** `bao_cao_vung` (1 dòng/ngày) · `bao_cao_buu_cuc` · `bao_cao_nhan_vien` (có `cod_gtb`, `ltc`) · `chi_tiet_don` (chi tiết đơn) · `bao_cao_ton_dong` (tồn Lấy·Giao·Trả·LC 4 nhóm giờ + `g_red`).
- **Retention:** `chi_tiet_don` giữ **60 ngày** (tự xóa, env `DB_KEEP_DETAIL_DAYS`); 4 bảng tổng hợp **giữ mãi**.
- Schema: `supabase_schema.sql`; migrations: `supabase_migration_ltc_tondong.sql`, `supabase_migration_gred.sql` (chạy 1 lần trong SQL Editor).

---

## 3) FILE MÃ NGUỒN (repo)

| File | Vai trò |
|---|---|
| `report.py` | ENGINE: fetch chuyến FINISHED (lọc ≥10h), `aggregate()` gộp mã đơn, `dedup_orders()`, `send_gtalk()`. Phân biệt trùng tên bằng driverId. |
| `report_live.py` | Trang trực tiếp (index/live) + gọi `report_vngh` xuất `vngh.html` + fallback Supabase. |
| `report_vngh.py` | Dựng `vngh.html` từ rows của `fetch_live` (không fetch lại). |
| `report_dashboard.py` | `eod.html` + `db_sync` + gửi GTalk + `fetch_backlog`/`fetch_ontrip` + mục **Theo AM**. |
| `report_backlog_web.py` | `backlog.html` + `backlog_rows()` (ghi `bao_cao_ton_dong`) + fallback. |
| `report_trend.py` | `trend.html` + `nhanvien.html` + `khochuyentiep.html` đọc Supabase. `_get_all()` phân trang **BẮT BUỘC order=id**. |
| `report_db_sync.py` | Job 23h: fetch + aggregate + `db_sync.sync()` + `sync_backlog()`. KHÔNG đụng Pages. |
| `report_morning.py` | Bản tin "Việc cần làm hôm nay" (đọc Supabase chốt tối qua → GTalk). |
| `db_sync.py` | Upsert Supabase (`_upsert` CHỊU LỖI cột thiếu → strip+retry). `sync()`, `sync_backlog()`, `_cleanup_detail()`. |
| `snapshot.py` | Fallback: `load_snapshot()` (agg từ Supabase) + `load_ton_dong()` + `banner_html()`. |
| `am_map.py` | Ánh xạ **BƯU CỤC → AM** (54 bưu cục, 7 AM). Cập nhật khi đổi phân công. |
| `report_overview.py`, `report_alert_drop.py`, `report_backlog_web.py` | Overview / cảnh báo tụt %GTC. |

---

## 4) WORKFLOWS (`.github/workflows/`) & LỊCH

| Workflow | Lịch | Việc |
|---|---|---|
| `live-30m.yml` | cron `*/15 23,0-16 * * *` (mỗi 15' trong khung 23h–16h UTC) | Deploy Pages: tạo index/live/backlog/trend/nhanvien/khochuyentiep/vngh. Slot **23:30 VN** tạo `eod.html`. |
| `sync-23h.yml` | cron `35 16 * * *` = **23:35 VN** | `report_db_sync` ghi Supabase (summary + ton_dong). Độc lập Pages. |
| `morning-730.yml` | **workflow_dispatch** (kích qua cron-job.org) | `report_morning` gửi bản tin sáng. KHÔNG dùng `schedule` (native GitHub hay bỏ nhịp). |
| `overview-2h.yml` | định kỳ | Overview. |
| `pages.yml` | disabled | (cũ) |

### cron-job.org (đảm bảo bản tin sáng 07:30 đúng giờ 100%)
- POST tới `https://api.github.com/repos/vietvk-ux/tbb-dashboard/actions/workflows/morning-730.yml/dispatches`
- Headers: `Authorization: Bearer <GitHub PAT có quyền Actions:read+write>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
- Body: `{"ref":"main","inputs":{"send":"1"}}` · Lịch: 07:30 VN · Phản hồi đúng: HTTP 204.

---

## 5) SECRETS (GitHub repo settings → Secrets)
`NHANH_TOKEN` · `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` · `GTALK_OA_TOKEN` · `GTALK_CHANNEL_ID`
(Set: `echo "<val>" | gh secret set <NAME> --repo vietvk-ux/tbb-dashboard`)

---

## 6) QUY TẮC TÍNH SỐ (quan trọng)

- **Gộp mã đơn (dedup):** 1 đơn gán nhiều chuyến chỉ tính 1 lần, theo `(bưu_cục, orderCode)`, giữ bản ghi điểm cao nhất (score = 4·đã_giao + 2·đã_thao_tác).
- **%GTC** = đơn giao thành công / tổng đơn giao (đã gộp mã).
- **LTC** = đơn LẤY (PICK) thành công, gộp theo mã đơn.
- **COD GTB (`gtb_cod`)** = Σ `collectAmount` các đơn giao **thất bại** (không thành công) của nhân viên. **COD GTB/đơn** = `gtb_cod / (tổng − giao_TC)`.
- **Đơn TikTok** = `orderCode` bắt đầu `VNGH`.
- **Lọc 10h (chỉ báo cáo cuối ngày):** chỉ tính chuyến FINISHED kết thúc **từ 10h sáng VN** (env `EOD_TRIP_CUTOFF_HOUR=10`) → loại chuyến đóng sớm (đuôi hôm trước).
- **Trùng tên nhân viên:** gộp theo `driverId`, thêm đuôi `#<6 số cuối id>` khi cùng tên trong 1 bưu cục.
- **Đơn đỏ (backlog quá hạn):** Giao>120h · Trả>120h · LC giao>36h · LC trả>48h (`g_red`).
- **Fallback:** token hết hạn → index/eod/backlog tự dựng từ snapshot Supabase mới nhất + banner đỏ cảnh báo.

---

## 7) BẢO TRÌ

### Đổi NHANH_TOKEN (khi hết hạn — TTL ~25–30 ngày, dự kiến ~2026-09-07/12)
Triệu chứng: mọi API trả **HTTP 400**; trang tự chuyển bản dự phòng (banner đỏ); Supabase thiếu ngày.
1. Lấy token mới (mục 2).
2. `echo "<TOKEN>" | gh secret set NHANH_TOKEN --repo vietvk-ux/tbb-dashboard` **VÀ** `--repo vietvk-ux/bao-cao-trip-tbb`.
3. Kích lại: `gh workflow run live-30m.yml --repo vietvk-ux/tbb-dashboard -f force_eod=0 -f send=0`.

### Sửa ánh xạ AM
Sửa `am_map.py` (`AM_OF = {bưu_cục: AM}`) → commit → push. Kiểm khớp tên: so keys với `get-locations`.

### Backfill 1 ngày vào Supabase (theo bộ lọc mới)
Chạy `report_db_sync.py` với env `EOD_DATE=YYYY-MM-DD` (upsert idempotent). Muốn dọn NV cũ (chuyến toàn <10h): xóa dòng `bao_cao_nhan_vien` không nằm trong tập lọc.

### Kích thủ công
- Trang: `gh workflow run live-30m.yml --repo vietvk-ux/tbb-dashboard -f force_eod=1 -f send=0` (tạo cả eod).
- Bản tin sáng thử: `gh workflow run morning-730.yml -f send=0` (chỉ in, không gửi nhóm).
- Sửa kẹt GitHub Pages: `gh api -X DELETE repos/vietvk-ux/tbb-dashboard/environments/github-pages`.

---

## 8) LỊCH SỬ TÍNH NĂNG CHÍNH (2026-08)
LTC (lấy TC) mọi cấp · COD GTB/đơn · tồn đọng theo ngày (Supabase) · fallback Supabase khi token chết · phân biệt trùng tên · retention 60 ngày · bản tin sáng 07:30 (cron-job.org) · biểu đồ đơn GIAO/Trả/LC quá hạn · trang đơn TikTok · tối ưu iPhone (safe-area) · cột COD trong drill NV · mục "NV còn chuyến chưa kết thúc" · **lọc chuyến ≥10h cho %GTC cuối ngày** · 3 chỉ số TikTok trên dải chỉ số · **xếp hạng theo AM**.

---

## 9) NHÂN SỰ VÙNG
GĐV: **Vũ Khắc Việt** (vietvk@ghn.vn) · Trợ lý vùng: Lường Văn Chung · Lead HR: Phượng.
**7 AM** (ánh xạ trong `am_map.py`): Nguyễn Công Nam (13 BC) · Bùi Văn Đông (5) · Hoàng Gia Đạt (7) · Đinh Văn Thu (4) · Nguyễn Đức Thịnh (9) · Điêu Chính Luân (6) · Bế Ngọc Chuyển (10). *(8 điểm "ĐG" chưa gán AM — bổ sung nếu có sản lượng.)*

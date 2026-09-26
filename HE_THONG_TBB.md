# HỆ THỐNG BÁO CÁO VẬN HÀNH VÙNG TÂY BẮC BỘ (TBB) — GHN

Tài liệu tổng hợp để **tiếp tục làm việc ở phiên sau / trên máy khác**. Repo: `vietvk-ux/tbb-dashboard` (public). Chủ: Vũ Khắc Việt (vietvk@ghn.vn) — GĐV Vùng TBB.
Cập nhật gần nhất: 18/09/2026.

> Nguyên tắc bảo mật: KHÔNG in/echo/commit giá trị `NHANH_TOKEN`, `SUPABASE_SERVICE_KEY`, `GTALK_OA_TOKEN`, PAT. Đặt qua `gh secret set` / GitHub Actions secrets. Dữ liệu số KHÔNG lưu trong repo — chỉ deploy lên GitHub Pages + Supabase.

---

## 0. SETUP MÁY MỚI (đọc đầu tiên khi đổi máy)

**Quan trọng:** toàn bộ tự động hoá chạy trên **GitHub Actions (đám mây)**, KHÔNG phụ thuộc máy cá nhân. Đổi máy KHÔNG làm hỏng hệ thống — web + bản tin GTalk + Supabase vẫn chạy. Máy cá nhân chỉ cần để **sửa code · test tay · push**.

Các bước dựng lại trên máy mới:
1. **Clone repo:** `gh repo clone vietvk-ux/tbb-dashboard` (cần `gh auth login` trước — đăng nhập GitHub tài khoản `vietvk-ux`, quyền repo + workflow để push/deploy).
2. **Python 3.11 + thư viện:** `pip install -r requirements.txt` (chỉ 2 gói: `aiohttp`, `requests`).
3. **Tạo `.env` local** (để chạy tay — KHÔNG commit) với 5 biến, lấy giá trị như sau:
   - `NHANH_TOKEN` — đăng nhập nhanh.ghn.vn → F12 Console → `copy(localStorage.SESSION)` (xem §2).
   - `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` — Supabase Dashboard → Project Settings → API (service_role key).
   - `GTALK_OA_TOKEN` + `GTALK_CHANNEL_ID` — từ cấu hình bot GTalk cũ (hỏi user nếu chưa có).
   - *Cách nạp khi chạy tay:* export ra shell, hoặc đọc từ `.env` bằng `grep|cut` (đừng in giá trị). Các repo bot khác (`tbb-gtalk-bot/.env`, `tbb-bot-python/.env`) trên máy CŨ chứa sẵn các key này — máy mới phải tạo lại.
4. **Secrets trên GitHub Actions ĐÃ có sẵn** trong repo (không đọc lại được) — chỉ cần cập nhật khi token hết hạn: `gh secret set NHANH_TOKEN` (§2, §6). Automation KHÔNG dùng `.env` local.
5. **Chạy tay 1 trang để test:** vd `NHANH_TOKEN=... python report_live.py` (sinh `docs/<slug>/index.html`). Hoặc kích workflow: `gh workflow run live-30m.yml`.

---

## 1. TỔNG QUAN

- **Vùng TBB = 5 tỉnh:** Lào Cai (LCA), Yên Bái (YBA), Sơn La (SLA), Điện Biên (DBI), Lai Châu (LCH). ~62 bưu cục.
- Prefix mã bưu cục: `(DBI) `, `(LCA) `, `(LCH) `, `(SLA) `, `(YBA) `.
- Hệ thống gồm: (a) **trang web dashboard** (GitHub Pages, mobile) tự cập nhật; (b) **các bản tin GTalk** tự động; (c) **kho số liệu Supabase** để phân tích lịch sử/xu hướng.

### URL trang web (slug bí mật)
Gốc: `https://vietvk-ux.github.io/tbb-dashboard/9c7e4b21a6f0/`
**Trang chính + các trang phụ:**
- `index.html` / `live.html` — TRANG CHÍNH, GẦN REALTIME (mỗi ~15'). **TOÀN TRANG theo Bento (Mẫu 3, 24/09):** (1) **Menu 9 báo cáo** lưới 2 cột `.menu/.mtile`, mỗi ô icon chip màu + tên + phụ đề + mini-nhịp; ô "Báo cáo tổng quan" feature span 2 cột kèm %GTC vùng lớn (`_menu` list). (2) **Dải 12 chỉ số** bo 16px, **kỷ luật ngữ nghĩa (24/09)**: 8 ô đếm số = tối trung tính slate (`.st.neu`, số trắng); ô cảnh báo lên màu đúng nghĩa — Chưa gán & COD = amber, XP muộn = đỏ khi >0, %GTC TikTok = ngưỡng, **🔴 Giao >120h = ĐỎ** (đơn Giao tồn quá 120h toàn vùng, thay 'Tiến độ chạy' 26/09 — nguồn `fetch_giao_120h`: 1 call get-general-info truyền hết hub_ids order_type=ALL, cộng bucket 120_192+192; `fetch_live`→`(rows,giao_120h)`). (`kpis` list, cờ `neu`, màu động `xp_rgb`/`tt_rgb`). (3) **Thanh Tồn chưa gán** = ô feature bento đỏ `.cgbento` (icon chip + số lớn + caret ▾), drill AM→bưu cục→tuyến bên trong. (4) **Hero %GTC** + **cards Theo AM/Tỉnh/Bưu cục** (`.bc`/`.bc.sub`): bỏ spine trái, nền radial tint theo màu %GTC (`--h`: good/warn/bad = xanh/amber/đỏ), viền hue, bo 16px. Tất cả dùng `--h` (rgb) + rgba (không cần color-mix) → CẢ trang là 1 hệ bento thống nhất. **Hero thêm mốc & chẩn đoán (24/09):** vạch target **70%** trên thanh (`_bar target=`) + chips "Mục tiêu 70% · Hôm qua chốt X% · TB N ngày Y% · Còn Z điểm tới mục tiêu 70%" (nguồn `_khuvuc_ref()` đọc khuvuc_data, 0 creds); dòng `.diag` CHẨN ĐOÁN VÙNG tự sinh từ rows (chưa gán giao cao nhất ở BC nào · **AM yếu nhất tên+%GTC** so tương đối · **N bưu cục %GTC <50%** (lọc BC ≥20 đơn) · NV xuất phát muộn · ổn định). **COD GTB `_codm`: ≥1 tỷ hiện '1,17 tỷ', <1 tỷ 'X,Ytr'** (đơn vị nằm trong hàm, caller không thêm 'tr'). (Không làm delta "tải vs hôm qua" vì so giữa-ngày với cả-ngày lệch giờ — cần log volume theo giờ nếu muốn.) Dải 12 tile (gồm 🚛 Còn phải giao · 📊 Tiến độ chạy · 🕘 XP muộn >9h30 · 💰 COD GTB kẹt · 3 chỉ số TikTok…) + chú giải chân trang. **(23/09) Thêm %GTC TikTok theo NV/bưu cục/AM** (`_tt_cell`/`_tt_chip`, agg AM+prov cộng vngh): bảng NV bỏ cột 'Ch' → thêm cột 🛍️GTC; meta BC/AM/tỉnh có chip 🛍️ %GTC (GTC/gán); media ≤430px thu gọn bảng cho iPhone.
- `tongquan.html` — TỔNG QUAN 1 màn hình (hero %GTC · 8 KPI · điểm nóng · drill AM→BC→NV). Link "📋 Báo cáo tổng quan" đầu index. (Bỏ 24/09: biểu đồ %GTC 14 ngày, thẻ NV xuất phát muộn, thẻ Xã 0% GTC.)
- `khuvuc.html` — BẢN ĐỒ KHU VỰC (Trang 9, xem §2): GTC theo xã + toạ độ, bản đồ nhiệt, biểu đồ tuần/tháng.
- `eod.html` — CUỐI NGÀY (chốt ~23:30). Gồm: ② So sánh: 3 thẻ %GTC (hôm nay/hôm qua/TB7) + **bảng VÙNG đủ 9 chỉ số** cột **Hôm nay · Hôm qua · TB 7 ngày** (đơn giao·giao TC·%GTC·GTB·chưa gán·LTC·COD GTB·TikTok gán·%GTC TikTok), ô Hôm nay tô màu xu hướng vs TB7 (GTB/chưa gán/COD tăng=đỏ, GTC/LTC tăng=xanh) + **từng AM gấp gọn bấm mở** 6 chỉ số (Hôm nay·Hôm qua·TB7). Nguồn: `_fetch_hist` (bao_cao_vung, TB7=bình quân hist[:7]) + `_fetch_bc_days(d,7)` (bao_cao_buu_cuc 7 ngày gộp (AM,ngày) rồi bình quân). Σ AM = vùng; ③ Tồn chuyển sang mai (chưa gán + GTB + tổng) + **🗺 Tồn chưa gán theo tuyến phường/xã** (AM→bưu cục→xã bấm mở, bảng xã/phường + số đơn Giao chưa xếp chuyến; `fetch_backlog` giữ `wards` từ `fetch_chua_gan`, 0 call thêm; thêm 24/09); **🚨 Bưu cục nguy hiểm của vùng · Top 10** (điểm tổng hợp 3 tiêu chí: chưa gán cao + tồn đỏ>120h cao + %GTC thấp, kết hợp cuối ngày + TB7; nhãn vấn đề Xấu toàn diện/Ùn tắc chưa gán/Tồn đỏ quá hạn/%GTC yếu + note phân tích; tồn đỏ lấy qua `report_backlog_web.fetch_all` → +~128 call/ngày ở eod); ⚠️ Nhóm NV nguy hiểm COD GTB (top 10, `class='danger'`); 🚗 NV còn chuyến chưa kết thúc (AM→BC→NV); Theo AM/tỉnh; Tất cả bưu cục. (ĐÃ BỎ 21/09: Lý do giao hỏng, Tốt nhất hôm nay, Top 10 bưu cục COD GTB — failNote vẫn tính ngầm trong aggregate.)
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
  - **"Chưa gán giao" (chuẩn):** `report.fetch_chua_gan` gọi **`get-detail-by-status`** (body `{hub_id, order_type:DAILY_TRIP_NONE, view_mode:WARD, status:[PICK,DELIVER,DELIVER_PRIORITY,RETURN]}` — chính là view "Tồn LGT → Theo phường/xã") → **DELIVER** = số Giao chưa gán chuyến (VD Bum Tở 653, Bình Lư 478). Đây là NGUỒN "chưa gán" cho live/tongquan/eod/Supabase `chua_gan`. **1 call/bưu cục** trả CẢ tổng 4 loại VÀ tách theo xã → thêm `wards=[(tên xã, số đơn Giao)]` top20 (khớp 100% get-general-info cũ — verify Âu Lâu DELIVER 551, Văn Phú 1485; đổi endpoint 24/09, trước 21/09 dùng get-general-info, trước nữa `count-orders-to-assign.deliver` nhỏ hơn thực). `report_live` gắn `backlog_wards` vào mỗi row. **Kiểm 24/09 (quét 64 BC, có retry):** get-detail-by-status ≡ get-general-info (region 11.018=11.018, lệch 0); Σ xã = tổng DELIVER mỗi BC & toàn vùng; **phủ AM 100%** (11.014/11.014 đơn thuộc 54 BC có AM, 10 điểm "ĐG" đều 0 → Σ AM=vùng). **Lưu ý cap:** `wards` cắt **top 20 xã/BC** → BC >20 xã thì đuôi vài đơn lẻ không hiện (tổng BC vẫn đủ). Raw fetch không retry dễ dính 429 trả 0 tạm thời — `_post` production có retry/backoff nên không hụt.

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
- Ngưỡng (theo phân phối thật, trung vị đơn/giờ vùng ~4): cần chú ý = đơn/giờ **<2.5**; muộn **≥9h**; scan **<40%**. Bố cục: hero + dải 6 chỉ số · 🔴 cần chú ý · 🟢 hiệu suất cao · 🧑‍💼 theo AM (drill NV) · 🏃 đang chạy · **🕘 NV xuất phát muộn (sau 9h30) AM→bưu cục→NV + giờ XP, muộn nhất lên đầu** (thêm 26/09, nguồn `late_nv=[m in timed if m['late']]`).

### Trang 8 — XẾP HẠNG TỔNG HỢP (`xephang.html`, thêm 24/08) — Supabase 30 ngày
- `report_xephang.py`, đọc **Supabase 30 ngày gần nhất** (`WINDOW_DAYS=30`; đánh giá ổn định, không live). `report_trend.main()` sinh trang.
- **Điểm tổng hợp 0-100**, scorecard 3 cấp **AM → bưu cục → nhân viên** (`<details>` lồng), xếp TỆ→TỐT mỗi cấp.
- **Trọng số (dict `W`, user chỉnh 25/08):** %GTC **35** · Năng suất (GTC/ngày làm) **20** · Tồn đỏ (Σ`g_red` ngày mới nhất) **20** · COD kẹt/đơn **15** · Kỷ luật (% ngày XP<9h) **10**. Cấp NV bỏ Tồn đỏ → chuẩn hoá lại 4.
- Chuẩn hoá con: NS ≥120→100/≤30→0 (`NS_HI/NS_LO`); COD ≥2tr/đơn→0 (`COD_CAP`); tồn đỏ ratio=đỏ/(đơn giao TB ngày thật)≥0.5→0 (`RED_CAP`). Đổi các hằng số này để tinh chỉnh.
- **Màu theo NHÓM 3 (tỉ lệ)** (`_tcolor`): mỗi cấp xếp tăng dần, 1/3 cuối 🔴 · giữa 🟡 · 1/3 đầu 🟢 — luôn đủ 3 màu.

### Trang 9 — BẢN ĐỒ KHU VỰC (`khuvuc.html`, thêm 14/09) — chốt cuối ngày, lưu 30 ngày
- **Mục đích:** phân tích GTC theo **địa lý** (xã/phường + toạ độ GPS). Link đặt SAU "Năng suất Nhân viên" trên index.
- **Dữ liệu:** mỗi đơn DELIVER có `deliverInfo`/`receiverContact` = {cityName tỉnh, districtName huyện, **wardName xã/phường**, wardCode, **lat/lng**}. `report.py::_trip_items` nay GIỮ thêm geo (`ward/dist/city/lat/lng`) — thuần cộng thêm, `aggregate`/`dedup_orders` không đọc.
- **Lưu trữ:** file JSON trong repo `khuvuc_data/YYYY-MM-DD.json` (1 file/ngày, ~300KB, giữ **30 ngày**, prune file cũ). KHÔNG dùng Supabase (payload toạ độ nặng — tránh ăn quota 500MB). Mỗi file: `{ngay, tot, gtc, provs[name,n,g,latC,lngC], bbox, cells[lat,lng,n,g] (ô ~2km), wards[dist,ward,n,g], nv[bc,nv,dist,ward,n,g], stray[...]}`.
- **`report_khuvuc.py` 2 chế độ:** `collect` (gom 1 ngày → ghi JSON; `EOD_DATE=YYYY-MM-DD` để backfill; chạy độc lập tốn call API) · `render` (đọc ≤30 file → dựng `docs/<slug>/khuvuc.html`).
- **Thu geo MIỄN PHÍ:** `report_db_sync.py` (sync 23:35) sau khi sync Supabase → gọi `report_khuvuc.build_day_payload(payload)` + `write_day` dùng LẠI payload đã bóc item → **KHÔNG tốn call API thêm**. Lỗi phần này KHÔNG làm hỏng sync chính (try/except).
- **Workflow:** `sync-23h.yml` `contents:write` + bước commit `khuvuc_data/` mỗi tối (git pull --rebase → add → commit → push). `live-30m.yml` thêm bước `python report_khuvuc.py render` (đọc khuvuc_data trong checkout, mỗi 15').
- **Trang gồm:** dải chỉ số ngày mới nhất · **bản đồ nhiệt canvas** 2 chế độ (🌡 Mật độ đơn = blob sáng theo số đơn · 🎯 %GTC = chấm đỏ→xanh theo tỉ lệ, size theo đơn; nhãn 5 tỉnh từ centroid) · **📦 Số đơn giao về theo ngày (14 ngày)** (cột SVG, màu theo %GTC ngày đó) · **🏙 Top 12 Huyện/TP theo số đơn** (bảng # · Huyện · Đơn · GTC · %GTC, thanh bar tỉ lệ nền — thay 3 biểu đồ %GTC tuần/tháng/ngày cũ vì trùng trang Xu hướng, đổi 24/09) · 🔴 xã khó giao (≥20 đơn) · 📦 **Top 20 xã/phường có đơn giao về nhiều nhất vùng** (# · Huyện · Xã · Đơn · GTC · %GTC, sắp đơn↓ — thay cho "đơn lạc tuyến" cũ, bỏ 24/09) · 👤 drill NV×xã (AM→bưu cục→NV→bảng xã).
- **Mục drill "GTC theo xã" gom AM → Bưu cục → Nhân viên → bảng xã** (24/09, `AM_OF`): AM xếp %GTC thấp→cao (`_am_pct`), trong AM bưu cục thấp→cao (`_bc_pct`), trong BC nhân viên thấp→cao. Σ AM = tổng.
- **Tối ưu iPhone (`@media max-width:430px`):** thu gọn padding/font bảng, `td.l` max 88px (tên dài tự cắt "…"), `.dtl`+`.tw` bọc `overflow-x:auto` (bảng cuộn trong khung, KHÔNG tràn trang), tôn trọng `safe-area-inset`. `_svg_bars` chặn bề rộng cột ≤80px + căn giữa (1-2 cột không kéo dài cả biểu đồ). Đã verify khổ 375px không tràn ngang.
- **Riêng tư:** chỉ gom mức **xã/phường trở lên**, không lộ địa chỉ/GPS/SĐT từng khách.
- Số khớp Supabase (13/09: 31.961 đơn · GTC 20.155 · 63%). Backfill 10-13/09; **FINISHED API chỉ trả vài ngày gần** nên biểu đồ tuần/tháng đầy dần theo thời gian (mỗi tối +1 ngày). 14/09 chốt tối nay 23:35.

---

## 3. XẾP HẠNG THEO AM

- **`am_map.py`** — `AM_OF = {tên_bưu_cục: tên_AM}` là NGUỒN DUY NHẤT (54 BC → 7 AM). Sửa 1 file này là áp cho TẤT CẢ báo cáo.
- 7 AM (54 BC, sau khi chuyển Văn Phú 14/09): Nguyễn Công Nam(12), Bùi Văn Đông(5), Hoàng Gia Đạt(7), Đinh Văn Thu(4), Nguyễn Đức Thịnh(9), Điêu Chính Luân(6), Bế Ngọc Chuyển(11). 10 điểm "ĐG" nhỏ chưa gán (thường 0 sản lượng).
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

### Nhật ký 25–28/08
- **Xếp hạng (`xephang.html`)**: trọng số user chỉnh 25/08 = %GTC 35 · Năng suất 20 · **Tồn đỏ 20 · COD 15 · Kỷ luật 10** (dict `W`). Cửa sổ đổi sang **THÁNG dương lịch** (`fetch(offset)`, ngày 1 → nay, tự reset đầu tháng) + **nút xem tháng trước** (`write_pages` sinh `xephang.html` + `xephang_prev.html`, toggle `.mtoggle`). Thêm mục **"🏤 Xếp hạng bưu cục TOÀN VÙNG"** (54 BC phẳng, màu nhóm-3 theo tất cả BC, drill NV).
- **Tồn đọng (`backlog.html`)**: (28/08) BỎ "Top bưu cục >120h" + "Theo AM" ở 2 mục **Lấy·Giao·Trả** và **Luân chuyển**. → **HOÀN TÁC 17/09** (xem Nhật ký 17/09): thêm lại **"🧑‍💼 Theo AM · bấm xem bưu cục"** cho cả 2 mục. GIỮ Top BC + Theo AM ở mục **Đơn backlog (đơn đỏ)** + mục "🏤 Tất cả bưu cục" (danh sách + tìm kiếm).
- **LỖI EOD UNDERCOUNT (27/08)**: chuyến của ngày D đóng SAU nửa đêm → `endDateIndex=D+1` + <10h → rơi khỏi cả 2 báo cáo (26/08 hụt 185 NV: 21k thay vì ~37.8k). Fix opt-in: env `EOD_OPERATING_DAY=1` (mặc định TẮT — automation 23:30 KHÔNG đổi) → `_finished_trips(...,next_ymd)` gộp chuyến `endDate=D+1` <10h. Input `opday` cho `live-30m.yml` để chốt lại 1 ngày cũ: `gh workflow run live-30m.yml -f force_eod=1 -f eod_date=YYYY-MM-DD -f opday=1 -f send=0`. (User giữ mốc chốt 23:30, không đổi lịch.)
- **TRANG TRỰC TIẾP lọc 10h (27/08)**: `report_live.fetch_live` nay lọc FINISHED bằng `_ended_after_cutoff` (≥10h) → không lẫn chuyến hôm qua đóng sau nửa đêm vào số "hôm nay". Ảnh hưởng index + chuyendi + chỉ số TikTok.
- **CHỐNG TRẮNG TRANG Supabase (27/08)**: `report_trend._get` retry 3× + timeout (15,90); `report_trend.main` khi fetch Supabase lỗi → GIỮ trang cũ (`_preserve_or`) thay vì đè "Chưa cấu hình". (trend/nhanvien/xephang.)
- **CHUYENDI đổi cột Scan → %GTC (27/08)**: `isScanned=True` thực chất ⟺ đơn giao HỎNG đã quét (không phải "cầm hàng") → cột "Scan" đổi thành **%GTC** (gtc/total, màu đỏ<60/vàng<80/xanh≥80, `GTC_MIN=60`). Mục "🏃 đang chạy" gộp **AM → bưu cục → nhân viên** (drill) thay bảng phẳng.
- **EOD dải chỉ số (28/08)**: bỏ 2 thẻ GTB & COD GTB khỏi strip, thêm **🛍️ TikTok gán** (`vngh_total`) + **🛍️ %GTC TikTok** (`vngh_gtc`). Thứ tự: Đơn giao · Giao TC · TikTok gán · %GTC TikTok · LTC. (Hero + mục "nguy hiểm COD" vẫn giữ GTB/COD.)
- **TRANG TRỰC TIẾP dải chỉ số (28/08)**: bỏ thẻ ❌ GTB thao tác, thêm **🛒 LTC** ở cuối. Thứ tự: Đã gán · Chưa gán · Đang chạy · GTC nay · TikTok gán · TikTok GTC · %GTC TikTok · LTC.

### Nhật ký 29/08
- **CHUYENDI (`chuyendi.html`) thêm 2 thẻ dải** sau "🏃 Đang chạy": **🕘 XP muộn ≥9h** (`late_count = Σ NV có cờ late`, kỷ luật ra hàng — NV xuất phát sau 9h) + **📦 Còn phải giao** (`on_road = Σ(ot_tot−ot_done)` của NV đang chạy = đơn đang trên đường, dự báo áp lực cuối ngày). Strip đủ 8 thẻ: Chuyến · Đơn/giờ TB · Giờ XP TB · %GTC vùng · Cần chú ý · Đang chạy · XP muộn ≥9h · Còn phải giao.
- **NÚT LÀM MỚI (FAB) trang chính** (`report_live.gen_html`): nút tròn `⟳` cố định góc phải-dưới màn hình (`.fab position:fixed`, tôn trọng `env(safe-area-inset-bottom)` để tránh vạch home). Bấm → `location.replace(pathname+'?t='+Date.now())` (cache-buster, luôn lấy bản mới nhất trên GitHub Pages CDN), nút xoay khi tải (`.spin`). Trang vẫn giữ auto-refresh 5' (`<meta http-equiv=refresh 300>`).

### Nhật ký 17/09
- **EOD — thêm 4 mục quản lý** (`report_dashboard.gen_html`, chèn sau banner chưa gán): **① Lý do giao hỏng hôm nay** (gom `failNote` 3 nhóm — `report._fail_group`: do khách·shop / không liên lạc / do NV·địa chỉ — + bảng top 8 lý do; extract ở `report._trip_items` `rec["fail"]`, tổng hợp trong `aggregate` → `agg["fail"]={groups,top,tong}`); **② So sánh %GTC hôm nay vs hôm qua & TB 7 ngày** (`_fetch_hist(d)` đọc Supabase `bao_cao_vung` 8 ngày trước, `_delta` ▲/▼); **③ Tồn chuyển sang mai** (chưa gán + GTB + tổng); **④ Tốt nhất hôm nay** (Top 5 NV ≥30 đơn + Top 5 bưu cục ≥100 đơn theo %GTC). Sửa dải strip 5 thẻ tràn iPhone (media query ≤430px). eod chỉ regen lúc chốt ~23:30. Verify 16/09: khach 6171/lienlac 4405/nvdc 517; %GTC 63.6 khớp Supabase.
- **FIX ô tìm kiếm bưu cục chết** (backlog.html + eod.html): `filt()` quét mọi `.bc`/`details.bc` → gặp phần tử mục **"Theo AM"** (chỉ có `data-u`, KHÔNG `data-k`) thì `e.dataset.k` = undefined → `.indexOf()` **ném lỗi, dừng cả `forEach`** → search không lọc gì. Sửa: thu hẹp selector còn **`.bc[data-k]` / `details.bc[data-k]`** (chỉ danh sách bưu cục). QUY TẮC: khi tạo `details.bc` mà KHÔNG có `data-k` (mục AM), search vẫn an toàn nhờ guard `[data-k]`.
- **BACKLOG — thêm lại "🧑‍💼 Theo AM · bấm xem bưu cục"** cho 2 mục **Tồn Lấy·Giao·Trả** + **Luân chuyển** (hoàn tác việc bỏ hồi 28/08). `render_summary` gộp theo `AM_OF`, xếp cao→thấp, mỗi AM `<details.bc data-u>` (không data-k → không đụng search) mở ra bảng bưu cục con theo từng loại + Tổng (xếp giảm dần). Verify: Σ AM (LGT) = tổng vùng, mỗi AM = Σ bưu cục con (7/7 khớp).
- **TỔNG QUAN — thêm 4 thẻ Điểm nóng cấp NV/bưu cục** (`report_tongquan.build_html`, 0 call thêm): **👤 NV %GTC thấp nhất toàn vùng** (≥30 đơn, top 15, sort `(%GTC↑, tổng đơn↓)` — cùng 0% thì NV nhiều đơn lên trước; chú thích giữa ngày 0%=chuyến chưa đóng); **💸 NV COD GTB kẹt cao nhất** (top 15 NV giữ tiền thu hộ ≥100k); **🚛 Bưu cục còn phải giao nhiều nhất** (on_road=Σ`ot_tot`−`ot_done`, mở ra **top 10 bưu cục cao→thấp** + số chuyến đang chạy); **⏳ Bưu cục chưa gán cao nhất** (top 10 BC backlog — **mỗi bưu cục bấm xổ ra chi tiết xã/phường chưa gán giao + số đơn**, từ `row["backlog_wards"]`, CSS `.bcw/.wl/.wr/.wn`, thêm 24/09; BC không có ward hiện phẳng). Helper `_bc_tail`/`_nv_hotrow`. Điểm nóng 5→9 thẻ, không tràn iPhone. Verify 18/09: NV kém nhất Lò Văn Hoàng 0% (75 đơn), NV COD Phạm Văn Cường 29,7tr, BC còn phải giao Cầu Thia 1.019, BC chưa gán Sa Pa 734.
- **TỔNG QUAN — mục "📊 Biến động %GTC vs hôm qua" (2 ngày chốt):** `report_tongquan._momentum()` đọc `bao_cao_buu_cuc` 2 ngày CHỐT gần nhất (D-1 vs D-2 — SẠCH, không lẫn %GTC live pha loãng giữa ngày). Hiển thị: chip theo AM (▲ xanh tăng/▼ đỏ giảm, điểm %) + 🔻 top 8 bưu cục tụt sâu nhất (≥100 đơn cả 2 ngày) + 🔺 top 8 cải thiện nhất, mỗi dòng `%cũ→%mới · số đơn`. %GTC: TĂNG=tốt(xanh)/GIẢM=xấu(đỏ). Verify khớp Supabase (Đông Cuông 81→59=-22, Sa Pa 52→60=+8). Cần ≥2 ngày trong `bao_cao_vung` (có sẵn).
- **LIVE (index) — thêm 4 tile theo dõi vào dải trực tiếp (0 call thêm):** tính trong `report_live.gen_html` từ `rows[].drivers`: **🚛 Còn phải giao** = Σ(`ot_tot`−`ot_done`) đơn đang trên đường của chuyến ĐANG CHẠY (khác "cần giao" = đã gán+chưa gán); **📊 Tiến độ chạy** = Σ`ot_done`/Σ`ot_tot` %; **🕘 XP muộn >9h30** = số NV có `st` (giờ XP sớm nhất hôm nay) > 9h30 (phút>570); **💰 COD GTB kẹt** = `R["cod_gtb"]` (đã tính, `_codm` triệu). Dải 8→12 tile, lưới 3 cột không tràn iPhone. Verify 18/09 14:59: còn phải giao 27.040 · tiến độ 29% · muộn 58 NV · COD 928,4tr. **Chân trang (`.foot`) viết lại thành CHÚ GIẢI đầy đủ** từng chỉ số (tên đậm + giải thích ngắn) + lưu ý "%GTC thấp giữa ngày là bình thường (chuyến chưa đóng)".
- **BACKLOG — cảnh báo sớm "⏰ Sắp quá hạn SLA" (Giao/Trả 96–120h):** mục mới ngay sau Đơn đỏ (tab `#soon`). `_soon_of(e)` lấy bucket `96_120` của DELIVER+RETURN = đơn CẬN mốc 120h (chưa vỡ). Hero tổng vùng (màu warn) + `render_soon_section` liệt kê **top 12** bưu cục sắp quá hạn nhiều nhất (cao→thấp, cột Giao/Trả 96–120 + tổng). Mục đích: đẩy TRƯỚC khi thành đơn đỏ. Verify 18/09: 835 đơn (Giao 832/Trả 3), Đông Cuông 125 cao nhất. (Rà soát 10 trang: hệ thống đã phủ rộng; 3 mỏ còn trống = đơn hoàn RETURN, lý do "do NV" đích danh, %GTC đơn-đã-xử-lý — user chọn làm SLA sắp quá hạn trước.)
- **BACKLOG — kiểm số + 2 tối ưu (user chọn):** đã audit toàn bộ CHUẨN (LGT 28.497 = Σ loại = Σ nhóm giờ = Σ AM; Luân chuyển 6.785 tương tự; Đơn đỏ 1.200 = Giao>120+Trả>120+LCg>48+LCt>48; 10 điểm giao 0 tồn). Thêm: **(a) Cột 🔴>120h trong drill Theo AM** (LGT + Luân chuyển): mỗi bưu cục hiện số >120h (`sec_groups[">120h"]`), dòng AM hiện tổng >120h, sắp bưu cục theo `(>120h, tổng)` giảm dần; Σ AM >120h = >120h của section (verify 1.052). **(b) So với CHỐT tối qua** ở 3 hero (Đỏ/LGT/Luân chuyển): `_prev_ton()` đọc `bao_cao_ton_dong` chốt gần nhất `<` hôm nay (Σ total theo nhóm LGT/TR, Σ `g_red` cho đỏ), `_delta_ton` hiển thị ▲(đỏ=xấu)/▼(xanh=tốt) — **TĂNG tồn = xấu nên đảo màu** so %GTC. Nhãn ghi rõ "so chốt DD/MM" vì live giữa ngày vs chốt 23:35 (LGT thường ▲ do đơn mới vào ban ngày; đỏ & luân chuyển ít biến động nội ngày nên có ý nghĩa hơn). `bao_cao_ton_dong` chốt 23:35 mới có từ 15/09 nên lịch sử đầy dần.

### Nhật ký 17/09
- **EOD — mục "🚗 Nhân viên còn chuyến CHƯA kết thúc" nhóm theo AM → Bưu cục → Nhân viên** (`report_dashboard.gen_html`): trước là bảng phẳng top 20 xếp theo đơn treo. Nay gom bằng `AM_OF.get(bc)`: mỗi AM là dòng tiêu đề đậm (màu `--warn`) + tổng (số người · số bưu cục · Σ chuyến · Σ đơn treo), xếp AM theo **Σ đơn treo cao→thấp**; trong AM gom theo bưu cục (Σ đơn treo↓), mỗi bưu cục liệt kê NV (đơn treo↓, thụt lề). BỎ giới hạn top 20 (đã gọn vì nhóm). BC chưa map → "(chưa phân AM)". (User yêu cầu cho gọn.)

### Nhật ký 16/09
- **NGƯỠNG MÀU %GTC (đổi 24/09, mục tiêu 70%):** xanh (tốt) **≥70%** · vàng 60-70% · đỏ <60% (đồng bộ mọi trang, kể cả live). Đồng bộ ở `_cls` của 6 file sinh trang: `report_live`, `report_dashboard`(eod), `report_tongquan`, `report_khuvuc`, `report_trend` + `_gtc_cls` `report_chuyendi`; vạch target `_spark` tongquan cũng 70. **GIỮ NGUYÊN** 2 report GTalk `report.py` (icon 🟢 + "TOP NV cao ≥80%") và `report_overview.py` — tin nhắn bot, muốn đổi báo riêng.
- **BẢNG MÀU NỀN mỗi trang (nền NGOÀI có màu · thẻ TRONG tối gốc):** chèn `<style>body{background:radial-gradient(...glow .10...,#base) !important;background-attachment:fixed}.top{...cùng tông...}` riêng từng trang; thẻ giữ `--card` gốc (#161b2d) nên đậm hơn nền, nổi rõ, dịu không loá. Base màu mỗi trang: Trực tiếp xanh lá `#0e2318` · Tổng quan xanh dương `#132449` · Cuối ngày chàm `#171640` · Tồn đọng cam `#241804` · Chuyến đi teal `#0c2521` · Xu hướng cyan `#0c2430` · Khu vực hồng `#26121d` · Xếp hạng tím `#191238` · Năng suất NV vàng `#241a06` · Kho đỏ đô `#2e1114`. Chèn ở `_CSS`/`_HEAD` từng file sinh (report_live/tongquan/dashboard/backlog/chuyendi/khuvuc/xephang + `_NV_BG`/`_TREND_BG`/`_KHO_BG` trong report_trend). LƯU Ý: user đã thử "màu ăn cả vào thẻ trong" rồi BỎ — CHỐT chỉ màu nền ngoài, thẻ tối. eod cập nhật màu ở chốt 23:30.
- **NĂNG SUẤT NV — thêm dòng bình quân vùng + mục "GTC hôm qua theo bưu cục" + đưa 2 drill lên đầu trang:** dòng 🌐 năng suất bình quân toàn vùng (Σ GTC ÷ Σ ngày-NV, ~48 đơn/ngày) đầu drill TB 30 ngày. Mục mới `_ns_yesterday_bc`: **dòng đầu Tổng GTC hôm qua toàn vùng** (Σ tất cả BC, so TB ngày = Σ GTC 30 ngày ÷ số ngày, ▲/▼) + GTC ngày gần nhất theo bưu cục (xếp cao→thấp), bấm ra NV, so TB 30 ngày (▲ trên/▼ dưới, 🟢/🔴). 3 cấp: Tổng vùng → Bưu cục → Nhân viên. Thứ tự trang nhanvien: (1) drill TB 30 ngày AM→BC→NV, (2) GTC hôm qua theo BC→NV, (3) NS xếp hạng, (4) Top COD.
- **RÀ SOÁT TOÀN BỘ 16/09 — tất cả chuẩn:** 10 trang tươi/không lỗi; Σ NV=bưu cục=AM=tỉnh=vùng (live + Supabase); Supabase 41 ngày liên tục không lỗ hổng (06/08→15/09); trend khớp bao_cao_vung; **xephang gộp đúng (driver_id,bưu cục)** — nhan_vien = buu_cuc khớp tuyệt đối (Cầu Thia 43.743 đơn/29.191 GTC); GTC hôm qua (nhan_vien) = bao_cao_vung 16.901; bình quân vùng 48. Chỉ phát hiện & vá 1 lỗi ở 2 drill nhanvien (xem dưới).
- **FIX gộp tổng bưu cục sai khi NV làm 2 bưu cục:** `_ns_drill` & `_ns_yesterday_bc` trước gộp NV theo `driver_id` đơn → NV chuyển bưu cục bị dồn hết GTC về BC cuối → tổng BC lệch ~1% (Cầu Thia TB 983 sai vs 973 thật). Sửa: **key theo (driver_id, bưu cục)** → mỗi BC tính đúng đơn của mình, khớp tuyệt đối tính tay + Supabase (GTC hôm qua vùng 16.901, bình quân vùng 48). LƯU Ý khác với [[nv-trung-ten-driverid]] (đó là phân biệt TRÙNG TÊN khác driver_id; đây là 1 driver_id ở NHIỀU bưu cục).
- **NĂNG SUẤT NV (cũ) (`nhanvien.html`) — mục "ngày gần nhất vs TB 30 ngày" → drill AM → Bưu cục → Nhân viên:** `report_trend._ns_drill` (thay `_ns_today_card`, dữ liệu `data["nv_gtc"]` = bao_cao_nhan_vien 30 ngày). Mỗi NV: TB/ngày = Σ GTC ÷ số ngày làm. Nhóm AM→BC→NV, **xếp TB/ngày THẤP→CAO** mỗi cấp, **màu đỏ/vàng/xanh theo nhóm-3 (tertile) toàn vùng** (ngưỡng LO/HI = phân vị 33/67 của avg NV). Pill = số TB/ngày; NV kèm số ngày làm + tổng đơn. Import `AM_OF` vào report_trend. CSS `details.amx/.bcx/.nvr`. Bỏ bảng Bứt phá/Sa sút cũ.
- **SỐ KIỆN GIAO (100% chính xác) theo NV·bưu cục·AM·tỉnh:** `report_live._it` bóc `len(x["items"])` mỗi đơn DELIVER → cộng `d["kien"]` (+`kien_gtc`), gộp BC/AM/tỉnh/vùng (0 call thêm). Hiển thị "📦 N kiện" ở dòng meta + cột **Kiện** bảng NV (index + drill Tổng quan + NV card). Σ NV=BC=AM khớp (37.242 kiện/29.019 đơn = 1,28 kiện/đơn). `weight` (gram) CÓ trong `items[].weight` nhưng chỉ ~45% khai báo → chưa dùng (chỉ tương đối). Icon "còn phải giao" đổi 📦→🚛 (📦 chỉ dành cho kiện). **BỎ HIỂN THỊ số kiện (16/09, user yêu cầu cho gọn):** gỡ 📦 N kiện ở meta AM/tỉnh/bưu cục + cột "Kiện" bảng NV (index) + chip/meta drill Tổng quan. `d["kien"]`/`kien_gtc` vẫn TÍNH ngầm (0 cost) — bật lại chỉ cần thêm lại dòng hiển thị.
- **TRANG TỔNG QUAN (`tongquan.html`)** — gom số chính cần theo dõi từ báo cáo trực tiếp + dữ liệu 30 ngày vào 1 màn hình. `report_tongquan.build_html(rows)` (rows = `fetch_live`, **0 call API thêm**), sinh trong `report_live.main` cạnh index. Gồm: hero %GTC vùng · **8 KPI** (Đã gán·Chưa gán·Đang chạy·Còn phải giao·GTC·💰COD GTB kẹt·LTC·%GTC TikTok) · **điểm nóng tự động** (AM & bưu cục %GTC thấp nhất, bưu cục COD GTB cao nhất, bưu cục còn phải giao/chưa gán…) · **8 link chi tiết** tới mọi trang. (Đã bỏ 24/09: biểu đồ xu hướng %GTC 14 ngày, thẻ NV xuất phát muộn, thẻ Xã 0% GTC.) Link **📋 Báo cáo tổng quan** (nút xanh nổi bật `.eod.tq`) đặt ĐẦU danh sách trên index. Mobile: KPI grid 2 cột <430px, không tràn ngang. Verify Σ khớp, xu hướng hiện 6 ngày (10-15/09 — xác nhận auto-collect tối chạy tốt).
- **TỔNG QUAN — mục "Xem chi tiết" → BÁO CÁO TỔNG HỢP AM → Bưu cục → Nhân viên** (`_consolidated(rows)`): drill 3 cấp bấm mở, mỗi cấp dòng meta (📥gán·⏳chưa gán·🏃đang chạy·📦còn phải giao·✅GTC·💰COD GTB·🛒LTC), NV là thẻ chip đầy đủ (gán·GTC·COD·LTC·TikTok·chuyến·giờ XP·tiến độ đang chạy) — chỉ hiện chip có số liệu. Xếp %GTC thấp→cao mỗi cấp. **Đã BỎ toàn bộ mục "Trang chuyên sâu" (link)** cho gọn (bỏ cả Kho chuyển tiếp). Tất cả từ `rows` (0 call thêm).
- **NGƯỠNG "MUỘN" → SAU 9h30 (thay ≥9h), TOÀN HỆ THỐNG:** `report.py` `EOD_LATE_START_MIN=570`, `late = phút trong ngày > 570` (áp `xuat_phat_muon` lưu Supabase → xephang Kỷ luật + eod). `report_tongquan` `LATE_MIN=570` `_late()`, `report_chuyendi` `LATE_H=9.5` `start_h>9.5`. Nhãn: Tổng quan "sau 9h30", chuyến đi "XP muộn >9h30". Ngày cũ (trước 16/09) đã lưu theo ≤9h nên giữ nguyên; từ 16/09 chốt theo 9h30. Test 15/09: ≥9h=72 NV → >9h30=38 NV.
- **TỔNG QUAN — thẻ Điểm nóng BẤM MỞ chi tiết** (`_hot(..., detail)` → `<details>`): AM thấp→list bưu cục; Bưu cục thấp→NV card (%GTC↑); Bưu cục COD cao→NV card (COD↓, ai giữ tiền); Bưu cục chưa gán→xổ chi tiết xã/phường (24/09). Helper `_dl_bc`/`_nv_list`, CSS `details.ht`/`.dl`/`.hcar`. (Đã bỏ 24/09: thẻ NV xuất phát muộn, thẻ Xã 0% GTC `zero_wards`.) Mobile không tràn. **Tối ưu iPhone khi mở (16/09):** `_metaline` mỗi chỉ số thành span `.mi` (`white-space:nowrap`) → không gãy dòng giữa chừng (vd "📦419 kiện" dính liền), `.ml` flex-wrap gap; media <430px thu lề `.hd/.dtl/.bc.sub/.nvc` cho card lồng rộng hơn.

### Nhật ký 14/09
- **CƠ CẤU AM — chuyển `(YBA) Văn Phú`**: từ **Nguyễn Công Nam → Bế Ngọc Chuyển** (sửa 1 file `am_map.py`, tự áp mọi trang xếp hạng theo AM). Sau đổi: Nam **12 BC** · Chuyển **11 BC** · tổng vẫn **54 BC**, không trùng key. Đã verify trên trang live (Văn Phú nằm dưới Bế Ngọc Chuyển). Quy trình chuẩn khi user báo đổi cơ cấu: sửa `am_map.py` → kiểm tên khớp hub + không trùng key → commit/push repo thật `vietvk-ux/tbb-dashboard` → deploy → verify Σ AM = tổng vùng.
- **TRANG TRỰC TIẾP — thêm ⏳ chưa gán vào "Theo AM" + khối bưu cục**: dòng tóm tắt mỗi AM (`v["backlog"]`) và mỗi bưu cục con khi bấm mở (`_bc_drv_details`, `r["backlog"]`) nay hiển thị số **⏳ chưa gán**. Drill đầy đủ **AM (⏳ tổng) → bưu cục (⏳ từng BC) → nhân viên**; Σ ⏳ theo AM khớp tổng vùng.
- **TRANG TRỰC TIẾP — tile "⏳ Chưa gán" BẤM MỞ drill AM → Bưu cục → tuyến xã** (24/09): tile có `class='st cg'` + onclick mở `details#cgd` (cuộn tới). Mục drill (đặt sau dải chỉ số): AM (sort tổng chưa gán↓) → bưu cục (sort↓) → bảng **tuyến xã/phường + số đơn chưa gán** từ `row["backlog_wards"]` (nguồn get-detail-by-status, 0 call thêm). Dùng lại `details.bc/.sub/table.drv`; CSS `.st.cg`, `.cgdrill`. BC chưa có dữ liệu tuyến → fallback "Không lấy được chi tiết tuyến".
- **SỬA TÌM KIẾM BƯU CỤC (`report_live.filt()`)**: trước lọc mọi `.bc` (gồm thẻ AM/tỉnh/BC-con KHÔNG có `data-k`) → `e.dataset.k` undefined → TypeError, tìm kiếm hỏng. Nay chỉ lọc `.bc[data-k]` (54 bưu cục ở danh sách dưới cùng), fallback `k=''` an toàn, thông báo "không tìm thấy" chỉ hiện khi có từ khoá mà 0 kết quả. Đã test live: gõ "si ma cai"→1, "lào cai"→1, chuỗi rác→0 + báo trống, xoá→54, không lỗi JS.
- **TOKEN nhanh.ghn.vn HẾT HẠN → refresh 14/09**: token cũ (đặt 13/08) chết ~13/09 → mọi run live-30m + sync-23h **failure** (`code 1003 "Token is not alive"`, HTTP 400 ở get-locations). Đã lấy token SESSION mới (F12 Console `copy(localStorage.SESSION)`), set secret `NHANH_TOKEN` cho **2 repo** (tbb-dashboard + bao-cao-trip-tbb) + `.env` local, chạy lại → OK. **TTL ~30 ngày, dự kiến hết lại ~10-14/10.** (Token JWT KHÔNG có field `exp` → phải test bằng call thật.)
- **BACKFILL LỖ HỔNG 13/09**: sự cố token đêm 13/09 khiến chốt eod 23:30 + sync 23:35 đều fail → Supabase thiếu đúng **13/09** (trend/nhanvien/xephang/báo cáo sáng hụt 1 ngày). Đã chạy `sync-23h.yml -f date=2026-09-13` (idempotent upsert, KHÔNG gửi nhóm) → 13/09 vào Supabase: 64 BC · 494 chuyến · 31.961 đơn · %GTC 63,1% · chưa gán 18.490 · LTC 6.204 · giờ XP TB 8:13 · 66 NV muộn. Chạy lại live-30m thường → trend/nhanvien/xephang đã gồm 13/09. **eod.html** vẫn hiển thị 12/09 (không ép tạo lại vì force_eod sẽ kích cảnh báo NV tụt %GTC gửi nhóm) — sẽ tự cập nhật tối nay 14/09 khi chốt 23:30. **Bài học vá sau outage token:** backfill ngày thiếu bằng `sync-23h.yml -f date=<ngày>` (an toàn), TRÁNH `force_eod` giờ hành chính (gửi alert cũ vào nhóm).
- **TRANG 9 — BẢN ĐỒ KHU VỰC (`khuvuc.html`)**: xem chi tiết §2 "Trang 9". Phân tích GTC theo xã/phường + toạ độ, bản đồ nhiệt, số đơn/ngày (14 ngày), Top 12 Huyện theo đơn, xã khó giao, Top 20 xã đơn nhiều nhất vùng, drill NV×xã. Lưu `khuvuc_data/*.json` (30 ngày), thu geo miễn phí trong sync 23:35 (`report.py::_trip_items` + `report_khuvuc.py`), render mỗi 15' ở live-30m. Backfill 10-13/09.
- **EOD — thay "Kỷ luật ra hàng" bằng "💰 Top 10 bưu cục COD GTB cao nhất"** (`report_dashboard.gen_html`): gộp `gtb_cod` theo bưu cục, xếp cao→thấp, top 10 (#·Bưu cục·GTB đơn·COD GTB triệu) + tổng COD vùng. Bỏ bảng NV xuất phát muộn. (User chọn giữ nguyên cảnh báo %GTC 23:30 định kỳ.)
- **TRANG TRỰC TIẾP — thay số đếm ❌ GTB bằng COD GTB (tiền kẹt)** ở NV·bưu cục·AM·tỉnh: `report_live._it` bóc thêm `collectAmount` từ item DELIVER → mỗi NV cộng `cod_gtb` cho đơn `att & không succ` (GTB); gộp lên BC/AM/tỉnh (0 call API thêm). Hiển thị `❌COD X,Xtr` (pmeta/bcm) + cột `COD GTB` (bảng NV), helper `_codm` (đồng→triệu, <100k hiện '0'). Verify Σ NV=BC=AM=tỉnh=vùng khớp, HTML=số tính. `isScanned` vẫn thu nhưng KHÔNG dùng. (Tư vấn còn có thể thêm: lý do giao hỏng failNote, đơn hoàn RETURN, còn phải giao — user chưa chọn.)

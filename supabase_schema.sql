-- =====================================================================
-- SCHEMA DATABASE BÁO CÁO GIAO HÀNG VÙNG TÂY BẮC BỘ (Supabase / Postgres)
-- Chạy 1 LẦN: Supabase → SQL Editor → New query → dán toàn bộ → Run.
-- Các UNIQUE(...) bên dưới là BẮT BUỘC để upsert (ghi đè khi chạy lại) hoạt động.
-- =====================================================================

-- 1) Tổng hợp toàn VÙNG mỗi ngày (1 dòng/ngày)
create table if not exists bao_cao_vung (
  ngay        date primary key,
  so_buu_cuc  int,
  so_chuyen   int,
  don_giao    int,        -- tổng đơn gán giao (đã gộp mã đơn)
  gtc         int,        -- đơn giao thành công
  gtb         int,        -- đơn giao thất bại
  pct_gtc     numeric(5,1),
  cod_gtb     bigint,     -- tiền COD kẹt ở đơn GTB
  chua_gan    int,        -- tồn chưa gán vào chuyến
  ltc         int,        -- đơn lấy thành công (LTC)
  vngh_don    int,        -- đơn TikTok Shop (VNGH)
  vngh_gtc    numeric(5,1),
  created_at  timestamptz default now()
);

-- 2) Theo BƯU CỤC mỗi ngày
create table if not exists bao_cao_buu_cuc (
  id        bigserial primary key,
  ngay      date not null,
  buu_cuc   text not null,
  tinh      text,
  so_chuyen int,
  don_giao  int,
  gtc       int,
  gtb       int,
  pct_gtc   numeric(5,1),
  chua_gan  int,
  ltc       int,          -- đơn lấy thành công (LTC)
  unique (ngay, buu_cuc)
);
create index if not exists idx_bc_ngay on bao_cao_buu_cuc (ngay);
create index if not exists idx_bc_bc   on bao_cao_buu_cuc (buu_cuc);

-- 3) Theo NHÂN VIÊN mỗi ngày
create table if not exists bao_cao_nhan_vien (
  id        bigserial primary key,
  ngay      date not null,
  buu_cuc   text not null,
  tinh      text,
  ten_nv    text,
  driver_id text not null default '',
  so_chuyen int,
  don_giao  int,
  gtc       int,
  gtb       int,
  pct_gtc   numeric(5,1),
  cod_gtb   bigint,
  ltc       int,          -- đơn lấy thành công (LTC)
  unique (ngay, buu_cuc, driver_id)
);
create index if not exists idx_nv_ngay on bao_cao_nhan_vien (ngay);
create index if not exists idx_nv_ten  on bao_cao_nhan_vien (ten_nv);

-- 3b) TỒN ĐỌNG theo bưu cục × loại đơn × 4 nhóm khung giờ (chốt mỗi tối)
create table if not exists bao_cao_ton_dong (
  id         bigserial primary key,
  ngay       date not null,
  buu_cuc    text not null,
  tinh       text,
  order_type text not null,   -- PICK/DELIVER/DELIVER_PRIORITY/RETURN/TRANSPORT_DELIVERY/TRANSPORT_RETURN
  total      int,
  g_lt24     int,             -- <24h
  g_24_72    int,             -- 24–72h
  g_72_120   int,             -- 72–120h
  g_gt120    int,             -- >120h
  unique (ngay, buu_cuc, order_type)
);
create index if not exists idx_td_ngay on bao_cao_ton_dong (ngay);
create index if not exists idx_td_bc   on bao_cao_ton_dong (buu_cuc);

-- 4) CHI TIẾT từng đơn (đã gộp mã đơn — 1 dòng/đơn/ngày)
create table if not exists chi_tiet_don (
  id        bigserial primary key,
  ngay      date not null,
  buu_cuc   text not null,
  tinh      text,
  ma_don    text not null,
  ma_chuyen text,
  ten_nv    text,
  driver_id text default '',
  da_giao   boolean,      -- true = giao thành công (GTC)
  da_xu_ly  boolean,      -- true = đã thao tác/cập nhật
  cod       bigint,
  vngh      boolean,      -- true = đơn TikTok Shop
  unique (ngay, buu_cuc, ma_don)
);
create index if not exists idx_ct_ngay  on chi_tiet_don (ngay);
create index if not exists idx_ct_madon on chi_tiet_don (ma_don);
create index if not exists idx_ct_nv    on chi_tiet_don (ten_nv);

-- =====================================================================
-- Hệ thống TỰ ĐỘNG giữ chi tiết đơn 60 NGÀY (~2 tháng, gọn gói Free 500MB) sau
-- mỗi lần sync (db_sync._cleanup_detail; đổi bằng env DB_KEEP_DETAIL_DAYS). Bảng
-- tổng hợp (vùng/bưu cục/nhân viên) rất nhẹ → giữ NHIỀU NĂM. Dọn thủ công nếu cần:
--   delete from chi_tiet_don where ngay < current_date - interval '60 days';
-- =====================================================================

-- =====================================================================
-- MIGRATION (2026-08-17): thêm cột LTC + bảng TỒN ĐỌNG Lấy·Giao·Trả·Luân chuyển
-- Chạy 1 LẦN: Supabase → SQL Editor → New query → dán toàn bộ → Run.
-- An toàn chạy lại nhiều lần (dùng IF NOT EXISTS).
-- =====================================================================

-- 1) Cột LTC (lấy thành công) cho 3 bảng tổng hợp
alter table bao_cao_vung       add column if not exists ltc int;
alter table bao_cao_buu_cuc    add column if not exists ltc int;
alter table bao_cao_nhan_vien  add column if not exists ltc int;

-- 2) Bảng TỒN ĐỌNG theo bưu cục × loại đơn × 4 nhóm khung giờ (chốt mỗi tối)
--    order_type: PICK / DELIVER / DELIVER_PRIORITY / RETURN
--                TRANSPORT_DELIVERY / TRANSPORT_RETURN
create table if not exists bao_cao_ton_dong (
  id         bigserial primary key,
  ngay       date not null,
  buu_cuc    text not null,
  tinh       text,
  order_type text not null,
  total      int,            -- tổng tồn của loại này
  g_lt24     int,            -- <24h
  g_24_72    int,            -- 24–72h
  g_72_120   int,            -- 72–120h
  g_gt120    int,            -- >120h
  unique (ngay, buu_cuc, order_type)
);
create index if not exists idx_td_ngay on bao_cao_ton_dong (ngay);
create index if not exists idx_td_bc   on bao_cao_ton_dong (buu_cuc);

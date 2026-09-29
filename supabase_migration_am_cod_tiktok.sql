-- ============================================================
-- Migration: thêm COD GTB + TikTok theo BƯU CỤC (để so sánh từng AM)
-- Chạy 1 lần trong Supabase → SQL Editor. An toàn (IF NOT EXISTS).
-- Bảng bao_cao_buu_cuc trước đây chỉ có don_giao/gtc/gtb/chua_gan/ltc.
-- ============================================================

ALTER TABLE bao_cao_buu_cuc ADD COLUMN IF NOT EXISTS cod_gtb  bigint;   -- tiền COD kẹt (đồng)
ALTER TABLE bao_cao_buu_cuc ADD COLUMN IF NOT EXISTS vngh_don int;      -- số đơn TikTok gán
ALTER TABLE bao_cao_buu_cuc ADD COLUMN IF NOT EXISTS vngh_gtc numeric;  -- %GTC TikTok (1 số lẻ)

-- Sau khi chạy: db_sync (sync 23:35) tự ghi 3 cột này mỗi tối.
-- COD so sánh hôm qua/TB7 lấy NGAY từ bao_cao_nhan_vien (đã có sẵn cod_gtb).
-- TikTok hôm qua/TB7 TÍCH LŨY DẦN: hôm qua có sau 1 ngày, TB7 đủ sau ~1 tuần.

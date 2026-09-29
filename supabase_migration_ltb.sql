-- ============================================================
-- Migration: thêm LTB (Lấy THẤT BẠI) — vùng + từng bưu cục (để so sánh AM)
-- Chạy 1 lần trong Supabase → SQL Editor. An toàn (IF NOT EXISTS).
-- LTB = đơn LẤY đã thao tác nhưng KHÔNG lấy được (att & not succ).
-- ============================================================

ALTER TABLE bao_cao_vung     ADD COLUMN IF NOT EXISTS ltb int;   -- LTB toàn vùng
ALTER TABLE bao_cao_buu_cuc  ADD COLUMN IF NOT EXISTS ltb int;   -- LTB theo bưu cục (gộp AM)

-- Sau khi chạy: db_sync (23:35) tự ghi LTB mỗi tối.
-- Hôm nay có ngay; hôm qua có sau 1 ngày; TB7 đủ sau ~1 tuần
-- (LTB không lưu ở chi_tiet_don nên KHÔNG backfill được — tích lũy dần).

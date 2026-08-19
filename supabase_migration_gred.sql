-- =====================================================================
-- MIGRATION (2026-08-19): thêm cột g_red vào bao_cao_ton_dong
-- g_red = số ĐƠN ĐỎ (quá hạn) tính ĐÚNG NGƯỠNG từng loại:
--   Giao >120h · Trả >120h · LC giao >36h · LC trả >48h
-- (không suy ra được từ 4 nhóm giờ đã lưu → cần cột riêng, ghi từ tối nay).
-- Chạy 1 LẦN: Supabase → SQL Editor → dán → Run. An toàn chạy lại nhiều lần.
-- =====================================================================
alter table bao_cao_ton_dong add column if not exists g_red int;

-- Lưu KHỐI LƯỢNG (kg thực) đơn giao ĐÃ GÁN của vùng theo ngày
-- → vẽ đồ thị 14 ngày ô "Khối lượng giao / ngày" trang trực tiếp.
-- report_live._store_weight partial-upsert theo ngay (cuối ngày = chốt).
alter table public.bao_cao_vung add column if not exists weight_kg numeric;

-- Thêm cột g120 vào bao_cao_gio để so "Backlog giao 120h" cùng giờ hôm qua.
-- (gtc/total đã thêm ở supabase_migration_gio_rate.sql — cho Đã gán & GTC cùng giờ.)
-- g120 = số đơn Giao quá 120h (Backlog) luỹ kế tại thời điểm ghi.
ALTER TABLE bao_cao_gio
  ADD COLUMN IF NOT EXISTS g120 integer;

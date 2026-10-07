-- Thêm cột cho NHỊP ĐỘ GIAO (đơn/giờ thực) vào bao_cao_gio.
-- bao_cao_gio đang có: ngay (date), gio (int), pct_gtc (numeric), PK (ngay, gio).
-- Thêm:
--   gtc   = số đơn GIAO THÀNH CÔNG luỹ kế tại thời điểm ghi (để diff giữa 2 mốc → đơn/giờ)
--   total = số đơn ĐÃ GÁN luỹ kế tại thời điểm ghi (tham chiếu)
--   phut  = phút trong giờ của lần ghi mới nhất (để tính khoảng thời gian chính xác)
ALTER TABLE bao_cao_gio
  ADD COLUMN IF NOT EXISTS gtc   integer,
  ADD COLUMN IF NOT EXISTS total integer,
  ADD COLUMN IF NOT EXISTS phut  integer;

-- Thêm cột nvsnap (jsonb) vào bao_cao_gio: lưu %GTC TỪNG NHÂN VIÊN (driver_id) theo GIỜ
-- → để so %GTC mỗi NV vs TB 7 ngày cùng giờ của CHÍNH NV đó (mũi tên ▲/▼ trong bảng nhân viên).
-- nvsnap = {"<driver_id>": [gtc, total], ...}
ALTER TABLE bao_cao_gio
  ADD COLUMN IF NOT EXISTS nvsnap jsonb;

-- Thêm cột bcsnap (jsonb) vào bao_cao_gio: lưu %GTC từng BƯU CỤC theo GIỜ
-- → để so %GTC cùng giờ hôm qua cho thẻ AM / Tỉnh / Bưu cục (mũi tên xanh tốt hơn · đỏ kém hơn).
-- bcsnap = {"<tên bưu cục>": [gtc, total], ...}  (AM/Tỉnh = tổng các BC con)
ALTER TABLE bao_cao_gio
  ADD COLUMN IF NOT EXISTS bcsnap jsonb;

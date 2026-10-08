-- Thêm cột snap (jsonb) vào bao_cao_gio: lưu SNAPSHOT toàn bộ chỉ số dải theo GIỜ
-- → để so "tăng/giảm vs cùng giờ hôm qua" cho MỌI ô dải 'Chỉ số quan trọng của vùng'.
-- snap = {total,ontrip,on_road,gtc,late,vngh,vngh_gtc,vpct,cod_gtb,ltc,ltb,nv_low,g120}
ALTER TABLE bao_cao_gio
  ADD COLUMN IF NOT EXISTS snap jsonb;

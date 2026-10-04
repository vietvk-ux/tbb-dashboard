-- Thêm 2 cột lưu Tồn Lấy / Tồn Trả (CHƯA GÁN) theo ngày vào bao_cao_vung
-- → để vẽ biểu đồ 14 ngày cho 2 ô "Tồn Lấy chưa gán" + "Tồn Trả" trên Tổng Quan Vận Hành.
-- Chạy 1 lần trong Supabase → SQL Editor. An toàn (IF NOT EXISTS), không đụng dữ liệu cũ.

ALTER TABLE bao_cao_vung ADD COLUMN IF NOT EXISTS ton_lay integer;
ALTER TABLE bao_cao_vung ADD COLUMN IF NOT EXISTS ton_tra integer;

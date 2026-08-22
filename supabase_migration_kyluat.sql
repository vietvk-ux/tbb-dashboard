-- Migration: KỶ LUẬT RA HÀNG (giờ xuất phát / kết thúc / thời lượng theo NV)
-- Chạy 1 lần trên Supabase SQL Editor. An toàn chạy lại (IF NOT EXISTS).

-- Toàn vùng: giờ xuất phát TB (giờ thập phân, vd 8.32 = 08:19) + số NV muộn
alter table bao_cao_vung
  add column if not exists gio_xuat_phat_tb numeric(4,2),  -- giờ XP trung bình vùng (giờ VN, thập phân)
  add column if not exists so_nv_muon       int;           -- số NV xuất phát muộn (>= ngưỡng)

-- Theo nhân viên: giờ ra hàng thật trong ngày
alter table bao_cao_nhan_vien
  add column if not exists gio_xuat_phat   text,   -- 'HH:MM' chuyến bắt đầu trong ngày sớm nhất
  add column if not exists gio_ket_thuc    text,   -- 'HH:MM' chuyến kết thúc muộn nhất
  add column if not exists thoi_luong_phut int,     -- phút từ XP -> KT (thời lượng làm việc)
  add column if not exists xuat_phat_muon  boolean; -- true nếu XP >= EOD_LATE_START_HOUR (mặc định 9h)

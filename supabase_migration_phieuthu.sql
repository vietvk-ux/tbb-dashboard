-- Lưu PHIẾU THU CHƯA THU TIỀN (tiền COD nhân viên chưa nộp) theo NGÀY
-- → để vẽ đồ thị 14 ngày ô "NV chưa nộp tiền" trên trang trực tiếp.
-- report_live.py ghi (upsert theo ngay) mỗi lần chạy; giá trị lần chạy cuối ngày = bản chốt.
create table if not exists public.bao_cao_phieuthu (
    ngay       date primary key,
    so_nv      integer,     -- số CBĐP đang giữ tiền chưa nộp
    so_tien    bigint,      -- tổng tiền treo (đồng)
    updated_at timestamptz default now()
);

-- Bảng LOG %GTC THEO GIỜ → để DỰ BÁO %GTC về đích cuối ngày trên Hero trang trực tiếp.
-- Mỗi lần chạy (~15') ghi %GTC hiện tại của giờ đó; cuối ngày so với bao_cao_vung (chốt)
-- để tính "độ bứt tốc" same-hour → cuối ngày. Cần vài ngày dữ liệu mới dự báo được.
-- Chạy 1 lần trong Supabase → SQL Editor. An toàn.

CREATE TABLE IF NOT EXISTS bao_cao_gio (
    ngay     date    NOT NULL,
    gio      int     NOT NULL,          -- 0..23 (giờ VN)
    pct_gtc  numeric,                   -- %GTC luỹ kế tại giờ đó
    PRIMARY KEY (ngay, gio)
);

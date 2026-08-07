-- =====================================================================
-- VIEW so sánh %GTC nhân viên theo TUẦN & THÁNG (cải thiện / giảm)
-- Chạy 1 LẦN trong Supabase → SQL Editor → Run.
-- report_trend.py sẽ đọc 2 view này để xếp hạng nhân viên cải thiện/tụt.
-- =====================================================================

-- TUẦN: 7 ngày gần nhất vs 7 ngày trước đó. Cần ≥30 đơn ở CẢ 2 kỳ mới so.
create or replace view v_nv_tuan as
with cur as (
  select driver_id, buu_cuc, max(ten_nv) ten_nv, max(tinh) tinh,
         sum(don_giao) dg, sum(gtc) gtc
  from bao_cao_nhan_vien
  where ngay >= current_date - 7
  group by driver_id, buu_cuc
),
prev as (
  select driver_id, buu_cuc, sum(don_giao) dg, sum(gtc) gtc
  from bao_cao_nhan_vien
  where ngay >= current_date - 14 and ngay < current_date - 7
  group by driver_id, buu_cuc
)
select c.driver_id, c.buu_cuc, c.ten_nv, c.tinh,
       c.dg  as dg_cur,
       round(c.gtc*100.0/nullif(c.dg,0),1)  as pct_cur,
       p.dg  as dg_prev,
       round(p.gtc*100.0/nullif(p.dg,0),1)  as pct_prev,
       round(c.gtc*100.0/nullif(c.dg,0) - p.gtc*100.0/nullif(p.dg,0),1) as delta
from cur c
join prev p on c.driver_id = p.driver_id and c.buu_cuc = p.buu_cuc
where c.dg >= 30 and p.dg >= 30;

-- THÁNG: 30 ngày gần nhất vs 30 ngày trước đó. Cần ≥80 đơn ở CẢ 2 kỳ.
create or replace view v_nv_thang as
with cur as (
  select driver_id, buu_cuc, max(ten_nv) ten_nv, max(tinh) tinh,
         sum(don_giao) dg, sum(gtc) gtc
  from bao_cao_nhan_vien
  where ngay >= current_date - 30
  group by driver_id, buu_cuc
),
prev as (
  select driver_id, buu_cuc, sum(don_giao) dg, sum(gtc) gtc
  from bao_cao_nhan_vien
  where ngay >= current_date - 60 and ngay < current_date - 30
  group by driver_id, buu_cuc
)
select c.driver_id, c.buu_cuc, c.ten_nv, c.tinh,
       c.dg  as dg_cur,
       round(c.gtc*100.0/nullif(c.dg,0),1)  as pct_cur,
       p.dg  as dg_prev,
       round(p.gtc*100.0/nullif(p.dg,0),1)  as pct_prev,
       round(c.gtc*100.0/nullif(c.dg,0) - p.gtc*100.0/nullif(p.dg,0),1) as delta
from cur c
join prev p on c.driver_id = p.driver_id and c.buu_cuc = p.buu_cuc
where c.dg >= 80 and p.dg >= 80;

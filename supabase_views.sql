-- =====================================================================
-- VIEW xếp hạng %GTC nhân viên theo TUẦN & THÁNG (Cách B)
-- - Hiện NGAY sau 1 tuần / 1 tháng (chỉ cần kỳ HIỆN TẠI đủ đơn).
-- - Cột delta (tăng/giảm) tự có khi đã tồn tại KỲ TRƯỚC để so.
-- Chạy 1 LẦN (hoặc chạy lại để cập nhật) trong Supabase → SQL Editor → Run.
-- =====================================================================

-- TUẦN: xếp hạng theo %GTC 7 ngày gần nhất. Cần ≥30 đơn (kỳ hiện tại).
drop view if exists v_nv_tuan;
create view v_nv_tuan as
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
       case when p.dg >= 30
            then round(c.gtc*100.0/nullif(c.dg,0) - p.gtc*100.0/nullif(p.dg,0),1)
       end   as delta
from cur c
left join prev p on c.driver_id = p.driver_id and c.buu_cuc = p.buu_cuc
where c.dg >= 30;

-- THÁNG: xếp hạng theo %GTC 30 ngày gần nhất. Cần ≥80 đơn (kỳ hiện tại).
drop view if exists v_nv_thang;
create view v_nv_thang as
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
       case when p.dg >= 80
            then round(c.gtc*100.0/nullif(c.dg,0) - p.gtc*100.0/nullif(p.dg,0),1)
       end   as delta
from cur c
left join prev p on c.driver_id = p.driver_id and c.buu_cuc = p.buu_cuc
where c.dg >= 80;

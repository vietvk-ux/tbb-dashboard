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

-- =====================================================================
-- CẢNH BÁO TỤT SÂU: %GTC ngày mới nhất giảm ≥20 điểm so TRUNG BÌNH 7 ngày
-- trước của chính nhân viên đó (ngày này ≥20 đơn, nền 7 ngày ≥20 đơn).
-- report_alert_drop.py đọc view này rồi gửi cảnh báo GTalk. Trống → không gửi.
-- =====================================================================
drop view if exists v_nv_tut;
create view v_nv_tut as
with latest as (select max(ngay) d from bao_cao_nhan_vien),
cur as (
  select n.driver_id, n.buu_cuc, max(n.ten_nv) ten_nv, max(n.tinh) tinh,
         sum(n.don_giao) dg, sum(n.gtc) gtc
  from bao_cao_nhan_vien n, latest l
  where n.ngay = l.d
  group by n.driver_id, n.buu_cuc
),
base as (
  select n.driver_id, n.buu_cuc, sum(n.don_giao) dg, sum(n.gtc) gtc
  from bao_cao_nhan_vien n, latest l
  where n.ngay >= l.d - 7 and n.ngay < l.d
  group by n.driver_id, n.buu_cuc
)
select c.driver_id, c.buu_cuc, c.ten_nv, c.tinh,
       c.dg as dg_today,
       round(c.gtc*100.0/nullif(c.dg,0),1) as pct_today,
       round(b.gtc*100.0/nullif(b.dg,0),1) as pct_base,
       round(c.gtc*100.0/nullif(c.dg,0) - b.gtc*100.0/nullif(b.dg,0),1) as delta
from cur c
join base b on c.driver_id = b.driver_id and c.buu_cuc = b.buu_cuc
where c.dg >= 20 and b.dg >= 20
  and (c.gtc*100.0/nullif(c.dg,0)) <= (b.gtc*100.0/nullif(b.dg,0)) - 20;

-- =====================================================================
-- NĂNG SUẤT GTC nhân viên (30 ngày): năng_suất = tổng đơn GTC / số NGÀY LÀM VIỆC
-- (số ngày có đơn giao). Xếp hạng ai giao được nhiều đơn thành công/ngày nhất.
-- =====================================================================
drop view if exists v_nv_nangsuat;
create view v_nv_nangsuat as
select driver_id, buu_cuc, max(ten_nv) as ten_nv, max(tinh) as tinh,
       sum(gtc) as so_don_gtc,
       sum(don_giao) as tong_don,
       count(distinct ngay) filter (where don_giao > 0) as so_ngay_lam,
       round(sum(gtc)::numeric
             / nullif(count(distinct ngay) filter (where don_giao > 0), 0), 1) as nang_suat
from bao_cao_nhan_vien
where ngay >= current_date - 30
group by driver_id, buu_cuc
having count(distinct ngay) filter (where don_giao > 0) >= 1;

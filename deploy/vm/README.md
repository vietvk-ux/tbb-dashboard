# Deploy 19 báo cáo TBB lên VM Oracle Cloud VN

**Mục tiêu**: chạy toàn bộ 19 báo cáo trên VM Oracle (IP VN) để tránh block
`mbff.ghn.vn` từ GitHub Actions runners (US/EU).

**Pre-requisite**: VM Oracle Ubuntu 22.04 đã có sẵn (dùng chung với
`tbb-gtalk-bot` theo plan `tbb-bot-deploy-cloud.md`).

---

## Setup 1 lần (anh SSH vào VM rồi chạy)

```bash
# 1. SSH vào VM (bằng cách anh đang dùng cho tbb-gtalk-bot)
#    ssh -i <key> ubuntu@<PUBLIC_IP>

# 2. Chạy setup — tự clone 2 repo + cài deps + tạo .env template
cd /home/ubuntu/tbb-dashboard && git pull && bash deploy/vm/setup.sh
```

## Sau setup — điền secrets

```bash
nano /home/ubuntu/tbb-reports.env
```

Dán 6 giá trị (lấy từ GitHub Secrets 2 repo):
- `NHANH_TOKEN`
- `GTALK_OA_TOKEN`
- `GTALK_CHANNEL_ID`
- `GTALK_CHANNEL_KHO` (đã có sẵn `2091128193108430848`)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`

Lưu & thoát (Ctrl+O, Enter, Ctrl+X).

## Test 1 báo cáo trước khi bật cron

```bash
bash /home/ubuntu/run-report.sh tbb-dashboard report_khochuyentiep.py
```

Nếu nhóm KHO nhận được tin → OK, bật cron:

```bash
crontab /home/ubuntu/tbb-dashboard/deploy/vm/crontab-tbb.txt
crontab -l    # verify cài xong
```

## Sau khi cron chạy ổn → tắt cron-job.org

Vào https://console.cron-job.org → disable toàn bộ job TBB (hoặc giữ như
fallback — GitHub Actions sẽ fail nhưng không spam vì mbff.ghn.vn timeout).

## Vận hành hằng ngày

| Việc | Lệnh |
|---|---|
| Xem cron | `crontab -l` |
| Xem log 20 báo cáo mới nhất | `ls -lt /home/ubuntu/tbb-reports-logs/ \| head -20` |
| Tail log 1 báo cáo | `tail -f /home/ubuntu/tbb-reports-logs/<file>.log` |
| Chạy thủ công 1 báo cáo | `bash /home/ubuntu/run-report.sh <repo> <script.py>` |
| Cập nhật code (tự động 6:00 hàng ngày) | `cd ~/tbb-dashboard && git pull` |
| Đổi NHANH_TOKEN mới | sửa `nano /home/ubuntu/tbb-reports.env` |

## Nếu NHANH_TOKEN hết hạn

```bash
nano /home/ubuntu/tbb-reports.env
# Sửa dòng NHANH_TOKEN=<token mới từ F12 nhanh.ghn.vn → localStorage.SESSION>
# Lưu & thoát — các cron sau sẽ auto-dùng token mới (không cần restart gì)
```

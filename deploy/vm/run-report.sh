#!/bin/bash
# Wrapper chạy 1 báo cáo: load .env rồi chạy Python script
# Usage: ./run-report.sh <repo> <script.py>
# Ví dụ:   ./run-report.sh tbb-dashboard report_khochuyentiep.py

REPO=$1
SCRIPT=$2
HOME_DIR=/home/ubuntu
LOG_DIR=$HOME_DIR/tbb-reports-logs
TS=$(date +%Y%m%d-%H%M%S)
LOG="$LOG_DIR/$TS-$REPO-$SCRIPT.log"

if [ -z "$REPO" ] || [ -z "$SCRIPT" ]; then
    echo "Usage: $0 <repo> <script.py>"
    exit 1
fi

if [ ! -f "$HOME_DIR/tbb-reports.env" ]; then
    echo "[ERR] Chưa có /home/ubuntu/tbb-reports.env — chạy setup.sh trước"
    exit 1
fi

# Load env (format KEY=VALUE)
set -a
source "$HOME_DIR/tbb-reports.env"
set +a

cd "$HOME_DIR/$REPO" || { echo "[ERR] Không có repo $REPO"; exit 1; }

echo "[$(date '+%H:%M:%S')] Chạy $REPO/$SCRIPT" | tee -a "$LOG"
python3 "$SCRIPT" >> "$LOG" 2>&1
RC=$?
echo "[$(date '+%H:%M:%S')] Exit code: $RC" | tee -a "$LOG"
exit $RC

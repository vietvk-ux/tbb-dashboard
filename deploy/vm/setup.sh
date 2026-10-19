#!/bin/bash
# Setup 19 báo cáo TBB chạy trên VM Oracle Cloud VN
# Chạy 1 lần: bash deploy/vm/setup.sh

set -e
HOME_DIR=/home/ubuntu
cd "$HOME_DIR"

echo "═══════════════════════════════════════════════════"
echo "📦 SETUP 19 BÁO CÁO TBB TRÊN VM"
echo "═══════════════════════════════════════════════════"

# 1. Clone repo thứ 2 nếu chưa có
if [ ! -d "$HOME_DIR/bao-cao-trip-tbb" ]; then
    echo "[1/5] Clone bao-cao-trip-tbb..."
    git clone https://github.com/vietvk-ux/bao-cao-trip-tbb.git "$HOME_DIR/bao-cao-trip-tbb"
else
    echo "[1/5] bao-cao-trip-tbb đã có, git pull..."
    cd "$HOME_DIR/bao-cao-trip-tbb" && git pull && cd "$HOME_DIR"
fi

# 2. Pull repo tbb-dashboard
if [ -d "$HOME_DIR/tbb-dashboard" ]; then
    echo "[2/5] Pull tbb-dashboard..."
    cd "$HOME_DIR/tbb-dashboard" && git pull && cd "$HOME_DIR"
fi

# 3. Cài dependencies
echo "[3/5] pip install requirements cho 2 repo..."
pip3 install -q -r "$HOME_DIR/bao-cao-trip-tbb/requirements.txt"
pip3 install -q -r "$HOME_DIR/tbb-dashboard/requirements.txt"

# 4. Copy run wrapper + .env template
echo "[4/5] Copy run-report.sh + .env template..."
cp "$HOME_DIR/tbb-dashboard/deploy/vm/run-report.sh" "$HOME_DIR/run-report.sh"
chmod +x "$HOME_DIR/run-report.sh"
if [ ! -f "$HOME_DIR/tbb-reports.env" ]; then
    cp "$HOME_DIR/tbb-dashboard/deploy/vm/env.template" "$HOME_DIR/tbb-reports.env"
    echo "   → Đã tạo /home/ubuntu/tbb-reports.env — CẦN ĐIỀN SECRETS trước khi crontab"
fi
mkdir -p "$HOME_DIR/tbb-reports-logs"

# 5. Xong
echo "[5/5] Xong setup. Tiếp theo:"
echo ""
echo "   📝 1. Điền secrets:  nano /home/ubuntu/tbb-reports.env"
echo "   ⏱  2. Cài crontab:   crontab /home/ubuntu/tbb-dashboard/deploy/vm/crontab-tbb.txt"
echo "   🧪 3. Test 1 báo cáo:"
echo "         bash /home/ubuntu/run-report.sh tbb-dashboard report_khochuyentiep.py"
echo "   📜 4. Xem log:       ls -lt /home/ubuntu/tbb-reports-logs/ | head"
echo ""
echo "═══════════════════════════════════════════════════"

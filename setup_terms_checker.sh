#!/bin/bash
#
# Google 規約変更チェッカー セットアップスクリプト
# macOS の launchd を使って毎朝10時に自動実行を設定する
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/check_terms.py"
PLIST_NAME="com.osamu.check-google-terms"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="${SCRIPT_DIR}/logs"

echo "======================================"
echo " Google 規約変更チェッカー セットアップ"
echo "======================================"
echo ""

# 必要なディレクトリを作成
mkdir -p "${SCRIPT_DIR}/terms_snapshots"
mkdir -p "${SCRIPT_DIR}/terms_reports"
mkdir -p "${LOG_DIR}"
mkdir -p "${HOME}/Library/LaunchAgents"

# Python スクリプトに実行権限を付与
chmod +x "${PYTHON_SCRIPT}"

# 既存の plist があればアンロード
if launchctl list "${PLIST_NAME}" &>/dev/null; then
    echo "既存のスケジュールをアンロード中..."
    launchctl unload "${PLIST_PATH}" 2>/dev/null || true
fi

# launchd plist を作成
cat > "${PLIST_PATH}" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${PYTHON_SCRIPT}</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/check_terms_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/check_terms_stderr.log</string>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>LANG</key>
        <string>ja_JP.UTF-8</string>
    </dict>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

echo "✅ launchd plist を作成しました: ${PLIST_PATH}"

# plist をロード
launchctl load "${PLIST_PATH}"
echo "✅ スケジュールをロードしました（毎日 10:00 に実行）"
echo ""

# 初回実行
echo "--------------------------------------"
echo " 初回チェックを実行中..."
echo "--------------------------------------"
echo ""
/usr/bin/python3 "${PYTHON_SCRIPT}"

echo ""
echo "======================================"
echo " セットアップ完了！"
echo "======================================"
echo ""
echo "📁 スナップショット: ${SCRIPT_DIR}/terms_snapshots/"
echo "📄 レポート:         ${SCRIPT_DIR}/terms_reports/"
echo "📝 ログ:             ${LOG_DIR}/"
echo "⏰ スケジュール:     毎日 10:00 に自動実行"
echo ""
echo "【手動実行】"
echo "  python3 ${PYTHON_SCRIPT}"
echo ""
echo "【スケジュール確認】"
echo "  launchctl list | grep check-google-terms"
echo ""
echo "【スケジュール停止】"
echo "  launchctl unload ${PLIST_PATH}"
echo ""
echo "【スケジュール再開】"
echo "  launchctl load ${PLIST_PATH}"

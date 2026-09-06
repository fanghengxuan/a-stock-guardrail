#!/bin/bash
# ============================================================
# 自动获取东方财富 Cookie（macOS）
# 原理：AppleScript 在 Chrome 中打开东财页面，读取 document.cookie
# 前提：Chrome 已登录东财 + 已开启"允许 Apple 事件中的 JavaScript"
#       (Chrome 菜单栏 → 查看 → 开发者 → 勾选)
# 用法：./fetch_em_cookie.sh
# ============================================================
set -euo pipefail

COOKIE_FILE="$(dirname "$0")/eastmoney_cookie.txt"
EM_URL="https://quote.eastmoney.com/center/gridlist.html#hs_a_board"

echo "🍪 自动获取东方财富 Cookie..."

COOKIE=$(osascript -e "
tell application \"Google Chrome\"
  tell front window
    set newTab to make new tab with properties {URL:\"$EM_URL\"}
  end tell
  delay 5
  set cookieStr to execute active tab of front window javascript \"document.cookie\"
  close active tab of front window
  return cookieStr
end tell
" 2>&1)

if [ -n "$COOKIE" ] && [ ${#COOKIE} -gt 50 ]; then
  echo "$COOKIE" > "$COOKIE_FILE"
  echo "✅ Cookie 已写入 $COOKIE_FILE (${#COOKIE} 字符)"
  echo "   包含: $(echo "$COOKIE" | tr ';' '\n' | sed 's/=.*//' | sed 's/^ *//' | head -5 | tr '\n' ', ')..."
  echo ""
  echo "🔄 重启后端即可生效: cd .. && ./start.command"
else
  echo "❌ 获取失败。请确保："
  echo "   1. Chrome 已打开且已登录东方财富网"
  echo "   2. Chrome 已开启: 查看 → 开发者 → 允许 Apple 事件中的 JavaScript"
  echo "   3. 系统已授权终端控制 Chrome (系统设置 → 隐私 → 自动化)"
  exit 1
fi

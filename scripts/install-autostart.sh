#!/usr/bin/env bash
# 树莓派开机自启安装脚本
#
# 用法（在树莓派上执行）：
#   1) 把整个项目目录拷到树莓派（例如 ~/qrcode-tool/）
#   2) cd ~/qrcode-tool
#   3) bash scripts/install-autostart.sh
#
# 它会做三件事：
#   1) 把启动脚本放到 ~/.local/bin/qrcode-tool.sh
#   2) 把 LXDE 桌面自启项放到 ~/.config/autostart/qrcode-tool.desktop
#   3) 下次重启登录桌面后，应用自动启动
#
# 立即前台测试：
#   bash ~/.local/bin/qrcode-tool.sh
#
# 立即后台测试：
#   nohup bash ~/.local/bin/qrcode-tool.sh >/dev/null 2>&1 &
#
# 卸载：
#   rm ~/.config/autostart/qrcode-tool.desktop
#   rm ~/.local/bin/qrcode-tool.sh

set -euo pipefail

# 项目目录 = 脚本所在目录的上一级
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 选择 venv 下的 python：优先 unix 路径，兼容 windows 调试
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PY="$PROJECT_DIR/.venv/bin/python"
elif [[ -x "$PROJECT_DIR/.venv/Scripts/python.exe" ]]; then
    PY="$PROJECT_DIR/.venv/Scripts/python.exe"
else
    echo "❌ 未找到虚拟环境 Python，请先在 $PROJECT_DIR 创建 venv 并安装依赖"
    echo "   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

MAIN="$PROJECT_DIR/main.py"
if [[ ! -f "$MAIN" ]]; then
    echo "❌ 未找到主程序 $MAIN"
    exit 1
fi

# 显示环境信息（便于排查）
echo "项目目录: $PROJECT_DIR"
echo "Python:   $PY"
echo "主程序:   $MAIN"

# 1) 启动脚本
START_SH="$HOME/.local/bin/qrcode-tool.sh"
LOG="/tmp/qrcode-tool.log"
mkdir -p "$(dirname "$START_SH")"

cat > "$START_SH" <<EOF
#!/usr/bin/env bash
# 二维码集合工具启动脚本（由 install-autostart.sh 生成）
# 等待图形会话就绪，避免开机时 DISPLAY 还没好
for i in 1 2 3 4 5 6 7 8 9 10; do
    if [ -n "\${DISPLAY:-}" ] || [ -n "\${WAYLAND_DISPLAY:-}" ]; then
        break
    fi
    sleep 1
done
exec "$PY" "$MAIN" >> "$LOG" 2>&1
EOF
chmod +x "$START_SH"

# 2) 桌面自启项（LXDE / PIXEL / Xfce 等兼容 freedesktop 规范的环境都认）
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
DESKTOP_FILE="$AUTOSTART_DIR/qrcode-tool.desktop"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=二维码集合工具
Comment=扫码枪扫码生成集合二维码
Exec=$START_SH
Icon=applications-office
Terminal=false
X-GNOME-Autostart-enabled=true
Categories=Utility;
EOF
chmod 644 "$DESKTOP_FILE"

echo ""
echo "✅ 自启配置完成"
echo "   启动脚本:   $START_SH"
echo "   桌面自启:   $DESKTOP_FILE"
echo "   运行日志:   $LOG"
echo ""
echo "▶ 立即测试（不开机）："
echo "   bash $START_SH"
echo ""
echo "▶ 重启验证："
echo "   sudo reboot"
echo "   登录桌面后会看到窗口；日志在 $LOG"

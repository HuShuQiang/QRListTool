# 树莓派部署

## 一次性部署

```bash
# 1. 把整个项目目录拷贝到树莓派，例如放到 ~/qrcode-tool/
scp -r qrcode-tool pi@raspberrypi.local:~/

# 2. SSH 登录
ssh pi@raspberrypi.local

# 3. 安装依赖（首次需要）
cd ~/qrcode-tool
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 配置开机自启
bash scripts/install-autostart.sh
```

## 卸载自启

```bash
rm ~/.config/autostart/qrcode-tool.desktop
rm ~/.local/bin/qrcode-tool.sh
```

## 立即运行（不重启）

```bash
bash ~/.local/bin/qrcode-tool.sh
```

## 查看日志

```bash
tail -f /tmp/qrcode-tool.log
```

## 注意事项

- **必须登录桌面**：应用是 PyQt6 GUI，没有图形会话会闪退。树莓派 OS 默认是 PIXEL 桌面，需在「Raspberry Pi Configuration → Auto login」开启自动登录。
- **无桌面场景**：要 SSH/远程跑，请用 X11 forwarding 或 VNC；纯 CLI 启动会因缺少 `$DISPLAY` 失败。
- **扫码枪**：把扫码枪插到 USB，按下扫码键应能在光标所在的输入框中"输入 + 回车"——确保自动登录桌面后光标能落到本应用输入框，或者通过「设置」配置扫码枪的"末尾回车"行为。

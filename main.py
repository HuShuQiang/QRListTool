"""基于 PyQt6 的二维码集合工具：扫码枪模式。

- 单输入框接收扫码枪输入（每扫一次回车，作为一段）
- 缓冲到设定的"扫码数量"自动生成集合二维码
- 未达数量时也可以手动点生成按钮
- 像素大小/边框/纠错级别放在「设置」菜单里
- 可选：正则表达式校验，匹配失败的段不入缓冲
- 同批次防重复，重复时中央红色 banner 提示
- 已扫描内容列表 + 二维码预览左右并排
- Web API POST 工具：URL + JSON body 异步发送，显示响应
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import qrcode
from PIL import Image
from PyQt6.QtCore import QObject, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QImage, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)


class QRGenerator:
    """二维码生成器：把文本转成 PIL Image。"""

    ERROR_CORRECTION = {
        "L (低 ~7%)": qrcode.constants.ERROR_CORRECT_L,
        "M (中 ~15%)": qrcode.constants.ERROR_CORRECT_M,
        "Q (高 ~25%)": qrcode.constants.ERROR_CORRECT_Q,
        "H (最高 ~30%)": qrcode.constants.ERROR_CORRECT_H,
    }

    def __init__(self, box_size: int = 10, border: int = 4,
                 error_correction: str = "M (中 ~15%)",
                 fill_color: str = "black", back_color: str = "white") -> None:
        self.box_size = box_size
        self.border = border
        self.error_correction = error_correction
        self.fill_color = fill_color
        self.back_color = back_color

    def make(self, data: str) -> Image.Image:
        if not data:
            raise ValueError("内容不能为空")
        qr = qrcode.QRCode(
            version=None,
            error_correction=self.ERROR_CORRECTION[self.error_correction],
            box_size=self.box_size,
            border=self.border,
        )
        qr.add_data(data)
        qr.make(fit=True)
        return qr.make_image(fill_color=self.fill_color, back_color=self.back_color).convert("RGB")


class SettingsDialog(QDialog):
    """参数设置：像素大小、边框、纠错级别、可选正则校验。"""

    def __init__(self, parent: QWidget, box_size: int, border: int, ec_text: str,
                 regex_enabled: bool, regex_pattern: str,
                 station_check_enabled: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(560, 520)

        form = QFormLayout(self)

        self._box_size = QSpinBox()
        self._box_size.setRange(2, 40)
        self._box_size.setValue(box_size)
        form.addRow("像素大小:", self._box_size)

        self._border = QSpinBox()
        self._border.setRange(0, 10)
        self._border.setValue(border)
        form.addRow("边框:", self._border)

        self._ec = QComboBox()
        self._ec.addItems(list(QRGenerator.ERROR_CORRECTION.keys()))
        self._ec.setCurrentText(ec_text)
        form.addRow("纠错级别:", self._ec)

        # 正则校验
        self._regex_enabled = QCheckBox("启用正则校验（不匹配则不入缓冲）")
        self._regex_enabled.setChecked(regex_enabled)
        form.addRow(self._regex_enabled)

        self._regex = QLineEdit()
        self._regex.setPlaceholderText(r"例如 ^[A-Z]\d{6}$  或  ^ORD-\d{4}-\d+$")
        self._regex.setText(regex_pattern)
        form.addRow("正则表达式:", self._regex)

        # 工站检查
        self._station_check = QCheckBox("启用工站检查（POST 验证漏液/老化）")
        self._station_check.setChecked(station_check_enabled)
        form.addRow(self._station_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_accept(self) -> None:
        # 启用正则时校验语法
        if self._regex_enabled.isChecked():
            try:
                re.compile(self._regex.text())
            except re.error as e:
                QMessageBox.warning(self, "正则无效", f"正则表达式语法错误：\n{e}")
                return
        self.accept()

    def values(self) -> tuple[int, int, str, bool, str, bool]:
        return (
            self._box_size.value(),
            self._border.value(),
            self._ec.currentText(),
            self._regex_enabled.isChecked(),
            self._regex.text(),
            self._station_check.isChecked(),
        )


class ApiWorker(QObject):
    """在子线程中发起 POST 请求，避免阻塞 UI。"""

    finished = pyqtSignal(int, str)  # status_code, body
    failed = pyqtSignal(str)         # error message

    def __init__(self, url: str, body: str, timeout: int = 30) -> None:
        super().__init__()
        self.url = url
        self.body = body
        self.timeout = timeout

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self.url,
                data=self.body.encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                self.finished.emit(resp.status, raw)
        except urllib.error.HTTPError as e:
            # 4xx/5xx 也算"完成"，把状态码和 body 回传
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            self.finished.emit(e.code, raw)
        except urllib.error.URLError as e:
            self.failed.emit(f"网络错误：{e.reason}")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


# 工站状态查询 URL（固化），{sn} 会被替换为扫码内容
STATION_STATUS_URL = (
    "http://192.168.8.8/box_cms_v3/mes_v1/getDutTestFlowResult.php"
    "?model=T8FJ&dut_number_name=sn&dut_number_value={sn}"
)


def query_station_status(sn: str, timeout: int = 30) -> dict:
    """查询工站状态：对固定 URL 发 POST 请求，{sn} 替换为传入值。

    返回值同 WebApi.post_json：
    - 成功：{"ok": True, "status": int, "data": <dict or str>, "raw": str}
    - 失败：{"ok": False, "error": str, "status": None 或 int}
    """
    url = STATION_STATUS_URL.replace("{sn}", sn)
    return WebApi.post_json(url, "", timeout=timeout)


class WebApi:
    """Web API POST 请求类（无界面）：输入 URL + JSON 字符串，返回请求结果。

    用法：
        result = WebApi.post_json(
            "http://example.com/api",
            '{"key": "value"}'
        )
        if result["ok"]:
            print("状态码:", result["status"], "数据:", result["data"])
        else:
            print("错误:", result["error"])
    """

    @staticmethod
    def post_json(url: str, body: str, timeout: int = 30) -> dict:
        """同步发送 JSON POST 请求，返回 dict：
        - 成功（2xx-5xx）：{"ok": True, "status": int, "data": <parsed json or raw str>}
        - 失败（网络/超时/解析）：{"ok": False, "error": str, "status": <int 或 None>}

        body 可为空字符串（表示无 body）；非空时必须是合法 JSON。
        """
        # 校验 URL
        if not url:
            return {"ok": False, "error": "URL 为空", "status": None}
        if not (url.startswith("http://") or url.startswith("https://")):
            return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头", "status": None}
        # 校验 body
        body_bytes = b""
        if body and body.strip():
            try:
                json.loads(body)  # 校验合法性
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"body 不是合法 JSON：{e}", "status": None}
            body_bytes = body.encode("utf-8")
        # 发请求
        try:
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status": resp.status,
                    "data": _try_parse_json(raw),
                    "raw": raw,
                }
        except urllib.error.HTTPError as e:
            # 4xx/5xx 也算"完成"
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            return {
                "ok": True,
                "status": e.code,
                "data": _try_parse_json(raw),
                "raw": raw,
            }
        except urllib.error.URLError as e:
            return {"ok": False, "error": f"网络错误：{e.reason}", "status": None}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "status": None}


def _try_parse_json(text: str):
    """尝试解析 JSON 字符串；失败返回原文本。"""
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return text


class QRWindow(QMainWindow):
    """主窗口：扫码输入 + 数量控制 + 大预览。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("二维码集合工具")
        self.resize(800, 600)

        # 扫码缓冲
        self._buffer: list[str] = []
        # 同批次去重：flush_and_generate 后清空
        self._seen: set[str] = set()
        # 生成参数（默认值；可在「设置」里改）
        self._box_size_val = 10
        self._border_val = 4
        self._ec_val = "M (中 ~15%)"
        # 正则校验（默认启用，过滤 A51114260827 开头 + 6 位数字）
        self._regex_enabled = True
        self._regex_pattern = r"^A51114260827\d{6}$"
        self._regex_re: re.Pattern | None = None
        # 工站检查（默认启用；关闭后扫码不再 POST 工站接口）
        self._station_check_enabled = True
        # 当前生成的图
        self._current_image: Image.Image | None = None

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        self._load_config()
        self._update_counter()

    # ---- UI ----
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 第 1 行：扫码数量 + 自动生成
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(QLabel("扫码数量:"))
        self._count = QSpinBox()
        self._count.setRange(1, 999)
        self._count.setValue(72)
        self._count.setMinimumHeight(40)
        self._count.setMinimumWidth(120)
        self._count.valueChanged.connect(self._on_count_changed)
        row1.addWidget(self._count)

        self._auto = QCheckBox("达到数量自动生成")
        self._auto.setChecked(True)
        row1.addWidget(self._auto)
        row1.addStretch(1)

        # 右上：上一次扫描内容
        self._last_scan_label = QLabel("")
        self._last_scan_label.setStyleSheet(
            "QLabel { color: #444; background: #f0f4ff; padding: 6px 12px;"
            " border: 1px solid #b8c6e0; border-radius: 4px; }"
        )
        self._last_scan_label.setMinimumWidth(160)
        self._last_scan_label.setMaximumWidth(360)
        self._last_scan_label.setToolTip("上一次成功接收的扫描内容")
        self._last_scan_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._last_scan_label.hide()  # 无扫描时完全不显示
        row1.addWidget(QLabel("上次扫描:"))
        row1.addWidget(self._last_scan_label)
        root.addLayout(row1)

        # 第 2 行：扫码输入框
        self._input = QLineEdit()
        self._input.setPlaceholderText("扫码/输入后回车")
        self._input.setMinimumHeight(44)
        self._input.returnPressed.connect(self._on_scan)
        self._input.textChanged.connect(self._on_input_changed)
        root.addWidget(self._input)

        # 第 3 行：生成按钮
        self._btn_gen = QPushButton("生成集合二维码")
        self._btn_gen.setMinimumHeight(48)
        self._btn_gen.clicked.connect(self._on_manual_gen)
        root.addWidget(self._btn_gen)

        # 警示 banner（重复内容时居中显示，红色加粗）
        self._alert_label = QLabel()
        self._alert_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._alert_label.setMinimumHeight(40)
        self._alert_label.setStyleSheet(
            "QLabel { color: #c0392b; background: #fff0f0;"
            " border: 2px solid #e74c3c; border-radius: 6px;"
            " font-weight: bold; padding: 8px; }"
        )
        self._alert_label.hide()
        root.addWidget(self._alert_label)
        self._alert_timer = QTimer(self)
        self._alert_timer.setSingleShot(True)
        self._alert_timer.timeout.connect(lambda: self._alert_label.hide())

        # 进度 / 状态
        self._counter_label = QLabel()
        self._counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._counter_label)

        # 底部 HBox：左侧已扫描列表 + 右侧二维码预览
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        # 左侧：已扫描内容列表
        list_box = QVBoxLayout()
        list_box.setSpacing(4)
        list_title = QLabel("已扫描内容")
        list_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        list_box.addWidget(list_title)
        self._scan_list = QListWidget()
        self._scan_list.setMinimumWidth(220)
        self._scan_list.setMaximumWidth(360)
        self._scan_list.setAlternatingRowColors(True)
        list_box.addWidget(self._scan_list, 1)
        bottom.addLayout(list_box, 0)

        # 右侧：二维码预览
        self._preview = QLabel("扫码后将在此显示集合二维码")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(420)
        self._preview.setStyleSheet(
            "QLabel { background: #fafafa; border: 1px dashed #bbb; color: #888; }"
        )
        bottom.addWidget(self._preview, 1)
        root.addLayout(bottom, 1)

        # 焦点常驻输入框，方便连续扫码
        self._input.setFocus()

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        # 设置（顶级 action，点击直接打开参数设置弹窗）
        act_settings = QAction("设置(&S)", self)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)

        # 退出（顶级 action，直接显示在菜单栏）
        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        menu.addAction(act_quit)

    def _build_statusbar(self) -> None:
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")

    # ---- 扫码缓冲 ----
    def _show_last_scan(self, text: str) -> None:
        """更新右上角"上一次扫描"标签；过长截断。空文本则隐藏。"""
        if not text:
            self._last_scan_label.hide()
            return
        max_len = 40
        display = text if len(text) <= max_len else text[: max_len - 1] + "…"
        self._last_scan_label.setText(display)
        self._last_scan_label.setToolTip(text)
        self._last_scan_label.show()


    def _show_alert(self, text: str, duration_ms: int = 3000) -> None:
        """显示中央红色警示 banner，duration_ms 后自动隐藏。"""
        self._alert_label.setText(text)
        self._alert_label.show()
        self._alert_timer.start(duration_ms)

    def _validate_part(self, part: str) -> tuple[bool, str]:
        """对一段扫码做 POST 验证（工站状态查询）。

        返回 (是否通过, 失败原因)。关闭工站检查时直接放行。

        业务规则（漏液测试 = stations.power_bank_voc，老化测试 = aging_test）：
        1) 漏液测试 == "PASS"：已测试，无法重复测试
        2) 漏液测试 ∈ {NONE, NG} 且 老化测试 != "PASS"：老化测试未通过！无法进行漏液测试
        3) 漏液测试 ∈ {NONE, NG} 且 老化测试 == "PASS"：通过
        4) 其它：未知状态，按不通过处理
        """
        if not self._station_check_enabled:
            return True, ""

        result = query_station_status(part)
        if not result["ok"]:
            return False, f"请求失败：{result['error']}"

        data = result.get("data")
        if not isinstance(data, dict):
            return False, f"响应不是 JSON 对象（status={result.get('status')}）"

        station = data.get("stations") or {}
        pb_voc = station.get("power_bank_voc")
        aging = station.get("aging_test")

        if pb_voc == "PASS":
            return False, "已测试，无法重复测试"
        if pb_voc in ("NONE", "NG") and aging != "PASS":
            return False, "老化测试未通过！无法进行漏液测试"
        if pb_voc in ("NONE", "NG") and aging == "PASS":
            return True, ""
        return False, f"未知状态：voc={pb_voc!r}, aging={aging!r}"

    def _on_input_changed(self, _text: str) -> None:
        """输入框内容变化：二维码和列表同步清空。"""
        if self._current_image is not None:
            self._current_image = None
            self._preview.clear()
            self._preview.setText("扫码后将在此显示集合二维码")
            self._preview.setStyleSheet(
                "QLabel { background: #fafafa; border: 1px dashed #bbb; color: #888; }"
            )
            if self._scan_list.count() > 0:
                self._scan_list.clear()
                self._buffer.clear()
                self._seen.clear()


    def _on_scan(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        # 每次回车 = 一段数据；不再按空格切分（保留段内空格）
        parts = [text]

        # 正则校验：把不匹配的段剔除，并在状态栏提示
        accepted: list[str] = []
        rejected: list[str] = []
        if self._regex_enabled and self._regex_re is not None:
            for p in parts:
                if self._regex_re.fullmatch(p):
                    accepted.append(p)
                else:
                    rejected.append(p)
        else:
            accepted = parts

        # 同批次去重：拒绝重复段
        deduped: list[str] = []
        dups: list[str] = []
        for p in accepted:
            if p in self._seen:
                dups.append(p)
            else:
                deduped.append(p)

        # 扫码后 POST 验证（URL 模板非空才启用；同步调用会卡 UI 几十 ms~30s）
        validated: list[str] = []
        for p in deduped:
            ok, reason = self._validate_part(p)
            if ok:
                validated.append(p)
            else:
                self._show_alert(f"⚠ 扫码 {p} 未通过：{reason}", 3000)
                self.statusBar().showMessage(f"扫码 {p} 验证未通过：{reason}", 5000)
        deduped = validated

        if deduped:
            self._buffer.extend(deduped)
            self._seen.update(deduped)
            # 列表实时累积：每扫一段就 addItem
            for item in deduped:
                li = QListWidgetItem(item)
                li.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._scan_list.addItem(li)
            # 自动滚动到最新一行
            self._scan_list.scrollToBottom()
            # 右上角显示"上一次成功接收"的扫描内容
            self._show_last_scan(deduped[-1])
        # 清空输入框不应当触发 textChanged → 清掉刚加入的列表项
        self._input.blockSignals(True)
        self._input.clear()
        self._input.blockSignals(False)
        self._update_counter()

        if dups:
            preview = ", ".join(repr(x) for x in dups[:3])
            more = f" 等 {len(dups)} 段" if len(dups) > 3 else ""
            self._show_alert(f"⚠ 重复内容，已忽略：{preview}{more}", 3000)
            self.statusBar().showMessage(
                f"重复内容，已忽略：{preview}{more}", 5000
            )
        elif rejected:
            preview = ", ".join(repr(x) for x in rejected[:3])
            more = f" 等 {len(rejected)} 段" if len(rejected) > 3 else ""
            self._show_alert(f"⚠ 正则不匹配，已忽略：{preview}{more}", 3000)
            self.statusBar().showMessage(
                f"正则不匹配，已忽略：{preview}{more}", 5000
            )
        elif self._regex_enabled and self._regex_re is not None:
            self.statusBar().showMessage("已通过正则校验", 2000)

        # 自动生成：缓冲达到设定数量
        target = self._count.value()
        if self._auto.isChecked() and len(self._buffer) >= target:
            self._flush_and_generate(auto=True)

    def _on_manual_gen(self) -> None:
        if not self._buffer:
            QMessageBox.information(self, "提示", "缓冲区为空，请先扫码或输入内容")
            return
        self._flush_and_generate(auto=False)

    def _flush_and_generate(self, auto: bool) -> None:
        parts = list(self._buffer)
        self._buffer.clear()
        # 跨批次防重：seen 保留，用户无法重复扫已入过的内容
        # list 里的扫描记录也保留，等用户键入新内容时再统一清空
        self._update_counter()
        self._on_generate(parts, auto_triggered=auto)

    def _clear_buffer(self) -> None:
        self._buffer.clear()
        self._seen.clear()  # 手动清空：包括去重集合也一并清空
        self._input.blockSignals(True)
        self._input.clear()
        self._input.blockSignals(False)
        self._scan_list.clear()
        self._update_counter()
        self.statusBar().showMessage("已清空缓冲区", 3000)

    def _update_counter(self) -> None:
        n = len(self._buffer)
        target = self._count.value()
        self._counter_label.setText(f"已扫描 {n} / {target} 次")
        self._btn_gen.setEnabled(n > 0)

    def _on_count_changed(self, _value: int) -> None:
        """扫码数量变化：刷新计数 + 自动持久化到 config。"""
        self._update_counter()
        self._save_config()

    # ---- 生成 / 预览 / 保存 ----
    def _on_generate(self, parts: list[str], auto_triggered: bool = False) -> None:
        if not parts:
            return
        # 多段用 ; 拼接为一个紧凑字符串写入二维码（不带序号、不换行）
        merged = ",".join(parts)

        try:
            gen = QRGenerator(
                box_size=self._box_size_val,
                border=self._border_val,
                error_correction=self._ec_val,
            )
            img = gen.make(merged)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", f"无法生成二维码：\n{exc}")
            return

        self._current_image = img
        self._show_image(img)
        # 列表已经实时累积所有扫描内容，生成时不需要再动
        mode = "自动" if auto_triggered else "手动"
        self.statusBar().showMessage(
            f"{mode}生成 | 段数 {len(parts)} | 内容长度 {len(merged)} 字符 | {img.size[0]}×{img.size[1]} px",
            5000,
        )

    def _show_image(self, img: Image.Image) -> None:
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.size[0], img.size[1], img.size[0] * 3, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(
            self._preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)
        self._preview.setStyleSheet("QLabel { background: #fff; border: 1px solid #ddd; }")

    def _on_save(self) -> None:
        if self._current_image is None:
            QMessageBox.information(self, "提示", "暂无可保存的二维码")
            return
        default_name = "qrcode_collection.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存二维码", default_name,
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;BMP 图片 (*.bmp)"
        )
        if not path:
            return
        try:
            self._current_image.save(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.statusBar().showMessage(f"已保存到 {path}", 5000)

    # ---- 配置持久化 ----
    def _config_path(self) -> Path:
        """返回配置文件路径：QStandardPaths.AppConfigLocation/qrcode-tool/config.json"""
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )
        if not base:
            base = str(Path.home() / ".config")
        d = Path(base) / "qrcode-tool"
        d.mkdir(parents=True, exist_ok=True)
        return d / "config.json"

    def _load_config(self) -> None:
        """从配置文件读取设置；不存在或解析失败时使用默认值。"""
        try:
            with open(self._config_path(), "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return

        if not isinstance(cfg, dict):
            return

        # 扫码数量（默认 72，存到 config 让用户修改持久化）
        cnt = cfg.get("count")
        if isinstance(cnt, int) and 1 <= cnt <= 999:
            # blockSignals 防止加载时触发 _on_count_changed → 又写回 config
            self._count.blockSignals(True)
            self._count.setValue(cnt)
            self._count.blockSignals(False)

        # 像素大小
        bs = cfg.get("box_size")
        if isinstance(bs, int) and 2 <= bs <= 40:
            self._box_size_val = bs
        # 边框
        bd = cfg.get("border")
        if isinstance(bd, int) and 0 <= bd <= 10:
            self._border_val = bd
        # 纠错级别
        ec = cfg.get("error_correction")
        if isinstance(ec, str) and ec in QRGenerator.ERROR_CORRECTION:
            self._ec_val = ec
        # 正则
        if isinstance(cfg.get("regex_enabled"), bool):
            self._regex_enabled = cfg["regex_enabled"]
        rp = cfg.get("regex_pattern")
        if isinstance(rp, str):
            self._regex_pattern = rp
        # 预编译
        self._regex_re = None
        if self._regex_enabled and self._regex_pattern:
            try:
                self._regex_re = re.compile(self._regex_pattern)
            except re.error:
                self._regex_enabled = False
                self._regex_re = None

        # 工站检查
        if isinstance(cfg.get("station_check_enabled"), bool):
            self._station_check_enabled = cfg["station_check_enabled"]

    def _save_config(self) -> None:
        """把当前设置写入配置文件。"""
        cfg = {
            "box_size": self._box_size_val,
            "border": self._border_val,
            "error_correction": self._ec_val,
            "count": self._count.value(),
            "regex_enabled": self._regex_enabled,
            "regex_pattern": self._regex_pattern,
            "station_check_enabled": self._station_check_enabled,
        }
        try:
            with open(self._config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.statusBar().showMessage(f"保存配置失败：{exc}", 5000)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(
            self,
            self._box_size_val,
            self._border_val,
            self._ec_val,
            self._regex_enabled,
            self._regex_pattern,
            self._station_check_enabled,
        )
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            (self._box_size_val, self._border_val, self._ec_val,
             self._regex_enabled, self._regex_pattern,
             self._station_check_enabled) = dlg.values()
            # 预编译正则；启用且非空才编译
            self._regex_re = None
            if self._regex_enabled and self._regex_pattern:
                try:
                    self._regex_re = re.compile(self._regex_pattern)
                except re.error:
                    self._regex_enabled = False
                    self._regex_re = None
            self._save_config()
            tag = "正则开启" if self._regex_enabled and self._regex_re else "正则关闭"
            self.statusBar().showMessage(f"设置已更新（{tag}）", 3000)

    def _on_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            "二维码集合工具\n\n基于 PyQt6 + qrcode + Pillow 构建。\n"
            "扫码枪扫描后自动缓冲，达设定数量时生成集合二维码；\n"
            "参数（像素 / 边框 / 纠错）在「设置」菜单中调整。\n"
            "快捷键：Ctrl+S 保存 · Ctrl+L 清空 · Ctrl+, 设置"
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._current_image is not None:
            self._show_image(self._current_image)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("二维码集合工具")
    # 树莓派友好：全局加大加粗（触控/远距离可读）
    base_font = QFont()
    base_font.setPointSize(14)
    base_font.setWeight(QFont.Weight.Bold)
    app.setFont(base_font)
    # 全局 QSS：
    # - 加粗全部 UI（部分 Windows 主题不继承 app font weight）
    # - 加大 QCheckBox 勾选框（Qt 默认 indicator 不随 font-size 缩放）
    app.setStyleSheet(
        "QWidget, QMenuBar, QMenuBar::item, QMenu, QMenu::item, "
        "QPushButton, QLabel, QLineEdit, QSpinBox, QComboBox, QListWidget, "
        "QDialog, QStatusBar, QToolTip { font-weight: bold; }"
        "QCheckBox { spacing: 10px; padding: 4px 0; }"
        "QCheckBox::indicator { width: 22px; height: 22px; }"
    )
    win = QRWindow()
    win.resize(800, 600)
    # 跨平台显示策略：Linux 树莓派全屏（嵌屏/无人值守），Windows 正常窗口
    if sys.platform.startswith("linux"):
        win.showFullScreen()
    else:
        win.show()
    # 窗口显示后把光标自动落到输入框，方便立即扫码
    QTimer.singleShot(0, win._input.setFocus)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

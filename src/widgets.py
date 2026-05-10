"""
自定义控件模块：折叠面板、自适应下拉框、控制台面板、命令预览对话框。
"""
import os
import sys
import json

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QDialog, QApplication, QFileDialog, QMessageBox, QSizePolicy, QListWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from .platform import save_script
from .config import MSG, CONSOLE_COLORS


# ═══════════════════════════════════════════════
#  CollapsibleSection
# ═══════════════════════════════════════════════

class CollapsibleSection(QWidget):
    """可点击标题栏展开/收纳的折叠面板。箭头使用 Unicode 字符，清晰无渲染瑕疵。"""

    def __init__(self, title: str, content: QWidget, section_key: str = "",
                 collapsed: bool = False, parent=None):
        super().__init__(parent)
        self._key       = section_key
        self._collapsed = collapsed
        self._content   = content

        # 标题栏
        header = QWidget()
        header.setObjectName("collapsibleHeader")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 2, 6, 2)
        h_layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("collapsibleTitle")
        self._title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._arrow_label = QLabel()
        self._arrow_label.setObjectName("collapsibleArrow")
        self._arrow_label.setFixedWidth(20)
        self._arrow_label.setMinimumWidth(20)
        self._arrow_label.setAlignment(Qt.AlignCenter)
        self._arrow_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        h_layout.addWidget(self._title_label)
        h_layout.addWidget(self._arrow_label, 0, Qt.AlignRight | Qt.AlignVCenter)

        header.setCursor(Qt.PointingHandCursor)
        header.mousePressEvent = lambda e: self._toggle()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._content)

        self.set_collapsed(collapsed, update_header=True)

    def _toggle(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool, update_header: bool = True):
        self._collapsed = collapsed
        self._content.setVisible(not collapsed)
        if update_header:
            self._arrow_label.setText("▶" if collapsed else "▼")
            # ▶ 字形偏窄，折叠态增大补正
            f = self._arrow_label.font()
            f.setPointSize(12 if collapsed else 9)
            self._arrow_label.setFont(f)


# ═══════════════════════════════════════════════
#  AdaptiveComboBox
# ═══════════════════════════════════════════════

class NoWheelSpinBox(QSpinBox):
    """QSpinBox 子类：禁用鼠标滚轮更改数值。"""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox 子类：禁用鼠标滚轮更改数值。"""
    def wheelEvent(self, event):
        event.ignore()


class AdaptiveComboBox(QComboBox):
    """下拉列表固定上限 + 自适应窗口比例，避免顶满窗口。
    鼠标滚轮仅在弹出列表内生效，否则忽略。"""

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

    def showPopup(self):
        max_items = 8  # 硬上限
        window = self.window()
        if window:
            max_h = int(window.height() * 0.28)
        else:
            max_h = 200
        item_h = 24
        visible_count = min(max_items, max(3, max_h // item_h))
        self.setMaxVisibleItems(visible_count)
        super().showPopup()
        # 弹出后强制限制弹出窗口高度并重定位
        view = self.view()
        if view:
            popup = view.parentWidget()
            if popup:
                desired_h = visible_count * item_h + 4
                popup.setMaximumHeight(desired_h)
                popup.resize(popup.width(), desired_h)
                combo_rect = self.rect()
                bottom_left = self.mapToGlobal(combo_rect.bottomLeft())
                popup.move(bottom_left.x(), bottom_left.y())


# ═══════════════════════════════════════════════
#  ConsoleWidget
# ═══════════════════════════════════════════════

class ConsoleWidget(QWidget):
    """可停靠的控制台面板，显示进程输出并支持 CLI 交互输入。"""
    input_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._interactive = False
        self._theme = "dark"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 9))
        layout.addWidget(self.output, 1)

        self.input_row = QWidget()
        ir = QHBoxLayout(self.input_row)
        ir.setContentsMargins(0, 0, 0, 0)
        ir.setSpacing(3)

        self.clear_btn = QPushButton(MSG.get("clear", "清空"))
        self.clear_btn.clicked.connect(self.output.clear)
        ir.addWidget(self.clear_btn)

        self.export_btn = QPushButton(MSG.get("export_log", "导出日志"))
        self.export_btn.clicked.connect(self._export_log)
        ir.addWidget(self.export_btn)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(MSG.get("console_input_placeholder", "输入消息后回车发送..."))
        self.input_edit.returnPressed.connect(self._send_input)
        ir.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton(MSG.get("send", "发送"))
        self.send_btn.clicked.connect(self._send_input)
        ir.addWidget(self.send_btn)

        layout.addWidget(self.input_row)

        self._apply_theme()

    def _export_log(self):
        """导出控制台日志到文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self,
            MSG.get("export_log_title", "导出日志"),
            os.path.expanduser("~"),
            "Text (*.txt);;All (*)",
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.output.toPlainText())
                self.append_output(MSG.get("log_exported", "✅ 日志已导出").replace("{path}", path), "green")
            except Exception as e:
                self.append_output(
                    MSG.get("log_export_failed", "❌ 导出失败: {err}").replace("{err}", str(e)), "red")

    def _send_input(self):
        text = self.input_edit.text()
        if text:
            self.input_signal.emit(text)
            self.input_edit.clear()

    def set_interactive(self, enabled: bool):
        self._interactive = enabled
        self.input_row.setVisible(enabled)
        if enabled:
            self.input_edit.setFocus()

    def set_theme(self, theme: str):
        self._theme = theme
        self._apply_theme()

    def _apply_theme(self):
        colors = CONSOLE_COLORS.get(self._theme, CONSOLE_COLORS.get("dark", {}))
        self._console_colors = colors

        if self._theme == "light":
            self.output.setStyleSheet(f"""
                QTextEdit {{
                    background-color: #ffffff;
                    color: #1a1a2e;
                    border: 1px solid #c8ccd4;
                    border-radius: 5px;
                    padding: 4px;
                }}
            """)
            self.input_edit.setStyleSheet(
                "background-color:#ffffff;color:#1a1a2e;border:1px solid #c8ccd4;"
                "border-radius:3px;padding:2px 6px;")
        else:
            self.output.setStyleSheet("""
                QTextEdit {
                    background-color: #1a1a2e;
                    color: #e0e0f0;
                    border: 1px solid #3f3f5c;
                    border-radius: 5px;
                    padding: 4px;
                }
            """)
            self.input_edit.setStyleSheet(
                "background-color:#2a2a40;color:#e0e0f0;border:1px solid #3f3f5c;"
                "border-radius:3px;padding:2px 6px;")

    def append_output(self, text: str, color: str = None):
        """追加文本到输出区，可选颜色 (red/green/yellow/blue/gray)，使用主题色值。"""
        if color:
            hex_color = getattr(self, '_console_colors', {}).get(color, color)
            self.output.append(f'<span style="color:{hex_color}">{text}</span>')
        else:
            self.output.append(text)
        # 自动滚动到底部
        self.output.verticalScrollBar().setValue(
            self.output.verticalScrollBar().maximum())

    def refresh_last_line(self, text: str, color: str = None):
        """刷新模式：替换最后一行（用于 aria2c 进度实时刷新）。"""
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.End)
        cursor.movePosition(cursor.StartOfBlock, cursor.KeepAnchor)
        cursor.removeSelectedText()
        self.output.setTextCursor(cursor)
        self.append_output(text, color)

# ═══════════════════════════════════════════════
#  CommandPreviewDialog
# ═══════════════════════════════════════════════

class CommandPreviewDialog(QDialog):
    """显示完整的命令行参数，支持复制和保存为 bat/sh。"""

    def __init__(self, cmd_parts: list, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self.cmd_parts = cmd_parts
        self.theme = theme
        self.setWindowTitle(MSG.get("cmd_preview_title", "命令预览"))
        self.setMinimumSize(500, 300)

        import re
        layout = QVBoxLayout(self)

        # 单行展示
        oneline = " ".join(cmd_parts)
        self.cmd_label = QLabel(f"<pre>{oneline}</pre>")
        self.cmd_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cmd_label.setWordWrap(True)
        layout.addWidget(self.cmd_label)

        # 逐行展示
        self.list_widget = QListWidget()
        for i, a in enumerate(cmd_parts):
            self.list_widget.addItem(f"[{i}] {a}")
        layout.addWidget(self.list_widget)

        # 按钮行
        btn_layout = QHBoxLayout()

        copy_btn = QPushButton(MSG.get("copy_command", "复制命令"))
        copy_btn.clicked.connect(self._copy_command)
        btn_layout.addWidget(copy_btn)

        save_btn = QPushButton(MSG.get("save_script", "保存脚本"))
        save_btn.clicked.connect(self._save_script)
        btn_layout.addWidget(save_btn)

        close_btn = QPushButton(MSG.get("close", "关闭"))
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self._apply_theme()

    def _apply_theme(self):
        if self.theme == "light":
            self.setStyleSheet("background-color:#f0f0f0;color:#222;")
        else:
            self.setStyleSheet("background-color:#161b22;color:#c9d1d9;")

    def _copy_command(self):
        QApplication.clipboard().setText(" ".join(self.cmd_parts))
        self.cmd_label.setText(
            f"<pre>{' '.join(self.cmd_parts)}</pre>\n<span style='color:green'>✅ {MSG.get('copied', '已复制到剪贴板')}</span>")

    def _save_script(self):
        """在脚本同目录保存 .bat/.sh 文件。"""
        path = save_script(self.cmd_parts, os.path.dirname(os.path.abspath(__file__)))
        QMessageBox.information(
            self,
            MSG.get("script_saved_title", "脚本已保存"),
            MSG.get("script_saved", "脚本已保存到:\n{path}").replace("{path}", path),
        )

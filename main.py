"""
llama.cpp 启动器 —— 主 UI 入口。
将 UI 与下载、字符拼写、bat/sh 生成、平台检测解耦。
标签页: 参数 | 设置 | 控制台 (参数可折叠)
"""
import sys
import os
import re
import json
import ctypes
import webbrowser
import subprocess
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QScrollArea, QFrame, QFileDialog, QMessageBox, QSizePolicy,
    QRadioButton, QProgressBar, QSlider, QGroupBox,
)
from PyQt5.QtCore import Qt, QTimer, QByteArray, QPointF
from PyQt5.QtGui import QFont, QFontDatabase, QColor, QPixmap, QPainter, QPolygonF

# ── 从 src 包导入解耦模块 ──
from src.config import (
    BASE_DIR, BIN_DIR, CONFIG_PATH,
    COMMON_EXES,
    STYLESHEET, LIGHT_STYLESHEET,
    BTN, WIN_TITLES, PH, UI_LABELS, MSG,
    DYNAMIC_UI_SCHEMA, DEFAULT_CONFIG,
    _open_folder,
    _list_locales, _apply_locale_to_globals,
)
from src.platform import (
    open_folder, save_script,
)
from src.launcher import LaunchThread
from src.download import ReleaseDownloadThread, VramCheckThread
from src.widgets import (
    CollapsibleSection, AdaptiveComboBox, ConsoleWidget, CommandPreviewDialog,
    NoWheelSpinBox, NoWheelDoubleSpinBox,
)

_LEFT  = Qt.AlignLeft
_RIGHT = Qt.AlignRight

# ═══════════════════════════════════════════════
#  LlamaProLauncher —— 主窗口
# ═══════════════════════════════════════════════

class LlamaProLauncher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hello Llama.cpp Launcher")
        self.setMinimumSize(420, 280)

        self.config_path = CONFIG_PATH
        self.config      = dict(DEFAULT_CONFIG)
        self.load_settings()

        # 多语言初始化
        lang = self.config.get("lang", "zh")
        _apply_locale_to_globals(lang)
        self.setWindowTitle(WIN_TITLES.get("main", "Hello Llama.cpp Launcher"))

        # 字体
        self._custom_font_family: Optional[str] = None
        self._font_scale  = 1.0
        self._resize_timer: Optional[QTimer] = None

        # 模型路径映射
        self.full_paths: dict = {}

        # 控制台
        self.console: Optional[ConsoleWidget] = None

        # 启动线程
        self.launch_thread: Optional[LaunchThread] = None
        self._restarting         = False
        self._server_ready_opened = False

        # 显存检测线程
        self._vram_thread: Optional[VramCheckThread] = None

        # 下载线程
        self._dl_thread: Optional[ReleaseDownloadThread] = None

        # 动态控件 & 折叠面板
        self.dynamic_vars: dict = {}
        self.custom_widgets: dict = {}
        self._sections: list = []

        # 思考模式
        self._think_mode = self.config.get("think_mode", "normal")

        # 构建 UI
        self.build_ui()

        # 应用主题
        self.apply_theme()

        # 加载字体
        self._load_custom_font()

        # 恢复窗口几何
        self._restore_window_geometry()

        # 自动检测可执行文件
        QTimer.singleShot(100, self.detect_executables)
        # 自动刷新模型列表
        QTimer.singleShot(200, self.refresh_models)

    # ── 配置存取 ──────────────────────────────────

    def load_settings(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.config.update(saved)
        except Exception:
            pass

    def save_settings(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ── 样式表缩放与字体注入 ──────────────────────────

    def _scale_stylesheet(self, sheet: str, scale: float) -> str:
        def _repl(m):
            val = float(m.group(1))
            return f"{val * scale:.1f}px"
        return re.sub(r"(\d+(?:\.\d+)?)px", _repl, sheet)

    def _inject_custom_font_into_sheet(self, sheet: str) -> str:
        if self._custom_font_family:
            return f"* {{ font-family: \"{self._custom_font_family}\"; }}\n" + sheet
        return sheet

    def apply_theme(self, theme: Optional[str] = None):
        if theme is None:
            theme = str(self.config.get("theme", "dark"))
        else:
            self.config["theme"] = theme
        is_light = (theme == "light")
        base_sheet = LIGHT_STYLESHEET if is_light else STYLESHEET
        base_sheet = self._inject_custom_font_into_sheet(base_sheet)
        ui_scale = self.config.get("ui_scale", 1.0)
        combined = self._font_scale * ui_scale
        scaled = self._scale_stylesheet(base_sheet, combined)

        app = QApplication.instance()
        if app:
            app.setStyleSheet(scaled)

        self._apply_spinbox_arrows(theme)

        if self.console:
            self.console.set_theme(theme)

        self.save_settings()

    def _on_section_toggled(self, key: str, collapsed: bool):
        self.config.setdefault("collapsed_sections", {})[key] = collapsed
        self.save_settings()

    def _apply_spinbox_arrows(self, theme: str):
        """生成 SpinBox 上下箭头 PNG 并注入 QSS（高精度 2x 画布消除锯齿）。"""
        c = QColor("#222222" if theme == "light" else "#e0e0f0")
        W, H = 20, 16  # 2x 精度画布
        up_pm, down_pm = QPixmap(W, H), QPixmap(W, H)
        for pm, up in ((up_pm, True), (down_pm, False)):
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            if up:
                p.drawPolygon(QPolygonF([QPointF(10, 2), QPointF(2, 13), QPointF(18, 13)]))
            else:
                p.drawPolygon(QPolygonF([QPointF(10, 13), QPointF(2, 2), QPointF(18, 2)]))
            p.end()
        import tempfile
        u = os.path.join(tempfile.gettempdir(), "llama_spin_up.png").replace("\\", "/")
        d = os.path.join(tempfile.gettempdir(), "llama_spin_down.png").replace("\\", "/")
        up_pm.save(u); down_pm.save(d)
        css = f'QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{{image:url("{u}");width:10px;height:8px}}' \
              f'QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{{image:url("{d}");width:10px;height:8px}}'
        app = QApplication.instance()
        if app:
            lines = [l for l in app.styleSheet().split("\n") if "llama_spin_" not in l]
            app.setStyleSheet("\n".join(lines) + "\n" + css)

    # ── 多语言 ──────────────────────────────────

    def _on_language_changed(self, idx: int):
        codes = _list_locales()
        if 0 <= idx < len(codes):
            self.config["lang"] = codes[idx]
            self.save_settings()
            _apply_locale_to_globals(codes[idx])
            self._rebuild_ui()

    def _rebuild_ui(self):
        self.setWindowTitle(WIN_TITLES.get("main", "Hello Llama.cpp Launcher"))
        central = self.centralWidget()
        if central:
            central.deleteLater()
        self._sections.clear()
        self.dynamic_vars.clear()
        self.custom_widgets.clear()
        self.build_ui()
        self.apply_theme()
        self._load_custom_font()
        self.detect_executables()
        self.refresh_models()

    def _on_ui_scale_changed(self, value: int):
        new_scale = value / 100.0
        self.config["ui_scale"] = new_scale
        if hasattr(self, 'scale_value_label'):
            self.scale_value_label.setText(f"{value}%")
        theme = self.config.get("theme", "dark")
        self.apply_theme(theme)

    # ── 主题切换 ──────────────────────────────────

    def toggle_theme(self):
        cur = str(self.config.get("theme", "dark"))
        self.apply_theme("light" if cur == "dark" else "dark")

    # ── 辅助方法 ──────────────────────────────────

    def get_combo(self):
        return self._model_combo

    # ═══════════════════════════════════════════════
    #  主界面构建 (build_ui)
    # ═══════════════════════════════════════════════

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(4, 4, 4, 2)
        root_layout.setSpacing(2)

        # ── 标签页 ──
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        # 标签页 1: 参数
        params_tab = QWidget()
        self.tabs.addTab(params_tab, WIN_TITLES.get("params_tab", "📊 参数"))
        self._build_params_tab(params_tab)

        # 标签页 2: 设置
        settings_tab = QWidget()
        self.tabs.addTab(settings_tab, WIN_TITLES.get("settings_tab", "⚙ 设置"))
        self._build_settings_tab(settings_tab)

        # 标签页 3: 控制台
        self.console = ConsoleWidget()
        self.console.input_signal.connect(self._on_console_input)
        self.tabs.addTab(self.console, WIN_TITLES.get("console_tab", "📟 控制台"))

        # ── 底部操作栏 ──
        bottom_bar = QFrame()
        bottom_bar.setObjectName("actionFrame")
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(4, 2, 4, 2)
        bb_layout.setSpacing(4)

        self.btn_launch = QPushButton(BTN.get("launch", "▶ 启动"))
        self.btn_launch.clicked.connect(self.launch)
        bb_layout.addWidget(self.btn_launch)

        self.btn_stop = QPushButton(BTN.get("stop", "⏹ 停止"))
        self.btn_stop.clicked.connect(self.stop_launch)
        self.btn_stop.setEnabled(False)
        bb_layout.addWidget(self.btn_stop)

        self.btn_preview = QPushButton(BTN.get("preview", "📋 预览"))
        self.btn_preview.clicked.connect(self.show_command_preview)
        bb_layout.addWidget(self.btn_preview)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumWidth(120)
        self.progress_bar.setMaximumHeight(14)
        bb_layout.addWidget(self.progress_bar)

        bb_layout.addStretch()

        self.status_label = QLabel(MSG.get("ready", "就绪"))
        self.status_label.setObjectName("statusLabel")
        bb_layout.addWidget(self.status_label)

        root_layout.addWidget(bottom_bar)

        # ── 最下层状态栏 ──
        status_bar = QFrame()
        status_bar.setObjectName("statusBar")
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(6, 1, 6, 1)
        sb_layout.setSpacing(6)
        self.vram_label = QLabel(MSG.get("vram_label_default", "VRAM: --- / --- MiB"))
        sb_layout.addWidget(self.vram_label)
        self.log_label = QLabel("")
        sb_layout.addWidget(self.log_label, 1)
        root_layout.addWidget(status_bar)

    # ═══════════════════════════════════════════════
    #  标签页 1: 参数 (模型选择 + 动态参数，可折叠)
    # ═══════════════════════════════════════════════

    def _build_params_tab(self, tab: QWidget):
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        tab_layout.addWidget(scroll_area)

        container = QWidget()
        container.setObjectName("paramsContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        def _mk_section(key: str, title: str, widget: QWidget) -> CollapsibleSection:
            collapsed = self.config.get("collapsed_sections", {}).get(key, False)
            sec = CollapsibleSection(title, widget, section_key=key, collapsed=collapsed)
            sec._toggle = (lambda s=sec, k=key: (
                s.set_collapsed(not s._collapsed),
                self._on_section_toggled(k, s._collapsed),
            )[-1])
            self._sections.append(sec)
            return sec

        # ── 模型选择 ──
        model_w = QWidget()
        mw = QVBoxLayout(model_w)
        mw.setContentsMargins(4, 2, 4, 2)
        mw.setSpacing(3)

        # 模型下拉 + 预设按钮
        model_sel = QWidget()
        ms = QHBoxLayout(model_sel)
        ms.setContentsMargins(0, 0, 0, 0)
        ms.setSpacing(3)
        lbl = QLabel(UI_LABELS.get("label_model_select", "选择模型:"))
        lbl.setFont(QFont('Segoe UI', 9, QFont.Bold))
        ms.addWidget(lbl)

        del_btn = QPushButton(BTN.get("delete_preset", "🗑 删除预设"))
        del_btn.setToolTip(BTN.get("delete_preset_tooltip", "删除当前选中模型的已保存预设"))
        del_btn.clicked.connect(self.delete_current_preset)
        ms.addWidget(del_btn, alignment=_RIGHT)
        mw.addWidget(model_sel)

        self._model_combo = AdaptiveComboBox()
        self._model_combo.currentIndexChanged.connect(self.on_model_change)
        mw.addWidget(self._model_combo)

        # 预设按钮
        preset_btns = QWidget()
        pb = QHBoxLayout(preset_btns)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(3)
        refresh_btn = QPushButton(BTN.get("refresh", "🔄 刷新模型"))
        refresh_btn.clicked.connect(self.refresh_models)
        pb.addWidget(refresh_btn)
        save_btn = QPushButton(BTN.get("save_preset", "💾 保存预设"))
        save_btn.clicked.connect(self.save_current_preset)
        pb.addWidget(save_btn)
        pb.addStretch()
        mw.addWidget(preset_btns)

        layout.addWidget(_mk_section("model", UI_LABELS.get("model_section", "模型选择"), model_w))

        # ── 动态参数（紧凑 4 列 grid：大参数占整行，小参数每行 2 对）──
        if DYNAMIC_UI_SCHEMA:
            for group in DYNAMIC_UI_SCHEMA:
                params_w = QWidget()
                gl = QGridLayout(params_w)
                gl.setSpacing(3)
                gl.setContentsMargins(4, 2, 4, 2)
                gl.setColumnStretch(0, 0)
                gl.setColumnStretch(1, 1)
                gl.setColumnStretch(2, 0)
                gl.setColumnStretch(3, 1)
                gl.setColumnMinimumWidth(0, 75)
                gl.setColumnMinimumWidth(2, 75)
                row, col, max_col = 0, 0, 4

                def _make_widget(ptype: str, param: dict):
                    if ptype == "string":
                        w = QLineEdit(str(param.get("default", "")))
                    elif ptype == "int":
                        w = NoWheelSpinBox()
                        w.setRange(param.get("min", 0), param.get("max", 999999))
                        w.setSingleStep(param.get("step", 1))
                        w.setValue(int(param.get("default", 0)))
                    elif ptype == "float":
                        w = NoWheelDoubleSpinBox()
                        w.setRange(param.get("min", 0.0), param.get("max", 100.0))
                        w.setSingleStep(param.get("step", 0.1))
                        w.setDecimals(3)
                        w.setValue(float(param.get("default", 0.0)))
                    elif ptype == "bool":
                        w = QCheckBox("启用")
                        w.setChecked(bool(param.get("default", False)))
                    else:
                        w = QLineEdit(str(param.get("default", "")))
                    return w

                for param in group["params"]:
                    pid   = param["id"]
                    ptype = param.get("type", "string")
                    wide  = param.get("width", 0)
                    tt    = param.get("tooltip", "")
                    w     = _make_widget(ptype, param)
                    self.dynamic_vars[pid] = w
                    lbl = QLabel(param["label"])
                    if tt:
                        lbl.setToolTip(tt); w.setToolTip(tt)

                    # 所有参数每行 2 对（bool 也并排）
                    gl.addWidget(lbl, row, col, _LEFT)
                    gl.addWidget(w,   row, col + 1)
                    col += 2
                    if col >= max_col:
                        col = 0; row += 1

                title = group.get("group_name", group.get("title", UI_LABELS.get("params_section", "参数")))
                layout.addWidget(_mk_section(f"params_{title}", title, params_w))

        # ── 运行模式与端口 ──
        server_w = QWidget()
        sv = QHBoxLayout(server_w)
        sv.setContentsMargins(4, 2, 4, 2)
        sv.setSpacing(3)
        self.custom_widgets["is_server_mode"] = QCheckBox(UI_LABELS.get("checkbox_server_mode", "Server (API) 模式"))
        self.custom_widgets["is_server_mode"].setChecked(self.config.get("is_server_mode", False))
        self.custom_widgets["is_server_mode"].toggled.connect(self.on_server_mode_toggle)
        sv.addWidget(self.custom_widgets["is_server_mode"])
        sv.addWidget(QLabel(UI_LABELS.get("label_port", "端口:")))
        self.custom_widgets["port"] = QLineEdit(self.config.get("last_port", "8080"))
        self.custom_widgets["port"].setMaximumWidth(80)
        sv.addWidget(self.custom_widgets["port"])
        self.mode_label = QLabel("")
        sv.addWidget(self.mode_label)
        sv.addStretch()
        console_cb = QCheckBox(UI_LABELS.get("console_mode", "外部控制台"))
        console_cb.setChecked(self.config.get("console_mode", False))
        console_cb.toggled.connect(lambda v: self.config.update({"console_mode": v}) or self.save_settings())
        self.custom_widgets["console_mode"] = console_cb
        sv.addWidget(console_cb)
        layout.addWidget(_mk_section("server", UI_LABELS.get("group_mode_args", "运行与端口"), server_w))

        # ── 思考模式 + MMProj（含开关） ──
        think_w = QWidget()
        tl = QGridLayout(think_w)
        tl.setSpacing(3)
        tl.setContentsMargins(4, 2, 4, 2)
        tl.setColumnStretch(0, 0)
        tl.setColumnStretch(1, 0)
        tl.setColumnStretch(2, 1)
        tl.setColumnStretch(3, 0)

        self.radio_normal = QRadioButton(UI_LABELS.get("radio_normal", "正常输出"))
        self.radio_hide   = QRadioButton(UI_LABELS.get("radio_hide", "完全隐藏"))
        self.radio_stop   = QRadioButton(UI_LABELS.get("radio_stop", "物理截断"))
        self.radio_normal.setChecked(self._think_mode == "normal")
        self.radio_hide.setChecked(self._think_mode == "hide")
        self.radio_stop.setChecked(self._think_mode == "stop")
        self.radio_normal.toggled.connect(lambda c: c and self.set_think_mode("normal"))
        self.radio_hide.toggled.connect(  lambda c: c and self.set_think_mode("hide"))
        self.radio_stop.toggled.connect(  lambda c: c and self.set_think_mode("stop"))
        tl.addWidget(self.radio_normal, 0, 0)
        tl.addWidget(self.radio_hide,   1, 0)
        tl.addWidget(self.radio_stop,   2, 0)

        tl.addWidget(QLabel(UI_LABELS.get("label_think_budget", "思考 Token 限制:")), 0, 1, _RIGHT)
        self.custom_widgets["think_budget"] = QLineEdit(self.config.get("think_budget", "0"))
        tl.addWidget(self.custom_widgets["think_budget"], 0, 2)

        # MMProj 行：开关 + 路径 + 按钮
        tl.addWidget(QLabel(UI_LABELS.get("label_mmproj", "MMProj 文件:")), 3, 0, _LEFT)
        mmproj_cb = QCheckBox(UI_LABELS.get("mmproj_enable", "启用"))
        mmproj_cb.setChecked(self.config.get("mmproj_enable", False))
        mmproj_cb.toggled.connect(lambda v: self.config.update({"mmproj_enable": v}) or self.save_settings())
        self.custom_widgets["mmproj_enable"] = mmproj_cb
        tl.addWidget(mmproj_cb, 3, 1)
        self.custom_widgets["mmproj"] = QLineEdit(self.config.get("mmproj", ""))
        tl.addWidget(self.custom_widgets["mmproj"], 3, 2)
        btn_mm = QPushButton(BTN.get("select_mmproj", "选择"))
        btn_mm.clicked.connect(self.select_mmproj)
        tl.addWidget(btn_mm, 3, 3, _LEFT)

        layout.addWidget(_mk_section("think", UI_LABELS.get("group_think_vision", "思考控制与多模态"), think_w))

        # ── 额外参数 ──
        extra_w = QWidget()
        el = QGridLayout(extra_w)
        el.setSpacing(3)
        el.setContentsMargins(4, 2, 4, 2)
        el.setColumnStretch(0, 0)
        el.setColumnStretch(1, 1)
        el.setColumnStretch(2, 1)

        el.addWidget(QLabel(UI_LABELS.get("label_global_args", "全局默认参数:")), 0, 0, _LEFT)
        self.custom_widgets["global_args"] = QLineEdit(self.config.get("global_args", ""))
        self.custom_widgets["global_args"].setPlaceholderText(PH.get("global_args_placeholder", "--no-warmup --cont-batching"))
        el.addWidget(self.custom_widgets["global_args"], 0, 1, 1, 2)

        el.addWidget(QLabel(UI_LABELS.get("label_custom_args", "当前模型专属参数:")), 1, 0, _LEFT)
        self.custom_widgets["custom_args"] = QLineEdit("")
        self.custom_widgets["custom_args"].setPlaceholderText(PH.get("custom_args_placeholder", "--special-token ..."))
        el.addWidget(self.custom_widgets["custom_args"], 1, 1, 1, 2)

        layout.addWidget(_mk_section("extra_args", UI_LABELS.get("extra_args_section", "额外参数"), extra_w))

        layout.addStretch()
        scroll_area.setWidget(container)

    # ═══════════════════════════════════════════════
    #  标签页 2: 设置 (全部设置项，可折叠)
    # ═══════════════════════════════════════════════

    def _build_settings_tab(self, tab: QWidget):
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        tab_layout.addWidget(scroll_area)

        container = QWidget()
        container.setObjectName("settingsContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        def _section(title: str) -> QWidget:
            """Inline section: bold label + thin separator, no box."""
            w = QWidget()
            vl = QVBoxLayout(w)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(2)
            hdr = QLabel(title)
            hdr.setObjectName("settingsSection")
            vl.addWidget(hdr)
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            vl.addWidget(sep)
            body = QWidget()
            vl.addWidget(body)
            w._body = body
            return w

        # ═══ 路径 ═══
        sec_path = _section("📂 核心路径")
        pg = QGridLayout(sec_path._body); pg.setSpacing(3); pg.setContentsMargins(0,2,0,0)
        pg.setColumnStretch(0, 0); pg.setColumnStretch(1, 1); pg.setColumnStretch(2, 0)

        pg.addWidget(QLabel("Bin:"), 0, 0)
        self.custom_widgets["bin_dir"] = QLineEdit(self.config.get("bin_dir", ""))
        pg.addWidget(self.custom_widgets["bin_dir"], 0, 1)
        btn_bin = QPushButton("浏览"); btn_bin.clicked.connect(self.select_bin_dir)
        pg.addWidget(btn_bin, 0, 2)

        pg.addWidget(QLabel("模型:"), 1, 0)
        self.custom_widgets["model_dir"] = QLineEdit(self.config.get("model_dir", ""))
        pg.addWidget(self.custom_widgets["model_dir"], 1, 1)
        btn_model = QPushButton("浏览"); btn_model.clicked.connect(self.select_model_dir)
        pg.addWidget(btn_model, 1, 2)

        pg.addWidget(QLabel("EXE:"), 2, 0)
        self.exe_label = QLabel(UI_LABELS.get("label_exe_not_found", "(未检测)"))
        self.exe_label.setObjectName("exeLabel")
        pg.addWidget(self.exe_label, 2, 1)
        btn_detect_exe = QPushButton("重检"); btn_detect_exe.clicked.connect(self.detect_executables)
        pg.addWidget(btn_detect_exe, 2, 2)
        layout.addWidget(sec_path)

        # ═══ 外观 ═══
        sec_appear = _section("🎨 外观")
        ag = QGridLayout(sec_appear._body); ag.setSpacing(3); ag.setContentsMargins(0,2,0,0)
        ag.setColumnStretch(0, 0); ag.setColumnStretch(1, 1); ag.setColumnStretch(2, 0); ag.setColumnStretch(3, 1)

        ag.addWidget(QLabel("语言:"), 0, 0)
        self._lang_combo = AdaptiveComboBox()
        self._lang_combo.addItems(_list_locales())
        cur_lang = self.config.get("lang", "zh")
        for i in range(self._lang_combo.count()):
            if self._lang_combo.itemText(i) == cur_lang:
                self._lang_combo.setCurrentIndex(i); break
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        ag.addWidget(self._lang_combo, 0, 1)
        ag.addWidget(QLabel("主题:"), 0, 2)
        self._theme_combo = AdaptiveComboBox()
        self._theme_combo.addItems(["dark", "light"])
        self._theme_combo.setCurrentText(str(self.config.get("theme", "dark")))
        self._theme_combo.currentTextChanged.connect(self.apply_theme)
        ag.addWidget(self._theme_combo, 0, 3)
        layout.addWidget(sec_appear)

        # ═══ 字体 ═══
        sec_font = _section("🔤 字体")
        fg = QGridLayout(sec_font._body); fg.setSpacing(3); fg.setContentsMargins(0,2,0,0)
        fg.setColumnStretch(0, 0); fg.setColumnStretch(1, 1); fg.setColumnStretch(2, 0)
        fg.addWidget(QLabel("自定义:"), 0, 0)
        self.custom_widgets["font_path"] = QLineEdit(self.config.get("font_path", ""))
        self.custom_widgets["font_path"].setPlaceholderText("留空=系统默认")
        fg.addWidget(self.custom_widgets["font_path"], 0, 1)
        btn_font = QPushButton("选择"); btn_font.clicked.connect(self._select_font_file)
        fg.addWidget(btn_font, 0, 2)
        layout.addWidget(sec_font)

        # ═══ 缩放 ═══
        sec_scale = _section("📐 缩放")
        sg = QHBoxLayout(sec_scale._body); sg.setContentsMargins(0,2,0,0); sg.setSpacing(5)
        self.scale_label = QLabel("缩放:")
        sg.addWidget(self.scale_label)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(50, 200)
        self.scale_slider.setValue(int(self.config.get("ui_scale", 1.0) * 100))
        self.scale_slider.setFixedWidth(120)
        self.scale_slider.valueChanged.connect(self._on_ui_scale_changed)
        sg.addWidget(self.scale_slider)
        self.scale_value_label = QLabel(f"{self.scale_slider.value()}%")
        self.scale_value_label.setFixedWidth(36)
        sg.addWidget(self.scale_value_label)
        auto_scale_cb = QCheckBox("自适应")
        auto_scale_cb.setChecked(self.config.get("auto_scale", True))
        auto_scale_cb.toggled.connect(lambda v: (
            self.config.update({"auto_scale": v}),
            self.save_settings(),
            (self._apply_font_scale() if v else None),
        ))
        sg.addWidget(auto_scale_cb)
        sg.addStretch()
        layout.addWidget(sec_scale)

        # ═══ 下载 ═══
        sec_dl = _section("⬇ 下载与更新")
        dg = QGridLayout(sec_dl._body); dg.setSpacing(3); dg.setContentsMargins(0,2,0,0)
        dg.setColumnStretch(0, 0); dg.setColumnStretch(1, 0); dg.setColumnStretch(2, 0); dg.setColumnStretch(3, 1)

        self.btn_fetch = QPushButton("📡 获取可用文件")
        self.btn_fetch.clicked.connect(lambda: self._start_download(is_update=False))
        dg.addWidget(self.btn_fetch, 0, 0, 1, 2)
        self.btn_detect = QPushButton("🔍 VRAM")
        self.btn_detect.clicked.connect(self.show_auto_ngl)
        dg.addWidget(self.btn_detect, 0, 2)

        retry_row = QWidget(); rrl = QHBoxLayout(retry_row); rrl.setContentsMargins(0,0,0,0); rrl.setSpacing(3)
        rrl.addWidget(QLabel("重试:"))
        retry_spin = NoWheelSpinBox(); retry_spin.setRange(1,10); retry_spin.setValue(self.config.get("retry_count",3))
        retry_spin.setMaximumWidth(50)
        retry_spin.valueChanged.connect(lambda v: self.config.update({"retry_count": v}) or self.save_settings())
        rrl.addWidget(retry_spin)
        rrl.addWidget(QLabel("超时(s):"))
        timeout_spin = NoWheelSpinBox(); timeout_spin.setRange(30,3600); timeout_spin.setValue(self.config.get("dl_timeout",300))
        timeout_spin.setMaximumWidth(65)
        timeout_spin.valueChanged.connect(lambda v: self.config.update({"dl_timeout": v}) or self.save_settings())
        rrl.addWidget(timeout_spin)
        rrl.addStretch()
        dg.addWidget(retry_row, 3, 0, 1, 4)

        # 下载文件按钮列表（获取 Release 后动态填充）
        self._dl_list_widget = QWidget()
        self._dl_list_layout = QVBoxLayout(self._dl_list_widget)
        self._dl_list_layout.setContentsMargins(0, 4, 0, 0)
        self._dl_list_layout.setSpacing(2)
        self._dl_list_widget.setVisible(False)
        dg.addWidget(self._dl_list_widget, 4, 0, 1, 4)

        layout.addWidget(sec_dl)

        layout.addStretch()
        scroll_area.setWidget(container)

    # ═══════════════════════════════════════════════
    #  Helper Methods
    # ═══════════════════════════════════════════════

    def set_think_mode(self, mode: str):
        self._think_mode = mode

    def on_server_mode_toggle(self, checked: bool):
        mode_text = UI_LABELS.get("mode_server", "Server") if checked else UI_LABELS.get("mode_cli", "CLI")
        if hasattr(self, 'mode_label'):
            self.mode_label.setText(MSG.get("mode_label", "  |  模式: {mode}").replace("{mode}", mode_text))
        self.status_label.setText(MSG.get("mode_switched", "已切换至 {mode} 模式").replace("{mode}", mode_text))

    def select_bin_dir(self):
        p = QFileDialog.getExistingDirectory(self, WIN_TITLES.get("select_bin", "选择 Bin 目录"),
                                               self.custom_widgets["bin_dir"].text())
        if p:
            self.custom_widgets["bin_dir"].setText(os.path.normpath(p))
            self.config["bin_dir"] = os.path.normpath(p)
            self.save_settings()
            self.detect_executables()

    def select_model_dir(self):
        p = QFileDialog.getExistingDirectory(self, WIN_TITLES.get("select_model", "选择模型目录"),
                                               self.custom_widgets["model_dir"].text())
        if p:
            self.custom_widgets["model_dir"].setText(os.path.normpath(p))
            self.config["model_dir"] = os.path.normpath(p)
            self.save_settings()
            self.refresh_models()

    def select_mmproj(self):
        p, _ = QFileDialog.getOpenFileName(
            self, WIN_TITLES.get("select_mmproj", "选择 MMProj 文件"),
            self.custom_widgets["model_dir"].text(),
            "MMProj (*.mmproj *.gguf);;All (*.*)")
        if p:
            self.custom_widgets["mmproj"].setText(os.path.normpath(p))
            self.config["mmproj"] = os.path.normpath(p)
            self.save_settings()

    def detect_executables(self):
        """检测关键可执行文件（llama-cli + llama-server，兼容 Linux 无后缀）。"""
        bin_dir = os.path.abspath(self.custom_widgets["bin_dir"].text())
        _exe_suffix = ".exe" if sys.platform == "win32" else ""
        key_exes = [f"llama-cli{_exe_suffix}", f"llama-server{_exe_suffix}"]
        key_lower = [k.lower() for k in key_exes]
        found = []
        if os.path.isdir(bin_dir):
            for exe in key_exes:
                p = os.path.join(bin_dir, exe)
                if os.path.isfile(p):
                    found.append(exe)
        if not found:
            for root, _, files in os.walk(BASE_DIR):
                for f in files:
                    if f.lower() in key_lower:
                        found.append(os.path.relpath(os.path.join(root, f), BASE_DIR))
        if found:
            self.exe_label.setText(" | ".join(found))
        else:
            self.exe_label.setText(MSG.get("exe_not_found_alert", "⚠ 未找到 (请设置 Bin 目录)"))
        self._update_download_buttons()

    # ═══════════════════════════════════════════════
    #  下载
    # ═══════════════════════════════════════════════

    def _has_bin_files(self) -> bool:
        """检测 bin 目录是否有关键 llama 可执行文件（兼容 Linux 无后缀）。"""
        bin_dir = self.custom_widgets["bin_dir"].text()
        if not bin_dir or not os.path.isdir(bin_dir):
            bin_dir = BIN_DIR
        _exe_suffix = ".exe" if sys.platform == "win32" else ""
        key_names = [f"llama-cli{_exe_suffix}", f"llama-server{_exe_suffix}"]
        # 也尝试无后缀版本（跨平台通用回退）
        key_names += ["llama-cli", "llama-server"]
        for kf in set(key_names):
            if os.path.isfile(os.path.join(bin_dir, kf)):
                return True
        return False

    def _update_download_buttons(self):
        """根据是否已有 bin 文件启用/停用获取按钮。"""
        has_bin = self._has_bin_files()
        self.btn_fetch.setEnabled(True)  # 始终允许获取列表
        if has_bin:
            self.btn_fetch.setToolTip("已检测到可执行文件，可获取更新列表。")
        else:
            self.btn_fetch.setToolTip("获取当前平台可下载的 llama.cpp 二进制文件列表")

    def _start_download(self, is_update: bool = False):
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self.btn_fetch.setText("📡 获取可用文件")
            self.status_label.setText(MSG.get("download_cancelled", "下载已取消"))
            return

        bin_dir = self.custom_widgets["bin_dir"].text()
        retry = self.config.get("retry_count", 3)
        timeout = self.config.get("dl_timeout", 300)

        # 更新模式：Windows 下清空 bin 目录
        if is_update and sys.platform == "win32":
            target_dir = os.path.abspath(bin_dir) if bin_dir and os.path.isdir(bin_dir) else os.path.abspath(BIN_DIR)
            os.makedirs(target_dir, exist_ok=True)
            import shutil
            for item in os.listdir(target_dir):
                item_path = os.path.join(target_dir, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception:
                    pass
            self.status_label.setText(MSG.get("update_cleared", "🗑 已清理旧 bin 文件，开始更新..."))

        self._dl_thread = ReleaseDownloadThread(
            bin_dir, backend_id="",
            retry_count=retry, timeout=timeout,
        )

        # raw_signal：全部追加到控制台
        self._dl_thread.raw_signal.connect(lambda msg: (
            self.console and self.console.append_output(msg, "gray"),
        ))
        self._dl_thread.status_signal.connect(lambda msg: (
            self.status_label.setText(msg),
        ))
        self._dl_thread.progress_signal.connect(lambda cur, total: (
            self.progress_bar.setVisible(True),
            self.progress_bar.setMaximum(total),
            self.progress_bar.setValue(cur),
        ))
        # Phase 1: 在设置页动态生成下载按钮列表
        def _on_assets(available: list):
            # 清空旧按钮
            while self._dl_list_layout.count():
                item = self._dl_list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            # 生成下载按钮
            for a in available:
                label = f"[{a['backend_label']}] {a['name']}  ({a['size']}MB)"
                btn = QPushButton(label)
                btn.setStyleSheet("QPushButton{text-align:left;padding:2px 6px;font-size:7.5pt;}")
                def _make_callback(asset):
                    return lambda: self._start_asset_download(asset)
                btn.clicked.connect(_make_callback(a))
                self._dl_list_layout.addWidget(btn)
            self._dl_list_widget.setVisible(True)
            self._dl_thread = None
            self.btn_fetch.setText("📡 获取可用文件")
            self.btn_fetch.setEnabled(True)
            self.console and self.console.append_output(
                f"📋 获取到 {len(available)} 个可用文件，在设置页点击下载", "green")
        self._dl_thread.assets_signal.connect(_on_assets)
        # Phase 2 下载完成
        self._dl_thread.finished_signal.connect(lambda path: (
            setattr(self, '_dl_thread', None),
            self.btn_fetch.setText("📡 获取可用文件"),
            self.progress_bar.setVisible(False),
            self.status_label.setText(MSG.get("download_done", "✅ 下载完成")),
            self.detect_executables(),
            self._update_download_buttons(),
        ))
        self._dl_thread.error_signal.connect(lambda err: (
            setattr(self, '_dl_thread', None),
            self.btn_fetch.setText("📡 获取可用文件"),
            self.progress_bar.setVisible(False),
            self.status_label.setText(MSG.get("download_failed", "❌ 下载失败")),
            self.console and self.console.append_output(err, "red"),
        ))
        self._dl_thread.start()
        self.btn_fetch.setText(BTN.get("stop_download", "⏹ 停止下载"))
        self.btn_stop.setEnabled(True)  # 下载时启用停止按钮

    def _start_asset_download(self, asset: dict):
        """从下载列表中点击按钮触发 Phase 2 下载。"""
        bin_dir = self.custom_widgets["bin_dir"].text()
        retry = self.config.get("retry_count", 3)
        timeout = self.config.get("dl_timeout", 300)
        self.status_label.setText(f"⬇ 下载: {asset['name']}")
        self._dl_thread = ReleaseDownloadThread(
            bin_dir, backend_id="",
            retry_count=retry, timeout=timeout,
        )
        self._dl_thread.set_asset(asset["name"], asset["url"], asset.get("size", 0))
        self._dl_thread.raw_signal.connect(lambda msg: (
            self.console and self.console.append_output(msg, "gray"),
        ))
        self._dl_thread.status_signal.connect(lambda msg: (
            self.status_label.setText(msg),
        ))
        self._dl_thread.progress_signal.connect(lambda cur, total: (
            self.progress_bar.setVisible(True),
            self.progress_bar.setMaximum(total),
            self.progress_bar.setValue(cur),
        ))
        self._dl_thread.finished_signal.connect(lambda path: (
            setattr(self, '_dl_thread', None),
            self.btn_fetch.setText("📡 获取可用文件"),
            self.progress_bar.setVisible(False),
            self.status_label.setText(MSG.get("download_done", "✅ 下载完成")),
            self.detect_executables(),
            self._update_download_buttons(),
        ))
        self._dl_thread.error_signal.connect(lambda err: (
            setattr(self, '_dl_thread', None),
            self.btn_fetch.setText("📡 获取可用文件"),
            self.progress_bar.setVisible(False),
            self.status_label.setText(MSG.get("download_failed", "❌ 下载失败")),
            self.console and self.console.append_output(err, "red"),
        ))
        self._dl_thread.start()
        self.btn_fetch.setText(BTN.get("stop_download", "⏹ 停止下载"))
        self.btn_stop.setEnabled(True)

    # ═══════════════════════════════════════════════
    #  显存检测
    # ═══════════════════════════════════════════════

    def show_auto_ngl(self):
        """仅检测并显示显存剩余/总量，不自动修改 ngl 字段（llama.cpp 原生支持 --gpu-layers auto）。"""
        if self._vram_thread and self._vram_thread.isRunning():
            return
        self.vram_label.setText(MSG.get("vram_detecting_label", "VRAM: 检测中..."))
        self._vram_thread = VramCheckThread()
        self._vram_thread.result_signal.connect(self.on_vram_result)
        self._vram_thread.start()

    def on_vram_result(self, total: int, free: int):
        """仅显示显存信息，不推算 ngl 层数。"""
        used = total - free
        self.vram_label.setText(
            MSG.get("vram_label", "VRAM: 已用 {used} / 总计 {total} MiB")
            .replace("{used}", str(used)).replace("{total}", str(total)))

    # ═══════════════════════════════════════════════
    #  模型刷新 (匹配旧版: 📄/📂 前缀)
    # ═══════════════════════════════════════════════

    def refresh_models(self):
        path = self.custom_widgets["model_dir"].text()
        self.full_paths.clear()
        display = []
        try:
            if os.path.exists(path):
                grouped = {}
                for root, dirs, files in os.walk(path):
                    gguf = [f for f in files if f.lower().endswith(".gguf")]
                    if gguf:
                        rd = os.path.relpath(root, path).replace("\\", "/")
                        grouped[rd] = sorted(gguf)
                for f in grouped.pop(".", []):
                    n = f"📄 {f}"
                    display.append(n)
                    self.full_paths[n] = f
                for folder in sorted(grouped.keys()):
                    ft = f"📂 {folder}/"
                    display.append(ft)
                    self.full_paths[ft] = "__DIRECTORY__"
                    for f in grouped[folder]:
                        fd = f"    {f}"
                        display.append(fd)
                        self.full_paths[fd] = f"{folder}/{f}"
        except Exception as e:
            self.status_label.setText(
                MSG.get("refresh_error", "刷新出错: {error}").replace("{error}", str(e)))

        combo = self.get_combo()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(display)
        combo.blockSignals(False)

        last = self.config.get("last_model", "")
        if last in display:
            combo.setCurrentText(last)
        else:
            self.select_first_valid_model()
        cnt = sum(1 for v in self.full_paths.values() if v != "__DIRECTORY__")
        self.status_label.setText(
            MSG.get("refresh_done", "已刷新: {count} 个模型").replace("{count}", str(cnt)))

    def select_first_valid_model(self):
        combo = self.get_combo()
        for i in range(combo.count()):
            if self.full_paths.get(combo.itemText(i)) != "__DIRECTORY__":
                combo.setCurrentIndex(i)
                return

    # ═══════════════════════════════════════════════
    #  预设管理 (匹配旧版: think_mode/think_budget/mmproj/custom_args)
    # ═══════════════════════════════════════════════

    def on_model_change(self, index: int):
        combo = self.get_combo()
        name  = combo.currentText()
        if not name:
            return
        presets = self.config.get("presets", {})
        self.config["last_model"] = name

        if name in presets:
            p = presets[name]
            for pid, widget in self.dynamic_vars.items():
                if pid in p:
                    if isinstance(widget, QLineEdit):
                        widget.setText(str(p[pid]))
                    elif isinstance(widget, QDoubleSpinBox):
                        widget.setValue(float(p[pid]))
                    elif isinstance(widget, QSpinBox):
                        widget.setValue(int(p[pid]))
                    elif isinstance(widget, QCheckBox):
                        widget.setChecked(bool(p[pid]))
            for cid in ["think_budget", "mmproj", "custom_args"]:
                w = self.custom_widgets.get(cid)
                if w and isinstance(w, QLineEdit):
                    w.setText(str(p.get(cid, "")))
            mode = p.get("think_mode", "normal")
            self._think_mode = mode
            if mode == "normal":
                self.radio_normal.setChecked(True)
            elif mode == "hide":
                self.radio_hide.setChecked(True)
            elif mode == "stop":
                self.radio_stop.setChecked(True)
        else:
            for group in DYNAMIC_UI_SCHEMA:
                for param in group["params"]:
                    pid = param["id"]
                    w   = self.dynamic_vars[pid]
                    if isinstance(w, QLineEdit):
                        w.setText(str(param["default"]))
                    elif isinstance(w, QDoubleSpinBox):
                        w.setValue(float(param["default"]))
                    elif isinstance(w, QSpinBox):
                        w.setValue(int(param["default"]))
                    elif isinstance(w, QCheckBox):
                        w.setChecked(bool(param["default"]))
            self._think_mode = "normal"
            self.radio_normal.setChecked(True)
            for cid in ["think_budget", "mmproj", "custom_args"]:
                w = self.custom_widgets.get(cid)
                if w and isinstance(w, QLineEdit):
                    w.setText("")

        self.status_label.setText(
            MSG.get("preset_loaded", "已加载配置: {name}").replace("{name}", name.strip()))

    def save_current_preset(self):
        combo = self.get_combo()
        name  = combo.currentText()
        if not name:
            return
        state = {}
        for pid, widget in self.dynamic_vars.items():
            if isinstance(widget, QLineEdit):
                state[pid] = widget.text()
            elif isinstance(widget, QDoubleSpinBox):
                state[pid] = widget.value()
            elif isinstance(widget, QSpinBox):
                state[pid] = widget.value()
            elif isinstance(widget, QCheckBox):
                state[pid] = widget.isChecked()
        state["think_mode"] = self._think_mode
        for cid in ["think_budget", "mmproj", "custom_args"]:
            w = self.custom_widgets.get(cid)
            if w and isinstance(w, QLineEdit):
                state[cid] = w.text()
        self.config.setdefault("presets", {})[name] = state
        self.config.update({
            "bin_dir":        self.custom_widgets["bin_dir"].text(),
            "model_dir":      self.custom_widgets["model_dir"].text(),
            "last_port":      self.custom_widgets["port"].text(),
            "is_server_mode": self.custom_widgets["is_server_mode"].isChecked(),
            "global_args":    self.custom_widgets["global_args"].text(),
            "last_model":     name,
        })
        self.save_settings()

    def delete_current_preset(self):
        combo = self.get_combo()
        name  = combo.currentText()
        if not name:
            return
        if self.full_paths.get(name) == "__DIRECTORY__":
            QMessageBox.information(self,
                WIN_TITLES.get("preset_info_title", "提示"),
                MSG.get("preset_folder", "当前选中的是文件夹标题。"))
            return
        presets = self.config.get("presets", {})
        if name not in presets:
            QMessageBox.information(self,
                WIN_TITLES.get("preset_info_title", "提示"),
                MSG.get("preset_no_preset", "「{name}」没有预设。").replace("{name}", name.strip()))
            return
        if QMessageBox.question(self,
                WIN_TITLES.get("preset_confirm_title", "确认删除"),
                MSG.get("preset_confirm_msg", "确定删除「{name}」的预设吗？").replace("{name}", name.strip()),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            del presets[name]
            self.save_settings()
            self.status_label.setText(
                MSG.get("preset_deleted", "已删除预设: {name}").replace("{name}", name.strip()))
            self.on_model_change(combo.currentIndex())

    # ═══════════════════════════════════════════════
    #  窗口几何
    # ═══════════════════════════════════════════════

    def closeEvent(self, event):
        # 1. 保存窗口几何与自适应缩放参数
        self._save_window_geometry()
        self.config["_font_scale"] = self._font_scale
        self.save_settings()
        # 2. 保存当前模型预设
        self.save_current_preset()
        # 3. 终止下载线程
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self._dl_thread.wait(3000)
        # 4. 终止启动线程
        if self.launch_thread and self.launch_thread.isRunning():
            if not self.custom_widgets.get("console_mode", QCheckBox()).isChecked():
                self.console.append_output("🔄 关闭窗口，正在终止进程...", "yellow")
                self.launch_thread.stop()
                self.launch_thread.wait(3000)
                self.console.append_output("✅ 进程已终止", "green")
        super().closeEvent(event)

    def _save_window_geometry(self):
        try:
            geo = self.saveGeometry()
            if geo:
                self.config["window_geometry"] = geo.toBase64().data().decode("ascii")
            state = self.windowState()
            self.config["window_state"] = int(state)
            self.config["window_width"] = self.width()
            self.config["window_height"] = self.height()
        except Exception:
            pass

    def _restore_window_geometry(self):
        # 恢复自适应字体缩放
        saved_font_scale = self.config.get("_font_scale")
        if saved_font_scale is not None:
            self._font_scale = float(saved_font_scale)
        try:
            geo_b64 = self.config.get("window_geometry")
            if geo_b64:
                geo = QByteArray.fromBase64(geo_b64.encode("ascii"))
                if not geo.isEmpty():
                    self.restoreGeometry(geo)
            state_val = self.config.get("window_state")
            if state_val is not None:
                self.setWindowState(self.windowState() | state_val)
        except Exception:
            pass
        if not self.config.get("window_geometry"):
            w = self.config.get("window_width", 800)
            h = self.config.get("window_height", 480)
            self.resize(w, h)

    # ═══════════════════════════════════════════════
    #  自适应字体缩放
    # ═══════════════════════════════════════════════

    def resizeEvent(self, event):
        if not self.config.get("auto_scale", True):
            super().resizeEvent(event)
            return
        if self._resize_timer is None:
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._apply_font_scale)
        self._resize_timer.start(200)
        super().resizeEvent(event)

    def _apply_font_scale(self):
        ref_width = 500
        window_scale = max(0.35, min(1.50, self.width() / ref_width))
        if abs(window_scale - self._font_scale) < 0.03:
            return
        self._font_scale = window_scale
        ui_scale = self.config.get("ui_scale", 1.0)
        combined = window_scale * ui_scale
        theme = self.config.get("theme", "dark")
        base_sheet = LIGHT_STYLESHEET if theme == "light" else STYLESHEET
        base_sheet = self._inject_custom_font_into_sheet(base_sheet)
        scaled_sheet = self._scale_stylesheet(base_sheet, combined)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(scaled_sheet)
        self._apply_spinbox_arrows(theme)
        if hasattr(self, 'console') and self.console:
            cf = self.console.output.font()
            cf.setPointSize(max(6, int(9 * combined)))
            self.console.output.setFont(cf)

    # ═══════════════════════════════════════════════
    #  字体加载
    # ═══════════════════════════════════════════════

    def _select_font_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择字体文件",
            self.config.get("font_path", ""),
            "字体文件 (*.ttf *.otf);;所有文件 (*)")
        if path:
            self.custom_widgets["font_path"].setText(path)
            self.config["font_path"] = path
            self._load_custom_font()
            self.save_settings()
            self.apply_theme()

    def _load_custom_font(self):
        font_path = self.config.get("font_path", "")
        self._custom_font_family = None
        if not font_path or not os.path.isfile(font_path):
            return
        try:
            db = QFontDatabase()
            font_id = db.addApplicationFont(font_path)
            if font_id >= 0:
                families = db.applicationFontFamilies(font_id)
                if families:
                    family = families[0]
                    self._custom_font_family = family
                    app = QApplication.instance()
                    if app:
                        f = QFont(family, 9)
                        app.setFont(f)
                    self.status_label.setText(
                        MSG.get("font_loaded", "已加载字体: {name}").replace("{name}", family))
            else:
                self.status_label.setText(
                    MSG.get("font_load_failed", "字体加载失败: {path}").replace("{path}", os.path.basename(font_path)))
        except Exception as e:
            self.status_label.setText(
                MSG.get("font_error", "字体错误: {err}").replace("{err}", str(e)))

    # ═══════════════════════════════════════════════
    #  可执行文件查找
    # ═══════════════════════════════════════════════

    def find_executable(self, is_server: bool = False) -> Optional[str]:
        _exe_suffix = ".exe" if sys.platform == "win32" else ""
        target = f"llama-server{_exe_suffix}" if is_server else f"llama-cli{_exe_suffix}"
        target_lower = target.lower()
        bin_dir = os.path.abspath(self.custom_widgets["bin_dir"].text())
        # 在 bin_dir 中直接查找 target 或任何 COMMON_EXES
        if os.path.isdir(bin_dir):
            p = os.path.join(bin_dir, target)
            if os.path.isfile(p):
                return p
            for exe in COMMON_EXES:
                p = os.path.join(bin_dir, exe)
                if os.path.isfile(p):
                    return p
        # 回退：在整个 BASE_DIR 下递归搜索
        all_lower = [x.lower() for x in COMMON_EXES]
        for root, _, files in os.walk(BASE_DIR):
            for f in files:
                fl = f.lower()
                if fl == target_lower or fl in all_lower:
                    found = os.path.join(root, f)
                    self.custom_widgets["bin_dir"].setText(root)
                    self.config["bin_dir"] = root
                    self.save_settings()
                    return found
        return None

    # ═══════════════════════════════════════════════
    #  构建命令
    # ═══════════════════════════════════════════════

    def build_command_args(self) -> Optional[list]:
        combo    = self.get_combo()
        name     = combo.currentText()
        rel_path = self.full_paths.get(name)
        if not rel_path or rel_path == "__DIRECTORY__":
            QMessageBox.warning(self, "启动错误",
                MSG.get("startup_error_no_model", "请选择具体的模型文件。"))
            return None

        is_server = self.custom_widgets["is_server_mode"].isChecked()
        exe = self.find_executable(is_server)
        if not exe:
            QMessageBox.critical(self, "启动错误",
                MSG.get("startup_error_no_exe", "找不到可执行文件，请检查 Bin 目录。"))
            return None

        model_path = os.path.abspath(os.path.normpath(
            os.path.join(self.custom_widgets["model_dir"].text(), rel_path)))
        if not os.path.exists(model_path):
            QMessageBox.critical(self, "启动错误",
                MSG.get("startup_error_model_missing", "模型文件不存在:\n{path}").replace("{path}", model_path))
            return None

        args = [exe, "-m", model_path]

        for group in DYNAMIC_UI_SCHEMA:
            for param in group["params"]:
                pid = param["id"]
                w   = self.dynamic_vars[pid]
                if isinstance(w, QCheckBox):
                    if w.isChecked():
                        bv = param.get("bool_val")
                        if bv:
                            args.extend([param["arg"], bv])
                        else:
                            args.append(param["arg"])
                else:
                    val = w.text() if isinstance(w, QLineEdit) else str(w.value())
                    if val.strip():
                        args.extend([param["arg"], val.strip()])

        if is_server:
            args += ["--port", self.custom_widgets["port"].text()]
        else:
            args += ["--color", "on", "-cnv"]

        mode   = self._think_mode
        budget = self.custom_widgets["think_budget"].text().strip()
        if mode == "normal":
            args += ["--reasoning", "on"]
            if budget and budget != "0":
                args += ["--reasoning-budget", budget]
        elif mode == "hide":
            args += ["--reasoning-format", "none", "--reasoning-budget", "0", "-rea", "off"]
        elif mode == "stop":
            args += ["--reasoning-format", "none", "-r", "</think>",
                     "--reasoning-budget", budget or "0"]

        mmproj = self.custom_widgets["mmproj"].text().strip()
        mmproj_enabled = self.custom_widgets.get("mmproj_enable", QCheckBox()).isChecked()
        if mmproj and mmproj_enabled:
            if os.path.exists(mmproj):
                args += ["--mmproj", os.path.normpath(mmproj)]
            else:
                QMessageBox.warning(self, "警告",
                    MSG.get("startup_warning_mmproj", "MMProj 文件不存在:\n{path}").replace("{path}", mmproj))

        ga = self.custom_widgets["global_args"].text().strip()
        if ga:
            args += self._split_args(ga)
        ca = self.custom_widgets["custom_args"].text().strip()
        if ca:
            args += self._split_args(ca)

        return args

    def _split_args(self, s: str) -> list:
        r = []
        i = 0
        while i < len(s):
            c = s[i]
            if c in ('"', "'"):
                quote = c
                i += 1
                start = i
                while i < len(s) and s[i] != quote:
                    i += 1
                r.append(s[start:i])
                if i < len(s):
                    i += 1
            elif c == ' ':
                i += 1
            else:
                start = i
                while i < len(s) and s[i] != ' ':
                    i += 1
                r.append(s[start:i])
        return r

    # ═══════════════════════════════════════════════
    #  命令预览
    # ═══════════════════════════════════════════════

    def show_command_preview(self):
        args = self.build_command_args()
        if args is None:
            return
        theme = str(self.config.get("theme", "dark"))
        dlg = CommandPreviewDialog(args, self, theme=theme)
        dlg.exec_()

    # ═══════════════════════════════════════════════
    #  启动逻辑
    # ═══════════════════════════════════════════════

    def launch(self):
        self.save_current_preset()
        args = self.build_command_args()
        if args is None:
            return

        is_server = self.custom_widgets["is_server_mode"].isChecked()
        use_console = self.custom_widgets["console_mode"].isChecked()

        # 如果已有进程在运行，先停止再以新参数启动
        if self.launch_thread and self.launch_thread.isRunning():
            self.console.append_output("⚠ 正在终止旧进程以应用新参数...", "yellow")
            self.launch_thread.stop()
            self.launch_thread.wait(3000)
            # 等旧线程彻底结束
            QTimer.singleShot(500, lambda args=args, is_server=is_server, use_console=use_console:
                self._do_launch(args, is_server, use_console))
            return

        self._do_launch(args, is_server, use_console)

    def _do_launch(self, args: list, is_server: bool, use_console: bool):
        if use_console:
            exe_name = os.path.basename(args[0])
            self.status_label.setText(
                MSG.get("launch_console_mode", "🖥 外部控制台: {name}").replace("{name}", exe_name))
            self.console.append_output("=" * 60, "blue")
            self.console.append_output(f"🖥 启动外部控制台: {exe_name}", "blue")
            self.console.append_output(f"📂 工作目录: {os.path.dirname(args[0])}", "blue")
            self.console.append_output("=" * 60, "blue")
            self.progress_bar.setVisible(False)
            self.btn_launch.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_preview.setEnabled(True)

            try:
                popen_kwargs = {
                    "cwd": os.path.dirname(args[0]),
                    "close_fds": True,
                }
                if sys.platform == "win32":
                    popen_kwargs["creationflags"] = 0x00000010  # CREATE_NEW_CONSOLE
                subprocess.Popen(args, **popen_kwargs)
                self.console.append_output(
                    MSG.get("launch_console_started", "✅ 外部控制台已打开，进程独立运行中。"), "green")
            except Exception as e:
                self.console.append_output(
                    MSG.get("launch_failed", "❌ 启动失败: {error}").replace("{error}", str(e)), "red")
                self.status_label.setText(
                    MSG.get("launch_failed", "❌ 启动失败: {error}").replace("{error}", str(e)))
            return

        # ── 内部捕获模式 ──
        self.tabs.setCurrentIndex(2)
        self.console.output.clear()
        self.console.append_output("=" * 60, "blue")
        self.console.append_output(f"🚀 启动命令: {os.path.basename(args[0])}", "blue")
        self.console.append_output(f"📂 工作目录: {os.path.dirname(args[0])}", "blue")
        self.console.append_output("=" * 60, "blue")

        exe_name = os.path.basename(args[0])
        self.status_label.setText(
            MSG.get("launch_starting", "⏳ 启动中: {name}...").replace("{name}", exe_name))
        self.progress_bar.setVisible(True)
        self.btn_launch.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_preview.setEnabled(False)

        self._server_ready_opened = False
        work_dir = os.path.dirname(args[0])
        self.launch_thread = LaunchThread(args, work_dir)
        self.launch_thread.output_signal.connect(self._on_process_output)
        self.launch_thread.finished_signal.connect(self._on_process_finished)
        self.launch_thread.error_signal.connect(self._on_process_error)
        self.launch_thread.start()

        QTimer.singleShot(2000, self._check_launch_status)

    def _on_process_output(self, line: str):
        color = None
        if any(kw in line.lower() for kw in ["error", "fatal", "panic", "fail"]):
            color = "red"
        elif any(kw in line.lower() for kw in ["warn", "info:", "loaded", "llm"]):
            color = "yellow"
        self.console.append_output(line, color)

        is_server = self.custom_widgets["is_server_mode"].isChecked()
        if is_server and not self._server_ready_opened:
            if any(kw in line.lower() for kw in [
                "http server listening", "listening on", "starting the main loop",
                "server is listening", "accessible via url"]):
                port = self.custom_widgets["port"].text()
                url = f"http://127.0.0.1:{port}"
                self._server_ready_opened = True
                webbrowser.open(url)
                self.console.append_output(f"🌐 已自动打开浏览器 → {url}", "blue")
                self.status_label.setText(f"🌐 Server 就绪 → {url}")

    def _on_console_input(self, text: str):
        if self.launch_thread and self.launch_thread.isRunning():
            self.console.append_output(f">>> {text}", "green")
            self.launch_thread.send_input(text)

    def _check_launch_status(self):
        if self.launch_thread and self.launch_thread.isRunning():
            self.status_label.setText(MSG.get("launch_running", "进程已在后台运行中"))

    def _on_process_finished(self, rc: int):
        self.progress_bar.setVisible(False)
        self.btn_launch.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_preview.setEnabled(True)
        if rc == 0:
            self.status_label.setText(MSG.get("launch_exit_ok", "✅ 进程已正常退出"))
        else:
            self.status_label.setText(
                MSG.get("launch_exit_fail", "⚠ 进程退出 (返回码 {code})").replace("{code}", str(rc)))

    def _on_process_error(self, err: str):
        self.progress_bar.setVisible(False)
        self.btn_launch.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_preview.setEnabled(True)
        self.status_label.setText(MSG.get("launch_failed", "❌ 启动失败"))
        QMessageBox.critical(self, MSG.get("launch_failed", "启动失败"), err)

    def stop_launch(self):
        """停止按钮：终止所有子线程（llama 启动进程 + aria2c 下载）。"""
        stopped = False
        # 终止 llama 启动线程
        if self.launch_thread and self.launch_thread.isRunning():
            self.launch_thread.stop()
            self.status_label.setText(MSG.get("launch_stopping", "⏹ 正在停止进程..."))
            stopped = True
        # 终止 aria2c 下载线程
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.cancel()
            self.btn_fetch.setText("📡 获取可用文件")
            self.status_label.setText(MSG.get("download_cancelled", "下载已取消"))
            stopped = True
        if stopped:
            self.btn_stop.setEnabled(False)


# ═══════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════

def main():
    # ── 修复 Qt 平台插件路径（uv / start.bat 等进程隔离场景）──
    if sys.platform == "win32":
        try:
            import PyQt5
            _pkg = os.path.dirname(PyQt5.__file__)
            # 注册 DLL 目录（Qt5/bin）确保 qwindows.dll 等被发现
            _bin = os.path.join(_pkg, "Qt5", "bin")
            if os.path.isdir(_bin):
                os.add_dll_directory(_bin)
            # 显式设置插件路径（Qt5/plugins）
            _plugins = os.path.join(_pkg, "Qt5", "plugins")
            if os.path.isdir(_plugins):
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _plugins
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    except AttributeError:
        pass
    try:
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = LlamaProLauncher()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

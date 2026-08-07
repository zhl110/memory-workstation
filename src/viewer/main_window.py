"""Memory Workstation v2 — 桌面端完整主窗口
~1800行，4页 + Sidebar + DetailPanel + MemoryCard + 无边框窗口 + 托盘
"""
from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot, QUrl, QSize, Signal, QObject, QPoint, QRect, QEvent
from PySide6.QtGui import (
    QIcon, QFont, QColor, QAction, QCursor, QPainter, QPen, QPixmap,
    QPalette, QFontDatabase,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem,
    QLabel, QLineEdit, QPushButton, QTextBrowser, QTextEdit,
    QStatusBar, QSplitter, QFrame, QStackedWidget, QScrollArea,
    QFormLayout, QComboBox, QFileDialog, QMessageBox, QDialog,
    QMenu, QMenuBar, QSystemTrayIcon, QStyle, QSizePolicy, QGroupBox,
    QSpinBox, QDialogButtonBox, QAbstractItemView,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from .file_bridge import DataBridge, MemoryItem, CategoryNode

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  主题 & 样式
# ═══════════════════════════════════════════════════════════════
BG_DARK = "#1e1e2e"
BG_MID = "#2b2b3b"
BG_LIGHT = "#363648"
BG_HOVER = "#40405a"
BG_SELECTED = "#2a3a4a"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c0c0d0"
TEXT_MUTED = "#8b8fa3"
ACCENT = "#7aa2f7"
ACCENT_HOVER = "#89b4fa"
BORDER = "#44445a"

DARK_THEME = f"""
QMainWindow, QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
}}
QMenuBar {{
    background-color: {BG_DARK};
    color: {TEXT_SECONDARY};
    border-bottom: 1px solid {BORDER};
    padding: 2px;
}}
QMenuBar::item {{
    padding: 4px 8px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {BG_HOVER};
}}
QMenu {{
    background-color: {BG_LIGHT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {ACCENT};
}}
QStatusBar {{
    background-color: {BG_DARK};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QScrollBar:vertical {{
    background-color: {BG_MID};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background-color: {BG_HOVER};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""

# ─── 图标资源路径 ──────────────────────────────────────────
_RES_DIR = Path(__file__).resolve().parent / "resources"


def _icon(name: str, size: int = 32) -> QIcon:
    """加载图标，优先32px，fallback到64px"""
    for s in [size, 64]:
        p = _RES_DIR / f"{name}_{s}.png"
        if p.exists():
            return QIcon(str(p))
    return QIcon()


# ═══════════════════════════════════════════════════════════════
#  无边框窗口基类
# ═══════════════════════════════════════════════════════════════
class FramelessWindow(QMainWindow):
    """无边框窗口，支持拖拽缩放"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMouseTracking(True)
        self._drag_pos = None
        self._resize_edge = None
        self._edge_size = 8
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self:
            if event.type() == QEvent.MouseMove:
                if self._resize_edge:
                    self._do_resize(event.globalPos())
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                self._resize_edge = None
                self.setCursor(Qt.ArrowCursor)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            rect = self.rect()
            edge = self._get_edge(pos, rect)
            if edge:
                self._resize_edge = edge
                self._drag_pos = event.globalPos()
                event.accept()
            elif pos.y() < 40:  # 标题栏区域
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edge:
            self._do_resize(event.globalPos())
        elif self._drag_pos and event.buttons() & Qt.LeftButton:
            if event.pos().y() < 40:
                self.move(event.globalPos() - self._drag_pos)
                event.accept()
        else:
            # 显示缩放箭头
            pos = event.pos()
            rect = self.rect()
            edge = self._get_edge(pos, rect)
            if edge:
                cursors = {
                    "left": Qt.SizeHorCursor,
                    "right": Qt.SizeHorCursor,
                    "top": Qt.SizeVerCursor,
                    "bottom": Qt.SizeVerCursor,
                    "topleft": Qt.SizeFDiagCursor,
                    "bottomright": Qt.SizeFDiagCursor,
                    "topright": Qt.SizeBDiagCursor,
                    "bottomleft": Qt.SizeBDiagCursor,
                }
                self.setCursor(cursors.get(edge, Qt.ArrowCursor))
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._resize_edge = None
        self.setCursor(Qt.ArrowCursor)

    def _get_edge(self, pos, rect):
        edge = None
        if pos.x() <= self._edge_size:
            edge = "left"
        elif pos.x() >= rect.width() - self._edge_size:
            edge = "right"
        if pos.y() <= self._edge_size:
            edge = "top" + (edge or "")
        elif pos.y() >= rect.height() - self._edge_size:
            edge = "bottom" + (edge or "")
        return edge if edge else None

    def _do_resize(self, global_pos):
        if not self._resize_edge:
            return
        diff = global_pos - self._drag_pos
        geom = self.geometry()
        if "right" in self._resize_edge:
            geom.setRight(geom.right() + diff.x())
        if "bottom" in self._resize_edge:
            geom.setBottom(geom.bottom() + diff.y())
        if "left" in self._resize_edge:
            geom.setLeft(geom.left() + diff.x())
        if "top" in self._resize_edge:
            geom.setTop(geom.top() + diff.y())
        self.setGeometry(geom)
        self._drag_pos = global_pos

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 40:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()


# ═══════════════════════════════════════════════════════════════
#  Sidebar（侧边栏）
# ═══════════════════════════════════════════════════════════════
class Sidebar(QWidget):
    pageChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(140)
        self.setMaximumWidth(400)
        self.setStyleSheet(f"""
            background-color: {BG_DARK};
            border-right: 1px solid {BORDER};
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo
        logo_frame = QFrame()
        logo_frame.setFixedHeight(48)
        logo_frame.setStyleSheet(f"background-color: {BG_DARK}; border-bottom: 1px solid {BORDER};")
        logo_layout = QHBoxLayout(logo_frame)
        logo_layout.setContentsMargins(12, 0, 12, 0)
        logo_label = QLabel("MW")
        logo_label.setStyleSheet(f"color: {ACCENT}; font-size: 18px; font-weight: bold;")
        logo_layout.addWidget(logo_label)
        logo_layout.addStretch()
        layout.addWidget(logo_frame)

        # Search
        search_frame = QFrame()
        search_frame.setFixedHeight(44)
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(8, 8, 8, 8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索记忆...")
        self.search_box.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        search_layout.addWidget(self.search_box)
        layout.addWidget(search_frame)

        # Navigation buttons
        nav_frame = QFrame()
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(4)

        self._buttons = []
        icons = ['browse', 'graph', 'workbench', 'settings']
        tips = ['浏览', '图谱', '工作台', '设置']

        for i, (ic, tip) in enumerate(zip(icons, tips)):
            btn = QPushButton(f"  {tip}")
            btn.setIcon(_icon(ic))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {TEXT_SECONDARY};
                    border: none;
                    border-radius: 6px;
                    text-align: left;
                    padding-left: 12px;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {BG_HOVER}; color: {TEXT_PRIMARY}; }}
                QPushButton:checked {{ background: {ACCENT}; color: {TEXT_PRIMARY}; }}
            """)
            btn.clicked.connect(lambda checked, idx=i: self._on_click(idx))
            nav_layout.addWidget(btn)
            self._buttons.append(btn)

        nav_layout.addStretch()
        layout.addWidget(nav_frame, 1)

        # Category tree
        tree_frame = QFrame()
        tree_layout = QVBoxLayout(tree_frame)
        tree_layout.setContentsMargins(8, 0, 8, 8)
        tree_label = QLabel("分类")
        tree_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 4px;")
        tree_layout.addWidget(tree_label)

        self.category_tree = QTreeWidget()
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: transparent;
                color: {TEXT_SECONDARY};
                border: none;
                font-size: 12px;
            }}
            QTreeWidget::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QTreeWidget::item:hover {{ background-color: {BG_HOVER}; }}
            QTreeWidget::item:selected {{ background-color: {ACCENT}; color: {TEXT_PRIMARY}; }}
        """)
        tree_layout.addWidget(self.category_tree, 1)
        layout.addWidget(tree_frame, 1)

        # Stats
        stats_frame = QFrame()
        stats_frame.setFixedHeight(40)
        stats_frame.setStyleSheet(f"border-top: 1px solid {BORDER};")
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(12, 0, 12, 0)
        self.stat_total = QLabel("总计 0")
        self.stat_total.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self.stat_graph = QLabel("图谱 0/0")
        self.stat_graph.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        stats_layout.addWidget(self.stat_total)
        stats_layout.addStretch()
        stats_layout.addWidget(self.stat_graph)
        layout.addWidget(stats_frame)

        if self._buttons:
            self._buttons[0].setChecked(True)

    def _on_click(self, idx: int):
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == idx)
        self.pageChanged.emit(idx)

    def update_stats(self, stats: dict):
        self.stat_total.setText(f"总计 {stats.get('total', 0)}")
        self.stat_graph.setText(f"图谱 {stats.get('graph_nodes', 0)}/{stats.get('graph_edges', 0)}")

    def set_categories(self, categories: list[CategoryNode]):
        self.category_tree.clear()
        root = self.category_tree.invisibleRootItem()
        for cat in categories:
            item = QTreeWidgetItem(root, [f"{cat.name} ({cat.count})"])
            item.setData(0, Qt.UserRole, cat.name)
        self.category_tree.expandAll()


# ═══════════════════════════════════════════════════════════════
#  DetailPanel（详情面板）
# ═══════════════════════════════════════════════════════════════
class DetailPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setStyleSheet(f"""
            background-color: {BG_MID};
            border-left: 1px solid {BORDER};
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title
        self.title_label = QLabel("选择一个记忆")
        self.title_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        # Meta
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.meta_label)

        # Tags
        self.tags_label = QLabel("")
        self.tags_label.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")
        self.tags_label.setWordWrap(True)
        layout.addWidget(self.tags_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {BORDER}; max-height: 1px;")
        layout.addWidget(sep)

        # Content
        self.content_browser = QTextBrowser()
        self.content_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                selection-background-color: {ACCENT};
            }}
        """)
        self.content_browser.setOpenExternalLinks(True)
        layout.addWidget(self.content_browser, 1)

        # Links
        self.links_label = QLabel("")
        self.links_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self.links_label.setWordWrap(True)
        layout.addWidget(self.links_label)

    def show_memory(self, mem: MemoryItem):
        self.title_label.setText(mem.label)
        meta_parts = [
            f"分类: {mem.category}",
            f"重要性: {mem.importance}",
            f"权重: {mem.weight}",
        ]
        if mem.file_path:
            meta_parts.append(f"来源: {mem.file_path.split('/')[-1]}")
        self.meta_label.setText(" | ".join(meta_parts))

        if mem.tags:
            self.tags_label.setText("标签: " + ", ".join(mem.tags))
        else:
            self.tags_label.setText("")

        content = mem.content or mem.summary or "无内容"
        self.content_browser.setMarkdown(content)

        if mem.wikilinks:
            links_text = "关联: " + " → ".join(mem.wikilinks[:5])
            if len(mem.wikilinks) > 5:
                links_text += f" ... (+{len(mem.wikilinks) - 5})"
            self.links_label.setText(links_text)
        else:
            self.links_label.setText("")

    def clear(self):
        self.title_label.setText("选择一个记忆")
        self.meta_label.setText("")
        self.tags_label.setText("")
        self.content_browser.clear()
        self.links_label.setText("")


# ═══════════════════════════════════════════════════════════════
#  EditDialog（编辑对话框）
# ═══════════════════════════════════════════════════════════════
class EditDialog(QDialog):
    def __init__(self, mem: MemoryItem, categories: list[str], parent=None):
        super().__init__(parent)
        self._mem = mem
        self.setWindowTitle(f"编辑: {mem.label}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_MID}; }}
            QLabel {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
            QLineEdit, QTextEdit, QComboBox, QSpinBox {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
            QPushButton {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; }}
            QPushButton#saveBtn {{
                background-color: {ACCENT};
                border: none;
                font-weight: 500;
            }}
            QPushButton#saveBtn:hover {{ background-color: {ACCENT_HOVER}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QLabel(f"编辑记忆 #{mem.doc_id}")
        header.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(10)

        # Label
        self.label_edit = QLineEdit(mem.label)
        form.addRow("标签:", self.label_edit)

        # Category
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(categories)
        idx = self.category_combo.findText(mem.category)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        else:
            self.category_combo.setCurrentText(mem.category)
        form.addRow("分类:", self.category_combo)

        # Importance
        self.importance_combo = QComboBox()
        self.importance_combo.addItems(["P0", "P1", "P2", "P3", "P4"])
        idx = self.importance_combo.findText(mem.importance)
        if idx >= 0:
            self.importance_combo.setCurrentIndex(idx)
        form.addRow("重要性:", self.importance_combo)

        # Weight
        weight_layout = QHBoxLayout()
        self.weight_spin = QSpinBox()
        self.weight_spin.setRange(0, 100)
        self.weight_spin.setValue(mem.weight)
        self.weight_spin.setSuffix("%")
        weight_layout.addWidget(self.weight_spin, 1)
        for w in [10, 30, 50, 70, 90]:
            btn = QPushButton(str(w))
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda checked, v=w: self.weight_spin.setValue(v))
            weight_layout.addWidget(btn)
        form.addRow("权重:", weight_layout)

        # Summary
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlainText(mem.summary)
        self.summary_edit.setMaximumHeight(100)
        form.addRow("摘要:", self.summary_edit)

        # Tags
        self.tags_edit = QLineEdit(", ".join(mem.tags))
        self.tags_edit.setPlaceholderText("用逗号分隔")
        form.addRow("标签:", self.tags_edit)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self.accept)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def get_data(self) -> dict:
        return {
            "label": self.label_edit.text().strip(),
            "content_category": self.category_combo.currentText().strip(),
            "importance": self.importance_combo.currentText(),
            "weight": self.weight_spin.value(),
            "summary": self.summary_edit.toPlainText().strip(),
            "tags": self.tags_edit.text().strip(),
        }


# ═══════════════════════════════════════════════════════════════
#  MemoryCard（记忆卡片）
# ═══════════════════════════════════════════════════════════════
class MemoryCard(QFrame):
    clicked = Signal(int)
    editRequested = Signal(int)
    deleteRequested = Signal(int)
    reclassifyRequested = Signal(int, str)
    weightChanged = Signal(int, int)

    def __init__(self, mem: MemoryItem, parent=None):
        super().__init__(parent)
        self._mem = mem
        self.setFixedHeight(72)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setStyleSheet(f"""
            MemoryCard {{
                background-color: {BG_LIGHT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                margin: 2px 0;
            }}
            MemoryCard:hover {{
                background-color: {BG_HOVER};
                border-color: {ACCENT};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        # Importance bar
        imp_colors = {"P0": "#f7768e", "P1": "#ff9e64", "P2": "#7aa2f7", "P3": "#9ece6a", "P4": "#7c7f93"}
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background-color: {imp_colors.get(mem.importance, '#7c7f93')}; border-radius: 2px;")
        layout.addWidget(bar)

        # Content
        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)

        title_row = QHBoxLayout()
        title = QLabel(mem.label)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 500;")
        title_row.addWidget(title, 1)

        weight_badge = QLabel(f"{mem.weight}")
        weight_badge.setStyleSheet(f"""
            color: {TEXT_MUTED};
            background-color: {BG_MID};
            border-radius: 8px;
            padding: 2px 6px;
            font-size: 10px;
        """)
        title_row.addWidget(weight_badge)
        content_layout.addLayout(title_row)

        meta_row = QHBoxLayout()
        cat_label = QLabel(mem.category)
        cat_label.setStyleSheet(f"color: {ACCENT}; font-size: 11px;")
        meta_row.addWidget(cat_label)
        meta_row.addWidget(QLabel("|"))
        imp_label = QLabel(mem.importance)
        imp_label.setStyleSheet(f"color: {imp_colors.get(mem.importance, '#7c7f93')}; font-size: 11px;")
        meta_row.addWidget(imp_label)
        meta_row.addStretch()
        content_layout.addLayout(meta_row)

        if mem.summary:
            summary = QLabel(mem.summary[:80] + ("..." if len(mem.summary) > 80 else ""))
            summary.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
            content_layout.addWidget(summary)

        layout.addLayout(content_layout, 1)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {ACCENT}; }}
        """)

        menu.addAction("编辑").triggered.connect(lambda: self.editRequested.emit(self._mem.doc_id))
        menu.addSeparator()

        weight_menu = menu.addMenu("调整权重")
        for delta in [+10, +5, -5, -10]:
            label = f"{'↑' if delta > 0 else ''}{delta:+d}"
            weight_menu.addAction(label).triggered.connect(
                lambda checked, d=delta: self.weightChanged.emit(self._mem.doc_id, d)
            )

        menu.addSeparator()
        reclass_menu = menu.addMenu("快速分类")
        for cat in ["工具类", "代码类", "项目记录", "踩坑经验", "用户信息", "安全类", "架构决策"]:
            if cat != self._mem.category:
                reclass_menu.addAction(cat).triggered.connect(
                    lambda checked, c=cat: self.reclassifyRequested.emit(self._mem.doc_id, c)
                )

        menu.addSeparator()
        menu.addAction("删除").triggered.connect(lambda: self.deleteRequested.emit(self._mem.doc_id))

        menu.exec_(self.mapToGlobal(pos))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._mem.doc_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.editRequested.emit(self._mem.doc_id)
        super().mouseDoubleClickEvent(event)


# ═══════════════════════════════════════════════════════════════
#  BrowsePage（浏览页）
# ═══════════════════════════════════════════════════════════════
class BrowsePage(QWidget):
    def __init__(self, bridge: DataBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._current_category = None
        self.setStyleSheet(f"background-color: {BG_MID};")
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 使用 QSplitter 支持拖拽调整
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {BORDER};
                width: 2px;
            }}
            QSplitter::handle:hover {{
                background-color: {ACCENT};
            }}
        """)

        # Memory list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.memory_scroll = QScrollArea()
        self.memory_scroll.setWidgetResizable(True)
        self.memory_scroll.setStyleSheet(f"QScrollArea {{ background-color: {BG_MID}; border: none; }}")
        self.memory_container = QWidget()
        self.memory_layout = QVBoxLayout(self.memory_container)
        self.memory_layout.setContentsMargins(12, 12, 12, 12)
        self.memory_layout.setSpacing(4)
        self.memory_layout.addStretch()
        self.memory_scroll.setWidget(self.memory_container)
        left_layout.addWidget(self.memory_scroll)
        splitter.addWidget(left_widget)

        # Detail panel
        self.detail_panel = DetailPanel()
        splitter.addWidget(self.detail_panel)

        splitter.setSizes([600, 400])
        layout.addWidget(splitter)

    def _load_memories(self, category: str = None):
        while self.memory_layout.count():
            item = self.memory_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._current_category = category
        memories = self.bridge.get_memories()
        if category:
            memories = [m for m in memories if m.category == category]

        for mem in memories:
            card = MemoryCard(mem)
            card.clicked.connect(self._on_card_clicked)
            card.editRequested.connect(self._on_edit_requested)
            card.deleteRequested.connect(self._on_delete_requested)
            card.reclassifyRequested.connect(self._on_reclassify_requested)
            card.weightChanged.connect(self._on_weight_changed)
            self.memory_layout.addWidget(card)

        self.memory_layout.addStretch()

    def _on_card_clicked(self, doc_id: int):
        mem = self.bridge.get_memory(doc_id)
        if mem:
            self.detail_panel.show_memory(mem)

    def _on_edit_requested(self, doc_id: int):
        mem = self.bridge.get_memory(doc_id)
        if not mem:
            return
        categories = [c.name for c in self.bridge.get_categories()]
        dlg = EditDialog(mem, categories, self)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            self.bridge.update_memory(doc_id, **data)
            self._load_memories(self._current_category)
            updated = self.bridge.get_memory(doc_id)
            if updated:
                self.detail_panel.show_memory(updated)

    def _on_delete_requested(self, doc_id: int):
        mem = self.bridge.get_memory(doc_id)
        if not mem:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除记忆 '{mem.label}' 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.bridge.delete_memory(doc_id)
            self._load_memories(self._current_category)
            self.detail_panel.clear()

    def _on_reclassify_requested(self, doc_id: int, new_category: str):
        self.bridge.reclassify_memory(doc_id, new_category)
        self._load_memories(self._current_category)

    def _on_weight_changed(self, doc_id: int, delta: int):
        self.bridge.adjust_weight(doc_id, delta)
        self._load_memories(self._current_category)

    def search(self, text: str):
        while self.memory_layout.count():
            item = self.memory_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not text:
            self._load_memories()
            return

        results = self.bridge.search_memories(text)
        for mem in results:
            card = MemoryCard(mem)
            card.clicked.connect(self._on_card_clicked)
            card.editRequested.connect(self._on_edit_requested)
            card.deleteRequested.connect(self._on_delete_requested)
            card.reclassifyRequested.connect(self._on_reclassify_requested)
            card.weightChanged.connect(self._on_weight_changed)
            self.memory_layout.addWidget(card)
        self.memory_layout.addStretch()

    def refresh(self):
        self._load_memories(self._current_category)


# ═══════════════════════════════════════════════════════════════
#  Graph Bridge + Page
# ═══════════════════════════════════════════════════════════════
class GraphBridge(QObject):
    updateGraph = Signal(str)
    nodeClicked = Signal(int, str, str)

    def __init__(self):
        super().__init__()

    @Slot(str)
    def on_node_clicked(self, data_json: str):
        try:
            data = json.loads(data_json)
            self.nodeClicked.emit(data.get("id", 0), data.get("label", ""), data.get("category", ""))
        except Exception:
            pass


class GraphPage(QWidget):
    def __init__(self, bridge: DataBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background-color: {BG_MID};")
        self._graph_bridge = GraphBridge()
        self._graph_bridge.nodeClicked.connect(self._on_node_clicked)
        self._webview = None
        self._current_center = None
        self._page_ready = False
        self._pending_data = None
        self._setup_ui()
        QTimer.singleShot(500, self._load_graph)

    def _setup_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)

        web_path = Path(__file__).resolve().parent / "web" / "graph.html"
        if not web_path.is_file():
            t = QLabel("graph.html not found")
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet(f"font-size: 16px; color: {TEXT_MUTED};")
            l.addWidget(t)
            return

        channel = QWebChannel()
        channel.registerObject("graphBridge", self._graph_bridge)

        self._webview = QWebEngineView()
        self._webview.page().setWebChannel(channel)
        self._webview.setUrl(QUrl.fromLocalFile(str(web_path)))
        self._webview.loadFinished.connect(self._on_page_loaded)
        l.addWidget(self._webview)

    def _on_page_loaded(self, ok):
        if ok:
            QTimer.singleShot(300, self._flush_pending)

    def _flush_pending(self):
        self._page_ready = True
        if self._pending_data:
            self._push_data(self._pending_data)
            self._pending_data = None

    def _push_data(self, json_data: str):
        safe_json = json.dumps(json_data)
        js = f"window.pushGraphData({safe_json});"
        self._webview.page().runJavaScript(js)

    def _load_graph(self):
        data = self.bridge.get_graph_data(center_id=self._current_center, max_nodes=300)
        json_data = json.dumps({"nodes": data.nodes, "edges": data.edges})
        if self._page_ready and self._webview:
            self._push_data(json_data)
        else:
            self._pending_data = json_data
        self._graph_bridge.updateGraph.emit(json_data)

    def _on_node_clicked(self, doc_id: int, label: str, category: str):
        self._current_center = doc_id
        self._load_graph()

    def refresh(self):
        self._current_center = None
        self._load_graph()


# ═══════════════════════════════════════════════════════════════
#  WorkbenchPage（工作台）
# ═══════════════════════════════════════════════════════════════
class WorkbenchPage(QWidget):
    def __init__(self, bridge: DataBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._staged_files: list[str] = []
        self._staging_dir = self._get_staging_dir()
        self.setStyleSheet(f"background-color: {BG_MID};")
        self._setup_ui()
        self._load_staging_dir()

    def _get_staging_dir(self) -> str:
        import tempfile
        base = os.environ.get("MW_DEV_DATA_HOME", tempfile.gettempdir())
        d = os.path.join(base, "staging")
        os.makedirs(d, exist_ok=True)
        return d

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        header = QLabel("工作台")
        header.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)

        # Left: Staging area
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("暂存区")
        left_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        left_layout.addWidget(left_label)
        self.file_list = QListWidget()
        self.file_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            QListWidget::item:hover {{ background-color: {BG_HOVER}; }}
            QListWidget::item:selected {{ background-color: {ACCENT}; }}
        """)
        left_layout.addWidget(self.file_list, 1)
        splitter.addWidget(left_widget)

        # Center: Preview
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_label = QLabel("文件预览")
        center_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        center_layout.addWidget(center_label)
        self.preview_browser = QTextBrowser()
        self.preview_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 12px;
            }}
        """)
        center_layout.addWidget(self.preview_browser, 1)
        splitter.addWidget(center_widget)

        # Right: AI Chat
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("AI 对话")
        right_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        right_layout.addWidget(right_label)
        self.chat_display = QTextBrowser()
        self.chat_display.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 12px;
            }}
        """)
        right_layout.addWidget(self.chat_display, 1)

        input_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("输入消息...")
        self.chat_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
            }}
            QLineEdit:focus {{ border-color: {ACCENT}; }}
        """)
        input_row.addWidget(self.chat_input, 1)
        send_btn = QPushButton("发送")
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: {TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        """)
        input_row.addWidget(send_btn)
        right_layout.addLayout(input_row)
        splitter.addWidget(right_widget)

        splitter.setSizes([200, 400, 300])
        layout.addWidget(splitter, 1)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        import_btn = QPushButton("导入文件")
        import_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: {TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        """)
        import_btn.clicked.connect(self._on_import)
        btn_layout.addWidget(import_btn)

        paste_btn = QPushButton("粘贴导入")
        paste_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; }}
        """)
        btn_layout.addWidget(paste_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; }}
        """)
        refresh_btn.clicked.connect(self._load_staging_dir)
        btn_layout.addWidget(refresh_btn)

        layout.addLayout(btn_layout)

    def _load_staging_dir(self):
        self._staged_files.clear()
        self.file_list.clear()
        count = 0
        for f in sorted(os.listdir(self._staging_dir)):
            fp = os.path.join(self._staging_dir, f)
            if os.path.isfile(fp):
                self._staged_files.append(fp)
                self.file_list.addItem(f)
                count += 1
        if count == 0:
            self.file_list.addItem("暂无待处理文件")

    def _on_import(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的文件", "",
            "All Files (*)"
        )
        if files:
            import shutil
            for f in files:
                dest = os.path.join(self._staging_dir, os.path.basename(f))
                shutil.copy2(f, dest)
            self._load_staging_dir()

    def refresh(self):
        self._load_staging_dir()


# ═══════════════════════════════════════════════════════════════
#  SettingsPage（设置页）
# ═══════════════════════════════════════════════════════════════
class SettingsPage(QWidget):
    def __init__(self, bridge: DataBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background-color: {BG_MID};")
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {BG_MID}; border: none; }}")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QLabel("设置")
        header.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: bold;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(12)

        style = f"""
            QLabel {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
            QLineEdit, QComboBox {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
        """

        # Data directory
        self.md_dir_edit = QLineEdit()
        self.md_dir_edit.setStyleSheet(style)
        browse_btn = QPushButton("浏览")
        browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; }}
        """)
        browse_btn.clicked.connect(self._on_browse)
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.md_dir_edit, 1)
        dir_layout.addWidget(browse_btn)
        form.addRow("数据目录:", dir_layout)

        # LLM Provider
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["Ollama（本地）", "DeepSeek", "OpenAI", "自定义"])
        self.provider_combo.setStyleSheet(style)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("LLM 提供商:", self.provider_combo)

        # API Base URL
        self.api_url_edit = QLineEdit()
        self.api_url_edit.setStyleSheet(style)
        form.addRow("API 地址:", self.api_url_edit)

        # API Key
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setStyleSheet(style)
        form.addRow("API Key:", self.api_key_edit)

        # Model
        model_layout = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setStyleSheet(style)
        model_layout.addWidget(self.model_combo, 1)

        fetch_btn = QPushButton("获取模型")
        fetch_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: {TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        """)
        fetch_btn.clicked.connect(self._on_fetch_models)
        model_layout.addWidget(fetch_btn)

        test_btn = QPushButton("测试连接")
        test_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; }}
        """)
        test_btn.clicked.connect(self._on_test_connection)
        model_layout.addWidget(test_btn)
        form.addRow("模型:", model_layout)

        # Log level
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level_combo.setStyleSheet(style)
        form.addRow("日志级别:", self.log_level_combo)

        layout.addLayout(form)
        layout.addStretch()

        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: {TEXT_PRIMARY};
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        """)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

        scroll.setWidget(content)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _load_settings(self):
        settings = self.bridge.get_settings()
        self.md_dir_edit.setText(settings.get("md_dir", ""))
        self.api_url_edit.setText(settings.get("api_base_url", ""))
        self.api_key_edit.setText(settings.get("api_key", ""))
        self.log_level_combo.setCurrentText(settings.get("log_level", "INFO"))
        provider = settings.get("api_provider", "Ollama（本地）")
        idx = self.provider_combo.findText(provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        model = settings.get("api_model", "")
        if model:
            self.model_combo.setCurrentText(model)

    def _on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据目录")
        if d:
            self.md_dir_edit.setText(d)

    def _on_provider_changed(self, provider: str):
        urls = {
            "Ollama（本地）": "http://localhost:11434/v1",
            "DeepSeek": "https://api.deepseek.com/v1",
            "OpenAI": "https://api.openai.com/v1",
        }
        url = urls.get(provider, "")
        if url:
            self.api_url_edit.setText(url)

    def _on_fetch_models(self):
        base_url = self.api_url_edit.text()
        api_key = self.api_key_edit.text()
        if not base_url:
            QMessageBox.warning(self, "提示", "请先填写 API 地址")
            return
        result = self.bridge.fetch_models(base_url, api_key)
        self.model_combo.clear()
        if result.get("success"):
            models = result.get("models", [])
            self.model_combo.addItems(models)
            QMessageBox.information(self, "成功", f"获取到 {len(models)} 个模型")
        else:
            QMessageBox.warning(self, "获取失败", result.get("error", "未知错误"))

    def _on_test_connection(self):
        base_url = self.api_url_edit.text()
        api_key = self.api_key_edit.text()
        if not base_url:
            QMessageBox.warning(self, "提示", "请先填写 API 地址")
            return
        result = self.bridge.fetch_models(base_url, api_key)
        if result.get("success"):
            QMessageBox.information(self, "成功", "连接正常")
        else:
            QMessageBox.warning(self, "连接失败", result.get("error", "未知错误"))

    def _on_save(self):
        settings = {
            "md_dir": self.md_dir_edit.text(),
            "api_provider": self.provider_combo.currentText(),
            "api_base_url": self.api_url_edit.text(),
            "api_key": self.api_key_edit.text(),
            "api_model": self.model_combo.currentText(),
            "log_level": self.log_level_combo.currentText(),
        }
        if self.bridge.save_settings(settings):
            QMessageBox.information(self, "成功", "设置已保存")
        else:
            QMessageBox.warning(self, "失败", "保存设置失败")

    def refresh(self):
        self._load_settings()


# ═══════════════════════════════════════════════════════════════
#  MainWindow（主窗口）
# ═══════════════════════════════════════════════════════════════
class MainWindow(FramelessWindow):
    def __init__(self, bridge: DataBridge):
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("Memory Workstation v2")
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)
        self.setStyleSheet(DARK_THEME)

        # Central widget
        central = QWidget()
        central.setStyleSheet(f"background-color: {BG_DARK};")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Custom title bar
        title_bar = QFrame()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet(f"background-color: {BG_DARK}; border-bottom: 1px solid {BORDER};")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 0, 8, 0)

        logo = QLabel("MW")
        logo.setStyleSheet(f"color: {ACCENT}; font-size: 14px; font-weight: bold;")
        title_layout.addWidget(logo)

        title_text = QLabel("Memory Workstation")
        title_text.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 13px;")
        title_layout.addWidget(title_text)

        title_layout.addStretch()

        # Menu bar
        menu_bar = QMenuBar()
        menu_bar.setStyleSheet(f"""
            QMenuBar {{
                background: transparent;
                color: {TEXT_SECONDARY};
                border: none;
            }}
            QMenuBar::item:selected {{ background-color: {BG_HOVER}; border-radius: 4px; }}
        """)

        file_menu = menu_bar.addMenu("文件")
        file_menu.addAction("导入文件")
        file_menu.addSeparator()
        file_menu.addAction("退出").triggered.connect(self.close)

        view_menu = menu_bar.addMenu("视图")
        self._view_actions = []
        for i, name in enumerate(["浏览", "图谱", "工作台", "设置"]):
            action = view_menu.addAction(name)
            action.triggered.connect(lambda checked, idx=i: self._on_page_changed(idx))
            self._view_actions.append(action)

        help_menu = menu_bar.addMenu("帮助")
        help_menu.addAction("关于")

        title_layout.addWidget(menu_bar)

        # Window controls
        for icon_char, slot in [("─", self.showMinimized), ("□", self._toggle_maximize), ("✕", self.close)]:
            btn = QPushButton(icon_char)
            btn.setFixedSize(32, 28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {TEXT_SECONDARY};
                    border: none;
                    font-size: 14px;
                }}
                QPushButton:hover {{ background-color: {BG_HOVER}; }}
            """)
            btn.clicked.connect(slot)
            title_layout.addWidget(btn)

        main_layout.addWidget(title_bar)

        # Content area
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar()
        self.sidebar.pageChanged.connect(self._on_page_changed)
        self.sidebar.search_box.textChanged.connect(self._on_search)
        self.sidebar.category_tree.itemClicked.connect(self._on_category_clicked)
        content_layout.addWidget(self.sidebar)

        # Pages
        self.pages = QStackedWidget()
        self.browse_page = BrowsePage(bridge)
        self.graph_page = GraphPage(bridge)
        self.workbench_page = WorkbenchPage(bridge)
        self.settings_page = SettingsPage(bridge)

        self.pages.addWidget(self.browse_page)
        self.pages.addWidget(self.graph_page)
        self.pages.addWidget(self.workbench_page)
        self.pages.addWidget(self.settings_page)

        content_layout.addWidget(self.pages, 1)
        main_layout.addLayout(content_layout, 1)

        # Status bar
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 2px 8px;")
        status_bar = QStatusBar()
        status_bar.setFixedHeight(24)
        status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {BG_DARK};
                border-top: 1px solid {BORDER};
            }}
        """)
        status_bar.addWidget(self._status_label)
        main_layout.addWidget(status_bar)

        # System tray
        self._setup_tray()

        # Load data
        self._load_data()

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(_icon("browse"))
        self.tray.setToolTip("Memory Workstation")

        tray_menu = QMenu()
        tray_menu.setStyleSheet(f"""
            QMenu {{
                background-color: {BG_LIGHT};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
            }}
            QMenu::item:selected {{ background-color: {ACCENT}; }}
        """)
        tray_menu.addAction("显示窗口").triggered.connect(self.showNormal)
        tray_menu.addSeparator()
        tray_menu.addAction("退出").triggered.connect(QApplication.quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def closeEvent(self, event):
        self.hide()
        self.tray.showMessage("Memory Workstation", "已最小化到托盘", QSystemTrayIcon.Information, 1000)
        event.ignore()

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_page_changed(self, idx: int):
        self.pages.setCurrentIndex(idx)
        pages = [self.browse_page, self.graph_page, self.workbench_page, self.settings_page]
        if idx < len(pages):
            pages[idx].refresh()

    def _on_search(self, text: str):
        self.browse_page.search(text)

    def _on_category_clicked(self, item: QTreeWidgetItem, col: int):
        cat_name = item.data(0, Qt.UserRole)
        if cat_name:
            self.browse_page._load_memories(cat_name)

    def _load_data(self):
        self.sidebar.set_categories(self.bridge.get_categories())
        self.sidebar.update_stats(self.bridge.get_stats())
        self.browse_page._load_memories()

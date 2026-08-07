"""Memory Workstation 控制面板 — v2 重设计

三标签页布局 (ttkbootstrap darkly)：
- Tab 1「首页」：状态栏压缩 + 审核面板为主体 + 底部操作
- Tab 2「审核」：筛选器 + 全量列表 + 详情预览 + 四操作
- Tab 3「知识库」：统计 + 关键词规则 + 排除规则 + 全局规则 + 领域
- 弹窗「添加规则」：6步工作流（类型→提取→关键词→配置→排除→预览）
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from ..core.config import _MEMORY_HOME

_CONFIG_PATH = Path(_MEMORY_HOME) / "config.toml"

logger = logging.getLogger("control_panel")

UPDATE_INTERVAL_MS = 1000
_show_requested = threading.Event()
_kb_state_ref = [None]


def request_show():
    _show_requested.set()


# ═══════════════════════════════════════════════════════════
# 审核相关常量
# ═══════════════════════════════════════════════════════════

_REVIEW_LABELS = [
    "meta_rule", "config_inventory", "planning_doc",
    "self_improve_learn", "memory_layer", "chat_log",
    "compact_archive", "unknown",
]
_REVIEW_IMPORTANCE = ["P0", "P1", "P2", "P3", "P4"]
_REVIEW_CATEGORIES = [
    "AI专属类", "技术类", "业务类", "流程类", "知识类",
    "日常类", "参考类", "个人类", "交互类", "未分类",
]

# 标签颜色映射
_LABEL_STYLES = {
    "meta_rule": "rule", "config_inventory": "rule",
    "planning_doc": "know", "self_improve_learn": "mem",
    "memory_layer": "mem", "chat_log": "chat",
    "compact_archive": "chat", "unknown": "unknown",
}


# ═══════════════════════════════════════════════════════════
# 全局样式定制
# ═══════════════════════════════════════════════════════════

def _apply_custom_styles(root):
    """统一美化全局控件样式 — 简约风

    设计原则：
    - 减少边框和颜色，用 flat relief 和留白代替
    - 按钮统一 outline 样式，hover 时才填色
    - 字体层次分明：标题粗体/正文常规/辅助灰色
    - 间距统一：padding 12px / margin 8px / gap 6px
    """
    import ttkbootstrap as ttk
    style = ttk.Style()

    # ─── 按钮：统一 outline 风格，去掉廉价实心色块 ───
    # 所有按钮用 -outline 变体，hover 时才填色
    style.configure("TButton", font=("", 10), padding=(14, 8))
    style.configure("primary.TButton", font=("", 10, "bold"), padding=(16, 9))
    style.configure("outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("success-outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("danger-outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("warning-outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("info-outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("secondary-outline.TButton", font=("", 10), padding=(14, 8))
    style.configure("primary-outline.TButton", font=("", 10), padding=(14, 8))

    # ─── LabelFrame：简约标题，减少视觉噪音 ───
    style.configure("TLabelframe", padding=10)
    style.configure("TLabelframe.Label", font=("", 11, "bold"), foreground="#8b9eb5")

    # ─── Treeview：加大行高，清晰可读 ───
    style.configure("Treeview", font=("", 10), rowheight=30)
    style.configure("Treeview.Heading", font=("", 10, "bold"), foreground="#5c7089")
    style.map("Treeview", background=[("selected", "#1a3a5c")], foreground=[("selected", "#e8edf2")])

    # ─── Notebook 标签页：大标签易点 ───
    style.configure("TNotebook.Tab", font=("", 11), padding=(22, 10))

    # ─── Combobox / Entry ───
    style.configure("TCombobox", font=("", 10), padding=(8, 5))
    style.configure("TEntry", font=("", 10), padding=(8, 5))

    # ─── Label 层次 ───
    style.configure("TLabel", font=("", 10))
    style.configure("Header.TLabel", font=("", 13, "bold"), foreground="#e8edf2")
    style.configure("SubHeader.TLabel", font=("", 10), foreground="#8b9eb5")
    style.configure("Accent.TLabel", font=("", 11, "bold"), foreground="#3b82f6")

    # ─── Progressbar：细线条 ───
    style.configure("success-striped.Horizontal.TProgressbar", thickness=6)


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def _build_window(ctx):
    """构建控制面板主窗口 — Notebook 三标签页布局

    Args:
        ctx: AppContext 实例，包含 config/storage/scanner/pipeline/llm 等全部组件
    """
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import filedialog, messagebox

    root = ttk.Window(themename="darkly")
    root.title("Memory Workstation 控制面板")
    # 窗口尺寸：加宽到960给列表足够空间
    root.geometry("960x940")
    root.minsize(800, 720)
    root.update_idletasks()
    _set_window_icon(root)
    root.update()

    # ═══════════════════════════════════════════════════════
    # 全局样式定制
    # ═══════════════════════════════════════════════════════
    _apply_custom_styles(root)

    # ═══════════════════════════════════════════════════════
    # 底部栏（固定在窗口底部）
    # ═══════════════════════════════════════════════════════
    ttk.Separator(root, orient="horizontal").pack(side="bottom", fill="x")
    bottom = ttk.Frame(root, padding=(0, 8))
    bottom.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
    ttk.Button(bottom, text="  ⭮ 重启服务  ", bootstyle="outline",
               command=lambda: threading.Thread(target=ctx.restart, daemon=True).start()).pack(side="left")
    ttk.Button(bottom, text="  ✕ 退出程序  ", bootstyle="danger-outline",
               command=lambda: (ctx.shutdown(), root.destroy())).pack(side="right")

    # ═══════════════════════════════════════════════════════
    # Notebook 三标签页
    # ═══════════════════════════════════════════════════════
    notebook = ttk.Notebook(root, bootstyle="primary")
    notebook.pack(side="top", fill="both", expand=True, padx=14, pady=(14, 0))

    tab_home = ttk.Frame(notebook)
    notebook.add(tab_home, text=" 首页 ")

    tab_review = ttk.Frame(notebook)
    notebook.add(tab_review, text=" 审核 ")

    tab_kb = ttk.Frame(notebook)
    notebook.add(tab_kb, text=" 知识库 ")

    # ═══════════════════════════════════════════════════════
    # Tab 1 — 首页
    # ═══════════════════════════════════════════════════════
    home_state = _build_tab_home(ctx, root, notebook, tab_home)
    status_vars = home_state["status_vars"]
    phase_var = home_state["phase_var"]
    progress_var = home_state["progress_var"]
    progress_label = home_state["progress_label_var"]
    path_tree = home_state["path_tree"]
    snap_combo = home_state["snap_combo"]
    home_canvas = home_state["home_canvas"]

    # ═══════════════════════════════════════════════════════
    # Tab 2 — 审核
    # ═══════════════════════════════════════════════════════
    review_state = _build_tab_review(ctx, root, tab_review)

    # ═══════════════════════════════════════════════════════
    # Tab 3 — 知识库
    # ═══════════════════════════════════════════════════════
    kb_state = _build_tab_kb(ctx, root, tab_kb)
    _kb_state_ref[0] = kb_state

    # ═══════════════════════════════════════════════════════
    # 定时刷新
    # ═══════════════════════════════════════════════════════
    _last_review_refresh = [0.0]
    _last_kb_refresh = [0.0]

    def _schedule():
        try:
            _fetch_home(ctx, status_vars, phase_var,
                        progress_var, progress_label, path_tree)
            _refresh_snapshots(ctx, snap_combo)
            if _show_requested.is_set():
                root.deiconify()
                root.lift()
                _show_requested.clear()
            current_tab = notebook.index(notebook.select())
            if current_tab == 1:
                now = time.time()
                if now - _last_review_refresh[0] >= 5.0:
                    _refresh_review_tree(ctx, review_state["tree"],
                                         review_state["status_var"],
                                         review_state["check_set"])
                    _last_review_refresh[0] = now
            if current_tab == 2:
                now = time.time()
                if now - _last_kb_refresh[0] >= 10.0:
                    _refresh_kb_all(ctx, kb_state)
                    _last_kb_refresh[0] = now
        except Exception as e:
            logger.warning("Schedule refresh failed: %s", e)
        root.after(UPDATE_INTERVAL_MS, _schedule)

    def _on_close():
        try:
            home_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        root.withdraw()

    root.after_idle(lambda: _set_window_icon(root))
    root.after(200, _schedule)
    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


# ═══════════════════════════════════════════════════════════
# Tab 1 — 首页：状态栏 + 审核面板 + 底部
# ═══════════════════════════════════════════════════════════

def _build_tab_home(ctx, root, notebook, tab_home):
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import filedialog, messagebox

    PAD = (0, 10)

    # ─── 可滚动容器 ───
    home_canvas = tk.Canvas(tab_home, borderwidth=0, highlightthickness=0)
    home_scroll = ttk.Scrollbar(tab_home, orient="vertical", command=home_canvas.yview)
    home_frame = ttk.Frame(home_canvas, padding=(8, 4))
    home_frame.bind("<Configure>", lambda e: home_canvas.configure(scrollregion=home_canvas.bbox("all")))
    home_frame_id = home_canvas.create_window((0, 0), window=home_frame, anchor="nw")

    def _on_home_resize(event):
        if event.width > 20:
            home_canvas.itemconfig(home_frame_id, width=event.width)
    home_canvas.bind("<Configure>", _on_home_resize)
    home_canvas.configure(yscrollcommand=home_scroll.set)

    def _on_home_mousewheel(event):
        home_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    home_canvas.bind("<Enter>", lambda e: home_canvas.bind_all("<MouseWheel>", _on_home_mousewheel))
    home_canvas.bind("<Leave>", lambda e: home_canvas.unbind_all("<MouseWheel>"))
    home_canvas.pack(side="left", fill="both", expand=True)
    home_scroll.pack(side="right", fill="y")

    # ─── 1.1 状态栏：4张独立卡片横排，每个卡片 icon+label+value 纵向排列 ───
    nonlocal_vars = {}

    f_status = ttk.Frame(home_frame)
    f_status.pack(fill="x", pady=PAD)
    f_status.columnconfigure((0, 1, 2, 3), weight=1, uniform="status")

    def _status_card(parent, col, icon, title, var, bootstyle=""):
        """创建单张状态卡片：LabelFrame 包裹，纵向排列 icon→title→value"""
        card = ttk.LabelFrame(parent)
        card.grid(column=col, row=0, sticky="nsew", padx=4, pady=0)
        inner = ttk.Frame(card, padding=(10, 8))
        inner.pack(fill="both", expand=True)
        ttk.Label(inner, text=icon, font=("", 16)).pack(anchor="w")
        ttk.Label(inner, text=title, font=("", 9), bootstyle="secondary").pack(anchor="w", pady=(2, 0))
        lbl = ttk.Label(inner, textvariable=var, font=("", 14, "bold"), bootstyle=bootstyle)
        lbl.pack(anchor="w", pady=(4, 0))
        return lbl

    # 状态卡片绑定的 StringVar — _fetch_home 会更新这些值
    status_vars = {
        "model": ttk.StringVar(value="—"),
        "docs": ttk.StringVar(value="—"),
        "queue": ttk.StringVar(value="—"),
        "api": ttk.StringVar(value="—"),
        "llm_status": ttk.StringVar(value="unknown"),
    }
    _status_card(f_status, 0, "🧠", "分类模式", status_vars["model"], "info")
    _status_card(f_status, 1, "📄", "文档总数", status_vars["docs"], "success")
    _status_card(f_status, 2, "📥", "待处理队列", status_vars["queue"], "warning")
    _status_card(f_status, 3, "🔗", "Embed 模型", status_vars["api"], "default")

    # ─── 1.2 审核面板（首页主体）───
    home_review = _build_review_panel(
        ctx, root, home_frame, compact=True,
    )

    # ─── 1.3 下方区域（两列网格）───
    f_bottom = ttk.Frame(home_frame)
    f_bottom.pack(fill="x", pady=PAD)
    f_bottom.columnconfigure((0, 1), weight=1, uniform="bottom")

    # 进度
    f_progress = ttk.LabelFrame(f_bottom, text="整理进度")
    f_progress.grid(column=0, row=0, sticky="nsew", padx=(0, 4))
    phase_var = ttk.StringVar(value="空闲")
    progress_var = ttk.DoubleVar()
    progress_label_var = ttk.StringVar(value="")
    ttk.Label(f_progress, textvariable=phase_var, font=("", 10)).pack(anchor="w")
    ttk.Progressbar(f_progress, variable=progress_var, maximum=100,
                    bootstyle="success-striped").pack(fill="x", pady=(4, 2))
    ttk.Label(f_progress, textvariable=progress_label_var, font=("", 9),
              bootstyle="secondary").pack(anchor="w")

    # 路径
    f_paths = ttk.LabelFrame(f_bottom, text="扫描路径")
    f_paths.grid(column=1, row=0, sticky="nsew", padx=(4, 0))
    path_tree = ttk.Treeview(f_paths, show="tree", height=4, selectmode="browse")
    path_tree.pack(fill="x")
    path_tree.column("#0", width=300, stretch=True)

    def _bind_path_scroll(event):
        home_canvas.unbind_all("<MouseWheel>")
        path_tree.bind_all("<MouseWheel>", _on_path_wheel)
    def _unbind_path_scroll(event):
        path_tree.unbind_all("<MouseWheel>")
        home_canvas.bind_all("<MouseWheel>", _on_home_mousewheel)
    def _on_path_wheel(event):
        path_tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
    path_tree.bind("<Enter>", _bind_path_scroll)
    path_tree.bind("<Leave>", _unbind_path_scroll)

    # 快照回退 + 操作按钮
    f_misc = ttk.LabelFrame(home_frame, text="快捷操作")
    f_misc.pack(fill="x", pady=PAD)
    misc_row = ttk.Frame(f_misc)
    misc_row.pack(fill="x")

    def _on_add_dir():
        d = ttk.Toplevel(root); d.withdraw(); d.attributes('-topmost', True)
        path = filedialog.askdirectory(title="选择扫描目录", parent=d); d.destroy()
        if path and path not in ctx.config.scan.custom_white_path:
            ctx.config.scan.custom_white_path.append(path)
            _save_config_paths(ctx)

    def _on_add_file():
        d = ttk.Toplevel(root); d.withdraw(); d.attributes('-topmost', True)
        path = filedialog.askopenfilename(title="选择扫描文件", parent=d); d.destroy()
        if path and path not in ctx.config.scan.custom_white_path:
            ctx.config.scan.custom_white_path.append(path)
            _save_config_paths(ctx)

    def _on_remove_path():
        sel = path_tree.selection()
        if sel:
            raw = path_tree.item(sel[0], "text") or path_tree.set(sel[0], "path")
            p = raw.replace("📁 ", "", 1).replace("🖊 ", "", 1)
            if p in ctx.config.scan.custom_white_path:
                ctx.config.scan.custom_white_path.remove(p)
            if p in ctx.config.scan.agent_paths:
                ctx.config.scan.agent_paths.remove(p)
            _save_config_paths(ctx)

    def _safe_scan():
        if messagebox.askyesno("确认全盘扫描",
                "此操作将：\n1. 备份当前 export 文件夹\n2. 创建快照\n3. 重新扫描并分类所有文件\n\n是否继续？", parent=root):
            threading.Thread(target=ctx.safe_full_scan, daemon=True).start()

    def _incremental():
        def _run():
            try:
                ctx.scan_progress["phase"] = "scanning"
                count, pending = ctx.scanner.full_scan()
                ctx.scan_progress.update({"count": count, "pending": len(pending)})
                if pending:
                    ctx.scan_progress["phase"] = "classifying"
                    ctx.pipeline.process_batch(pending)
                ctx.scan_progress["phase"] = "exporting"
                ctx._export_memories()
                ctx.scan_progress["phase"] = "idle"
            except Exception as e:
                ctx.scan_progress["phase"] = "error"
                logger.error("Incremental scan failed: %s", e)
        threading.Thread(target=_run, daemon=True).start()

    def _optimize():
        def _run():
            try:
                result = ctx.optimizer.run_once()
                root.after(0, lambda: messagebox.showinfo("整理完成",
                    f"衰减{result.get('decayed', 0)}条, 合并{result.get('merged', 0)}条, "
                    f"去重{result.get('removed', 0)}条", parent=root))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("整理失败", str(e), parent=root))
        threading.Thread(target=_run, daemon=True).start()

    ttk.Button(misc_row, text="＋ 添加目录", command=_on_add_dir, bootstyle="success-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="📄 添加文件", command=_on_add_file, bootstyle="info-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="✕ 删除路径", command=_on_remove_path, bootstyle="danger-outline").pack(side="left", padx=(0, 10))
    ttk.Separator(misc_row, orient="vertical").pack(side="left", fill="y", padx=6)
    ttk.Button(misc_row, text="🔍 增量扫描", command=_incremental, bootstyle="primary-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="⚠ 全盘扫描", command=_safe_scan, bootstyle="danger-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="🧹 立即整理", command=_optimize, bootstyle="success-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="📥 导入文件",
               command=lambda: _import_files(ctx, root), bootstyle="info-outline").pack(side="left", padx=(0, 5))
    ttk.Button(misc_row, text="📂 导入文件夹",
               command=lambda: _import_folder(ctx, root), bootstyle="info-outline").pack(side="left", padx=(0, 5))
    def _open_logs():
        """打开日志目录 — 用绝对路径避免 exe CWD 问题"""
        logs_dir = str(Path(_MEMORY_HOME) / "logs")
        if os.path.isdir(logs_dir):
            os.startfile(logs_dir)
        else:
            messagebox.showinfo("提示", f"日志目录不存在:\n{logs_dir}", parent=root)
    ttk.Button(misc_row, text="📋 日志",
               command=_open_logs, bootstyle="secondary-outline").pack(side="left")

    # 快照
    f_snap_row = ttk.Frame(f_misc)
    f_snap_row.pack(fill="x", pady=(8, 0))
    snap_combo = ttk.Combobox(f_snap_row, width=32)
    snap_combo.pack(side="left", padx=(0, 8))
    ttk.Button(f_snap_row, text="  ⟳ 恢复快照  ",
               command=lambda: _restore_snapshot(ctx, root, snap_combo),
               bootstyle="warning-outline").pack(side="left")

    # 暴露给 schedule 用
    nonlocal_vars.update({
        "status_vars": status_vars,
        "phase_var": phase_var, "progress_var": progress_var,
        "progress_label_var": progress_label_var, "path_tree": path_tree,
        "snap_combo": snap_combo, "home_canvas": home_canvas,
    })

    return nonlocal_vars


# ═══════════════════════════════════════════════════════════
# 审核面板（首页和审核Tab共用）
# ═══════════════════════════════════════════════════════════

def _build_review_panel(ctx, root, parent, compact=False):
    """构建审核面板：列表(左) + 详情(右) + 底部批量栏

    Args:
        compact: True=首页精简模式，False=审核Tab完整模式
    """
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import messagebox

    height = 12 if compact else 16

    panel = ttk.Frame(parent)
    panel.pack(fill="both", expand=True, pady=(0, 4))
    panel.columnconfigure(0, weight=1)
    panel.columnconfigure(1, weight=0)
    panel.rowconfigure(0, weight=1)

    # 选中状态追踪
    check_set = set()

    # ─── 左列：列表 ───
    list_frame = ttk.LabelFrame(panel, text="待审核文档")
    list_frame.grid(column=0, row=0, sticky="nsew", padx=(0, 4))

    cols = ("check", "doc_id", "label", "importance", "weight", "preview")
    tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=height, selectmode="browse")
    tree.heading("check", text="☑")
    tree.heading("doc_id", text="ID")
    tree.heading("label", text="Label")
    tree.heading("importance", text="重要性")
    tree.heading("weight", text="权重")
    tree.heading("preview", text="内容预览")
    tree.column("check", width=28, stretch=False, anchor="center")
    tree.column("doc_id", width=45, stretch=False)
    tree.column("label", width=100, stretch=False)
    tree.column("importance", width=55, stretch=False)
    tree.column("weight", width=45, stretch=False)
    tree.column("preview", width=260, stretch=True)

    tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    tree_scroll.pack(side="right", fill="y")

    def _toggle_check(event):
        """点击☑列切换勾选状态"""
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = tree.identify_column(event.x)
        if col != "#1":
            return
        item = tree.identify_row(event.y)
        if not item:
            return
        doc_id = tree.item(item, "values")[1]
        if doc_id in check_set:
            check_set.discard(doc_id)
            tree.set(item, "check", "☐")
        else:
            check_set.add(doc_id)
            tree.set(item, "check", "☑")
        _update_batch_label()

    tree.bind("<ButtonRelease-1>", _toggle_check)

    # ─── 右列：详情（缩窄到230px，给列表更多空间）───
    detail_frame = ttk.LabelFrame(panel, text="详情", width=230)
    detail_frame.grid(column=1, row=0, sticky="nsew")
    detail_frame.grid_propagate(False)

    # 文件路径
    ttk.Label(detail_frame, text="文件路径", font=("", 9), bootstyle="secondary").pack(anchor="w", pady=(0, 2))
    path_var = ttk.StringVar(value="未选中")
    ttk.Label(detail_frame, textvariable=path_var, font=("", 9), bootstyle="info",
              wraplength=240).pack(anchor="w", pady=(0, 6))

    # 内容预览（加大到8行，鼠标悬停时滚轮优先滚动此处）
    ttk.Label(detail_frame, text="内容预览", font=("", 9), bootstyle="secondary").pack(anchor="w", pady=(0, 2))
    preview_text = tk.Text(detail_frame, height=8, wrap="word", font=("", 9),
                           bg="#1e252e", fg="#c9d1d9", relief="flat", padx=6, pady=4)
    preview_text.pack(fill="x", pady=(0, 6))
    preview_text.configure(state="disabled")

    # 鼠标悬停预览区时，滚轮优先滚动预览文字
    def _preview_enter(event):
        """鼠标进入预览区：绑定滚轮到预览Text"""
        preview_text.bind_all("<MouseWheel>", lambda e: preview_text.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))

    def _preview_leave(event):
        """鼠标离开预览区：解绑滚轮，恢复首页默认滚动"""
        preview_text.unbind_all("<MouseWheel>")

    preview_text.bind("<Enter>", _preview_enter)
    preview_text.bind("<Leave>", _preview_leave)

    # 分类表单
    ttk.Label(detail_frame, text="分类设置", font=("", 9), bootstyle="secondary").pack(anchor="w", pady=(0, 4))

    form_row1 = ttk.Frame(detail_frame)
    form_row1.pack(fill="x", pady=(0, 4))
    ttk.Label(form_row1, text="Label", font=("", 9)).pack(side="left", padx=(0, 4))
    label_var = ttk.StringVar()
    ttk.Combobox(form_row1, textvariable=label_var, values=_REVIEW_LABELS,
                 width=14, state="readonly").pack(side="left", padx=(0, 4))

    form_row2 = ttk.Frame(detail_frame)
    form_row2.pack(fill="x", pady=(0, 4))
    ttk.Label(form_row2, text="重要性", font=("", 9)).pack(side="left", padx=(0, 4))
    imp_var = ttk.StringVar()
    ttk.Combobox(form_row2, textvariable=imp_var, values=_REVIEW_IMPORTANCE,
                 width=6, state="readonly").pack(side="left", padx=(0, 4))
    ttk.Label(form_row2, text="分类", font=("", 9)).pack(side="left", padx=(0, 4))
    cat_var = ttk.StringVar()
    ttk.Combobox(form_row2, textvariable=cat_var, values=_REVIEW_CATEGORIES,
                 width=10, state="readonly").pack(side="left")

    # 操作按钮
    btn_frame = ttk.Frame(detail_frame)
    btn_frame.pack(fill="x", pady=(8, 0))

    def _get_selected_doc_id():
        sel = tree.selection()
        if not sel:
            return None
        return int(tree.item(sel[0], "values")[1])

    def _on_keep():
        """保留：调用 apply_review 提升 weight 并标记 review_queue 已审核"""
        doc_id = _get_selected_doc_id()
        if not doc_id:
            return
        try:
            sel = tree.selection()
            vals = tree.item(sel[0], "values") if sel else None
            label = str(vals[2]) if vals and vals[2] else "unknown"
            importance = str(vals[3]) if vals and vals[3] else "P2"
            ctx.storage.sqlite.apply_review(doc_id, label, importance)
            if sel:
                tree.delete(sel[0])
                check_set.discard(str(doc_id))
            _refresh_home_review(ctx, tree, check_set)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=root)

    def _on_delete_soft():
        """删除：调用 soft_delete 标记 is_deleted + 回调"""
        doc_id = _get_selected_doc_id()
        if not doc_id:
            return
        try:
            ctx.storage.sqlite.soft_delete(doc_id)
            sel = tree.selection()
            if sel:
                tree.delete(sel[0])
                check_set.discard(str(doc_id))
            _refresh_home_review(ctx, tree, check_set)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=root)

    def _on_add_rule():
        """添加为筛选规则"""
        doc_id = _get_selected_doc_id()
        if not doc_id:
            return
        try:
            row = ctx.storage.sqlite._conn.execute(
                "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (doc_id,)
            ).fetchone()
            if row:
                _show_add_rule_dialog(root, ctx, doc_id, row[0] or "", row[1] or "")
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=root)

    def _on_exclude():
        """排除同类文件"""
        doc_id = _get_selected_doc_id()
        if not doc_id:
            return
        try:
            row = ctx.storage.sqlite._conn.execute(
                "SELECT file_path FROM document_files WHERE id=?", (doc_id,)
            ).fetchone()
            if row and row[0]:
                filepath = row[0]
                folder = str(Path(filepath).parent)
                if messagebox.askyesno("排除同类文件",
                        f"将排除路径包含以下内容的文件：\n{folder}\n\n确认？", parent=root):
                    ctx.storage.sqlite.add_exclusion(
                        "path_contains", folder,
                        f"从审核排除: {folder}")
                    ctx.storage.sqlite.mark_review_excluded(doc_id, f"path:{folder}")
                    sel = tree.selection()
                    if sel:
                        tree.delete(sel[0])
                        check_set.discard(str(doc_id))
                    _refresh_home_review(ctx, tree, check_set)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=root)

    def _on_apply_form():
        """应用表单中的分类设置"""
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中一条文档", parent=root)
            return
        lbl = label_var.get()
        imp = imp_var.get()
        cat = cat_var.get()
        if not lbl or not imp:
            messagebox.showwarning("提示", "请至少选择 Label 和重要性", parent=root)
            return
        doc_id = int(tree.item(sel[0], "values")[1])
        try:
            ok = ctx.storage.sqlite.apply_review(doc_id, lbl, imp, cat)
            if ok:
                tree.delete(sel[0])
                check_set.discard(str(doc_id))
        except Exception as e:
            messagebox.showerror("审核失败", str(e), parent=root)

    # 操作按钮（使用 outline 样式，不再用实心色块）
    ttk.Button(btn_frame, text="  ✓ 保留  ", command=_on_keep, bootstyle="success-outline").pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="  ✗ 删除  ", command=_on_delete_soft, bootstyle="danger-outline").pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="  ⚙ 添加规则  ", command=_on_add_rule, bootstyle="info-outline").pack(fill="x", pady=2)
    ttk.Button(btn_frame, text="  🚫 排除同类  ", command=_on_exclude, bootstyle="warning-outline").pack(fill="x", pady=2)
    ttk.Separator(detail_frame, orient="horizontal").pack(fill="x", pady=6)
    ttk.Button(btn_frame, text="  应用分类  ", command=_on_apply_form, bootstyle="primary-outline").pack(fill="x", pady=2)

    # ─── 底部批量栏（用 grid 避免与 list_frame/detail_frame 的 grid 冲突）───
    panel.rowconfigure(1, weight=0)
    batch_frame = ttk.Frame(panel)
    batch_frame.grid(column=0, row=1, columnspan=2, sticky="ew", pady=(4, 0))

    batch_label = ttk.StringVar(value="已选 0 条")
    ttk.Label(batch_frame, textvariable=batch_label, font=("", 10, "bold"),
              bootstyle="info").pack(side="left", padx=(0, 8))

    def _update_batch_label():
        batch_label.set(f"已选 {len(check_set)} 条")

    def _select_all():
        for item in tree.get_children():
            doc_id = tree.item(item, "values")[1]
            check_set.add(doc_id)
            tree.set(item, "check", "☑")
        _update_batch_label()

    def _clear_selection():
        check_set.clear()
        for item in tree.get_children():
            tree.set(item, "check", "☐")
        _update_batch_label()

    def _batch_keep():
        """批量保留：逐条调用 apply_review，更新 review_queue 状态"""
        if not check_set:
            return
        conn = ctx.storage.sqlite._conn
        for did in list(check_set):
            try:
                row = conn.execute(
                    "SELECT label, importance FROM memory_classify WHERE doc_id=?",
                    (int(did),)).fetchone()
                label = row[0] or "unknown" if row else "unknown"
                importance = row[1] or "P2" if row else "P2"
                ctx.storage.sqlite.apply_review(int(did), label, importance)
            except Exception:
                pass
        _refresh_home_review(ctx, tree, check_set)
        check_set.clear()

    def _batch_delete():
        """批量删除：逐条调用 soft_delete，触发回调"""
        if not check_set:
            return
        for did in list(check_set):
            try:
                ctx.storage.sqlite.soft_delete(int(did))
            except Exception:
                pass
        _refresh_home_review(ctx, tree, check_set)
        check_set.clear()

    ttk.Button(batch_frame, text="  全选  ", command=_select_all, bootstyle="outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  清空  ", command=_clear_selection, bootstyle="outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  批量保留  ", command=_batch_keep, bootstyle="success-outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  批量删除  ", command=_batch_delete, bootstyle="danger-outline").pack(side="left", padx=3)

    # ─── 选中事件：填充详情 ───
    def _on_select(event):
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        # vals = [check, doc_id, label, importance, weight, preview]
        label_var.set(str(vals[2]) if vals[2] else "")
        imp_var.set(str(vals[3]) if vals[3] else "")
        doc_id = int(vals[1])
        try:
            row = ctx.storage.sqlite._conn.execute(
                "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (doc_id,)
            ).fetchone()
            if row:
                path_var.set(row[0] or f"doc_id={doc_id}")
                preview_text.configure(state="normal")
                preview_text.delete("1.0", "end")
                preview_text.insert("1.0", (row[1] or "")[:500])
                preview_text.configure(state="disabled")
            else:
                path_var.set(f"doc_id={doc_id}")
        except Exception:
            path_var.set(f"doc_id={doc_id}")

    tree.bind("<<TreeviewSelect>>", _on_select)

    # 初始加载
    _refresh_home_review(ctx, tree, check_set)

    return {"tree": tree, "status_var": batch_label, "check_set": check_set}


def _refresh_home_review(ctx, tree, check_set):
    """刷新首页/审核面板的待审核文档列表

    优先从 review_queue 表查询 status='pending' 的文档，
    兜底直接查 memory_classify.weight=20（review_queue 可能还没数据）。
    刷新后清空 check_set，避免勾选状态残留。
    """
    for i in tree.get_children():
        tree.delete(i)
    try:
        rows = ctx.storage.sqlite.get_pending_reviews(limit=50)
        if not rows:
            conn = ctx.storage.sqlite._conn
            rows_raw = conn.execute("""
                SELECT c.doc_id, c.label, c.importance, c.weight,
                       substr(c.compact_content, 1, 80) as preview
                FROM memory_classify c
                JOIN document_files d ON c.doc_id = d.id
                WHERE c.weight = 20 AND d.is_deleted = 0
                ORDER BY c.doc_id DESC LIMIT 50
            """).fetchall()
            for r in rows_raw:
                preview = (r["preview"] or "").replace("\n", " ").strip()
                tree.insert("", "end", values=(
                    "☐", r["doc_id"], r["label"] or "unknown",
                    r["importance"] or "P2", r["weight"], preview,
                ))
        else:
            for r in rows:
                preview = (r.get("compact_content") or "").replace("\n", " ").strip()[:80]
                tree.insert("", "end", values=(
                    "☐", r["doc_id"], r.get("label") or "unknown",
                    r.get("importance") or "P2", r.get("weight") or 20, preview,
                ))
        check_set.clear()
    except Exception as e:
        logger.error("Review list refresh failed: %s", e)


def _refresh_review_tree(ctx, tree, status_var, check_set):
    """审核Tab的定时刷新入口，包装 _refresh_home_review 并更新状态栏"""
    _refresh_home_review(ctx, tree, check_set)
    count = len(tree.get_children())
    status_var.set(f"共 {count} 条 · 已选 {len(check_set)} 条")


# ═══════════════════════════════════════════════════════════
# Tab 2 — 审核：筛选器 + 完整列表
# ═══════════════════════════════════════════════════════════

def _build_tab_review(ctx, root, tab_review):
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import messagebox

    # ─── 筛选器工具栏 ───
    toolbar = ttk.Frame(tab_review)
    toolbar.pack(fill="x", padx=10, pady=(10, 4))

    ttk.Label(toolbar, text="Label:", font=("", 9)).pack(side="left", padx=(0, 4))
    filter_label = ttk.StringVar(value="全部")
    ttk.Combobox(toolbar, textvariable=filter_label,
                 values=["全部"] + _REVIEW_LABELS, width=14, state="readonly").pack(side="left", padx=(0, 8))

    ttk.Label(toolbar, text="重要性:", font=("", 9)).pack(side="left", padx=(0, 4))
    filter_imp = ttk.StringVar(value="全部")
    ttk.Combobox(toolbar, textvariable=filter_imp,
                 values=["全部"] + _REVIEW_IMPORTANCE, width=6, state="readonly").pack(side="left", padx=(0, 8))

    ttk.Label(toolbar, text="搜索:", font=("", 9)).pack(side="left", padx=(0, 4))
    filter_search = ttk.StringVar()
    ttk.Entry(toolbar, textvariable=filter_search, width=16).pack(side="left", padx=(0, 8))

    def _apply_filter():
        _refresh_full_review(ctx, tree, status_var, check_set,
                             filter_label.get(), filter_imp.get(), filter_search.get())

    ttk.Button(toolbar, text="🔍 筛选", command=_apply_filter, bootstyle="primary-outline").pack(side="left", padx=(0, 4))
    ttk.Button(toolbar, text="重置", command=lambda: (
        filter_label.set("全部"), filter_imp.set("全部"), filter_search.set(""),
        _apply_filter()), bootstyle="outline").pack(side="left", padx=(0, 8))

    status_var = ttk.StringVar(value="")
    ttk.Label(toolbar, textvariable=status_var, font=("", 9), bootstyle="secondary").pack(side="right")

    # ─── 列表 ───
    check_set = set()
    list_frame = ttk.Frame(tab_review)
    list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))

    cols = ("check", "doc_id", "label", "importance", "category", "weight", "source", "preview")
    tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=14, selectmode="browse")
    tree.heading("check", text="☑")
    tree.heading("doc_id", text="ID")
    tree.heading("label", text="Label")
    tree.heading("importance", text="重要性")
    tree.heading("category", text="分类")
    tree.heading("weight", text="权重")
    tree.heading("source", text="来源")
    tree.heading("preview", text="内容预览")
    tree.column("check", width=28, stretch=False, anchor="center")
    tree.column("doc_id", width=45, stretch=False)
    tree.column("label", width=100, stretch=False)
    tree.column("importance", width=55, stretch=False)
    tree.column("category", width=70, stretch=False)
    tree.column("weight", width=45, stretch=False)
    tree.column("source", width=55, stretch=False)
    tree.column("preview", width=250, stretch=True)

    tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    tree_scroll.pack(side="right", fill="y")

    def _toggle_check(event):
        region = tree.identify_region(event.x, event.y)
        if region != "cell" or tree.identify_column(event.x) != "#1":
            return
        item = tree.identify_row(event.y)
        if not item:
            return
        doc_id = tree.item(item, "values")[1]
        if doc_id in check_set:
            check_set.discard(doc_id)
            tree.set(item, "check", "☐")
        else:
            check_set.add(doc_id)
            tree.set(item, "check", "☑")
        status_var.set(f"共 {len(tree.get_children())} 条 · 已选 {len(check_set)} 条")

    tree.bind("<ButtonRelease-1>", _toggle_check)

    # ─── 详情面板 + 操作 ───
    right_frame = ttk.Frame(tab_review)
    right_frame.pack(side="right", fill="y", padx=(4, 10), pady=(0, 4))
    right_frame.configure(width=260)

    ttk.Label(right_frame, text="文件路径", font=("", 9), bootstyle="secondary").pack(anchor="w")
    detail_path = ttk.StringVar(value="未选中")
    ttk.Label(right_frame, textvariable=detail_path, font=("", 9), bootstyle="info",
              wraplength=240).pack(anchor="w", pady=(0, 6))

    ttk.Label(right_frame, text="内容预览", font=("", 9), bootstyle="secondary").pack(anchor="w")
    detail_preview = tk.Text(right_frame, height=8, wrap="word", font=("", 9),
                             bg="#1e252e", fg="#c9d1d9", relief="flat", padx=6, pady=4, width=28)
    detail_preview.pack(fill="x", pady=(0, 6))
    detail_preview.configure(state="disabled")

    # Tab 2 预览区滚轮优先：鼠标悬停时滚轮滚动预览文字
    def _preview2_enter(event):
        detail_preview.bind_all("<MouseWheel>", lambda e: detail_preview.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))

    def _preview2_leave(event):
        detail_preview.unbind_all("<MouseWheel>")

    detail_preview.bind("<Enter>", _preview2_enter)
    detail_preview.bind("<Leave>", _preview2_leave)

    ttk.Label(right_frame, text="分类设置", font=("", 9), bootstyle="secondary").pack(anchor="w")
    f1 = ttk.Frame(right_frame); f1.pack(fill="x", pady=2)
    ttk.Label(f1, text="Label", font=("", 9)).pack(side="left")
    r_label_var = ttk.StringVar()
    ttk.Combobox(f1, textvariable=r_label_var, values=_REVIEW_LABELS, width=12, state="readonly").pack(side="left", padx=4)

    f2 = ttk.Frame(right_frame); f2.pack(fill="x", pady=2)
    ttk.Label(f2, text="重要性", font=("", 9)).pack(side="left")
    r_imp_var = ttk.StringVar()
    ttk.Combobox(f2, textvariable=r_imp_var, values=_REVIEW_IMPORTANCE, width=5, state="readonly").pack(side="left", padx=4)
    ttk.Label(f2, text="分类", font=("", 9)).pack(side="left")
    r_cat_var = ttk.StringVar()
    ttk.Combobox(f2, textvariable=r_cat_var, values=_REVIEW_CATEGORIES, width=8, state="readonly").pack(side="left", padx=4)

    def _get_sel():
        sel = tree.selection()
        return int(tree.item(sel[0], "values")[1]) if sel else None

    def _review_keep():
        """保留：调用 apply_review 提升 weight 并标记 review_queue 已审核"""
        did = _get_sel()
        if not did: return
        sel = tree.selection()
        vals = tree.item(sel[0], "values") if sel else None
        label = str(vals[2]) if vals and vals[2] else "unknown"
        importance = str(vals[3]) if vals and vals[3] else "P2"
        ctx.storage.sqlite.apply_review(did, label, importance)
        if sel: tree.delete(sel[0])
        check_set.discard(str(did))

    def _review_delete():
        """删除：调用 soft_delete 标记 is_deleted + 回调"""
        did = _get_sel()
        if not did: return
        ctx.storage.sqlite.soft_delete(did)
        sel = tree.selection()
        if sel: tree.delete(sel[0])
        check_set.discard(str(did))

    def _review_add_rule():
        did = _get_sel()
        if not did: return
        row = ctx.storage.sqlite._conn.execute(
            "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (did,)
        ).fetchone()
        if row:
            _show_add_rule_dialog(root, ctx, did, row[0] or "", row[1] or "")

    def _review_exclude():
        did = _get_sel()
        if not did: return
        row = ctx.storage.sqlite._conn.execute(
            "SELECT file_path FROM document_files WHERE id=?", (did,)
        ).fetchone()
        if row and row[0]:
            folder = str(Path(row[0]).parent)
            if messagebox.askyesno("排除", f"排除路径包含 {folder} 的文件？", parent=root):
                ctx.storage.sqlite.add_exclusion("path_contains", folder, f"审核排除: {folder}")
                ctx.storage.sqlite.mark_review_excluded(did, f"path:{folder}")
                sel = tree.selection()
                if sel: tree.delete(sel[0])
                check_set.discard(str(did))

    def _review_apply():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中一条", parent=root)
            return
        lbl, imp, cat = r_label_var.get(), r_imp_var.get(), r_cat_var.get()
        if not lbl or not imp:
            messagebox.showwarning("提示", "请至少选 Label 和重要性", parent=root)
            return
        did = int(tree.item(sel[0], "values")[1])
        try:
            ctx.storage.sqlite.apply_review(did, lbl, imp, cat)
            tree.delete(sel[0])
            check_set.discard(str(did))
        except Exception as e:
            messagebox.showerror("审核失败", str(e), parent=root)

    btn_f = ttk.Frame(right_frame)
    btn_f.pack(fill="x", pady=(8, 0))
    # Tab 2 审核操作按钮：全部使用 outline 样式
    ttk.Button(btn_f, text="  ✓ 保留  ", command=_review_keep, bootstyle="success-outline").pack(fill="x", pady=2)
    ttk.Button(btn_f, text="  ✗ 删除  ", command=_review_delete, bootstyle="danger-outline").pack(fill="x", pady=2)
    ttk.Button(btn_f, text="  ⚙ 添加规则  ", command=_review_add_rule, bootstyle="info-outline").pack(fill="x", pady=2)
    ttk.Button(btn_f, text="  🚫 排除同类  ", command=_review_exclude, bootstyle="warning-outline").pack(fill="x", pady=2)
    ttk.Separator(right_frame, orient="horizontal").pack(fill="x", pady=6)
    ttk.Button(btn_f, text="  应用分类  ", command=_review_apply, bootstyle="primary-outline").pack(fill="x", pady=2)

    # ─── 底部批量栏 ───
    batch_frame = ttk.Frame(tab_review)
    batch_frame.pack(fill="x", padx=10, pady=(0, 6))

    def _batch_select_all():
        for item in tree.get_children():
            did = tree.item(item, "values")[1]
            check_set.add(did)
            tree.set(item, "check", "☑")
        status_var.set(f"共 {len(tree.get_children())} 条 · 已选 {len(check_set)} 条")

    def _batch_clear():
        check_set.clear()
        for item in tree.get_children():
            tree.set(item, "check", "☐")
        status_var.set(f"共 {len(tree.get_children())} 条 · 已选 0 条")

    def _batch_keep():
        """批量保留：逐条调用 apply_review，更新 review_queue 状态"""
        conn = ctx.storage.sqlite._conn
        for did in list(check_set):
            row = conn.execute(
                "SELECT label, importance FROM memory_classify WHERE doc_id=?",
                (int(did),)).fetchone()
            label = row[0] or "unknown" if row else "unknown"
            importance = row[1] or "P2" if row else "P2"
            ctx.storage.sqlite.apply_review(int(did), label, importance)
        check_set.clear()
        _apply_filter()

    def _batch_del():
        """批量删除：逐条调用 soft_delete，触发回调"""
        for did in list(check_set):
            ctx.storage.sqlite.soft_delete(int(did))
        check_set.clear()
        _apply_filter()

    def _batch_add_rules():
        count = 0
        for did in list(check_set):
            row = ctx.storage.sqlite._conn.execute(
                "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (int(did),)
            ).fetchone()
            if row and row[1]:
                _quick_add_rule(ctx, row[0] or "", row[1])
                count += 1
        if count:
            messagebox.showinfo("批量添加规则", f"已为 {count} 条文档提取关键词并添加规则", parent=root)

    ttk.Button(batch_frame, text="  全选  ", command=_batch_select_all, bootstyle="outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  清空  ", command=_batch_clear, bootstyle="outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  批量保留  ", command=_batch_keep, bootstyle="success-outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  批量删除  ", command=_batch_del, bootstyle="danger-outline").pack(side="left", padx=3)
    ttk.Button(batch_frame, text="  批量添加规则  ", command=_batch_add_rules, bootstyle="info-outline").pack(side="left", padx=3)

    # ─── 选中填充详情 ───
    def _on_select(event):
        sel = tree.selection()
        if not sel: return
        vals = tree.item(sel[0], "values")
        r_label_var.set(str(vals[2]) if vals[2] else "")
        r_imp_var.set(str(vals[3]) if vals[3] else "")
        r_cat_var.set(str(vals[4]) if vals[4] else "")
        did = int(vals[1])
        try:
            row = ctx.storage.sqlite._conn.execute(
                "SELECT file_path, raw_text_snippet FROM document_files WHERE id=?", (did,)
            ).fetchone()
            if row:
                detail_path.set(row[0] or "")
                detail_preview.configure(state="normal")
                detail_preview.delete("1.0", "end")
                detail_preview.insert("1.0", (row[1] or "")[:800])
                detail_preview.configure(state="disabled")
        except Exception:
            pass

    tree.bind("<<TreeviewSelect>>", _on_select)

    # 初始加载
    _refresh_full_review(ctx, tree, status_var, check_set, "全部", "全部", "")

    return {"tree": tree, "status_var": status_var, "check_set": check_set}


def _refresh_full_review(ctx, tree, status_var, check_set,
                         filter_label="全部", filter_imp="全部", search=""):
    """刷新审核Tab完整列表（带筛选条件）

    支持 Label/重要性/搜索文本三维筛选。
    优先查 review_queue + memory_classify 联表，兜底直接查 weight=20。
    """
    for i in tree.get_children():
        tree.delete(i)
    check_set.clear()
    try:
        conn = ctx.storage.sqlite._conn
        query = """
            SELECT c.doc_id, c.label, c.importance, c.category, c.weight,
                   c.enqueue_reason, substr(c.compact_content, 1, 100) as preview
            FROM review_queue rq
            JOIN memory_classify c ON rq.doc_id = c.doc_id
            JOIN document_files d ON c.doc_id = d.id
            WHERE rq.status = 'pending' AND d.is_deleted = 0
        """
        params = []
        if filter_label != "全部":
            query += " AND c.label = ?"
            params.append(filter_label)
        if filter_imp != "全部":
            query += " AND c.importance = ?"
            params.append(filter_imp)
        if search:
            query += " AND c.compact_content LIKE ?"
            params.append(f"%{search}%")
        query += " ORDER BY c.doc_id DESC LIMIT 200"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            # 兜底：直接查 weight=20
            query2 = """
                SELECT c.doc_id, c.label, c.importance, c.category, c.weight,
                       'keyword_miss' as enqueue_reason,
                       substr(c.compact_content, 1, 100) as preview
                FROM memory_classify c
                JOIN document_files d ON c.doc_id = d.id
                WHERE c.weight = 20 AND d.is_deleted = 0
            """
            params2 = []
            if filter_label != "全部":
                query2 += " AND c.label = ?"
                params2.append(filter_label)
            if filter_imp != "全部":
                query2 += " AND c.importance = ?"
                params2.append(filter_imp)
            if search:
                query2 += " AND c.compact_content LIKE ?"
                params2.append(f"%{search}%")
            query2 += " ORDER BY c.doc_id DESC LIMIT 200"
            rows = conn.execute(query2, params2).fetchall()

        for r in rows:
            preview = (r["preview"] or "").replace("\n", " ").strip()
            source = (r["enqueue_reason"] or "")[:6]
            tree.insert("", "end", values=(
                "☐", r["doc_id"], r["label"] or "unknown",
                r["importance"] or "P2", r["category"] or "",
                r["weight"], source, preview,
            ))
        count = len(tree.get_children())
        status_var.set(f"共 {count} 条 · 已选 0 条")
    except Exception as e:
        status_var.set(f"加载失败: {e}")
        logger.error("Full review refresh failed: %s", e)


# ═══════════════════════════════════════════════════════════
# Tab 3 — 知识库：统计 + 规则 + 排除 + 领域
# ═══════════════════════════════════════════════════════════

def _build_tab_kb(ctx, root, tab_kb):
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import messagebox

    state = {}

    # ─── 统计栏 ───
    stats_frame = ttk.Frame(tab_kb)
    stats_frame.pack(fill="x", padx=10, pady=(10, 6))
    stats_frame.columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="stat")

    stat_vars = {}
    for i, (icon, label) in enumerate([
        ("📄", "总文档"), ("🔑", "关键词规则"),
        ("🚫", "排除规则"), ("📜", "全局规则"), ("📂", "领域"),
    ]):
        f = ttk.Frame(stats_frame, padding=(12, 10))
        f.grid(column=i, row=0, sticky="nsew", padx=4)
        var = ttk.StringVar(value="—")
        ttk.Label(f, text=icon, font=("", 18)).pack()
        ttk.Label(f, textvariable=var, font=("", 20, "bold")).pack()
        ttk.Label(f, text=label, font=("", 10), bootstyle="secondary").pack()
        stat_vars[label] = var
    state["stat_vars"] = stat_vars

    # ─── 四宫格 ───
    grid_frame = ttk.Frame(tab_kb)
    grid_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    grid_frame.columnconfigure((0, 1), weight=1)
    grid_frame.rowconfigure((0, 1), weight=1)

    # 3a: 关键词规则
    kw_panel = ttk.LabelFrame(grid_frame, text="🔑 关键词分类规则")
    kw_panel.grid(column=0, row=0, sticky="nsew", padx=(0, 4), pady=(0, 4))
    kw_tree = ttk.Treeview(kw_panel, columns=("category", "count", "keywords"),
                           show="headings", height=6, selectmode="browse")
    kw_tree.heading("category", text="分类")
    kw_tree.heading("count", text="关键词数")
    kw_tree.heading("keywords", text="关键词（前5）")
    kw_tree.column("category", width=140, stretch=True)
    kw_tree.column("count", width=60, stretch=False)
    kw_tree.column("keywords", width=200, stretch=True)
    kw_scroll = ttk.Scrollbar(kw_panel, orient="vertical", command=kw_tree.yview)
    kw_tree.configure(yscrollcommand=kw_scroll.set)
    kw_tree.pack(side="left", fill="both", expand=True)
    kw_scroll.pack(side="right", fill="y")
    state["kw_tree"] = kw_tree

    kw_btn = ttk.Frame(kw_panel)
    kw_btn.pack(fill="x", pady=(4, 0))

    def _on_add_keyword():
        """添加关键词规则"""
        _show_add_keyword_dialog(root, ctx, state)

    ttk.Button(kw_btn, text="  ＋ 添加  ", command=_on_add_keyword, bootstyle="success-outline").pack(side="left")
    ttk.Button(kw_btn, text="  刷新  ", command=lambda: _refresh_kw_rules(ctx, state), bootstyle="outline").pack(side="right")

    # 3b: 排除规则
    ex_panel = ttk.LabelFrame(grid_frame, text="🚫 排除规则")
    ex_panel.grid(column=1, row=0, sticky="nsew", padx=(4, 0), pady=(0, 4))
    ex_tree = ttk.Treeview(ex_panel, columns=("type", "value", "hits"),
                           show="headings", height=6, selectmode="browse")
    ex_tree.heading("type", text="类型")
    ex_tree.heading("value", text="匹配模式")
    ex_tree.heading("hits", text="命中次数")
    ex_tree.column("type", width=80, stretch=False)
    ex_tree.column("value", width=200, stretch=True)
    ex_tree.column("hits", width=60, stretch=False)
    ex_scroll = ttk.Scrollbar(ex_panel, orient="vertical", command=ex_tree.yview)
    ex_tree.configure(yscrollcommand=ex_scroll.set)
    ex_tree.pack(side="left", fill="both", expand=True)
    ex_scroll.pack(side="right", fill="y")
    state["ex_tree"] = ex_tree

    ex_btn = ttk.Frame(ex_panel)
    ex_btn.pack(fill="x", pady=(4, 0))

    def _on_del_exclusion():
        sel = ex_tree.selection()
        if not sel: return
        vals = ex_tree.item(sel[0], "values")
        ex_id = vals[0]
        try:
            ctx.storage.sqlite._conn.execute(
                "UPDATE classification_exclusions SET is_active=0 WHERE rule_value=?", (vals[1],))
            ctx.storage.sqlite._conn.commit()
            ex_tree.delete(sel[0])
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=root)

    ttk.Button(ex_btn, text="停用选中", command=_on_del_exclusion, bootstyle="warning-outline").pack(side="left")

    # 3c: 全局规则
    gr_panel = ttk.LabelFrame(grid_frame, text="📜 全局规则")
    gr_panel.grid(column=0, row=1, sticky="nsew", padx=(0, 4), pady=(4, 0))
    gr_tree = ttk.Treeview(gr_panel, columns=("id", "text", "priority", "status", "refs"),
                           show="headings", height=6, selectmode="browse")
    gr_tree.heading("id", text="ID")
    gr_tree.heading("text", text="规则内容")
    gr_tree.heading("priority", text="优先级")
    gr_tree.heading("status", text="状态")
    gr_tree.heading("refs", text="引用")
    gr_tree.column("id", width=35, stretch=False)
    gr_tree.column("text", width=220, stretch=True)
    gr_tree.column("priority", width=50, stretch=False)
    gr_tree.column("status", width=50, stretch=False)
    gr_tree.column("refs", width=40, stretch=False)
    gr_scroll = ttk.Scrollbar(gr_panel, orient="vertical", command=gr_tree.yview)
    gr_tree.configure(yscrollcommand=gr_scroll.set)
    gr_tree.pack(side="left", fill="both", expand=True)
    gr_scroll.pack(side="right", fill="y")
    state["gr_tree"] = gr_tree

    gr_btn = ttk.Frame(gr_panel)
    gr_btn.pack(fill="x", pady=(4, 0))

    def _on_deactivate_rule():
        sel = gr_tree.selection()
        if not sel: return
        rule_id = int(gr_tree.item(sel[0], "values")[0])
        try:
            ctx.storage.sqlite.deactivate_global_rule(rule_id)
            gr_tree.delete(sel[0])
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=root)

    ttk.Button(gr_btn, text="停用选中", command=_on_deactivate_rule, bootstyle="warning-outline").pack(side="left")

    # 3d: 领域分布
    dom_panel = ttk.LabelFrame(grid_frame, text="📂 领域分布")
    dom_panel.grid(column=1, row=1, sticky="nsew", padx=(4, 0), pady=(4, 0))
    dom_tree = ttk.Treeview(dom_panel, columns=("name", "count"),
                            show="headings", height=6, selectmode="browse")
    dom_tree.heading("name", text="领域名称")
    dom_tree.heading("count", text="文档数")
    dom_tree.column("name", width=180, stretch=True)
    dom_tree.column("count", width=60, stretch=False)
    dom_scroll = ttk.Scrollbar(dom_panel, orient="vertical", command=dom_tree.yview)
    dom_tree.configure(yscrollcommand=dom_scroll.set)
    dom_tree.pack(side="left", fill="both", expand=True)
    dom_scroll.pack(side="right", fill="y")
    state["dom_tree"] = dom_tree

    # 初始加载
    _refresh_kb_all(ctx, state)

    return state


def _refresh_kb_all(ctx, state):
    """刷新知识库Tab全部数据：统计数字 + 关键词规则 + 排除规则 + 全局规则 + 领域"""
    stat_vars = state.get("stat_vars", {})

    # 统计
    try:
        conn = ctx.storage.sqlite._conn
        doc_count = conn.execute("SELECT COUNT(*) FROM document_files WHERE is_deleted=0").fetchone()[0]
        stat_vars.get("总文档").set(str(doc_count))

        ex_count = conn.execute("SELECT COUNT(*) FROM classification_exclusions WHERE is_active=1").fetchone()[0]
        stat_vars.get("排除规则").set(str(ex_count))

        gr_count = conn.execute("SELECT COUNT(*) FROM global_rules WHERE status='active'").fetchone()[0]
        stat_vars.get("全局规则").set(str(gr_count))

        dom_count = conn.execute("SELECT COUNT(DISTINCT name) FROM knowledge_domains").fetchone()[0]
        stat_vars.get("领域").set(str(dom_count))
    except Exception as e:
        logger.warning("Stats refresh failed: %s", e)

    # 关键词规则
    _refresh_kw_rules(ctx, state)

    # 排除规则
    _refresh_exclusions(ctx, state)

    # 全局规则
    _refresh_global_rules(ctx, state)

    # 领域
    _refresh_domains(ctx, state)


def _refresh_kw_rules(ctx, state):
    """刷新关键词规则列表 — 从 DynamicClassifier 读取 DEFAULT_KEYWORDS + custom_keywords"""
    tree = state.get("kw_tree")
    if not tree:
        return
    for i in tree.get_children():
        tree.delete(i)
    try:
        from ..classifier import DynamicClassifier
        dc = DynamicClassifier()
        all_kw = {**dc.keywords, **dc.custom_keywords}
        stat_vars = state.get("stat_vars", {})
        stat_vars.get("关键词规则").set(str(len(all_kw)))

        for cat, kws in sorted(all_kw.items()):
            preview = ", ".join(kws[:5])
            if len(kws) > 5:
                preview += f" +{len(kws)-5}"
            tree.insert("", "end", values=(cat, len(kws), preview))
    except Exception as e:
        logger.warning("KW rules refresh failed: %s", e)


def _refresh_exclusions(ctx, state):
    """刷新排除规则列表 — 从 classification_exclusions 表读取 is_active=1 的规则"""
    tree = state.get("ex_tree")
    if not tree:
        return
    for i in tree.get_children():
        tree.delete(i)
    try:
        exclusions = ctx.storage.sqlite.get_exclusions(active_only=True)
        for ex in exclusions:
            tree.insert("", "end", values=(
                ex.get("rule_type", ""),
                ex.get("rule_value", "")[:60],
                ex.get("hit_count", 0),
            ))
    except Exception as e:
        logger.warning("Exclusions refresh failed: %s", e)


def _refresh_global_rules(ctx, state):
    """刷新全局规则列表 — 从 global_rules 表读取 status='active' 的规则"""
    tree = state.get("gr_tree")
    if not tree:
        return
    for i in tree.get_children():
        tree.delete(i)
    try:
        rules = ctx.storage.sqlite.get_global_rules(limit=100)
        for r in rules:
            text = (r.get("rule_text") or "")[:60].replace("\n", " ")
            tree.insert("", "end", values=(
                r["id"], text,
                r.get("priority", ""),
                r.get("status", ""),
                r.get("reference_count", 0),
            ))
    except Exception as e:
        logger.warning("Global rules refresh failed: %s", e)


def _refresh_domains(ctx, state):
    """刷新领域分布列表 — 从 knowledge_domains 表读取领域及文档数"""
    tree = state.get("dom_tree")
    if not tree:
        return
    for i in tree.get_children():
        tree.delete(i)
    try:
        domains = ctx.storage.sqlite.list_domains()
        stat_vars = state.get("stat_vars", {})
        stat_vars.get("领域").set(str(len(domains)))
        for d in domains:
            tree.insert("", "end", values=(
                d["name"], d.get("doc_count", 0),
            ))
    except Exception as e:
        logger.warning("Domains refresh failed: %s", e)


# ═══════════════════════════════════════════════════════════
# 弹窗：添加筛选规则（6步工作流）
# ═══════════════════════════════════════════════════════════

def _show_add_rule_dialog(parent, ctx, doc_id, filepath, content):
    """添加规则弹窗 — 6步工作流

    ① 选择规则类型：关键词扩展 / 新建分类 / 排除规则
    ② 显示源文档路径和内容预览
    ③ 自动提取关键词（可点击选/取消，手动添加）
    ④ 配置规则参数（目标分类/Label/重要性）
    ⑤ 排除模式配置（仅排除规则时显示：路径/扩展名/正则/文件名）
    ⑥ 预览匹配效果（实时统计匹配文档数）

    后端调用：
    - 关键词扩展 → DynamicClassifier.learn_from_feedback()
    - 新建分类 → DynamicClassifier.add_category()
    - 排除规则 → storage.sqlite.add_exclusion()
    """
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import messagebox

    dlg = ttk.Toplevel(parent)
    dlg.title("添加筛选规则")
    dlg.geometry("560x680")
    dlg.transient(parent)
    dlg.grab_set()

    # 当前规则类型
    rule_type_var = ttk.StringVar(value="keyword_expand")

    # ─── ① 规则类型选择 ───
    ttk.Label(dlg, text="① 规则类型", font=("", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 4))

    type_frame = ttk.Frame(dlg)
    type_frame.pack(fill="x", padx=16, pady=(0, 8))
    type_frame.columnconfigure((0, 1, 2), weight=1)

    type_cards = {}
    for i, (rtype, icon, title, desc) in enumerate([
        ("keyword_expand", "🔑", "关键词扩展", "为已有分类添加关键词"),
        ("new_category", "➕", "新建分类规则", "创建全新分类+关键词"),
        ("exclusion", "🚫", "排除规则", "跳过符合模式的文件"),
    ]):
        card = ttk.Frame(type_frame, relief="ridge", padding=8)
        card.grid(column=i, row=0, sticky="nsew", padx=3)
        ttk.Label(card, text=icon, font=("", 16)).pack()
        ttk.Label(card, text=title, font=("", 10, "bold")).pack()
        ttk.Label(card, text=desc, font=("", 8), bootstyle="secondary").pack()
        type_cards[rtype] = card

        def _select_type(rt=rtype, c=card):
            rule_type_var.set(rt)
            for k, v in type_cards.items():
                v.configure(relief="ridge")
            c.configure(relief="solid")
            _toggle_exclusion_fields()

        card.bind("<Button-1>", lambda e, rt=rtype, c=card: _select_type(rt, c))
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda e, rt=rtype, c=card: _select_type(rt, c))

    # 默认选中
    type_cards["keyword_expand"].configure(relief="solid")

    # ─── ② 源文档 ───
    ttk.Label(dlg, text="② 源文档", font=("", 11, "bold")).pack(anchor="w", padx=16, pady=(8, 4))
    src_frame = ttk.Frame(dlg)
    src_frame.pack(fill="x", padx=16, pady=(0, 8))
    ttk.Label(src_frame, text=f"📄 {filepath}", font=("", 9), bootstyle="info",
              wraplength=480).pack(anchor="w")
    preview_box = tk.Text(src_frame, height=3, wrap="word", font=("", 9),
                          bg="#1e252e", fg="#c9d1d9", relief="flat", padx=6, pady=4)
    preview_box.pack(fill="x", pady=(4, 0))
    preview_box.insert("1.0", content[:300])
    preview_box.configure(state="disabled")

    # ─── ③ 自动提取关键词 ───
    ttk.Label(dlg, text="③ 关键词", font=("", 11, "bold")).pack(anchor="w", padx=16, pady=(8, 4))

    extracted_kws = _extract_keywords_from_content(content)
    kw_selected = set(extracted_kws[:6])

    kw_frame = ttk.Frame(dlg)
    kw_frame.pack(fill="x", padx=16, pady=(0, 4))
    kw_tags_frame = ttk.Frame(kw_frame)
    kw_tags_frame.pack(fill="x")

    kw_label_vars = {}

    def _rebuild_kw_tags():
        for w in kw_tags_frame.winfo_children():
            w.destroy()
        for kw in extracted_kws:
            is_sel = kw in kw_selected
            style = "info" if is_sel else "secondary"
            btn = ttk.Button(kw_tags_frame, text=kw, bootstyle=f"{style}-outline",
                             command=lambda k=kw: _toggle_kw(k))
            btn.pack(side="left", padx=2, pady=2)

    def _toggle_kw(kw):
        if kw in kw_selected:
            kw_selected.discard(kw)
        else:
            kw_selected.add(kw)
        _rebuild_kw_tags()

    _rebuild_kw_tags()

    # 手动添加关键词
    add_kw_frame = ttk.Frame(dlg)
    add_kw_frame.pack(fill="x", padx=16, pady=(0, 8))
    new_kw_var = ttk.StringVar()
    ttk.Entry(add_kw_frame, textvariable=new_kw_var, width=20).pack(side="left")
    def _add_manual_kw():
        kw = new_kw_var.get().strip()
        if kw and kw not in extracted_kws:
            extracted_kws.append(kw)
            kw_selected.add(kw)
            _rebuild_kw_tags()
            new_kw_var.set("")
    ttk.Button(add_kw_frame, text="添加", command=_add_manual_kw, bootstyle="success-outline").pack(side="left", padx=4)

    # ─── ④ 规则配置 ───
    ttk.Label(dlg, text="④ 规则配置", font=("", 11, "bold")).pack(anchor="w", padx=16, pady=(4, 4))
    cfg_frame = ttk.Frame(dlg)
    cfg_frame.pack(fill="x", padx=16, pady=(0, 4))

    ttk.Label(cfg_frame, text="目标分类", font=("", 9)).grid(row=0, column=0, padx=(0, 4))
    target_cat = ttk.StringVar(value="AI专属类.Agent配置")
    ttk.Combobox(cfg_frame, textvariable=target_cat, width=20, state="readonly",
                 values=list(set(list(_get_all_kw_categories()) + _REVIEW_CATEGORIES))).grid(row=0, column=1, padx=(0, 12))

    ttk.Label(cfg_frame, text="Label", font=("", 9)).grid(row=0, column=2, padx=(0, 4))
    target_label = ttk.StringVar(value="meta_rule")
    ttk.Combobox(cfg_frame, textvariable=target_label, width=12, state="readonly",
                 values=_REVIEW_LABELS).grid(row=0, column=3)

    ttk.Label(cfg_frame, text="重要性", font=("", 9)).grid(row=1, column=0, padx=(0, 4), pady=(4, 0))
    target_imp = ttk.StringVar(value="P1")
    ttk.Combobox(cfg_frame, textvariable=target_imp, width=6, state="readonly",
                 values=_REVIEW_IMPORTANCE).grid(row=1, column=1, padx=(0, 12), pady=(4, 0))

    # ─── ⑤ 排除模式（仅排除时显示）───
    ex_frame = ttk.LabelFrame(dlg, text="⑤ 排除模式")
    ex_frame.pack(fill="x", padx=16, pady=(4, 4))

    ex_type = ttk.StringVar(value="path_contains")
    ttk.Label(ex_frame, text="类型", font=("", 9)).pack(anchor="w")
    ttk.Combobox(ex_frame, textvariable=ex_type, width=20, state="readonly",
                 values=["path_contains", "extension", "content_regex", "filename"]).pack(anchor="w", pady=(0, 4))

    ex_pattern = ttk.StringVar()
    ttk.Label(ex_frame, text="匹配模式", font=("", 9)).pack(anchor="w")
    ttk.Entry(ex_frame, textvariable=ex_pattern, width=40).pack(anchor="w")

    def _toggle_exclusion_fields():
        if rule_type_var.get() == "exclusion":
            ex_frame.pack(fill="x", padx=16, pady=(4, 4))
        else:
            ex_frame.pack_forget()

    _toggle_exclusion_fields()

    # ─── ⑥ 预览 ───
    preview_frame = ttk.LabelFrame(dlg, text="⑥ 预览匹配")
    preview_frame.pack(fill="both", expand=True, padx=16, pady=(4, 8))

    match_count_var = ttk.StringVar(value="点击「预览」查看匹配")
    ttk.Label(preview_frame, textvariable=match_count_var, font=("", 10, "bold")).pack(anchor="w")

    match_list = tk.Text(preview_frame, height=3, wrap="word", font=("", 9),
                         bg="#1e252e", fg="#c9d1d9", relief="flat", padx=6, pady=4)
    match_list.pack(fill="both", expand=True, pady=(4, 0))
    match_list.configure(state="disabled")

    def _preview_match():
        """预览：统计匹配文档数"""
        kws = list(kw_selected)
        if rule_type_var.get() == "exclusion":
            pattern = ex_pattern.get()
            match_count_var.set(f"排除模式: {ex_type.get()} = {pattern}")
            match_list.configure(state="normal")
            match_list.delete("1.0", "end")
            match_list.insert("1.0", f"排除规则将阻止包含「{pattern}」的文件进入分类流程")
            match_list.configure(state="disabled")
            return

        try:
            conn = ctx.storage.sqlite._conn
            total = 0
            samples = []
            for kw in kws:
                rows = conn.execute(
                    "SELECT d.file_path FROM document_files d "
                    "JOIN memory_classify c ON d.id = c.doc_id "
                    "WHERE d.is_deleted = 0 AND c.compact_content LIKE ? LIMIT 5",
                    (f"%{kw}%",)
                ).fetchall()
                total += len(rows)
                samples.extend([r[0] for r in rows])

            match_count_var.set(f"关键词将匹配约 {total} 条文档")
            match_list.configure(state="normal")
            match_list.delete("1.0", "end")
            for s in samples[:5]:
                match_list.insert("end", f"✓ {s}\n")
            if total > 5:
                match_list.insert("end", f"... 还有 {total - 5} 条")
            match_list.configure(state="disabled")
        except Exception as e:
            match_count_var.set(f"预览失败: {e}")

    ttk.Button(preview_frame, text="刷新预览", command=_preview_match, bootstyle="outline").pack(anchor="w", pady=(4, 0))

    # ─── 底部按钮 ───
    btn_bar = ttk.Frame(dlg)
    btn_bar.pack(fill="x", padx=16, pady=(0, 12))

    def _confirm():
        kws = list(kw_selected)
        rt = rule_type_var.get()

        try:
            if rt == "keyword_expand":
                cat = target_cat.get()
                from ..classifier import DynamicClassifier
                dc = DynamicClassifier()
                dc.learn_from_feedback(cat, " ".join(kws))
                messagebox.showinfo("成功", f"已为「{cat}」添加 {len(kws)} 个关键词", parent=dlg)
                if _kb_state_ref[0]:
                    _refresh_kw_rules(ctx, _kb_state_ref[0])
            elif rt == "new_category":
                cat = target_cat.get()
                from ..classifier import DynamicClassifier
                dc = DynamicClassifier()
                dc.add_category(cat, keywords=kws)
                messagebox.showinfo("成功", f"已创建分类「{cat}」，含 {len(kws)} 个关键词", parent=dlg)
                if _kb_state_ref[0]:
                    _refresh_kw_rules(ctx, _kb_state_ref[0])
            elif rt == "exclusion":
                ctx.storage.sqlite.add_exclusion(
                    ex_type.get(), ex_pattern.get(),
                    f"从审核创建: {ex_pattern.get()}")
                messagebox.showinfo("成功", f"已添加排除规则: {ex_type.get()} = {ex_pattern.get()}", parent=dlg)
                if _kb_state_ref[0]:
                    _refresh_exclusions(ctx, _kb_state_ref[0])

            dlg.destroy()
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=dlg)

    ttk.Button(btn_bar, text="  取消  ", command=dlg.destroy, bootstyle="outline").pack(side="left")
    ttk.Button(btn_bar, text="  确认创建  ", command=_confirm, bootstyle="primary").pack(side="right")


def _quick_add_rule(ctx, filepath, content):
    """批量添加规则（审核Tab批量操作用）

    自动从内容提取关键词 → 用 DynamicClassifier.classify 匹配已有分类 →
    调用 learn_from_feedback 将关键词追加到对应分类的 custom_keywords。
    """
    try:
        kws = _extract_keywords_from_content(content)
        if not kws:
            return
        from ..classifier import DynamicClassifier
        dc = DynamicClassifier()
        # 尝试匹配已有分类
        result = dc.classify(content, filepath)
        cat = f"{result['category']}.{result['sub_category']}" if result.get("sub_category") else result["category"]
        if cat in dc.keywords or cat in dc.custom_keywords:
            dc.learn_from_feedback(cat, " ".join(kws[:5]))
    except Exception as e:
        logger.debug("Quick add rule failed: %s", e)


# ═══════════════════════════════════════════════════════════
# 弹窗：添加关键词规则（知识库Tab用）
# ═══════════════════════════════════════════════════════════

def _show_add_keyword_dialog(parent, ctx, state):
    """知识库Tab的「添加关键词规则」弹窗

    用户选择目标分类 + 输入逗号分隔的关键词，
    调用 DynamicClassifier.learn_from_feedback 追加到 custom_keywords。
    """
    import tkinter as tk
    import ttkbootstrap as ttk
    from tkinter import messagebox

    dlg = ttk.Toplevel(parent)
    dlg.title("添加关键词规则")
    dlg.geometry("400x280")
    dlg.transient(parent)
    dlg.grab_set()

    ttk.Label(dlg, text="目标分类", font=("", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
    cat_var = ttk.StringVar()
    from ..classifier import DynamicClassifier
    dc = DynamicClassifier()
    ttk.Combobox(dlg, textvariable=cat_var, width=30, state="readonly",
                 values=list(dc.get_all_categories()) + ["新建..."]).pack(padx=16, anchor="w")

    ttk.Label(dlg, text="关键词（逗号分隔）", font=("", 10, "bold")).pack(anchor="w", padx=16, pady=(8, 4))
    kw_var = ttk.StringVar()
    ttk.Entry(dlg, textvariable=kw_var, width=40).pack(padx=16, anchor="w")

    def _add():
        cat = cat_var.get()
        kws_text = kw_var.get().strip()
        if not cat or not kws_text:
            messagebox.showwarning("提示", "请填写分类和关键词", parent=dlg)
            return
        kws = [k.strip() for k in re.split(r"[,，]", kws_text) if k.strip()]
        if not kws:
            return
        dc.learn_from_feedback(cat, " ".join(kws))
        messagebox.showinfo("成功", f"已为「{cat}」添加 {len(kws)} 个关键词", parent=dlg)
        _refresh_kw_rules(ctx, state)
        dlg.destroy()

    ttk.Button(dlg, text="确认添加", command=_add, bootstyle="primary").pack(pady=16)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _extract_keywords_from_content(content: str) -> list:
    """从内容中提取高频关键词

    使用正则提取中文(≥2字)和英文(≥3字母)词汇，
    按出现频率降序排列，返回前12个。
    """
    import re
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', content)
    freq = {}
    for w in words:
        wl = w.lower()
        if wl not in freq:
            freq[wl] = 0
        freq[wl] += 1
    sorted_words = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, f in sorted_words[:12]]


def _get_all_kw_categories():
    """获取所有关键词分类名 — 合并 DEFAULT_KEYWORDS + custom_keywords 的 key"""
    try:
        from ..classifier import DynamicClassifier
        dc = DynamicClassifier()
        return list(dc.get_all_categories())
    except Exception:
        return []


def _save_config_paths(ctx):
    """保存扫描路径到 config.toml — 同步 agent_paths 和 custom_white_path"""
    _update_config(
        agent_paths=ctx.config.scan.agent_paths,
        custom_white_path=ctx.config.scan.custom_white_path,
    )


def _update_config(**kwargs):
    """更新 config.toml 中的配置项

    支持字符串和列表两种类型：
    - 字符串：正则替换 key = "old" → key = "new"
    - 列表：正则替换 key = [...] → key = [json]
    注意：列表值含 ] 时正则可能提前截断（已知风险）。
    """
    cfg_path = _CONFIG_PATH
    if not cfg_path.exists():
        return
    content = cfg_path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        if isinstance(value, list):
            content = re.sub(
                rf'{key}\s*=\s*\[.*?\]',
                f'{key} = {json.dumps(value, ensure_ascii=False)}',
                content, count=1, flags=re.DOTALL
            )
        else:
            content = re.sub(rf'{key}\s*=\s*"[^"]*"', f'{key} = "{value}"', content)
    cfg_path.write_text(content, encoding="utf-8")


def _refresh_snapshots(ctx, combo):
    """刷新快照下拉列表 — 读取 snapshot_dir 下的 .zip 文件，按时间倒序"""
    try:
        snap_dir = Path(ctx.config.storage.snapshot_dir)
        names = sorted([p.name for p in snap_dir.glob("*.zip")], reverse=True) if snap_dir.exists() else []
        combo["values"] = names
        if names and not combo.get():
            combo.set(names[0])
    except Exception:
        pass


def _restore_snapshot(ctx, root, combo):
    """恢复快照 — 从用户选中的 zip 快照恢复数据，弹窗确认后执行"""
    from tkinter import messagebox
    snap_name = combo.get()
    if not snap_name:
        return
    if messagebox.askyesno("确认恢复", f"将从快照 {snap_name} 恢复数据，当前数据将被覆盖。", parent=root):
        def _run():
            ok = ctx.restore_from_snapshot(snap_name)
            root.after(0, lambda: messagebox.showinfo(
                "恢复完成" if ok else "恢复失败",
                "数据已恢复" if ok else f"快照 {snap_name} 恢复失败", parent=root))
        threading.Thread(target=_run, daemon=True).start()


def _import_files(ctx, root):
    """导入文件 — 弹出文件选择对话框，支持 PDF/ZIP/EPUB/HTML/DOCX/MD/TXT/JSON/PY/JS

    流程：DocumentParser 解析 → DocumentSplitter 分块 → pipeline.process_one 分类入库。
    在子线程中执行避免阻塞 UI。
    """
    from tkinter import filedialog, messagebox
    files = filedialog.askopenfilenames(
        title="选择要导入的文件",
        parent=root,
        filetypes=[
            ("所有支持的文件", "*.pdf *.zip *.epub *.html *.htm *.docx *.md *.txt *.json *.yaml *.yml *.py *.js"),
            ("电子书", "*.pdf *.epub *.docx"),
            ("网页", "*.html *.htm"),
            ("压缩包", "*.zip"),
            ("文本文件", "*.md *.txt *.json *.yaml *.yml"),
            ("代码文件", "*.py *.js"),
        ],
    )
    if not files:
        return

    def _run():
        from ..import_manager.parser import DocumentParser
        from ..import_manager.splitter import DocumentSplitter
        parser = DocumentParser()
        splitter = DocumentSplitter(max_size=8000, min_size=50, overlap=200)
        imported = 0
        for fp in files:
            try:
                doc = parser.parse(fp)
                if not doc or not doc.body:
                    continue
                chunks = splitter.split(doc.body, doc.source_type)
                for chunk in chunks:
                    ctx.pipeline.process_one(doc_id=0, content=chunk.content, filepath=fp, fast_lane=True)
                    imported += 1
            except Exception as e:
                logger.error("Import failed for %s: %s", fp, e)
        root.after(0, lambda: messagebox.showinfo("导入完成",
            f"共导入 {imported} 个片段\n来自 {len(files)} 个文件", parent=root))
    threading.Thread(target=_run, daemon=True).start()


def _import_folder(ctx, root):
    """导入文件夹 — 递归遍历选中目录下所有支持格式文件并导入

    支持格式：PDF/ZIP/EPUB/HTML/DOCX/MD/TXT/JSON/YAML/PY/JS
    自动跳过 node_modules/.git/__pycache__ 等目录。
    在子线程中执行避免阻塞 UI。
    """
    from tkinter import filedialog, messagebox
    folder = filedialog.askdirectory(title="选择要导入的文件夹", parent=root)
    if not folder:
        return

    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build",
                  "venv", ".venv", ".env", ".cache", ".tmp", ".bak"}
    _SUPPORTED_EXT = {".pdf", ".zip", ".epub", ".html", ".htm", ".docx",
                      ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js"}

    def _run():
        from ..import_manager.parser import DocumentParser
        from ..import_manager.splitter import DocumentSplitter
        parser = DocumentParser()
        splitter = DocumentSplitter(max_size=8000, min_size=50, overlap=200)
        # 收集所有支持的文件
        all_files = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if Path(fn).suffix.lower() in _SUPPORTED_EXT:
                    all_files.append(os.path.join(dirpath, fn))
        if not all_files:
            root.after(0, lambda: messagebox.showinfo("导入完成",
                "文件夹中没有找到支持的文件", parent=root))
            return
        imported = 0
        for fp in all_files:
            try:
                doc = parser.parse(fp)
                if not doc or not doc.body:
                    continue
                chunks = splitter.split(doc.body, doc.source_type)
                for chunk in chunks:
                    ctx.pipeline.process_one(doc_id=0, content=chunk.content, filepath=fp, fast_lane=True)
                    imported += 1
            except Exception as e:
                logger.error("Import failed for %s: %s", fp, e)
        root.after(0, lambda: messagebox.showinfo("导入完成",
            f"共导入 {imported} 个片段\n来自 {len(all_files)} 个文件", parent=root))
    threading.Thread(target=_run, daemon=True).start()


def _fetch_home(ctx, status_vars, phase_var,
                progress_var, progress_label, path_tree):
    """每秒刷新首页状态数据 — 模型/文档数/队列/连接 + 扫描进度 + 路径列表

    读取 ctx.get_status_snapshot() 获取实时状态，
    根据 scan_progress.phase 更新进度条和文案。
    """
    try:
        snap = ctx.get_status_snapshot()
    except Exception:
        return

    embed_status = "✓" if (ctx.llm and ctx.llm.has_embed_model) else "✗"
    status_vars["model"].set(f"关键词分类 Embed:{embed_status}")
    status_vars["llm_status"].set("keyword_only")
    status_vars["api"].set("无需LLM")
    status_vars["docs"].set(f"{snap.get('doc_count', 0)}")
    status_vars["queue"].set(f"{snap.get('classify_queue', 0)}")

    scan = snap.get("scan_progress", {})
    phase = scan.get("phase", "idle")
    pm = {"idle": "空闲", "scanning": "扫描中…", "classifying": "分类中…",
          "exporting": "导出中…", "error": "出错"}
    phase_var.set(f"阶段: {pm.get(phase, phase)}")

    cp = snap.get("classify_progress", {})
    total = cp.get("total", 0)
    completed = cp.get("completed", 0)
    if phase == "classifying" and total > 0:
        pct = min(int(completed / total * 100), 100)
        progress_var.set(pct)
        cur = cp.get("current_item")
        cur_file = os.path.basename(cur) if cur else ""
        detail = f"已完成 {completed}/{total} ({pct}%)"
        if cur_file:
            detail += f" · {cur_file}"
        progress_label.set(detail)
    elif phase == "scanning":
        progress_var.set(50)
        progress_label.set(f"已扫描 {scan.get('count', 0)} 个文件…")
    elif phase == "exporting":
        progress_var.set(90)
        progress_label.set("正在导出记忆文件…")
    else:
        progress_var.set(0)
        progress_label.set("")

    all_paths = [f"📁 {p}" for p in ctx.config.scan.agent_paths] + \
                [f"🖊 {p}" for p in ctx.config.scan.custom_white_path]
    existing = {path_tree.item(i, "text") or path_tree.set(i, "path") for i in path_tree.get_children("")}
    if set(all_paths) != existing:
        for i in path_tree.get_children(""):
            path_tree.delete(i)
        for p in all_paths:
            path_tree.insert("", "end", text=p)


def _set_window_icon(root):
    """设置窗口图标 — 读取项目根目录的 tray_icon.png，缩放为 64x64"""
    from PIL import Image, ImageTk
    try:
        png_path = str(Path(__file__).resolve().parent.parent.parent / "tray_icon.png")
        img = Image.open(png_path).resize((64, 64), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)
        root._icon_img = photo
    except Exception as e:
        logger.warning("set window icon failed: %s", e)

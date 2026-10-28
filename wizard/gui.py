# -*- coding: utf-8 -*-
"""wizard.gui — 图形向导主界面（tkinter，无第三方 UI 依赖）。

五步流程：安全须知 → 体检 → 制作动画 → 安装试用 → 固化/回滚。
所有耗时操作都在线程里跑，结果通过队列回到 UI 线程（tkinter 非线程安全）。

界面支持：
  * 中文 / English 即时切换（i18n，切换后重建界面但保留状态）
  * 字体与字号自选（写入 settings.json）
  * 首次启动的"用户须知"（同意记录持久化）
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import ANIM_NAME, APP_NAME, __version__, i18n, paths
from . import deploy, esp, pack, sysinfo

C_OK = '#1f7a1f'
C_WARN = '#a86400'
C_FAIL = '#b00020'
C_DIM = '#5a5a5a'
BG = '#f7f7f7'      # 与 ttk vista 主题的底色接近，画布不显突兀

NOTICE_VERSION = 1

# (i18n key, 实际值)
RES_CHOICES = [
    ('res.auto', 'auto'),
    ('res.1080', (1920, 1080)),
    ('res.2k', (2560, 1440)),
    ('res.4k', (3840, 2160)),
    ('res.720', (1280, 720)),
]
FMT_CHOICES = [('fmt.qoi', 'QOI'), ('fmt.bmp', 'BMP')]
FPS_CHOICES = ['12', '15', '24', '30', '60', '120']

# 常见中英文字体优先排在前面，其余按系统列表补齐
PREFERRED_FONTS = ('Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei', 'SimSun',
                   'Segoe UI', 'Consolas', 'Arial')


def _choice_labels(choices):
    return [i18n.t(k) for k, _ in choices]


def _choice_value(choices, label):
    for k, v in choices:
        if i18n.t(k) == label:
            return v
    return choices[0][1]


class Gui(object):
    def __init__(self, show_notice=True):
        self.settings = paths.load_settings()
        i18n.set_lang(self.settings.get('lang', i18n.DEFAULT_LANG))

        # DPI 感知必须在创建 Tk 之前设置，否则界面在高分屏上被系统位图放大而发虚
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self._flex_labels = []          # 需要随窗口宽度自动换行的长文本标签
        self._check_redraw_pending = False
        self._preview_path = ''

        self.log = paths.Logger(sink=self._log_append)
        self.q = queue.Queue()
        self.pf = None
        self.src = ''
        self.anim_dir = ''
        self.install_result = None
        self.busy = False
        self.step = 0
        self.checks = []
        self._log_buf = []

        self._init_style()
        self._build()
        self._on_resize()
        self.root.title('%s %s' % (i18n.t('app.title'), __version__))
        self.root.bind('<Configure>', self._on_resize)
        self._show(0)
        self.root.after(80, self._poll)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.log('[gui] 启动，日志：%s' % self.log.path)
        self._startup_state()
        if show_notice:
            self.root.after(120, self._first_run_notice)

    # ------------------------------------------------------------------ #
    # 字体与样式                                                          #
    # ------------------------------------------------------------------ #

    def _font(self, delta=0, bold=False):
        fam = self.settings.get('font_family') or ''
        if not fam:
            fam = 'Segoe UI' if i18n.is_en() else 'Microsoft YaHei UI'
        try:
            size = int(self.settings.get('font_size', 12)) + delta
        except (TypeError, ValueError):
            size = 12 + delta
        return (fam, size, 'bold') if bold else (fam, size)

    def _mono(self):
        try:
            size = max(9, int(self.settings.get('font_size', 12)) - 2)
        except (TypeError, ValueError):
            size = 9
        return ('Consolas', size)

    def _init_style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use('vista')
        except tk.TclError:
            pass
        st.configure('.', font=self._font())
        st.configure('Title.TLabel', font=self._font(5, True))
        st.configure('H.TLabel', font=self._font(2, True))
        st.configure('Dim.TLabel', foreground=C_DIM)
        st.configure('Ok.TLabel', foreground=C_OK)
        st.configure('Warn.TLabel', foreground=C_WARN)
        st.configure('Fail.TLabel', foreground=C_FAIL)
        st.configure('Big.TButton', font=self._font(0, True), padding=(14, 8))
        # Treeview 的行高不吃字体设置，必须自己按字体行距算，否则大字号下文字会重叠
        try:
            import tkinter.font as tkfont
            lh = tkfont.Font(root=self.root, font=self._font()).metrics('linespace')
        except Exception:
            lh = 0
        row_h = max(20, int(lh), int(lh * 1.15) + 6)
        st.configure('Treeview', rowheight=row_h, font=self._font())
        st.configure('Treeview.Heading', font=self._font(0, True))
        self.root.option_add('*Font', self._font())

    def _build(self):
        # 顶部标题条
        head = ttk.Frame(self.root, padding=(16, 12, 16, 6))
        self.head_frame = head
        head.pack(fill='x')
        ttk.Label(head, text=i18n.t('app.title'), style='Title.TLabel').pack(side='left')
        self.var_admin = tk.StringVar()
        self.lbl_admin = ttk.Label(head, textvariable=self.var_admin, style='Ok.TLabel')
        self.lbl_admin.pack(side='right')
        ttk.Button(head, text=i18n.t('btn.logs'), command=self._open_logs).pack(
            side='right', padx=6)
        ttk.Button(head, text=i18n.t('btn.settings'), command=self._open_settings).pack(
            side='right', padx=2)
        ttk.Button(head, text=i18n.t('btn.notice'), command=self._open_notice).pack(
            side='right', padx=2)

        # 步骤指示
        self.stepbar = ttk.Frame(self.root, padding=(16, 0))
        self.stepbar.pack(fill='x')
        self.step_labels = []
        for i in range(1, 6):
            lb = ttk.Label(self.stepbar, text=i18n.t('step.%d' % i), padding=(0, 4, 16, 4))
            lb.pack(side='left')
            self.step_labels.append(lb)

        self.sep_top = ttk.Separator(self.root)
        self.sep_top.pack(fill='x', pady=(4, 0))

        # 内容区（每个步骤一个 Frame，切换 pack/pack_forget）
        self.body = ttk.Frame(self.root, padding=16)
        self.body.pack(fill='both', expand=True)
        self.pages = []
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        for builder in (self._page_welcome, self._page_check,
                        self._page_pack, self._page_install, self._page_done):
            fr = ttk.Frame(self.body, width=1)   # 宽度交给容器，不被内容撑开
            builder(fr)
            self.pages.append(fr)

        # 底部导航
        self.sep_bottom = ttk.Separator(self.root)
        self.sep_bottom.pack(fill='x')
        nav = ttk.Frame(self.root, padding=(16, 10))
        self.nav_frame = nav
        nav.pack(fill='x')
        self.btn_back = ttk.Button(nav, text=i18n.t('btn.back'), command=self._back)
        self.btn_back.pack(side='left')
        self.btn_next = ttk.Button(nav, text=i18n.t('btn.next'),
                                   style='Big.TButton', command=self._next)
        self.btn_next.pack(side='right')
        self.var_hint = tk.StringVar(value='')
        ttk.Label(nav, textvariable=self.var_hint, style='Dim.TLabel').pack(
            side='left', padx=12)
        self._fit_window()

    def _on_resize(self, ev=None):
        """窗口宽度变化时，把长文本标签的换行宽度跟着改，避免大字号的窄条。"""
        if ev is not None and ev.widget is not self.root:
            return
        w = (ev.width if ev is not None else self.root.winfo_width()) - 40
        if w < 320:
            return
        if self.step == 1 and getattr(self, 'pf', None) is not None:
            self._tree_layout()
        if self.step == 2 and getattr(self, '_preview_path', ''):
            if not getattr(self, '_preview_redraw_pending', False):
                self._preview_redraw_pending = True
                self.root.after(200, self._redraw_preview)
        for lb in list(getattr(self, '_flex_labels', [])):
            try:
                if lb.winfo_exists() and int(lb.cget('wraplength') or 0) != w:
                    lb.configure(wraplength=w)
            except Exception:
                continue
        self._last_flex_w = w

    @staticmethod
    def _work_area():
        """屏幕可用区域（扣除任务栏），返回 (w, h)；取不到就退回 Tk 的屏幕尺寸。"""
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

            r = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
                w, h = r.right - r.left, r.bottom - r.top
                if w > 320 and h > 240:
                    return w, h
        except Exception:
            pass
        return 0, 0

    def _fit_window(self, remember=False):
        """按当前字号把窗口调大到刚好装下内容；超过屏幕可用区域则最大化。

        用户手动改过的窗口大小会记在设置里，下次沿用。
        """
        try:
            self.root.update_idletasks()
            aw, ah = self._work_area()
            if not aw:
                aw, ah = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            # pack(expand=True) 会让 winfo_reqheight 失真，这里按各块实际需求相加
            chrome = 0
            for w in (self.head_frame, self.stepbar, self.nav_frame,
                      self.sep_top, self.sep_bottom):
                chrome += w.winfo_reqheight()
            page_h = max([p.winfo_reqheight() for p in self.pages] or [0])
            page_w = max([p.winfo_reqwidth() for p in self.pages] or [0])
            head_w = self.head_frame.winfo_reqwidth()
            need_w = max(page_w + 48, head_w + 48)
            need_h = chrome + page_h + 72

            try:
                rem_w = int(self.settings.get('win_w') or 0)
                rem_h = int(self.settings.get('win_h') or 0)
            except (TypeError, ValueError):
                rem_w = rem_h = 0

            w = min(max(need_w, rem_w, 1280), aw - 24)
            h = min(max(need_h, rem_h, 860), ah - 24)
            self.root.minsize(min(need_w, aw - 24), min(need_h, ah - 24))
            # 内容吃不下就最大化（比凭空超出屏幕好，标题栏也还看得见）
            if need_w >= aw - 40 or need_h >= ah - 60:
                if self.root.state() != 'zoomed':
                    self.root.state('zoomed')
            else:
                if self.root.state() != 'normal':
                    self.root.state('normal')
                self.root.geometry('%dx%d' % (w, h))
            self.root.update_idletasks()
            # 二次校正：按控件实际需求再补高度，避免底部导航被裁掉
            for _ in range(3):
                if self.root.state() != 'normal':
                    break
                need2 = (self.body.winfo_reqheight()
                         + chrome + 24)
                cur = self.root.winfo_height()
                if need2 <= cur + 2:
                    break
                cur_w = self.root.winfo_width()
                self.root.geometry('%dx%d' % (cur_w, min(need2, ah - 24)))
                self.root.update_idletasks()
            self._on_resize()
            if remember:
                self.settings['win_w'] = w
                self.settings['win_h'] = h
                paths.save_settings(self.settings)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 第 1 步：安全须知                                                   #
    # ------------------------------------------------------------------ #

    def _page_welcome(self, fr):
        ttk.Label(fr, text=i18n.t('p1.head'), style='H.TLabel').pack(anchor='w')
        ttk.Label(fr, text=i18n.t('p1.intro'), justify='left').pack(anchor='w', pady=(8, 12))

        box = ttk.LabelFrame(fr, text=i18n.t('p1.box'), padding=12)
        box.pack(fill='x')
        for i in (1, 2, 3):
            ttk.Label(box, text=i18n.t('p1.r%d.t' % i), style='H.TLabel').pack(
                anchor='w', pady=(6, 2))
            lbl_r = ttk.Label(box, text=i18n.t('p1.r%d.d' % i), justify='left')
            lbl_r.pack(anchor='w', fill='x')
            self._flex_labels.append(lbl_r)

        self.var_banner = tk.StringVar(value='')
        self.lbl_banner = ttk.Label(fr, textvariable=self.var_banner, style='Warn.TLabel',
                                    justify='left')
        self.lbl_banner.pack(anchor='w', fill='x', pady=(12, 0))
        self._flex_labels.append(self.lbl_banner)

    # ------------------------------------------------------------------ #
    # 第 2 步：体检                                                       #
    # ------------------------------------------------------------------ #

    def _page_check(self, fr):
        top = ttk.Frame(fr)
        top.pack(fill='x')
        ttk.Label(top, text=i18n.t('p2.head'), style='H.TLabel').pack(side='left')
        self.btn_recheck = ttk.Button(top, text=i18n.t('p2.recheck'), command=self._do_check)
        self.btn_recheck.pack(side='right')

        # 体检结果用“可滚动 + 自动换行”的标签列表，而不是 Treeview：
        # Treeview 的行高不吃字体、单元格不换行，大字号下必然重叠/截断。
        outer = ttk.Frame(fr)
        outer.pack(fill='both', expand=True, pady=(8, 8))
        self.check_canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0,
                                      background=BG)
        vsb = ttk.Scrollbar(outer, orient='vertical', command=self.check_canvas.yview)
        self.check_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self.check_canvas.pack(side='left', fill='both', expand=True)
        self.check_inner = ttk.Frame(self.check_canvas)
        self._check_win = self.check_canvas.create_window((0, 0), window=self.check_inner,
                                                         anchor='nw')
        self.check_inner.bind(
            '<Configure>',
            lambda e: self.check_canvas.configure(scrollregion=self.check_canvas.bbox('all')))
        self.check_canvas.bind(
            '<Configure>',
            lambda e: self.check_canvas.itemconfigure(self._check_win, width=e.width))
        self.tree = None

        self.txt_fix = tk.Text(fr, height=4, wrap='word', font=self._font(),
                               relief='solid', borderwidth=1, background='#fffbe6')
        self.txt_fix.pack(fill='x')
        self.txt_fix.configure(state='disabled')

        self.var_check = tk.StringVar(value=i18n.t('p2.idle'))
        ttk.Label(fr, textvariable=self.var_check).pack(anchor='w', pady=(8, 0))

        if not sysinfo.is_admin():
            row = ttk.Frame(fr)
            row.pack(fill='x', pady=(6, 0))
            ttk.Label(row, text=i18n.t('p2.need.admin'),
                      style='Fail.TLabel').pack(side='left')
            ttk.Button(row, text=i18n.t('p2.relaunch'),
                       command=self._relaunch_admin).pack(side='left', padx=8)

    def _tree_layout(self):
        """窗口/字体变化后按新宽度重排体检结果（标签换行宽度跟着变）。"""
        if self.pf is not None and not self._check_redraw_pending:
            self._check_redraw_pending = True
            self.root.after(120, self._redraw_checks)

    def _redraw_checks(self):
        self._check_redraw_pending = False
        if self.pf is not None:
            self._render_check(self.pf)

    def _relaunch_admin(self):
        if sysinfo.relaunch_as_admin(logger=self.log):
            self.root.after(300, self.root.destroy)
        else:
            messagebox.showerror(i18n.t('p2.relaunch.fail.t'),
                                 i18n.t('p2.relaunch.fail.b'))

    def _do_check(self):
        if self.busy:
            return
        self.busy = True
        self.btn_recheck.config(state='disabled')
        self.var_check.set(i18n.t('p2.running'))
        self._check_clear()

        def work():
            try:
                pf = deploy.preflight(logger=self.log.write)
                self.q.put(('check', pf, None))
            except Exception:
                self.q.put(('check_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _check_clear(self):
        for w in self.check_inner.winfo_children():
            w.destroy()

    def _render_check(self, pf):
        self.pf = pf
        self._check_clear()
        style_of = {'ok': 'Ok.TLabel', 'warn': 'Warn.TLabel', 'error': 'Fail.TLabel'}
        try:
            total = max(360, self.check_canvas.winfo_width() or 900)
        except Exception:
            total = 900
        try:
            cw = max(8, tkfont.Font(root=self.root, font=self._font()).measure('阻'))
        except Exception:
            cw = 16
        w_mark = max(cw * 2 + 24, int(total * 0.08))
        w_title = max(cw * 8, int(total * 0.38))
        w_detail = max(cw * 8, total - w_mark - w_title - 30)

        # 表头
        for col, txt, wid in ((0, 'p2.col.level', w_mark), (1, 'p2.col.title', w_title),
                              (2, 'p2.col.detail', w_detail)):
            ttk.Label(self.check_inner, text=i18n.t(txt), style='H.TLabel',
                      wraplength=max(40, wid - 8), justify='left').grid(
                row=0, column=col, sticky='w', padx=(0, 10), pady=(0, 4))

        for i, c in enumerate(pf.checks, start=1):
            st = style_of.get(c.level, 'TLabel')
            mark = (i18n.t('p2.mark.' + c.level)
                    if c.level in ('ok', 'warn', 'error') else c.level)
            ttk.Label(self.check_inner, text=mark, style=st, justify='left',
                      wraplength=max(40, w_mark - 8)).grid(
                row=i, column=0, sticky='nw', padx=(0, 10), pady=2)
            ttk.Label(self.check_inner, text=c.title, style=st, justify='left',
                      wraplength=max(80, w_title - 8)).grid(
                row=i, column=1, sticky='nw', padx=(0, 10), pady=2)
            ttk.Label(self.check_inner, text=c.detail, justify='left',
                      wraplength=max(80, w_detail - 8)).grid(
                row=i, column=2, sticky='nw', pady=2)
        fixes = [c for c in pf.checks if c.fix]
        self.txt_fix.configure(state='normal')
        self.txt_fix.delete('1.0', 'end')
        if fixes:
            for c in fixes:
                self.txt_fix.insert('end', '【%s】\n' % c.title)
                for ln in c.fix.splitlines():
                    self.txt_fix.insert('end', '    ' + ln + '\n')
        else:
            self.txt_fix.insert('end', i18n.t('p2.nofix'))
        self.txt_fix.configure(state='disabled')
        if pf.ok:
            self.var_check.set(i18n.t('p2.pass', len(pf.checks) - len(pf.warnings),
                                      len(pf.warnings)))
        else:
            self.var_check.set(i18n.t('p2.fail', len(pf.errors)))
        self._refresh_admin_badge()
        self._refresh_buttons()

    # ------------------------------------------------------------------ #
    # 第 3 步：制作动画                                                   #
    # ------------------------------------------------------------------ #

    def _page_pack(self, fr):
        ttk.Label(fr, text=i18n.t('p3.head'), style='H.TLabel').pack(anchor='w')
        lbl_tip = ttk.Label(fr, text=i18n.t('p3.tip'), style='Dim.TLabel',
                            justify='left')
        lbl_tip.pack(anchor='w', fill='x', pady=(4, 10))
        self._flex_labels.append(lbl_tip)

        grid = ttk.Frame(fr)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text=i18n.t('p3.src')).grid(row=0, column=0, sticky='w', pady=4)
        self.var_src = tk.StringVar(value=self.src)
        ttk.Entry(grid, textvariable=self.var_src).grid(row=0, column=1, sticky='we', padx=8)
        btns = ttk.Frame(grid)
        btns.grid(row=0, column=2, sticky='e')
        ttk.Button(btns, text=i18n.t('p3.pickdir'), command=self._pick_dir).pack(side='left')
        ttk.Button(btns, text=i18n.t('p3.pickfile'), command=self._pick_file).pack(
            side='left', padx=4)
        ttk.Button(btns, text=i18n.t('p3.demo'), command=self._pick_demo).pack(side='left')
        ttk.Button(btns, text=i18n.t('p3.pickbin'),
                   command=self._pick_bin).pack(side='left', padx=(4, 0))

        ttk.Label(grid, text=i18n.t('p3.out')).grid(row=1, column=0, sticky='w', pady=4)
        self.var_out = tk.StringVar(value=os.path.join(paths.data_dir(), 'anim'))
        ttk.Entry(grid, textvariable=self.var_out).grid(row=1, column=1, sticky='we', padx=8)
        ttk.Button(grid, text=i18n.t('p3.browse'),
                   command=self._pick_out).grid(row=1, column=2, sticky='e')

        opt = ttk.Frame(fr)
        opt.pack(fill='x', pady=(10, 0))
        ttk.Label(opt, text=i18n.t('p3.res')).pack(side='left')
        self.var_res = tk.StringVar(value=_choice_labels(RES_CHOICES)[0])
        ttk.Combobox(opt, textvariable=self.var_res, state='readonly', width=24,
                     values=_choice_labels(RES_CHOICES)).pack(side='left', padx=(6, 18))
        ttk.Label(opt, text=i18n.t('p3.fps')).pack(side='left')
        self.var_fps = tk.StringVar(value='30')
        ttk.Combobox(opt, textvariable=self.var_fps, state='readonly', width=6,
                     values=FPS_CHOICES).pack(side='left', padx=(6, 18))
        ttk.Label(opt, text=i18n.t('p3.fmt')).pack(side='left')
        self.var_fmt = tk.StringVar(value=_choice_labels(FMT_CHOICES)[0])
        ttk.Combobox(opt, textvariable=self.var_fmt, state='readonly', width=22,
                     values=_choice_labels(FMT_CHOICES)).pack(side='left', padx=6)

        self.btn_pack = ttk.Button(fr, text=i18n.t('p3.start'), style='Big.TButton',
                                   command=self._do_pack)
        self.btn_pack.pack(anchor='w', pady=(14, 6))
        self.pb = ttk.Progressbar(fr, mode='determinate')
        self.pb.pack(fill='x')
        self.var_pack = tk.StringVar(value=i18n.t('p3.idle'))
        lbl_done = ttk.Label(fr, textvariable=self.var_pack, justify='left')
        lbl_done.pack(anchor='w', fill='x', pady=(8, 0))
        self._flex_labels.append(lbl_done)

        self.preview_frame = ttk.LabelFrame(fr, text=i18n.t('p3.preview'), padding=8)
        self.preview_frame.pack(fill='x', pady=(10, 0))
        # 预览区自适应：Label 自己就是面板，图片按面板尺寸缩放
        self.preview_box = self.preview_frame
        self.lbl_preview = ttk.Label(self.preview_frame, text=i18n.t('p3.preview.empty'),
                                     style='Dim.TLabel', anchor='center')
        self.lbl_preview.pack(fill='both', expand=True)

    def _pick_dir(self):
        d = filedialog.askdirectory(title=i18n.t('p3.pickdir'))
        if d:
            self.var_src.set(os.path.normpath(d))

    def _pick_file(self):
        f = filedialog.askopenfilename(
            title=i18n.t('p3.pickfile'),
            filetypes=[(i18n.t('p3.filetypes'),
                        '*.gif *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff '
                        '*.mp4 *.mov *.mkv *.webm *.avi'),
                       (i18n.t('p3.allfiles'), '*.*')])
        if f:
            self.var_src.set(os.path.normpath(f))

    def _pick_out(self):
        d = filedialog.askdirectory(title=i18n.t('p3.browse'))
        if d:
            self.var_out.set(os.path.normpath(d))

    def _pick_bin(self):
        """直接选一个已经做好的 anim.bin，跳过制作步骤。"""
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title=i18n.t('p3.pickbin'),
            filetypes=[(i18n.t('p3.filetypes.bin'), '*.bin'),
                       (i18n.t('p3.allfiles'), '*.*')])
        if not path:
            return
        meta = pack.read_meta(path)
        if not meta:
            messagebox.showerror(i18n.t('p3.bin.bad.t'), i18n.t('p3.bin.bad.b'))
            return
        dest = os.path.join(paths.data_dir(), 'imported')
        try:
            os.makedirs(dest, exist_ok=True)
            target = os.path.join(dest, ANIM_NAME)
            if os.path.abspath(path) != os.path.abspath(target):
                shutil.copyfile(path, target)
            # 现成动画不知道播放器该怎么配，按文件头里的宽高/帧率写一份 config.ini
            pack.write_config(dest, meta['width'], meta['height'], meta['fps'])
        except OSError as e:
            messagebox.showerror(i18n.t('err.pack'), str(e))
            return
        self.anim_dir = dest
        self._preview_path = target
        self.log('[gui] 载入现成动画：%s -> %s' % (path, target))
        self.var_pack.set(i18n.t('p3.bin.loaded', os.path.basename(path),
                                 meta['width'], meta['height'], meta['frames'],
                                 meta['fps'], meta['size'] / 1048576.0))
        self._show_preview(target)
        self._refresh_buttons()

    def _pick_demo(self):
        if self.busy:
            return
        dest = os.path.join(paths.data_dir(), 'demo_frames')
        self.var_src.set(dest)
        self.var_pack.set(i18n.t('p3.demo.making'))

        def work():
            try:
                pack.make_demo_frames(dest, 1280, 720, 40, logger=self.log.write)
                self.q.put(('demo', dest, None))
            except Exception:
                self.q.put(('pack_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _do_pack(self):
        if self.busy:
            return
        src = self.var_src.get().strip().strip('"')
        if not src:
            messagebox.showwarning(i18n.t('p3.need.src.t'), i18n.t('p3.need.src.b'))
            return
        out = self.var_out.get().strip() or os.path.join(paths.data_dir(), 'anim')
        res = _choice_value(RES_CHOICES, self.var_res.get())
        fps = int(self.var_fps.get())
        fmt = _choice_value(FMT_CHOICES, self.var_fmt.get())
        budget = None
        if self.pf and self.pf.free_mb:
            budget = max(1.0, self.pf.free_mb - 8.0)
        self.src = src
        self.busy = True
        self.btn_pack.config(state='disabled', text=i18n.t('p3.busy'))
        self.pb.config(value=0, maximum=1)
        self.var_pack.set(i18n.t('p3.busy'))

        def work():
            try:
                r = pack.pack(src, out, res, fps, fmt, budget_mb=budget,
                              logger=self.log.write,
                              progress=lambda i, n: self.q.put(('prog', (i, n), None)))
                self.q.put(('packed', r, None))
            except pack.PackError as e:
                self.q.put(('pack_user_err', str(e), None))
            except Exception:
                self.q.put(('pack_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _render_pack(self, r):
        self.anim_dir = os.path.dirname(r['anim'])
        self._preview_path = r['anim']
        self.var_pack.set(i18n.t('p3.done', r['frames'], r['width'], r['height'], r['fps'],
                                 r['fmt'], r['size_mb'], r['elapsed'], r['anim']))
        if r.get('warn'):
            self.var_pack.set(self.var_pack.get() + '\n' + r['warn'])
        self._show_preview(r['anim'])
        # 制作成功即解锁“下一步”（0.1.1 的 bug：这里没刷新按钮，导致做完动画仍灰着）
        self._refresh_buttons()

    def _show_preview(self, anim_path):
        try:
            from PIL import ImageTk
        except ImportError:
            return
        try:
            im = self._pick_preview_frame(anim_path)
            self.preview_size = (self.preview_box.winfo_width(), self.preview_box.winfo_height())
            iw, ih = im.size
            if iw and ih:
                # 按面板可用空间等比缩放，高分屏/大字号下也能看清
                # 缩略图约占窗口宽的 28%、高的 22%，既看得清又不挤占排版
                ww = self.root.winfo_width() or 1280
                hh = self.root.winfo_height() or 860
                w = max(280, min(700, int(ww * 0.28)))
                h = max(160, min(400, int(hh * 0.22)))
                k = min(w / float(iw), h / float(ih))
                im = im.resize((max(1, int(iw * k)), max(1, int(ih * k))))
            self.photo = ImageTk.PhotoImage(im)
            self.lbl_preview.configure(image=self.photo, text='')
            if not getattr(self, '_preview_deferred', False):
                self._preview_deferred = True
                self.root.after(250, self._repreview)
        except Exception as e:
            self.log('[gui] 预览失败：%s' % e)
            self.lbl_preview.configure(text=i18n.t('p3.preview.fail'))

    @staticmethod
    def _pick_preview_frame(anim_path):
        """挑一张“看得见”的首帧：动画常以黑场/淡入开场，直接取第 0 帧是一条黑框。

        先取第 0 帧，若几乎全黑就往后跳着找，最多看 12 个候选；都黑就还是用第 0 帧。
        """
        im = pack.read_frame(anim_path, 0)
        try:
            total = pack.frame_count(anim_path) or 1
        except Exception:
            total = 1
        if total <= 1 or not _is_blank(im):
            return im
        step = max(1, total // 12)
        for i in range(step, total, step):
            try:
                cand = pack.read_frame(anim_path, i)
            except Exception:
                break
            if not _is_blank(cand):
                return cand
        return im

    def _redraw_preview(self):
        self._preview_redraw_pending = False
        if self._preview_path and os.path.isfile(self._preview_path):
            self._show_preview(self._preview_path)

    def _repreview(self, path=None):
        """窗口尺寸变化后按新面板大小重绘首帧预览。"""
        p = path or getattr(self, '_preview_path', None)
        if p and os.path.isfile(p):
            self._show_preview(p)

    # ------------------------------------------------------------------ #
    # 第 4 步：安装（试用）                                               #
    # ------------------------------------------------------------------ #

    def _page_install(self, fr):
        ttk.Label(fr, text=i18n.t('p4.head'), style='H.TLabel').pack(anchor='w')
        ttk.Label(fr, text=i18n.t('p4.intro'), justify='left').pack(anchor='w', pady=(6, 4))
        plan = ttk.LabelFrame(fr, text=i18n.t('p4.plan'), padding=10)
        plan.pack(fill='x')
        for k in ('p4.l1', 'p4.l2', 'p4.l3', 'p4.l4'):
            lbl_p = ttk.Label(plan, text=i18n.t(k), justify='left')
            lbl_p.pack(anchor='w', fill='x', pady=1)
            self._flex_labels.append(lbl_p)

        self.var_plan = tk.StringVar(value=i18n.t('p4.need.pack'))
        lbl_plan = ttk.Label(fr, textvariable=self.var_plan, justify='left',
                            style='Dim.TLabel')
        lbl_plan.pack(anchor='w', fill='x', pady=(10, 6))
        self._flex_labels.append(lbl_plan)

        self.var_agree = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text=i18n.t('p4.agree'), variable=self.var_agree,
                        command=self._refresh_buttons).pack(anchor='w')

        row = ttk.Frame(fr)
        row.pack(fill='x', pady=(12, 6))
        self.btn_install = ttk.Button(row, text=i18n.t('p4.install'), style='Big.TButton',
                                      command=self._do_install)
        self.btn_install.pack(side='left')
        ttk.Button(row, text=i18n.t('p4.demo.install'),
                   command=self._install_demo).pack(side='left', padx=8)

        self.txt_log = tk.Text(fr, height=10, wrap='none', font=self._mono(),
                               relief='solid', borderwidth=1)
        self.txt_log.pack(fill='both', expand=True, pady=(8, 0))
        sb = ttk.Scrollbar(self.txt_log, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        if self._log_buf:
            self.txt_log.insert('end', ''.join(self._log_buf))

    def _install_demo(self):
        """没有素材也能体验：生成 720p 演示动画并直接安装。"""
        if self.busy:
            return
        self.busy = True
        self.btn_install.config(state='disabled')
        self.var_plan.set(i18n.t('p3.demo.making'))

        def work():
            try:
                dest = os.path.join(paths.data_dir(), 'demo_frames')
                pack.make_demo_frames(dest, 1280, 720, 40, logger=self.log.write)
                r = pack.pack(dest, os.path.join(paths.data_dir(), 'anim'),
                              (1280, 720), 24, 'QOI', logger=self.log.write)
                self.q.put(('packed_demo', r, None))
            except Exception:
                self.q.put(('pack_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _do_install(self):
        if self.busy:
            return
        if not self.var_agree.get():
            messagebox.showwarning(i18n.t('p4.warn.agree.t'), i18n.t('p4.warn.agree.b'))
            return
        if not self.anim_dir:
            messagebox.showwarning(i18n.t('p4.warn.anim.t'), i18n.t('p4.warn.anim.b'))
            return
        if not self.pf or not self.pf.ok:
            messagebox.showwarning(i18n.t('p4.warn.check.t'), i18n.t('p4.warn.check.b'))
            return
        if not messagebox.askyesno(
                i18n.t('p4.confirm.t'),
                i18n.t('p4.confirm.b', self.pf.esp.describe(), self.anim_dir)):
            return
        self.busy = True
        self.btn_install.config(state='disabled', text=i18n.t('p4.install.busy'))
        self.var_plan.set(i18n.t('p4.installing'))

        def work():
            try:
                r = deploy.install(self.pf, self.anim_dir, logger=self.log.write, trial=True)
                self.q.put(('installed', r, None))
            except (deploy.DeployError, esp.EspError) as e:
                self.q.put(('install_user_err', str(e), None))
            except Exception:
                self.q.put(('install_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _render_install(self, r):
        self.install_result = r
        self.var_plan.set(i18n.t('p4.done', r.guid, r.backup_dir))
        self.step = 4
        self._show(4)
        self._refresh_done_page()

    # ------------------------------------------------------------------ #
    # 第 5 步：固化 / 回滚                                                #
    # ------------------------------------------------------------------ #

    def _page_done(self, fr):
        ttk.Label(fr, text=i18n.t('p5.head'), style='H.TLabel').pack(anchor='w')
        self.var_state = tk.StringVar(value=i18n.t('p5.reading'))
        lbl_state = ttk.Label(fr, textvariable=self.var_state, justify='left')
        lbl_state.pack(anchor='w', fill='x', pady=(8, 10))
        self._flex_labels.append(lbl_state)

        row = ttk.Frame(fr)
        row.pack(fill='x')
        ttk.Button(row, text=i18n.t('p5.activate'), style='Big.TButton',
                   command=self._do_activate).pack(side='left')
        ttk.Button(row, text=i18n.t('p5.rollback'),
                   command=self._do_rollback).pack(side='left', padx=10)
        ttk.Button(row, text=i18n.t('p5.refresh'),
                   command=self._refresh_done_page).pack(side='left')

        box = ttk.LabelFrame(fr, text=i18n.t('p5.trouble'), padding=10)
        box.pack(fill='x', pady=(14, 0))
        lbl_tb = ttk.Label(box, justify='left', text=i18n.t('p5.trouble.body'))
        lbl_tb.pack(anchor='w', fill='x')
        self._flex_labels.append(lbl_tb)

        self.txt_state = tk.Text(fr, height=8, wrap='word', font=self._mono(),
                                 relief='solid', borderwidth=1)
        self.txt_state.pack(fill='both', expand=True, pady=(12, 0))

    def _refresh_done_page(self):
        def work():
            try:
                st = deploy.status(logger=self.log.write)
                self.q.put(('status', st, None))
            except Exception:
                self.q.put(('status_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _render_status(self, st):
        if st.get('installed'):
            if st.get('default'):
                msg = i18n.t('p5.st.default', st.get('guid'))
            elif st.get('pending'):
                msg = i18n.t('p5.st.pending', st.get('guid'))
            else:
                msg = i18n.t('p5.st.inactive', st.get('guid'))
        elif st.get('installed') is None:
            msg = i18n.t('p5.st.unknown', st.get('error', ''))
        else:
            msg = i18n.t('p5.st.none')
        self.var_state.set(msg)
        lines = []
        if st.get('esp'):
            lines.append(i18n.t('p5.esp', st['esp']))
            for k, v in (st.get('esp_files') or {}).items():
                lines.append('  %-8s %s' % (k, i18n.t('p5.file.exists' if v
                                                      else 'p5.file.missing')))
            if st.get('anim_mb'):
                lines.append('  anim.bin %.2f MB' % st['anim_mb'])
        elif st.get('esp_error'):
            lines.append(i18n.t('p5.esp', st['esp_error']))
        if st.get('displayorder'):
            lines.append(i18n.t('p5.order'))
            for i, g in enumerate(st['displayorder']):
                lines.append('  %d. %s' % (i + 1, g))
        self.txt_state.configure(state='normal')
        self.txt_state.delete('1.0', 'end')
        self.txt_state.insert('end', '\n'.join(lines))
        self.txt_state.configure(state='disabled')

    def _do_activate(self):
        guid = (self.install_result.guid if self.install_result else '')
        if not messagebox.askyesno(i18n.t('p5.confirm.act.t'), i18n.t('p5.confirm.act.b')):
            return
        try:
            g = deploy.activate(guid, logger=self.log.write)
            messagebox.showinfo(i18n.t('p5.act.ok.t'), i18n.t('p5.act.ok.b', g))
        except Exception as e:
            messagebox.showerror(i18n.t('p5.act.err.t'), str(e))
        self._refresh_done_page()

    def _do_rollback(self):
        if not messagebox.askyesno(i18n.t('p5.confirm.rb.t'), i18n.t('p5.confirm.rb.b')):
            return
        st = deploy.load_state()
        backup = st.get('backup', '')
        e = None
        try:
            e = esp.pick_windows_esp(logger=self.log.write)
        except esp.EspError as ex:
            self.log('[rollback] ESP 不可用：%s' % ex)
        try:
            done = deploy.rollback(logger=self.log.write,
                                   remove_files=not backup,
                                   esp_obj=e,
                                   restore_from=backup if backup else '')
            messagebox.showinfo(i18n.t('p5.rb.ok.t'),
                                i18n.t('p5.rb.ok.b', '\n'.join(done) or i18n.t('p5.nochange')))
        except Exception as ex:
            messagebox.showerror(i18n.t('p5.rb.err.t'), str(ex))
        self._refresh_done_page()

    # ------------------------------------------------------------------ #
    # 设置 / 用户须知                                                     #
    # ------------------------------------------------------------------ #

    def _open_settings(self):
        if self.busy:
            messagebox.showwarning(i18n.t('set.title'), i18n.t('set.busy'))
            return
        win = tk.Toplevel(self.root)
        win.title(i18n.t('set.title'))
        win.transient(self.root)
        win.resizable(False, False)
        fr = ttk.Frame(win, padding=16)
        fr.pack(fill='both', expand=True)

        lang_names = [name for _, name in i18n.LANGS]
        cur_lang = dict((c, n) for c, n in i18n.LANGS).get(
            self.settings.get('lang', 'zh'), lang_names[0])
        var_lang = tk.StringVar(value=cur_lang)

        families = _font_families(self.root)
        auto_label = i18n.t('set.font.auto')
        cur_fam = self.settings.get('font_family') or ''
        var_font = tk.StringVar(value=cur_fam if cur_fam in families else auto_label)
        var_size = tk.StringVar(value=str(self.settings.get('font_size', 12)))

        ttk.Label(fr, text=i18n.t('set.lang')).grid(row=0, column=0, sticky='w', pady=6)
        ttk.Combobox(fr, textvariable=var_lang, state='readonly', width=32,
                     values=lang_names).grid(row=0, column=1, sticky='we', pady=6)

        ttk.Label(fr, text=i18n.t('set.font')).grid(row=1, column=0, sticky='w', pady=6)
        cb_font = ttk.Combobox(fr, textvariable=var_font, state='readonly', width=32,
                               values=[auto_label] + families)
        cb_font.grid(row=1, column=1, sticky='we', pady=6)

        ttk.Label(fr, text=i18n.t('set.size')).grid(row=2, column=0, sticky='w', pady=6)
        ttk.Spinbox(fr, from_=10, to=28, textvariable=var_size, width=6).grid(
            row=2, column=1, sticky='w', pady=6)

        ttk.Label(fr, text=i18n.t('set.note'), style='Dim.TLabel', wraplength=420,
                  justify='left').grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 4))

        btns = ttk.Frame(fr)
        btns.grid(row=4, column=0, columnspan=2, sticky='e', pady=(8, 0))

        def apply():
            new = dict(self.settings)
            new['lang'] = dict((n, c) for c, n in i18n.LANGS).get(var_lang.get(), 'zh')
            fam = var_font.get()
            new['font_family'] = '' if fam in ('', i18n.t('set.font.auto')) else fam
            try:
                new['font_size'] = max(9, min(16, int(var_size.get())))
            except (TypeError, ValueError):
                new['font_size'] = 10
            paths.save_settings(new)
            win.destroy()
            self.settings = new
            i18n.set_lang(new['lang'])
            self._rebuild()

        ttk.Button(btns, text=i18n.t('btn.apply'), command=apply).pack(side='right')
        ttk.Button(btns, text=i18n.t('btn.close'), command=win.destroy).pack(
            side='right', padx=6)
        win.grab_set()

    def _notice_dialog(self):
        """返回 True=同意。"""
        win = tk.Toplevel(self.root)
        win.title(i18n.t('notice.title'))
        win.transient(self.root)
        win.geometry('880x660')
        win.minsize(680, 460)
        fr = ttk.Frame(win, padding=14)
        fr.pack(fill='both', expand=True)

        ttk.Label(fr, text=i18n.t('notice.title'), style='H.TLabel').pack(anchor='w')
        txt = tk.Text(fr, wrap='word', font=self._font(), relief='solid', borderwidth=1)
        txt.pack(fill='both', expand=True, pady=(8, 8))
        txt.insert('1.0', i18n.t('notice.body'))
        txt.configure(state='disabled')

        var_ok = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text=i18n.t('notice.check'), variable=var_ok).pack(anchor='w')

        row = ttk.Frame(fr)
        row.pack(fill='x', pady=(10, 0))
        result = {'ok': False}

        def agree():
            if not var_ok.get():
                messagebox.showwarning(i18n.t('notice.title'), i18n.t('notice.must'))
                return
            result['ok'] = True
            win.destroy()

        ttk.Button(row, text=i18n.t('notice.agree'), style='Big.TButton',
                   command=agree).pack(side='right', padx=(0, 2))
        ttk.Button(row, text=i18n.t('notice.quit'), command=win.destroy).pack(
            side='right', padx=10)
        win.grab_set()
        win.wait_window()
        return result['ok']

    def _first_run_notice(self):
        if int(self.settings.get('notice_accepted') or 0) >= NOTICE_VERSION:
            return
        if not self._notice_dialog():
            self._on_close()
            return
        self.settings['notice_accepted'] = NOTICE_VERSION
        paths.save_settings(self.settings)
        self.log('[gui] 用户须知已同意（v%d）' % NOTICE_VERSION)

    def _open_notice(self):
        if self._notice_dialog():
            self.settings['notice_accepted'] = NOTICE_VERSION
            paths.save_settings(self.settings)

    def _rebuild(self):
        """重建整个界面（语言/字体变更后），保留业务状态。"""
        keep = dict(step=self.step, pf=self.pf, anim_dir=self.anim_dir,
                    install_result=self.install_result, src=self.src,
                    log=''.join(self._log_buf))
        for w in self.root.winfo_children():
            w.destroy()
        self._init_style()
        self._build()
        self.root.title('%s %s' % (i18n.t('app.title'), __version__))
        if keep['src']:
            self.var_src.set(keep['src'])
        if self.anim_dir:
            self.var_pack.set(i18n.t('p3.done.short', os.path.dirname(self.anim_dir)))
        self._show(keep['step'])
        if keep['pf']:
            self._render_check(keep['pf'])
        self._refresh_admin_badge()

    # ------------------------------------------------------------------ #
    # 导航与事件循环                                                      #
    # ------------------------------------------------------------------ #

    def _show(self, i):
        self.step = i
        for k, fr in enumerate(self.pages):
            if k == i:
                fr.grid(row=0, column=0, sticky='nsew')
            else:
                fr.grid_remove()
        for k, lb in enumerate(self.step_labels):
            lb.configure(style='H.TLabel' if k == i else 'Dim.TLabel')
        self.btn_back.config(state='normal' if i > 0 else 'disabled')
        self.btn_next.config(text=i18n.t('btn.next') if i < 4 else i18n.t('btn.finish'))
        self._refresh_buttons()
        if i == 1 and not self.pf:
            self._do_check()
        if i == 4:
            self._refresh_done_page()

    def _refresh_buttons(self):
        can_next = True
        hint = ''
        if self.step == 1:
            if not self.pf:
                can_next = False
                hint = i18n.t('hint.checking')
            elif not self.pf.ok:
                can_next = False
                hint = i18n.t('hint.block')
        elif self.step == 2:
            if not self.anim_dir:
                can_next = False
                hint = i18n.t('hint.pack')
            else:
                hint = i18n.t('hint.pack.ok')
        elif self.step == 3:
            can_next = False
            hint = i18n.t('hint.install')
            self.btn_install.config(state='normal'
                                    if (self.var_agree.get() and self.anim_dir
                                        and not self.busy) else 'disabled')
        self.btn_next.config(state='normal' if can_next else 'disabled')
        self.var_hint.set(hint)

    def _next(self):
        if self.step < 4:
            self._show(self.step + 1)
        else:
            self._on_close()

    def _back(self):
        if self.step > 0:
            self._show(self.step - 1)

    def _poll(self):
        try:
            while True:
                kind, payload, _ = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, kind, payload):
        if kind == 'log':
            self._log_show(payload)
            return
        if kind in ('check',):
            self.busy = False
            self.btn_recheck.config(state='normal')
            self._render_check(payload)
        elif kind == 'prog':
            i, n = payload
            self.pb.config(maximum=n, value=i)
            self.var_pack.set(i18n.t('p3.progress', i, n))
        elif kind == 'packed':
            self.busy = False
            self.btn_pack.config(state='normal', text=i18n.t('p3.start'))
            self._render_pack(payload)
        elif kind == 'packed_demo':
            self.busy = False
            self.btn_install.config(state='normal', text=i18n.t('p4.install'))
            self.var_plan.set(i18n.t('p4.demo.ready', payload['size_mb']))
            self.anim_dir = os.path.dirname(payload['anim'])
            self._refresh_buttons()
        elif kind == 'installed':
            self.busy = False
            self.btn_install.config(state='normal', text=i18n.t('p4.install'))
            self._render_install(payload)
        elif kind == 'status':
            self._render_status(payload)
        elif kind == 'demo':
            self.var_pack.set(i18n.t('p3.demo.ready', payload))
        titles = {'pack_user_err': 'err.pack.user', 'pack_err': 'err.pack',
                  'install_user_err': 'err.install.user', 'install_err': 'err.install',
                  'check_err': 'err.check', 'status_err': 'err.status'}
        if kind in titles:
            self.busy = False
            self.btn_pack.config(state='normal', text=i18n.t('p3.start'))
            self.btn_install.config(state='normal', text=i18n.t('p4.install'))
            try:
                self.btn_recheck.config(state='normal')
            except Exception:
                pass
            tail = '\n'.join(str(payload).strip().splitlines()[-6:])
            self.log('[error] %s' % tail)
            messagebox.showerror(i18n.t(titles[kind]), tail)
            self.var_pack.set(tail.splitlines()[-1] if tail else i18n.t('p5.nochange'))

    def _log_append(self, line):
        # Tk 不是线程安全的：工作线程只能入队，由主线程 _log_show 真正写控件。
        self._log_buf.append(line + '\n')
        if len(self._log_buf) > 5000:
            del self._log_buf[:1000]
        self.q.put(('log', line, None))

    def _log_show(self, line):
        try:
            self.txt_log.insert('end', line + '\n')
            self.txt_log.see('end')
        except Exception:
            pass

    def _open_logs(self):
        try:
            os.startfile(paths.log_dir())
        except Exception:
            subprocess.Popen(['explorer', paths.log_dir()])

    def _refresh_admin_badge(self):
        if sysinfo.is_admin():
            self.var_admin.set(i18n.t('admin.ok'))
            self.lbl_admin.configure(style='Ok.TLabel')
        else:
            self.var_admin.set(i18n.t('admin.no'))
            self.lbl_admin.configure(style='Fail.TLabel')

    def _startup_state(self):
        self._refresh_admin_badge()
        try:
            st = deploy.status(logger=None)
        except Exception:
            st = {}
        if st.get('installed'):
            if st.get('default'):
                self.var_banner.set(i18n.t('banner.default', st.get('guid')))
            elif st.get('pending'):
                self.var_banner.set(i18n.t('banner.pending', st.get('guid')))
            else:
                self.var_banner.set(i18n.t('banner.inactive', st.get('guid')))

    def _on_close(self):
        self._remember_geometry()
        try:
            self.log.close()
        finally:
            self.root.destroy()

    def _remember_geometry(self):
        """记下用户当前的窗口大小，下次启动沿用（忽略最大化时的屏幕尺寸）。"""
        try:
            if self.root.state() == 'normal':
                w = self.root.winfo_width()
                h = self.root.winfo_height()
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                if w >= 800 and h >= 560 and not (w >= sw - 20 and h >= sh - 60):
                    self.settings['win_w'] = w
                    self.settings['win_h'] = h
                    paths.save_settings(self.settings)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()
        return 0


def _font_families(root):
    """系统中可用的字体族（去掉 @ 开头的竖排变体），常用字体排前面。"""
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    fams = set(f for f in fams if f and not f.startswith('@'))
    head = [f for f in PREFERRED_FONTS if f in fams]
    rest = sorted(fams - set(head), key=lambda s: s.lower())
    return head + rest


def _is_blank(im):
    """近乎纯色（黑场/白场）的帧不适合当缩略图。"""
    try:
        small = im.convert('L').resize((32, 18))
        data = list(small.getdata())
        lo, hi = min(data), max(data)
        return (hi - lo) < 8
    except Exception:
        return False


def run(show_notice=True):
    return Gui(show_notice=show_notice).run()


def smoke():
    """无交互冒烟：建窗口、渲染各页（含两种语言）、销毁。返回 0/1。"""
    g = Gui(show_notice=False)
    try:
        for i in range(5):
            g._show(i)
            g.root.update()
        for code in ('en', 'zh'):
            g.settings['lang'] = code
            i18n.set_lang(code)
            g._rebuild()
            g.root.update()
        g.log('[smoke] ok')
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        try:
            g.log.close()
        except Exception:
            pass
        g.root.destroy()

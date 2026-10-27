# -*- coding: utf-8 -*-
"""wizard.gui — 图形向导主界面（tkinter，无第三方 UI 依赖）。

五步流程：安全须知 → 体检 → 制作动画 → 安装试用 → 固化/回滚。
所有耗时操作都在线程里跑，结果通过队列回到 UI 线程（tkinter 非线程安全）。
"""
import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, APP_TITLE, __version__, paths
from . import deploy, esp, pack, sysinfo

C_OK = '#1f7a1f'
C_WARN = '#a86400'
C_FAIL = '#b00020'
C_DIM = '#5a5a5a'

FONT = ('Microsoft YaHei UI', 10)
FONT_B = ('Microsoft YaHei UI', 10, 'bold')
FONT_T = ('Microsoft YaHei UI', 15, 'bold')
FONT_H = ('Microsoft YaHei UI', 12, 'bold')

RES_CHOICES = [
    ('自动（按当前分辨率）', 'auto'),
    ('1920x1080（1080p）', (1920, 1080)),
    ('2560x1440（2K）', (2560, 1440)),
    ('3840x2160（4K）', (3840, 2160)),
    ('1280x720（720p，体积最小）', (1280, 720)),
]


class Gui(object):
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('%s %s' % (APP_TITLE, __version__))
        self.root.geometry('980x720')
        self.root.minsize(900, 640)
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.log = paths.Logger(sink=self._log_append)
        self.q = queue.Queue()
        self.pf = None
        self.src = ''
        self.anim_dir = ''
        self.install_result = None
        self.busy = False
        self.step = 0
        self.checks = []

        self._init_style()
        self._build()
        self._show(0)
        self.root.after(80, self._poll)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.log('[gui] 启动，日志：%s' % self.log.path)
        self._startup_state()

    # ------------------------------------------------------------------ #
    # 样式与骨架                                                          #
    # ------------------------------------------------------------------ #

    def _init_style(self):
        st = ttk.Style()
        try:
            st.theme_use('vista')
        except tk.TclError:
            pass
        st.configure('.', font=FONT)
        st.configure('Title.TLabel', font=FONT_T)
        st.configure('H.TLabel', font=FONT_H)
        st.configure('Dim.TLabel', foreground=C_DIM)
        st.configure('Ok.TLabel', foreground=C_OK)
        st.configure('Warn.TLabel', foreground=C_WARN)
        st.configure('Fail.TLabel', foreground=C_FAIL)
        st.configure('Big.TButton', font=FONT_B, padding=(14, 8))
        self.root.option_add('*Font', FONT)

    def _build(self):
        # 顶部标题条
        head = ttk.Frame(self.root, padding=(16, 12, 16, 6))
        head.pack(fill='x')
        ttk.Label(head, text='UEFI 开机动画向导', style='Title.TLabel').pack(side='left')
        self.var_admin = tk.StringVar()
        self.lbl_admin = ttk.Label(head, textvariable=self.var_admin, style='Ok.TLabel')
        self.lbl_admin.pack(side='right')
        ttk.Button(head, text='打开日志', command=self._open_logs).pack(side='right', padx=6)

        # 步骤指示
        self.stepbar = ttk.Frame(self.root, padding=(16, 0))
        self.stepbar.pack(fill='x')
        self.step_labels = []
        names = ['1 安全须知', '2 环境体检', '3 制作动画', '4 安装试用', '5 完成']
        for i, n in enumerate(names):
            lb = ttk.Label(self.stepbar, text=n, padding=(0, 4, 16, 4))
            lb.pack(side='left')
            self.step_labels.append(lb)

        ttk.Separator(self.root).pack(fill='x', pady=(4, 0))

        # 内容区（每个步骤一个 Frame，切换 pack/pack_forget）
        self.body = ttk.Frame(self.root, padding=16)
        self.body.pack(fill='both', expand=True)
        self.pages = []
        for builder in (self._page_welcome, self._page_check,
                        self._page_pack, self._page_install, self._page_done):
            fr = ttk.Frame(self.body)
            builder(fr)
            self.pages.append(fr)

        # 底部导航
        ttk.Separator(self.root).pack(fill='x')
        nav = ttk.Frame(self.root, padding=(16, 10))
        nav.pack(fill='x')
        self.btn_back = ttk.Button(nav, text='< 上一步', command=self._back)
        self.btn_back.pack(side='left')
        self.btn_next = ttk.Button(nav, text='下一步 >', style='Big.TButton',
                                   command=self._next)
        self.btn_next.pack(side='right')
        self.var_hint = tk.StringVar(value='')
        ttk.Label(nav, textvariable=self.var_hint, style='Dim.TLabel').pack(
            side='left', padx=12)

    # ------------------------------------------------------------------ #
    # 第 1 步：安全须知                                                   #
    # ------------------------------------------------------------------ #

    def _page_welcome(self, fr):
        ttk.Label(fr, text='先看这三条，能省掉 90% 的麻烦', style='H.TLabel').pack(anchor='w')
        text = (
            '本工具会做两件事：\n'
            '  ① 往你的 EFI 系统分区（ESP）里写 3 个文件：播放器、动画、配置；\n'
            '  ② 在你的固件启动项里新增一条"UEFI 动画启动项"，指向这个播放器。\n'
            '它不会格式化磁盘、不会动 Windows 自己的引导文件（\\EFI\\Microsoft）。\n'
        )
        ttk.Label(fr, text=text, justify='left').pack(anchor='w', pady=(8, 12))

        box = ttk.LabelFrame(fr, text=' 三条硬前提 ', padding=12)
        box.pack(fill='x')
        items = [
            ('1. 必须关闭 Secure Boot（安全启动）',
             '固件只加载微软签名的引导程序，我们的播放器没有签名。\n'
             '代价：关掉后 Windows 11 的部分功能（如某些反作弊）可能报错；'
             '装完想开回来，动画就看不到了。\n'
             '怎么关：重启 → 开机按 Del/F2/F12 进 BIOS → Security/Boot 里把 Secure Boot 设 Disabled。'),
            ('2. 如果开了 BitLocker，先准备好恢复密钥',
             '改启动顺序有一定概率触发 BitLocker 恢复界面。没有 48 位恢复密钥就只能重装。\n'
             '稳妥做法：先在"管理 BitLocker"里"暂停保护"，装完确认能开机再恢复。'),
            ('3. 万一开不了机，要会进 WinRE 撤销',
             '动画启动项失败时会自动回到 Windows（本工具默认"只试用一次"）。\n'
             '真出问题：连续强制关机 3 次进入恢复环境 → 疑难解答 → 命令提示符，执行\n'
             '    bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
             '第 5 步和日志目录里都有完整的回滚命令，可直接复制。'),
        ]
        for title, detail in items:
            ttk.Label(box, text=title, style='H.TLabel').pack(anchor='w', pady=(6, 2))
            ttk.Label(box, text=detail, justify='left', wraplength=860).pack(anchor='w')

        self.var_banner = tk.StringVar(value='')
        self.lbl_banner = ttk.Label(fr, textvariable=self.var_banner, style='Warn.TLabel',
                                    justify='left', wraplength=880)
        self.lbl_banner.pack(anchor='w', pady=(12, 0))

    # ------------------------------------------------------------------ #
    # 第 2 步：体检                                                       #
    # ------------------------------------------------------------------ #

    def _page_check(self, fr):
        top = ttk.Frame(fr)
        top.pack(fill='x')
        ttk.Label(top, text='环境体检（只读，不会修改任何东西）',
                  style='H.TLabel').pack(side='left')
        self.btn_recheck = ttk.Button(top, text='重新体检', command=self._do_check)
        self.btn_recheck.pack(side='right')

        cols = ('level', 'title', 'detail')
        self.tree = ttk.Treeview(fr, columns=cols, show='headings', height=10)
        for c, w, t in (('level', 60, '结果'), ('title', 300, '检查项'),
                        ('detail', 600, '说明')):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor='w', stretch=(c == 'detail'))
        self.tree.tag_configure('ok', foreground=C_OK)
        self.tree.tag_configure('warn', foreground=C_WARN)
        self.tree.tag_configure('error', foreground=C_FAIL)
        self.tree.pack(fill='both', expand=True, pady=(8, 8))

        self.txt_fix = tk.Text(fr, height=7, wrap='word', font=FONT,
                               relief='solid', borderwidth=1, background='#fffbe6')
        self.txt_fix.pack(fill='x')
        self.txt_fix.configure(state='disabled')

        self.var_check = tk.StringVar(value='点"重新体检"开始。')
        ttk.Label(fr, textvariable=self.var_check).pack(anchor='w', pady=(8, 0))

        if not sysinfo.is_admin():
            row = ttk.Frame(fr)
            row.pack(fill='x', pady=(6, 0))
            ttk.Label(row, text='需要管理员权限才能继续：',
                      style='Fail.TLabel').pack(side='left')
            ttk.Button(row, text='以管理员身份重启本程序',
                       command=self._relaunch_admin).pack(side='left', padx=8)

    def _relaunch_admin(self):
        if sysinfo.relaunch_as_admin(logger=self.log):
            self.root.after(300, self.root.destroy)
        else:
            messagebox.showerror('提示', '无法自动提权，请关闭本程序后右键 exe → 以管理员身份运行。')

    def _do_check(self):
        if self.busy:
            return
        self.busy = True
        self.btn_recheck.config(state='disabled')
        self.var_check.set('体检中…（读取固件启动项、磁盘与安全设置，只读不改）')
        self.tree.delete(*self.tree.get_children())

        def work():
            try:
                pf = deploy.preflight(logger=self.log.write)
                self.q.put(('check', pf, None))
            except Exception:
                self.q.put(('check_err', traceback.format_exc(), None))
        threading.Thread(target=work, daemon=True).start()

    def _render_check(self, pf):
        self.pf = pf
        for c in pf.checks:
            tag = {'ok': 'ok', 'warn': 'warn', 'error': 'error'}.get(c.level, '')
            mark = {'ok': '通过', 'warn': '注意', 'error': '阻塞'}.get(c.level, c.level)
            self.tree.insert('', 'end', values=(mark, c.title, c.detail), tags=(tag,))
        fixes = [c for c in pf.checks if c.fix]
        self.txt_fix.configure(state='normal')
        self.txt_fix.delete('1.0', 'end')
        if fixes:
            for c in fixes:
                self.txt_fix.insert('end', '【%s】\n' % c.title)
                for ln in c.fix.splitlines():
                    self.txt_fix.insert('end', '    ' + ln + '\n')
        else:
            self.txt_fix.insert('end', '没有需要处理的阻塞项。')
        self.txt_fix.configure(state='disabled')
        if pf.ok:
            self.var_check.set('体检通过：%d 项通过，%d 条提醒。可以进入下一步。'
                               % (len(pf.checks) - len(pf.warnings), len(pf.warnings)))
        else:
            self.var_check.set('有 %d 项必须先处理，处理完点"重新体检"。'
                               % len(pf.errors))
        self._refresh_admin_badge()
        self._refresh_buttons()

    # ------------------------------------------------------------------ #
    # 第 3 步：制作动画                                                   #
    # ------------------------------------------------------------------ #

    def _page_pack(self, fr):
        ttk.Label(fr, text='制作动画（素材 → anim.bin）', style='H.TLabel').pack(anchor='w')
        ttk.Label(fr, text='支持：图片文件夹（PNG/BMP/JPG）、GIF/APNG 动图、视频（需 ffmpeg）、'
                           '内置演示动画。动画会被等比缩放并补黑边，不会变形。',
                  style='Dim.TLabel', wraplength=880, justify='left').pack(anchor='w', pady=(4, 10))

        grid = ttk.Frame(fr)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text='素材来源').grid(row=0, column=0, sticky='w', pady=4)
        self.var_src = tk.StringVar()
        ttk.Entry(grid, textvariable=self.var_src).grid(row=0, column=1, sticky='we', padx=8)
        btns = ttk.Frame(grid)
        btns.grid(row=0, column=2, sticky='e')
        ttk.Button(btns, text='选文件夹…', command=self._pick_dir).pack(side='left')
        ttk.Button(btns, text='选文件…', command=self._pick_file).pack(side='left', padx=4)
        ttk.Button(btns, text='用演示动画', command=self._pick_demo).pack(side='left')

        ttk.Label(grid, text='输出目录').grid(row=1, column=0, sticky='w', pady=4)
        self.var_out = tk.StringVar(value=os.path.join(paths.data_dir(), 'anim'))
        ttk.Entry(grid, textvariable=self.var_out).grid(row=1, column=1, sticky='we', padx=8)
        ttk.Button(grid, text='浏览…', command=self._pick_out).grid(row=1, column=2, sticky='e')

        opt = ttk.Frame(fr)
        opt.pack(fill='x', pady=(10, 0))
        ttk.Label(opt, text='分辨率').pack(side='left')
        self.var_res = tk.StringVar(value=RES_CHOICES[0][0])
        ttk.Combobox(opt, textvariable=self.var_res, state='readonly', width=26,
                     values=[n for n, _ in RES_CHOICES]).pack(side='left', padx=(6, 18))
        ttk.Label(opt, text='帧率').pack(side='left')
        self.var_fps = tk.StringVar(value='30')
        ttk.Combobox(opt, textvariable=self.var_fps, state='readonly', width=6,
                     values=['12', '15', '24', '30', '60', '120']).pack(side='left', padx=(6, 18))
        ttk.Label(opt, text='格式').pack(side='left')
        self.var_fmt = tk.StringVar(value='QOI（推荐，体积小）')
        ttk.Combobox(opt, textvariable=self.var_fmt, state='readonly', width=22,
                     values=['QOI（推荐，体积小）', 'BMP（无损，仅限小动画）']
                     ).pack(side='left', padx=6)

        self.btn_pack = ttk.Button(fr, text='开始制作', style='Big.TButton',
                                   command=self._do_pack)
        self.btn_pack.pack(anchor='w', pady=(14, 6))
        self.pb = ttk.Progressbar(fr, mode='determinate')
        self.pb.pack(fill='x')
        self.var_pack = tk.StringVar(value='还没有制作。选好素材后点"开始制作"。')
        ttk.Label(fr, textvariable=self.var_pack, justify='left',
                  wraplength=880).pack(anchor='w', pady=(8, 0))

        self.preview_frame = ttk.LabelFrame(fr, text=' 预览（首帧） ', padding=8)
        self.preview_frame.pack(fill='both', expand=True, pady=(10, 0))
        self.lbl_preview = ttk.Label(self.preview_frame, text='（制作完成后这里显示首帧缩略图）',
                                     style='Dim.TLabel')
        self.lbl_preview.pack()

    def _pick_dir(self):
        d = filedialog.askdirectory(title='选择帧图片文件夹')
        if d:
            self.var_src.set(os.path.normpath(d))

    def _pick_file(self):
        f = filedialog.askopenfilename(
            title='选择素材文件',
            filetypes=[('图片/动图/视频',
                        '*.gif *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff '
                        '*.mp4 *.mov *.mkv *.webm *.avi'),
                       ('所有文件', '*.*')])
        if f:
            self.var_src.set(os.path.normpath(f))

    def _pick_out(self):
        d = filedialog.askdirectory(title='选择输出目录')
        if d:
            self.var_out.set(os.path.normpath(d))

    def _pick_demo(self):
        if self.busy:
            return
        dest = os.path.join(paths.data_dir(), 'demo_frames')
        self.var_src.set(dest)
        self.var_pack.set('正在生成演示动画帧…')

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
            messagebox.showwarning('缺少素材', '请先选择素材（文件夹/文件），或点"用演示动画"。')
            return
        out = self.var_out.get().strip() or os.path.join(paths.data_dir(), 'anim')
        res = dict(RES_CHOICES)[self.var_res.get()]
        fps = int(self.var_fps.get())
        fmt = 'BMP' if self.var_fmt.get().startswith('BMP') else 'QOI'
        budget = None
        if self.pf and self.pf.free_mb:
            budget = max(1.0, self.pf.free_mb - 8.0)
        self.busy = True
        self.btn_pack.config(state='disabled', text='制作中…')
        self.pb.config(value=0, maximum=1)
        self.var_pack.set('开始制作…')

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
        self.var_pack.set(
            '完成：%d 帧，%dx%d @%dfps，%s，%.2f MB，用时 %.1f 秒。\n保存在：%s'
            % (r['frames'], r['width'], r['height'], r['fps'], r['fmt'],
               r['size_mb'], r['elapsed'], r['anim']))
        if r.get('warn'):
            self.var_pack.set(self.var_pack.get() + '\n注意：' + r['warn'])
        self._show_preview(r['anim'])

    def _show_preview(self, anim_path):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return
        try:
            im = pack.read_frame(anim_path, 0)
            im.thumbnail((640, 360))
            self.photo = ImageTk.PhotoImage(im)
            self.lbl_preview.configure(image=self.photo, text='')
        except Exception as e:
            self.log('[gui] 预览失败：%s' % e)
            self.lbl_preview.configure(text='（预览生成失败，不影响安装）')

    # ------------------------------------------------------------------ #
    # 第 4 步：安装（试用）                                               #
    # ------------------------------------------------------------------ #

    def _page_install(self, fr):
        ttk.Label(fr, text='安装（默认"只试用一次"）', style='H.TLabel').pack(anchor='w')
        ttk.Label(fr, text='安装会做这些事（每一步都有备份，可一键回滚）：',
                  justify='left').pack(anchor='w', pady=(6, 4))
        plan = ttk.LabelFrame(fr, text=' 将要发生的变化 ', padding=10)
        plan.pack(fill='x')
        for line in (
            '1. 备份 ESP 上已有的 \\EFI\\BootAnim 文件，并导出 BCD 到本地备份目录；',
            '2. 写入 3 个文件到 ESP：BootAnimation.efi、anim.bin、config.ini；',
            '3. 在固件启动项里新增一条（或更新已有）"UEFI Animation Agent"条目；',
            '4. 设置"只下一次启动生效"——重启一次看效果，失败会自动回到 Windows。',
        ):
            ttk.Label(plan, text=line, justify='left', wraplength=860).pack(anchor='w', pady=1)

        self.var_plan = tk.StringVar(value='（先完成第 3 步制作动画）')
        ttk.Label(fr, textvariable=self.var_plan, justify='left', wraplength=880,
                  style='Dim.TLabel').pack(anchor='w', pady=(10, 6))

        self.var_agree = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text='我已阅读安全须知，理解需要关闭 Secure Boot，并已完成备份/暂停 BitLocker',
                        variable=self.var_agree, command=self._refresh_buttons).pack(anchor='w')

        row = ttk.Frame(fr)
        row.pack(fill='x', pady=(12, 6))
        self.btn_install = ttk.Button(row, text='安装并试用一次', style='Big.TButton',
                                      command=self._do_install)
        self.btn_install.pack(side='left')
        ttk.Button(row, text='以演示动画安装（不选素材）',
                   command=self._install_demo).pack(side='left', padx=8)

        self.txt_log = tk.Text(fr, height=10, wrap='none', font=('Consolas', 9),
                               relief='solid', borderwidth=1)
        self.txt_log.pack(fill='both', expand=True, pady=(8, 0))
        sb = ttk.Scrollbar(self.txt_log, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')

    def _install_demo(self):
        """没有素材也能体验：生成 720p 演示动画并直接安装。"""
        if self.busy:
            return
        self.busy = True
        self.btn_install.config(state='disabled')
        self.var_plan.set('正在生成演示动画…')

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
            messagebox.showwarning('请先确认', '请先勾选"我已阅读安全须知…"。')
            return
        if not self.anim_dir:
            messagebox.showwarning('没有动画', '请先在第 3 步制作动画，或点"以演示动画安装"。')
            return
        if not self.pf or not self.pf.ok:
            messagebox.showwarning('体检未通过', '请先在"环境体检"里让所有阻塞项变绿。')
            return
        if not messagebox.askyesno(
                '确认安装',
                '即将写入 EFI 分区并新增启动项。\n\n'
                'ESP：%s\n动画目录：%s\n\n'
                '现在重启一次就会看到动画；如果看不到，固件会自动回到 Windows。\n\n继续？'
                % (self.pf.esp.describe(), self.anim_dir)):
            return
        self.busy = True
        self.btn_install.config(state='disabled', text='安装中…')
        self.var_plan.set('安装中，请稍候…')

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
        self.var_plan.set('安装完成！启动项 GUID：%s\n备份目录：%s\n\n'
                          '现在请重启电脑：应当先看到动画，然后自动进入 Windows。\n'
                          '看到动画后回到本程序，到第 5 步点"设为默认（固化）"。'
                          % (r.guid, r.backup_dir))
        self.step = 4
        self._show(4)
        self._refresh_done_page()

    # ------------------------------------------------------------------ #
    # 第 5 步：固化 / 回滚                                                #
    # ------------------------------------------------------------------ #

    def _page_done(self, fr):
        ttk.Label(fr, text='固化或回滚', style='H.TLabel').pack(anchor='w')
        self.var_state = tk.StringVar(value='正在读取状态…')
        ttk.Label(fr, textvariable=self.var_state, justify='left', wraplength=880
                  ).pack(anchor='w', pady=(8, 10))

        row = ttk.Frame(fr)
        row.pack(fill='x')
        ttk.Button(row, text='设为默认（每次开机都播放）', style='Big.TButton',
                   command=self._do_activate).pack(side='left')
        ttk.Button(row, text='回滚（恢复原开机流程）',
                   command=self._do_rollback).pack(side='left', padx=10)
        ttk.Button(row, text='刷新状态', command=self._refresh_done_page).pack(side='left')

        box = ttk.LabelFrame(fr, text=' 下次开机看不到动画？', padding=10)
        box.pack(fill='x', pady=(14, 0))
        ttk.Label(box, justify='left', wraplength=860, text=(
            '1) 先点上方"刷新状态"，再点"回滚"，一切都会回到装之前；\n'
            '2) 仍进不了系统：连续强制关机 3 次 → 疑难解答 → 命令提示符，执行\n'
            '     bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
            '   （把第一条启动项换回 Windows 引导器）\n'
            '3) 详细命令和备份位置见日志目录里的 rollback 说明，或点底部"打开日志"。'
        )).pack(anchor='w')

        self.txt_state = tk.Text(fr, height=8, wrap='word', font=('Consolas', 9),
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
                msg = '当前状态：已安装并设为默认（%s）—— 每次开机都会播放动画。' % st.get('guid')
            elif st.get('pending'):
                msg = '当前状态：已安装，仅"下一次启动"生效（%s）—— 重启看效果。' % st.get('guid')
            else:
                msg = '当前状态：已安装但未激活（%s）—— 需要设为默认或重新试用。' % st.get('guid')
        elif st.get('installed') is None:
            msg = '无法读取启动项状态：%s' % st.get('error', '')
        else:
            msg = '当前状态：未安装（开机直接进 Windows）。'
        self.var_state.set(msg)
        lines = []
        if st.get('esp'):
            lines.append('ESP：%s' % st['esp'])
            for k, v in (st.get('esp_files') or {}).items():
                lines.append('  %-8s %s' % (k, '存在' if v else '缺失'))
            if st.get('anim_mb'):
                lines.append('  anim.bin %.2f MB' % st['anim_mb'])
        elif st.get('esp_error'):
            lines.append('ESP：%s' % st['esp_error'])
        if st.get('displayorder'):
            lines.append('固件启动顺序：')
            for i, g in enumerate(st['displayorder']):
                lines.append('  %d. %s' % (i + 1, g))
        self.txt_state.configure(state='normal')
        self.txt_state.delete('1.0', 'end')
        self.txt_state.insert('end', '\n'.join(lines))
        self.txt_state.configure(state='disabled')

    def _do_activate(self):
        guid = (self.install_result.guid if self.install_result else '')
        if not messagebox.askyesno(
                '确认固化',
                '固化后每次开机都会先播放动画（约几秒）再进 Windows。\n\n'
                '如果动画有问题，随时可以用"回滚"恢复。继续？'):
            return
        try:
            g = deploy.activate(guid, logger=self.log.write)
            messagebox.showinfo('已固化', '已设为默认启动项：%s\n以后每次开机都会播放动画。' % g)
        except Exception as e:
            messagebox.showerror('固化失败', str(e))
        self._refresh_done_page()

    def _do_rollback(self):
        if not messagebox.askyesno(
                '确认回滚',
                '将删除动画启动项、恢复原启动顺序，并删除 ESP 上的动画文件。\n'
                '（如果之前备份过旧动画，会先还原备份。）\n\n继续？'):
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
            messagebox.showinfo('已回滚', '回滚完成：\n' + ('\n'.join(done) or '无变化')
                                + '\n\n重启后应当直接进入 Windows。')
        except Exception as ex:
            messagebox.showerror('回滚失败', str(ex))
        self._refresh_done_page()

    # ------------------------------------------------------------------ #
    # 导航与事件循环                                                      #
    # ------------------------------------------------------------------ #

    def _show(self, i):
        self.step = i
        for k, fr in enumerate(self.pages):
            if k == i:
                fr.pack(fill='both', expand=True)
            else:
                fr.pack_forget()
        for k, lb in enumerate(self.step_labels):
            lb.configure(style='H.TLabel' if k == i else 'Dim.TLabel')
        self.btn_back.config(state='normal' if i > 0 else 'disabled')
        self.btn_next.config(text='下一步 >' if i < 4 else '完成')
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
                hint = '正在体检…'
            elif not self.pf.ok:
                can_next = False
                hint = '先处理体检里的阻塞项'
        elif self.step == 2:
            if not self.anim_dir:
                can_next = False
                hint = '先制作动画（或选"以演示动画安装"）'
        elif self.step == 3:
            can_next = False
            hint = '点"安装并试用一次"完成后会自动进入下一步'
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
            self.var_pack.set('制作中：%d/%d 帧' % (i, n))
        elif kind == 'packed':
            self.busy = False
            self.btn_pack.config(state='normal', text='开始制作')
            self._render_pack(payload)
        elif kind == 'packed_demo':
            self.busy = False
            self.btn_install.config(state='normal', text='安装并试用一次')
            self.var_plan.set('演示动画已生成（%.2f MB）。点"安装并试用一次"继续。'
                              % payload['size_mb'])
            self.anim_dir = os.path.dirname(payload['anim'])
            self._refresh_buttons()
        elif kind == 'installed':
            self.busy = False
            self.btn_install.config(state='normal', text='安装并试用一次')
            self._render_install(payload)
        elif kind == 'status':
            self._render_status(payload)
        elif kind == 'demo':
            self.var_pack.set('演示帧已生成：%s\n现在可以点"开始制作"。' % payload)
        for err_kind, title in (('pack_user_err', '制作失败'),
                                ('pack_err', '制作异常'),
                                ('install_user_err', '安装失败'),
                                ('install_err', '安装异常'),
                                ('check_err', '体检异常'),
                                ('status_err', '状态读取异常')):
            if kind == err_kind:
                self.busy = False
                self.btn_pack.config(state='normal', text='开始制作')
                self.btn_install.config(state='normal', text='安装并试用一次')
                self.btn_recheck.config(state='normal')
                tail = '\n'.join(str(payload).strip().splitlines()[-6:])
                self.log('[error] %s' % tail)
                messagebox.showerror(title, tail)
                self.var_pack.set(tail.splitlines()[-1] if tail else '失败')

    def _log_append(self, line):
        # Tk 不是线程安全的：工作线程只能入队，由主线程 _log_show 真正写控件。
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
            self.var_admin.set('管理员权限 ✓')
            self.lbl_admin.configure(style='Ok.TLabel')
        else:
            self.var_admin.set('非管理员（无法安装）')
            self.lbl_admin.configure(style='Fail.TLabel')

    def _startup_state(self):
        self._refresh_admin_badge()
        try:
            st = deploy.status(logger=None)
        except Exception:
            st = {}
        if st.get('installed'):
            if st.get('default'):
                self.var_banner.set('检测到：已安装并设为默认（%s）。想撤掉请到第 5 步"回滚"。'
                                    % st.get('guid'))
            elif st.get('pending'):
                self.var_banner.set('检测到：上次设置了"试用一次"（%s）。重启看效果，'
                                    '或到第 5 步回滚。' % st.get('guid'))
            else:
                self.var_banner.set('检测到：已安装但未激活（%s）。到第 5 步可固化或回滚。'
                                    % st.get('guid'))

    def _on_close(self):
        try:
            self.log.close()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()
        return 0


def run():
    return Gui().run()


def smoke():
    """无交互冒烟：建窗口、渲染各页、销毁。返回 0/1。"""
    g = Gui()
    try:
        for i in range(5):
            g._show(i)
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

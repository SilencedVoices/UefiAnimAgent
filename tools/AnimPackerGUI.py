#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnimPackerGUI.py — UEFI 开机动画可视化打包工具（P7 / 设计 §2.2 §2.3 §2.4）

界面: tkinter（Python 自带）；图像: Pillow（缺失时自动 pip 安装）。
核心: 复用同目录 make_anim.py 的 UAN1 容器常量与自然排序工具，
      帧序列（PNG/BMP）→ 目标分辨率等比缩放（居中黑边）→ 编码 → UAN1。

用法:
  双击运行 或  python AnimPackerGUI.py
  无头自检:    python -c "import AnimPackerGUI as g; g.self_test()"
"""
import io
import os
import queue
import subprocess
import sys
import threading
import time
import traceback

# 复用同目录 make_anim.py（UAN1 容器格式权威实现）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_anim import (  # noqa: E402
    MAGIC, VERSION, FORMAT_QOI, FORMAT_BMP, INDEX_OFFSET,
    natural_key, REAL_ESP_BUDGET_MB,
)

RES_PRESETS = [
    ('auto（跟随首帧）', None),
    ('1080p（1920x1080）', (1920, 1080)),
    ('2K（2560x1440）', (2560, 1440)),
    ('4K（3840x2160）', (3840, 2160)),
]
FPS_PRESETS = [24, 30, 60, 120]
FMT_PRESETS = [
    ('QOI（推荐 · 体积小）', 'QOI'),
    ('BMP RAW24（无损 · ≤72帧且≤1080p）', 'BMP'),
]
FRAME_EXTS = ('.png', '.bmp')


class PackerError(Exception):
    """面向用户的可预期错误（空目录/坏图/阈值/预算/写盘失败）。"""


# ------------------------------------------------------------------ #
# Pillow 保障（缺失自动安装，P7 任务要求）                            #
# ------------------------------------------------------------------ #

def ensure_pillow(log=lambda s: None):
    """确保 Pillow 可导入；缺失时 pip 安装并重试。失败抛 PackerError。"""
    global Image
    try:
        from PIL import Image as _Img      # noqa
        Image = _Img
        return
    except ImportError:
        pass
    log('Pillow 缺失，正在自动安装（pip install pillow）…')
    try:
        r = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'pillow'],
            capture_output=True, text=True, timeout=600)
    except Exception as e:                                    # 网络/pip 异常
        raise PackerError('Pillow 自动安装失败：%s\n'
                          '请手动执行: python -m pip install pillow' % e)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or '').strip().splitlines()[-5:]
        raise PackerError('pip install pillow 失败：\n' + '\n'.join(tail))
    try:
        from PIL import Image as _Img        # noqa
        Image = _Img
    except ImportError:
        raise PackerError('Pillow 安装后仍无法导入，请手动安装后重试')
    log('Pillow 安装完成')


Image = None


# ------------------------------------------------------------------ #
# 核心打包（GUI 与无头自检共用）                                      #
# ------------------------------------------------------------------ #

def _resample():
    """兼容 Pillow 各版本的高质量重采样常量。"""
    return getattr(Image, 'Resampling', Image).LANCZOS


def fit_letterbox(im, tw, th):
    """等比缩放至 (tw,th)，不足处居中补黑边（P7 任务要求）。"""
    im = im.convert('RGB')
    if im.size == (tw, th):
        return im
    scale = min(tw / im.width, th / im.height)
    nw = max(1, min(tw, round(im.width * scale)))
    nh = max(1, min(th, round(im.height * scale)))
    im = im.resize((nw, nh), _resample())
    canvas = Image.new('RGB', (tw, th), (0, 0, 0))
    canvas.paste(im, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def pil_to_qoi(im):
    """RGB 图 → 完整 QOI 图像字节串（make_anim encode_qoi 同源）。"""
    buf = io.BytesIO()
    im.save(buf, format='QOI')
    return buf.getvalue()


def pil_to_bgr24(im):
    """RGB 图 → top-down BGR24 行数据（与 make_anim read_bmp_topdown_bgr 同构）。"""
    rgb = im.convert('RGB').tobytes()
    bgr = bytearray(len(rgb))
    bgr[0::3] = rgb[2::3]      # B
    bgr[1::3] = rgb[1::3]      # G
    bgr[2::3] = rgb[0::3]      # R
    return bytes(bgr)


def pack_anim(input_dir, output, target_wh, fps, fmt,
              allow_overbudget=False,
              progress=lambda i, n: None, log=lambda s: None):
    """帧目录 → UAN1 anim.bin。返回结果 dict；可预期失败抛 PackerError。

    target_wh: None=auto(取首帧尺寸) 或 (w, h)。
    fmt: 'QOI' | 'BMP'（BMP 遵守 §2.2 阈值：≤72 帧且 ≤1080p）。
    """
    t0 = time.time()
    if not os.path.isdir(input_dir):
        raise PackerError('输入目录不存在：%s' % input_dir)
    files = sorted((f for f in os.listdir(input_dir)
                    if f.lower().endswith(FRAME_EXTS)), key=natural_key)
    if not files:
        raise PackerError('目录中没有 PNG/BMP 帧文件（空目录）：%s' % input_dir)
    n = len(files)
    ensure_pillow(log)

    fmt_name = fmt.upper()
    if fmt_name not in ('QOI', 'BMP'):
        raise PackerError('未知编码格式：%s' % fmt)
    fmt_id = FORMAT_QOI if fmt_name == 'QOI' else FORMAT_BMP

    # ---- 打开首帧，确定统一目标尺寸 ----
    try:
        first = Image.open(os.path.join(input_dir, files[0]))
        first.load()
    except Exception as e:
        raise PackerError('首帧无法解码（文件损坏？）：%s\n%s' % (files[0], e))
    tw, th = target_wh if target_wh else first.size

    # ---- §2.2 阈值：BMP 仅限 ≤72 帧且 ≤1080p ----
    if fmt_id == FORMAT_BMP and (n > 72 or th > 1080 or tw > 1920):
        raise PackerError(
            'BMP RAW24 拒绝打包：%d 帧 %dx%d 超出阈值（≤72 帧且 ≤1080p，设计 §2.2）。\n'
            '请改用 QOI（体积也更小）。' % (n, tw, th))

    log('[PACK] %d 帧 → %dx%d, 格式=%s, 帧率=%dfps' % (n, tw, th, fmt_name, fps))

    frames = []
    for i, name in enumerate(files):
        path = os.path.join(input_dir, name)
        try:
            im = Image.open(path)
            im.load()
        except Exception as e:
            raise PackerError('第 %d/%d 帧无法解码（文件损坏？）：%s\n%s'
                              % (i + 1, n, name, e))
        im = fit_letterbox(im, tw, th)     # 统一尺寸（含黑边归一化）
        blob = pil_to_qoi(im) if fmt_id == FORMAT_QOI else pil_to_bgr24(im)
        frames.append(blob)
        progress(i + 1, n)

    # ---- 组容器（UAN1，§2.3）：header + index + frames ----
    index = bytearray()
    offset = INDEX_OFFSET + 8 * n
    for blob in frames:
        index += struct_pack('<II', offset, len(blob))
        offset += len(blob)
    header = struct_pack('<4sHHHHIII', MAGIC, VERSION, fmt_id, n, fps,
                         tw, th, INDEX_OFFSET)
    out = header + bytes(index) + b''.join(frames)
    try:
        with open(output, 'wb') as f:
            f.write(out)
    except OSError as e:
        raise PackerError('写出失败：%s\n%s' % (output, e))
    size_mb = len(out) / 1048576.0

    # ---- 预算校验（§2.4 / R2，默认真机 ESP 预算）----
    budget = REAL_ESP_BUDGET_MB
    over = size_mb > budget
    warn = None
    if over and not allow_overbudget:
        try:
            os.remove(output)
        except OSError:
            pass
        raise PackerError('体积 %.1f MB 超出真机 ESP 预算 %.0f MB（§2.4/R2），'
                          '已删除输出。\n虚拟 ESP 场景可勾选"允许超预算"重试。'
                          % (size_mb, budget))
    if over:
        warn = '超预算 %.1f MB > %.0f MB（已按勾选放行，真机部署前请降档）' % (
            size_mb, budget)

    # ---- 头回读自检（与 make_anim 同源校验）----
    with open(output, 'rb') as f:
        back = f.read(24)
    m, ver, fm, fc, fh, w, h, idx = struct_unpack('<4sHHHHIII', back)
    ok = (m == MAGIC and ver == VERSION and fm == fmt_id and fc == n
          and fh == fps and (w, h) == (tw, th) and idx == INDEX_OFFSET)
    if not ok:
        raise PackerError('容器头回读校验失败（内部错误，请反馈）')
    log('[VERIFY] header=UAN1 ver=%d fmt=%d frames=%d %dx%d fps=%d -> PASS'
        % (ver, fmt_id, n, w, h, fps))

    return dict(output=output, size_bytes=len(out), size_mb=size_mb,
                frames=n, w=tw, h=th, fps=fps, fmt=fmt_name,
                verify='PASS', elapsed=time.time() - t0, warn=warn)


def struct_pack(fmt, *a):
    import struct
    return struct.pack(fmt, *a)


def struct_unpack(fmt, b):
    import struct
    return struct.unpack(fmt, b)


# ------------------------------------------------------------------ #
# tkinter 界面                                                        #
# ------------------------------------------------------------------ #

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:                                    # HiDPI 清晰文本（失败无碍）
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('UEFI 开机动画打包工具（UAN1）')
            self.minsize(560, 0)
            self.q = queue.Queue()
            self.running = False

            pad = dict(padx=8, pady=4)
            frm = ttk.Frame(self, padding=12)
            frm.pack(fill='both', expand=True)
            frm.columnconfigure(1, weight=1)

            # ---- 输入帧目录 ----
            ttk.Label(frm, text='帧图片目录').grid(row=0, column=0, **pad)
            self.var_in = tk.StringVar()
            ttk.Entry(frm, textvariable=self.var_in).grid(
                row=0, column=1, sticky='we', **pad)
            ttk.Button(frm, text='浏览…', command=self.pick_dir).grid(
                row=0, column=2, **pad)

            # ---- 输出文件 ----
            ttk.Label(frm, text='输出文件').grid(row=1, column=0, **pad)
            self.var_out = tk.StringVar()
            ttk.Entry(frm, textvariable=self.var_out).grid(
                row=1, column=1, sticky='we', **pad)
            ttk.Button(frm, text='浏览…', command=self.pick_out).grid(
                row=1, column=2, **pad)

            # ---- 分辨率 + 帧率 ----
            ttk.Label(frm, text='目标分辨率').grid(row=2, column=0, **pad)
            self.var_res = tk.StringVar(value=RES_PRESETS[0][0])
            cb = ttk.Combobox(frm, textvariable=self.var_res, state='readonly',
                              values=[n for n, _ in RES_PRESETS], width=22)
            cb.grid(row=2, column=1, sticky='w', **pad)
            ttk.Label(frm, text='帧率').grid(row=2, column=2, **pad)
            self.var_fps = tk.StringVar(value='24')
            fcb = ttk.Combobox(frm, textvariable=self.var_fps, state='readonly',
                               values=[str(f) for f in FPS_PRESETS], width=6)
            fcb.grid(row=2, column=3, **pad)

            # ---- 编码格式 + 超预算 ----
            ttk.Label(frm, text='编码格式').grid(row=3, column=0, **pad)
            self.var_fmt = tk.StringVar(value=FMT_PRESETS[0][0])
            f2 = ttk.Combobox(frm, textvariable=self.var_fmt, state='readonly',
                              values=[n for n, _ in FMT_PRESETS], width=28)
            f2.grid(row=3, column=1, sticky='w', **pad)
            self.var_over = tk.BooleanVar(value=False)
            ttk.Checkbutton(frm, text='允许超预算（虚拟 ESP）',
                            variable=self.var_over).grid(
                row=3, column=2, columnspan=2, **pad)

            # ---- 打包按钮 + 进度 ----
            self.btn = ttk.Button(frm, text='一键打包', command=self.start)
            self.btn.grid(row=4, column=0, columnspan=4, sticky='we', **pad)
            self.pb = ttk.Progressbar(frm, mode='determinate')
            self.pb.grid(row=5, column=0, columnspan=4,
                         sticky='we', padx=8, pady=(2, 4))
            self.var_status = tk.StringVar(value='就绪。选择帧目录后点击"一键打包"。')
            ttk.Label(frm, textvariable=self.var_status,
                      wraplength=520, justify='left').grid(
                row=6, column=0, columnspan=4, sticky='w', **pad)
            self.after(100, self.poll)

        # ---- 交互 ----
        def pick_dir(self):
            d = filedialog.askdirectory(title='选择帧图片目录（PNG/BMP）')
            if d:
                self.var_in.set(os.path.normpath(d))
                if not self.var_out.get():
                    self.var_out.set(os.path.join(d, 'anim.bin'))

        def pick_out(self):
            f = filedialog.asksaveasfilename(
                title='输出 anim.bin', defaultextension='.bin',
                initialfile='anim.bin', filetypes=[('UAN1 动画包', '*.bin')])
            if f:
                self.var_out.set(os.path.normpath(f))

        def start(self):
            if self.running:
                return
            in_dir = self.var_in.get().strip()
            out = self.var_out.get().strip()
            if not in_dir:
                messagebox.showerror('缺少输入', '请先选择帧图片目录。')
                return
            if not out:
                messagebox.showerror('缺少输出', '请填写输出文件路径。')
                return
            if not out.lower().endswith('.bin'):
                out = os.path.splitext(out)[0] + '.bin'
                self.var_out.set(out)
            target = dict(RES_PRESETS)[self.var_res.get()]
            fps = int(self.var_fps.get())
            fmt = dict(FMT_PRESETS)[self.var_fmt.get()]
            allow_over = self.var_over.get()
            self.running = True
            self.btn.config(state='disabled', text='打包中…')
            self.pb.config(value=0, maximum=1)
            self.var_status.set('启动…（首次运行会自动安装 Pillow）')
            t = threading.Thread(target=self.worker, daemon=True,
                                 args=(in_dir, out, target, fps, fmt, allow_over))
            t.start()

        def worker(self, in_dir, out, target, fps, fmt, allow_over):
            lines = []

            def log(s):
                lines.append(s)

            def prog(i, n):
                self.q.put(('prog', i, n))
                if i % 10 == 0 or i == n:
                    log('  帧 %d/%d' % (i, n))

            try:
                r = pack_anim(in_dir, out, target, fps, fmt,
                              allow_overbudget=allow_over,
                              progress=prog, log=log)
                self.q.put(('ok', r, '\n'.join(lines)))
            except PackerError as e:
                self.q.put(('err', str(e), '\n'.join(lines)))
            except Exception:
                tb = traceback.format_exc().strip().splitlines()
                self.q.put(('err', '发生未预期异常（最近 3 行）：\n'
                            + '\n'.join(tb[-3:]), '\n'.join(lines)))

        def poll(self):
            try:
                while True:
                    ev = self.q.get_nowait()
                    if ev[0] == 'prog':
                        self.pb.config(maximum=ev[2], value=ev[1])
                        self.var_status.set('打包进度：%d/%d 帧' % (ev[1], ev[2]))
                    elif ev[0] == 'ok':
                        self._done(ev)
                    elif ev[0] == 'err':
                        self._fail(ev)
            except queue.Empty:
                pass
            self.after(100, self.poll)

        def _common(self):
            self.running = False
            self.btn.config(state='normal', text='一键打包')

        def _done(self, ev):
            self._common()
            _, r, logtxt = ev
            self.var_status.set('完成：' + os.path.basename(r['output']))
            msg = ('打包完成！\n\n'
                   '文件：%s\n大小：%.1f MB（%d 字节）\n帧数：%d\n'
                   '分辨率：%dx%d\n帧率：%d fps（%s）\n编码：%s\n'
                   '容器校验：UAN1 头 PASS\n耗时：%.1f 秒'
                   % (r['output'], r['size_mb'], r['size_bytes'], r['frames'],
                      r['w'], r['h'], r['fps'],
                      '帧预算 %.0f µs' % (1000000.0 / r['fps']),
                      r['fmt'], r['elapsed']))
            if r.get('warn'):
                msg += '\n\n⚠ %s' % r['warn']
            if logtxt:
                print(logtxt)
            messagebox.showinfo('打包完成', msg)

        def _fail(self, ev):
            self._common()
            _, err, logtxt = ev
            self.var_status.set('失败：' + err.splitlines()[0])
            if logtxt:
                print(logtxt)
            messagebox.showerror('打包失败', err)

    App().mainloop()


# ------------------------------------------------------------------ #
# 无头自检（无 GUI）：生成测试帧 → 打包 → 校验                        #
# ------------------------------------------------------------------ #

def self_test(out_dir=None):
    import shutil
    import struct
    import tempfile

    out_dir = out_dir or tempfile.mkdtemp(prefix='animpacker_')
    frames_dir = os.path.join(out_dir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)
    ensure_pillow()

    # 5 帧 2K 主尺寸 + 2 帧 720p（触发黑边归一化路径）
    from PIL import Image as P
    for i in range(5):
        P.new('RGB', (2560, 1440), (i * 40, 80, 160)).save(
            os.path.join(frames_dir, 'frame_%03d.png' % i))
    for i in range(5, 7):
        P.new('RGB', (1280, 720), (200, i * 30, 60)).save(
            os.path.join(frames_dir, 'frame_%03d.png' % i))

    results = {}

    # 1) 正常打包：2K + QOI + 120fps（P7 新档位）
    out1 = os.path.join(out_dir, 'anim_qoi.bin')
    r1 = pack_anim(frames_dir, out1, (2560, 1440), 120, 'QOI',
                   log=lambda s: None)
    with open(out1, 'rb') as f:
        hdr = f.read(24)
    m, ver, fmt, n, fps, w, h, idx = struct.unpack('<4sHHHHIII', hdr)
    results['qoi_pack'] = (m == b'UAN1' and n == 7 and fps == 120
                           and (w, h) == (2560, 1440) and idx == 0x18)

    # 2) BMP 阈值拒绝：2K → 应抛 PackerError
    try:
        pack_anim(frames_dir, os.path.join(out_dir, 'anim_bmp.bin'),
                  (2560, 1440), 24, 'BMP', log=lambda s: None)
        results['bmp_refused'] = False
    except PackerError:
        results['bmp_refused'] = True

    # 3) 空目录拒绝
    empty = os.path.join(out_dir, 'empty')
    os.makedirs(empty, exist_ok=True)
    try:
        pack_anim(empty, os.path.join(out_dir, 'x.bin'), None, 24, 'QOI',
                  log=lambda s: None)
        results['empty_refused'] = False
    except PackerError:
        results['empty_refused'] = True

    # 4) 坏图拒绝
    bad = os.path.join(out_dir, 'bad')
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, 'frame_001.png'), 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\nGARBAGE')
    try:
        pack_anim(bad, os.path.join(out_dir, 'x.bin'), None, 24, 'QOI',
                  log=lambda s: None)
        results['bad_refused'] = False
    except PackerError:
        results['bad_refused'] = True

    ok = all(results.values())
    for k, v in results.items():
        print('  SELFTEST %-14s : %s' % (k, 'PASS' if v else 'FAIL'))
    print('SELFTEST overall: %s (workdir=%s)' % ('PASS' if ok else 'FAIL', out_dir))
    shutil.rmtree(out_dir, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(self_test())
    run_gui()

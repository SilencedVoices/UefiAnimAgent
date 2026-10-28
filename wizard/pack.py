# -*- coding: utf-8 -*-
"""wizard.pack — 素材 → anim.bin（UAN1）+ config.ini。

支持的输入（都归一到"帧序列"再编码）：
  * 帧图片目录：PNG / BMP / JPG / WEBP，按文件名自然排序；
  * 动态图：GIF / APNG / 多页 TIFF（Pillow 逐帧读取）；
  * 视频：MP4 / MOV / MKV / WEBM / AVI —— 需要系统里能找到 ffmpeg.exe
    （放在 exe 同目录、或 PATH 中、或 C:\\ffmpeg\\bin）。

输出（UAN1，与播放器 AnimLoad.c 一致）：
  offset  size  field
  0       4     magic 'UAN1'
  4       2     version = 1
  6       2     format  0=BMP_RAW24(top-down BGR) 1=QOI
  8       2     frame_count
  10      2     fps
  12      2     width
  14      2     height
  16      4     index_offset = 0x18
  18      4     reserved
  0x18    N*8   index: {u32 offset, u32 size}
"""
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from . import ANIM_NAME, CONFIG_NAME, ESP_DIR, paths

MAGIC = b'UAN1'
VERSION = 1
FORMAT_BMP = 0
FORMAT_QOI = 1
INDEX_OFFSET = 0x18
HEADER = '<4sHHHHIII'

FRAME_EXTS = ('.png', '.bmp', '.jpg', '.jpeg', '.webp')
ANIM_EXTS = ('.gif', '.apng', '.tif', '.tiff')
VIDEO_EXTS = ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.wmv', '.flv')

MAX_FRAMES = 2000
FPS_MIN, FPS_MAX = 1, 120
BMP_MAX_FRAMES = 72          # BMP_RAW24 体积保护（与 make_anim.py 一致）
BMP_MAX_W, BMP_MAX_H = 1920, 1080


class PackError(Exception):
    """面向用户的打包错误（可预期，GUI 直接弹对话框）。"""


# --------------------------------------------------------------------- #
# Pillow                                                                #
# --------------------------------------------------------------------- #

def pillow():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise PackError('缺少图像库 Pillow。\n\n'
                        '源码运行：pip install pillow\n'
                        '打包版：说明文件损坏，请重新下载 exe。')


def _lanczos(Image):
    return getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', 1)


def fit_letterbox(im, tw, th):
    """等比缩放到 (tw,th)，不足处补黑边（不变形、不裁切）。"""
    Image = pillow()
    im = im.convert('RGB')
    if im.size == (tw, th):
        return im
    scale = min(float(tw) / im.width, float(th) / im.height)
    nw = max(1, min(tw, int(round(im.width * scale))))
    nh = max(1, min(th, int(round(im.height * scale))))
    if (nw, nh) != im.size:
        im = im.resize((nw, nh), _lanczos(Image))
    canvas = Image.new('RGB', (tw, th), (0, 0, 0))
    canvas.paste(im, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def _natural_key(name):
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r'(\d+)', name)]


def to_qoi(im):
    buf = io.BytesIO()
    im.convert('RGB').save(buf, format='QOI')
    return buf.getvalue()


def to_bgr24(im):
    """top-down BGR24：播放器 GOP Blt 直接吃，无需转换。"""
    rgb = im.convert('RGB').tobytes()
    bgr = bytearray(len(rgb))
    bgr[0::3] = rgb[2::3]
    bgr[1::3] = rgb[1::3]
    bgr[2::3] = rgb[0::3]
    return bytes(bgr)


# --------------------------------------------------------------------- #
# ffmpeg 定位（视频输入用）                                              #
# --------------------------------------------------------------------- #

def find_ffmpeg():
    names = ['ffmpeg.exe'] if os.name == 'nt' else ['ffmpeg']
    cands = []
    base = paths.app_dir()
    for n in names:
        cands.append(os.path.join(base, n))
        cands.append(os.path.join(base, 'ffmpeg', 'bin', n))
    for n in names:
        w = shutil.which(n)
        if w:
            cands.append(w)
    for n in names:
        cands.append(os.path.join('C:\\ffmpeg\\bin', n))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ''


def _extract_video(src, out_dir, fps, max_frames, logger, timeout=900):
    """ffmpeg 抽帧（不缩放，缩放交给 Pillow 统一处理）。"""
    ff = find_ffmpeg()
    if not ff:
        raise PackError(
            '当前输入是视频文件，需要 ffmpeg 才能解码，但没有找到 ffmpeg.exe。\n\n'
            '任选一种解决办法：\n'
            '  1) 把 ffmpeg.exe 放到本程序同目录（或 C:\\ffmpeg\\bin）；\n'
            '  2) 先把视频导出成 PNG 序列（如用 Pr / 剪映 / ffmpeg），\n'
            '     再用"图片文件夹"模式导入；\n'
            '  3) 直接用 GIF 动图（不需要 ffmpeg）。')
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, 'f_%05d.png')
    cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y',
           '-i', src,
           '-vf', 'fps=%g' % float(fps),
           '-frames:v', str(int(max_frames)),
           '-pix_fmt', 'rgb24', pattern]
    logger('[pack] ffmpeg 抽帧: %s' % ff)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PackError('ffmpeg 抽帧超时（>%d 秒）。请先裁剪视频长度或降低帧率。' % timeout)
    except OSError as e:
        raise PackError('无法运行 ffmpeg：%s' % e)
    if p.returncode != 0:
        msg = (p.stderr or b'').decode('utf-8', 'replace').strip()
        raise PackError('ffmpeg 解码失败（退出码 %d）：\n%s'
                        % (p.returncode, msg[-800:] or '（无错误输出）'))
    files = sorted((f for f in os.listdir(out_dir) if f.lower().endswith('.png')),
                   key=_natural_key)
    if not files:
        raise PackError('ffmpeg 没有抽出任何帧，可能视频损坏或不是视频文件。')
    return [os.path.join(out_dir, f) for f in files]


# --------------------------------------------------------------------- #
# 素材 → 帧列表                                                          #
# --------------------------------------------------------------------- #

def collect_frames(src, fps, logger, work_dir=None):
    """返回 (frame_paths or None, animation_objects or None, note)。"""
    if os.path.isdir(src):
        files = sorted((os.path.join(src, f) for f in os.listdir(src)
                        if os.path.splitext(f)[1].lower() in FRAME_EXTS),
                       key=lambda p: _natural_key(os.path.basename(p)))
        if not files:
            raise PackError('文件夹里没有找到图片帧（支持 %s）。'
                            % '、'.join(e.lstrip('.').upper() for e in FRAME_EXTS))
        if len(files) > MAX_FRAMES:
            raise PackError('帧数过多（%d 帧，上限 %d）。请降低帧率或截短动画。'
                            % (len(files), MAX_FRAMES))
        return files, None, '图片序列 %d 帧' % len(files)

    if not os.path.isfile(src):
        raise PackError('输入不存在：%s' % src)

    ext = os.path.splitext(src)[1].lower()
    if ext in VIDEO_EXTS:
        work_dir = work_dir or tempfile.mkdtemp(prefix='uefi_anim_frames_')
        fr = _extract_video(src, work_dir, fps, MAX_FRAMES, logger)
        return fr, None, '视频抽帧 %d 帧' % len(fr)

    if ext in ANIM_EXTS:
        return None, src, '动图逐帧'

    if ext in FRAME_EXTS:
        return [src], None, '单张图片'

    raise PackError('不支持的输入类型：%s\n\n'
                    '支持：图片文件夹、GIF/APNG、视频（需 ffmpeg）、单张图片。' % ext)


class _AnimFrames(object):
    """把 GIF/APNG/TIFF 包装成"可迭代 RGB 帧"。"""

    def __init__(self, path):
        Image = pillow()
        self.Image = Image
        self.path = path
        self.im = Image.open(path)
        self.n = getattr(self.im, 'n_frames', 1) or 1

    def __len__(self):
        return self.n

    def get(self, i):
        self.im.seek(i)
        return self.im.convert('RGB')


# --------------------------------------------------------------------- #
# 打包主流程                                                             #
# --------------------------------------------------------------------- #

def pack(src, out_dir, resolution='auto', fps=24, fmt='QOI',
         budget_mb=None, allow_over=False, logger=None, progress=None):
    """素材 → out_dir\\anim.bin + config.ini；返回结果 dict。"""
    log = logger or (lambda s: None)
    prog = progress or (lambda i, n: None)
    Image = pillow()

    fps = int(fps)
    if not (FPS_MIN <= fps <= FPS_MAX):
        raise PackError('帧率必须在 %d..%d 之间（当前 %d）。' % (FPS_MIN, FPS_MAX, fps))
    fmt = (fmt or 'QOI').upper()
    if fmt not in ('QOI', 'BMP'):
        raise PackError('未知格式：%s' % fmt)

    t0 = time.time()
    frames, anim_src, note = collect_frames(src, fps, log)
    if anim_src:
        frames = _AnimFrames(anim_src)
    total = len(frames)
    if total > MAX_FRAMES:
        raise PackError('帧数过多（%d 帧，上限 %d）。请降低帧率或截短动画。'
                        % (total, MAX_FRAMES))
    log('[pack] %s，共 %d 帧，目标 %s，%d fps，%s'
        % (note, total, resolution if resolution != 'auto' else '跟随首帧', fps, fmt))

    # 目标尺寸
    if resolution in (None, '', 'auto'):
        if anim_src:
            tw, th = frames.get(0).size
        else:
            try:
                with Image.open(frames[0]) as im:
                    tw, th = im.size
            except Exception as e:
                raise PackError('首帧无法解码：%s\n%s' % (frames[0], e))
    else:
        try:
            tw, th = resolution
            tw, th = int(tw), int(th)
        except (TypeError, ValueError):
            raise PackError('分辨率格式错误（应为 (宽, 高) 或 auto）：%r' % (resolution,))

    if tw % 2 or th % 2:
        tw -= tw % 2
        th -= th % 2
        log('[pack] 宽高调整为偶数：%dx%d' % (tw, th))
    if tw < 16 or th < 16 or tw > 7680 or th > 4320:
        raise PackError('分辨率不合理：%dx%d' % (tw, th))

    if fmt == 'BMP' and (total > BMP_MAX_FRAMES or tw > BMP_MAX_W or th > BMP_MAX_H):
        raise PackError('BMP 格式限制：最多 %d 帧且不超过 %dx%d（当前 %d 帧 %dx%d）。\n'
                        '请改用 QOI。' % (BMP_MAX_FRAMES, BMP_MAX_W, BMP_MAX_H,
                                          total, tw, th))

    blobs = []
    for i in range(total):
        try:
            if anim_src:
                im = frames.get(i)
            else:
                im = Image.open(frames[i])
                im.load()
            im = fit_letterbox(im, tw, th)
            blobs.append(to_qoi(im) if fmt == 'QOI' else to_bgr24(im))
        except PackError:
            raise
        except Exception as e:
            raise PackError('第 %d/%d 帧处理失败：%s' % (i + 1, total, e))
        prog(i + 1, total)

    index = bytearray()
    offset = INDEX_OFFSET + 8 * total
    for b in blobs:
        index += struct.pack('<II', offset, len(b))
        offset += len(b)
    data = (struct.pack(HEADER, MAGIC, VERSION,
                        FORMAT_QOI if fmt == 'QOI' else FORMAT_BMP,
                        total, fps, tw, th, INDEX_OFFSET)
            + bytes(index) + b''.join(blobs))

    size_mb = len(data) / 1048576.0
    if budget_mb and size_mb > budget_mb and not allow_over:
        raise PackError('动画体积 %.1f MB，超过 ESP 可用空间 %.1f MB。\n\n'
                        '可这样压缩：降低帧率、降低分辨率、缩短时长，或改用 QOI 格式。'
                        % (size_mb, budget_mb))

    out_dir = out_dir or paths.data_dir()
    os.makedirs(out_dir, exist_ok=True)
    anim_path = os.path.join(out_dir, ANIM_NAME)
    tmp = anim_path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, anim_path)

    # 回读校验
    with open(anim_path, 'rb') as f:
        h = f.read(24)
    m, ver, fm, fc, fh, w, hh, idx = struct.unpack(HEADER, h)
    if not (m == MAGIC and ver == VERSION and fc == total and fh == fps
            and (w, hh) == (tw, th) and idx == INDEX_OFFSET):
        raise PackError('写出的 anim.bin 头校验失败（磁盘问题？）')

    cfg_path = write_config(out_dir, w, hh, fps)
    log('[pack] 完成：%s（%.2f MB），%dx%d @%dfps %s，%.1f 秒'
        % (anim_path, size_mb, tw, th, fps, fmt, time.time() - t0))

    return dict(anim=anim_path, config=cfg_path, frames=total, width=tw,
                height=th, fps=fps, fmt=fmt, size_bytes=len(data),
                size_mb=size_mb, elapsed=time.time() - t0, warn=_warn_text(
                    total, tw, th, fps, fmt, size_mb, budget_mb, allow_over))


def _warn_text(total, tw, th, fps, fmt, size_mb, budget_mb, allow_over):
    w = []
    if budget_mb and size_mb > budget_mb:
        w.append('体积 %.1f MB 已超过 ESP 可用空间 %.1f MB（你选择了强制继续）。'
                 % (size_mb, budget_mb))
    if tw > 3840 or th > 2160:
        w.append('分辨率高于 4K，部分显卡在固件阶段可能不支持。')
    if fps > 60:
        w.append('%d fps 需要固件阶段稳定 60Hz 以上刷新，个别机器会掉帧。' % fps)
    if fmt == 'BMP':
        w.append('BMP 未压缩，占用空间大；QOI 通常只有它的 1/3~1/5。')
    if total and fps:
        dur = float(total) / fps
        if dur > 20:
            w.append('动画时长 %.1f 秒，开机等待会明显变长（建议 3~8 秒）。' % dur)
        if dur < 0.5:
            w.append('动画只有 %.1f 秒，几乎一闪而过。' % dur)
    return ' '.join(w)


def write_config(out_dir, width, height, fps, chainload=None,
                 skip_esc=True, extra_lines=None):
    """写 config.ini（播放器 Config.c 键名，大小写不敏感）。"""
    from . import DEFAULT_CHAINLOAD
    path = os.path.join(out_dir, CONFIG_NAME)
    lines = [
        '; UEFI 开机动画播放器配置（由 UEFI 开机动画向导生成）',
        '; 注释用 ; 开头。修改后重新安装即可生效。',
        ';',
        '; resolution：auto = 用当前固件显示模式；也可写 1920x1080 等固定值。',
        'resolution=%dx%d' % (width, height),
        '; fps：1..120，需与动画本身帧率匹配。',
        'fps=%d' % fps,
        '; animpath：动画文件，相对于 \\EFI\\BootAnim\\，也可写绝对路径。',
        'animpath=%s' % ANIM_NAME,
        '; skipkey：跳过动画的按键。0x1b=Esc，0xFFFF=任意键。',
        'skipkey=%s' % ('0x1b' if skip_esc else '0xFFFF'),
        '; chainload：动画结束后要启动的 Windows 引导器（不要改）。',
        'chainload=%s' % (chainload or DEFAULT_CHAINLOAD),
    ]
    if extra_lines:
        lines.extend(extra_lines)
    text = '\r\n'.join(lines) + '\r\n'
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(text)
    return path


# --------------------------------------------------------------------- #
# 自检                                                                   #
# --------------------------------------------------------------------- #

def self_test(logger=None):
    """无外部素材自检：生成渐变帧 → 打包 → 校验头与索引 → 解析 config。"""
    log = logger or (lambda s: None)
    Image = pillow()
    work = tempfile.mkdtemp(prefix='uefi_anim_selftest_')
    results = {}
    try:
        frames_dir = os.path.join(work, 'frames')
        os.makedirs(frames_dir, exist_ok=True)
        for i in range(6):
            im = Image.new('RGB', (320, 180), (0, 0, 0))
            px = im.load()
            for x in range(0, 320, 8):
                c = (x * 255 // 320, (i * 40) % 256, 128)
                for y in range(180):
                    px[x, y] = c
                    for dx in range(1, 8):
                        if x + dx < 320:
                            px[x + dx, y] = c
            im.save(os.path.join(frames_dir, 'f_%03d.png' % i))

        r = pack(frames_dir, os.path.join(work, 'out'), (320, 180), 30, 'QOI', logger=log)
        results['pack_qoi'] = os.path.isfile(r['anim']) and r['frames'] == 6
        with open(r['anim'], 'rb') as f:
            raw = f.read()
        m, ver, fm, fc, fh, w, h, idx = struct.unpack(HEADER, raw[:24])
        results['header'] = (m == MAGIC and fc == 6 and fh == 30
                             and (w, h) == (320, 180) and idx == INDEX_OFFSET)
        o1, s1 = struct.unpack('<II', raw[24:32])
        o2, s2 = struct.unpack('<II', raw[32:40])
        results['index'] = o1 == INDEX_OFFSET + 8 * 6 and o2 == o1 + s1
        results['size'] = (o1 + sum(struct.unpack('<II', raw[24 + 8 * i:32 + 8 * i])[1]
                                    for i in range(6))) == len(raw)
        cfg = open(r['config'], encoding='utf-8-sig').read()
        results['config'] = ('resolution=320x180' in cfg and 'fps=30' in cfg
                             and 'chainload=' in cfg)
        # BMP 阈值拒绝
        try:
            pack(frames_dir, os.path.join(work, 'out2'), (2560, 1440), 24, 'BMP')
            results['bmp_refused'] = False
        except PackError:
            results['bmp_refused'] = True
        # 空目录拒绝
        empty = os.path.join(work, 'empty')
        os.makedirs(empty, exist_ok=True)
        try:
            pack(empty, os.path.join(work, 'o3'), 'auto', 24, 'QOI')
            results['empty_refused'] = False
        except PackError:
            results['empty_refused'] = True
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def read_meta(anim_path):
    """读 anim.bin 头部元数据：dict(frames, fps, width, height, fmt)；读不到返回 None。"""
    try:
        with open(anim_path, 'rb') as f:
            head = f.read(24)
        if len(head) < 24:
            return None
        m, ver, fmt, count, fps, w, h, idx_off = struct.unpack(HEADER, head)
        if m != MAGIC:
            return None
        return dict(frames=count, fps=fps, width=w, height=h,
                    fmt='QOI' if fmt == FORMAT_QOI else ('BMP' if fmt == FORMAT_BMP else str(fmt)),
                    size=os.path.getsize(anim_path))
    except (OSError, struct.error):
        return None


def frame_count(anim_path):
    """读 anim.bin 的帧数（读不到就返回 0）。"""
    try:
        with open(anim_path, 'rb') as f:
            head = f.read(24)
        if len(head) < 24:
            return 0
        m, ver, fmt, count, fps, w, h, idx_off = struct.unpack(HEADER, head)
        return count if m == MAGIC else 0
    except (OSError, struct.error):
        return 0


def read_frame(anim_path, index=0):
    """从 anim.bin 读第 index 帧并返回 PIL RGB 图像（预览/诊断用）。"""
    Image = pillow()
    with open(anim_path, 'rb') as f:
        head = f.read(24)
        if len(head) < 24:
            raise PackError('文件太小，不是 anim.bin')
        m, ver, fmt, count, fps, w, h, idx_off = struct.unpack(HEADER, head)
        if m != MAGIC:
            raise PackError('不是 UAN1 动画包：%s' % anim_path)
        index = max(0, min(index, count - 1))
        f.seek(idx_off + 8 * index)
        off, size = struct.unpack('<II', f.read(8))
        f.seek(off)
        blob = f.read(size)
    if fmt == FORMAT_QOI:
        return Image.open(io.BytesIO(blob)).convert('RGB')
    if fmt == FORMAT_BMP:
        rgb = bytearray(size)
        rgb[0::3] = blob[2::3]
        rgb[1::3] = blob[1::3]
        rgb[2::3] = blob[0::3]
        return Image.frombytes('RGB', (w, h), bytes(rgb))
    raise PackError('未知帧格式：%d' % fmt)


def make_demo_frames(dest, width=1280, height=720, count=30, logger=None):
    """内置演示动画：黑底 + 呼吸环 + 进度条（无素材也能先跑通全流程）。"""
    Image = pillow()
    from PIL import ImageDraw
    os.makedirs(dest, exist_ok=True)
    cx, cy = width // 2, height // 2
    r = min(width, height) // 5
    for i in range(count):
        t = float(i) / max(1, count)
        im = Image.new('RGB', (width, height), (8, 8, 12))
        d = ImageDraw.Draw(im)
        import math
        grow = int(6 * math.sin(t * math.pi * 2))
        for k in range(3):
            rr = r - k * 10 + grow
            if rr > 4:
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                          outline=(60 + k * 40, 120 - k * 20, 200 - k * 30), width=3)
        bw = int(width * 0.4)
        x0 = (width - bw) // 2
        y0 = cy + r + 28
        d.rectangle([x0, y0, x0 + bw, y0 + 6], outline=(90, 90, 100))
        d.rectangle([x0, y0, x0 + int(bw * t), y0 + 6], fill=(80, 170, 255))
        im.save(os.path.join(dest, 'demo_%03d.png' % i))
    if logger:
        logger('[demo] 生成 %d 帧演示画面 → %s' % (count, dest))
    return dest

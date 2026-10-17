#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_anim.py — BMP 帧序列 → anim.bin 打包器（UEFI Boot Animation Agent 资源管线 P3）

依据: 设计文档 v1.0 §2.2(格式阈值) §2.3(UAN1 容器) §2.4(体积预算) R2/R9

用法:
  python make_anim.py --input  <BMP 目录>
                      --output <anim.bin 路径>
                      --format QOI|BMP        (帧数>72 或 2K 必须用 QOI, §2.2)
                      [--fps 24]              (fps_hint, 可被 config.ini 覆盖)
                      [--budget-mb 254]       (真机 ESP 剩余体积预算)
                      [--esp-free-mb N]       (可选: 实际 ESP 剩余空间)
                      [--allow-overbudget]    (虚拟 ESP 场景: 超预算警告但放行)

容器格式(小端, §2.3):
  0x00 4B magic 'UAN1'   0x04 2B version=1   0x06 2B format(0=BMP_RAW24,1=QOI)
  0x08 2B frame_count    0x0A 2B fps_hint    0x0C 4B width      0x10 4B height
  0x14 4B index_offset(=0x18)
  0x18 N*8B index[{u32 offset, u32 size}]    之后为帧数据
  BMP_RAW24 帧 = top-down BGR 行数据(本工具负责翻转 bottom-up BMP)
  QOI 帧 = 完整 QOI 图像(Pillow 编码)

校验: 全帧尺寸/位深一致(R9) | 预算超限默认拒绝(R2), --allow-overbudget 放行
"""
import argparse
import io
import os
import re
import struct
import sys
import time

MAGIC = b'UAN1'
VERSION = 1
FORMAT_BMP = 0
FORMAT_QOI = 1
INDEX_OFFSET = 0x18
REAL_ESP_BUDGET_MB = 254          # §2.4 实测真机 ESP 剩余


def natural_key(name):
    parts = re.split(r'(\d+)', name)
    return [(1, int(p), '') if p.isdigit() else (0, 0, p) for p in parts]


def bmp_header(path):
    """解析 BMP 头: 返回 (w, h, bpp, top_down, pixel_data_offset)。"""
    with open(path, 'rb') as f:
        head = f.read(54)
    if len(head) < 54 or head[:2] != b'BM':
        raise ValueError('%s: not BMP' % path)
    data_off = struct.unpack_from('<I', head, 10)[0]
    w, h = struct.unpack_from('<ii', head, 18)
    planes, bpp = struct.unpack_from('<HH', head, 26)
    comp = struct.unpack_from('<I', head, 30)[0]
    if planes != 1 or comp != 0 or bpp != 24:
        raise ValueError('%s: unsupported BMP (planes=%d bpp=%d comp=%d)'
                         % (path, planes, bpp, comp))
    return w, abs(h), bpp, h < 0, data_off


def read_bmp_topdown_bgr(path):
    """读取 24bpp BMP → top-down BGR 连续行数据(w*h*3)，含行对齐剥离与上下翻转。"""
    w, h, _, top_down, data_off = bmp_header(path)
    row_padded = (w * 3 + 3) & ~3
    size = row_padded * h
    with open(path, 'rb') as f:
        f.seek(data_off)
        raw = f.read(size)
    if len(raw) < size:
        raise ValueError('%s: pixel data truncated' % path)
    rows = []
    for y in range(h):
        row = raw[y * row_padded: y * row_padded + w * 3]
        rows.append(row)
    if not top_down:                # bottom-up → top-down
        rows.reverse()
    return w, h, b''.join(rows)


def encode_qoi(path, expect_w, expect_h):
    """BMP → 完整 QOI 图像字节串(Pillow)。返回字节串。"""
    from PIL import Image
    with Image.open(path) as im:
        if im.size != (expect_w, expect_h):
            raise ValueError('%s: size %s != first frame %s'
                             % (path, im.size, (expect_w, expect_h)))
        rgb = im.convert('RGB')
        buf = io.BytesIO()
        rgb.save(buf, format='QOI')
        return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description='BMP sequence -> UAN1 anim.bin packer')
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--format', required=True, choices=['QOI', 'BMP'],
                    help='QOI=BMP_RAW24 之外推荐(>72帧或2K强制)')
    ap.add_argument('--fps', type=int, default=24, choices=[24, 30, 60, 120])
    ap.add_argument('--budget-mb', type=float, default=REAL_ESP_BUDGET_MB)
    ap.add_argument('--esp-free-mb', type=float, default=None)
    ap.add_argument('--allow-overbudget', action='store_true')
    args = ap.parse_args()

    files = sorted([f for f in os.listdir(args.input)
                    if f.lower().endswith('.bmp')], key=natural_key)
    if not files:
        print('[ERROR] no .bmp in %s' % args.input)
        return 1
    n = len(files)

    fmt = FORMAT_QOI if args.format == 'QOI' else FORMAT_BMP

    # ---- 阈值策略复核(§2.2): >72 帧或 >1080p 时若选 BMP 则拒绝 ----
    w0, h0, _, _, _ = bmp_header(os.path.join(args.input, files[0]))
    if fmt == FORMAT_BMP and (n > 72 or h0 > 1080 or w0 > 1920):
        print('[REFUSED] %d frames %dx%d: BMP only allowed for <=72 frames '
              'and <=1080p (design 2.2). Use --format QOI.' % (n, w0, h0))
        return 1

    print('[PACK] %d frames %dx%d format=%s fps=%d'
          % (n, w0, h0, args.format, args.fps))
    t0 = time.time()
    frames = []
    total_frame_bytes = 0
    for i, name in enumerate(files):
        p = os.path.join(args.input, name)
        if fmt == FORMAT_QOI:
            blob = encode_qoi(p, w0, h0)
        else:
            fw, fh, blob = read_bmp_topdown_bgr(p)
            if (fw, fh) != (w0, h0):
                print('[REFUSED] %s: %dx%d != %dx%d (uniformity, R9)'
                      % (name, fw, fh, w0, h0))
                return 1
        frames.append(blob)
        total_frame_bytes += len(blob)
        if (i + 1) % 10 == 0 or i + 1 == n:
            rate = (i + 1) / (time.time() - t0)
            print('  ... %d/%d (%.1f fps pack, ETA %.0fs)'
                  % (i + 1, n, rate, (n - i - 1) / max(rate, 0.01)))

    # ---- 组容器: header + index + frames ----
    index = bytearray()
    offset = INDEX_OFFSET + 8 * n
    for blob in frames:
        index += struct.pack('<II', offset, len(blob))
        offset += len(blob)
    header = struct.pack('<4sHHHHIII', MAGIC, VERSION, fmt, n, args.fps,
                         w0, h0, INDEX_OFFSET)
    out = header + bytes(index) + b''.join(frames)
    with open(args.output, 'wb') as f:
        f.write(out)
    size_mb = len(out) / 1048576.0

    # ---- 预算校验(§2.4 / R2) ----
    budget = args.budget_mb
    if args.esp_free_mb is not None:
        budget = min(budget, args.esp_free_mb)
    over = size_mb > budget
    print('[STATS] anim.bin = %.1f MB | raw = %.1f MB | ratio = %.1f%%'
          % (size_mb, n * w0 * h0 * 3 / 1048576.0,
             100.0 * size_mb / (n * w0 * h0 * 3 / 1048576.0)))
    if over and not args.allow_overbudget:
        print('[REFUSED] %.1f MB > budget %.1f MB (real-ESP rule R2). '
              'Virtual ESP may re-run with --allow-overbudget.'
              % (size_mb, budget))
        try:
            os.remove(args.output)
        except OSError:
            pass
        return 2
    if over:
        print('[WARN] %.1f MB > budget %.1f MB — allowed by '
              '--allow-overbudget (virtual ESP scenario; real machine '
              'must downgrade per 2.4)' % (size_mb, budget))
    else:
        print('[OK] within budget %.1f MB' % budget)

    # ---- 头回读自检 ----
    with open(args.output, 'rb') as f:
        back = f.read(24)
    m, ver, fm, fc, fps, w, h, idx = struct.unpack('<4sHHHHIII', back)
    ok = (m == MAGIC and ver == VERSION and fm == fmt and fc == n
          and w == w0 and h == h0 and idx == INDEX_OFFSET)
    print('[VERIFY] header=%s ver=%d fmt=%d frames=%d %dx%d idx_off=0x%X -> %s'
          % (m.decode(), ver, fm, fc, w, h, idx, 'PASS' if ok else 'FAIL'))
    print('[DONE] %s (%.1f MB, %d frames, pack %.0fs)'
          % (args.output, size_mb, n, time.time() - t0))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

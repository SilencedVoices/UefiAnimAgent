#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_bmp.py — 扫描 BMP 帧序列规格（设计文档 P4/Step0）

用法: python scan_bmp.py --input <dir>
输出: 文件数 / 首帧宽高位深 / 尺寸一致性 / 总原始体积 / 命名连续性
"""
import argparse
import os
import re
import struct
import sys


def parse_bmp_header(path):
    """返回 (width, height, bpp, top_down)，失败抛异常。"""
    with open(path, 'rb') as f:
        data = f.read(54)
    if len(data) < 54 or data[:2] != b'BM':
        raise ValueError('not a BMP (magic)')
    bmp_w, bmp_h = struct.unpack_from('<ii', data, 18)
    planes, bpp = struct.unpack_from('<HH', data, 26)
    compression = struct.unpack_from('<I', data, 30)[0]
    if planes != 1 or compression != 0:
        raise ValueError('unsupported BMP: planes=%d comp=%d' % (planes, compression))
    top_down = bmp_h < 0
    return bmp_w, abs(bmp_h), bpp, top_down


def natural_key(name):
    """按数字段自然排序：合成 1_00007.bmp -> ('合成 1_', 7, '.bmp')。"""
    parts = re.split(r'(\d+)', name)
    key = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p), ''))
        else:
            key.append((0, 0, p))
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    args = ap.parse_args()

    files = sorted(
        [f for f in os.listdir(args.input) if f.lower().endswith('.bmp')],
        key=natural_key)
    if not files:
        print('NO_BMP_FILES')
        return 1

    specs = []
    errors = []
    for name in files:
        try:
            specs.append(parse_bmp_header(os.path.join(args.input, name)))
        except Exception as e:                        # noqa: BLE001
            errors.append('%s: %s' % (name, e))

    w, h, bpp, top_down = specs[0] if specs else (0, 0, 0, False)
    size_ok = all(s[0] == w and s[1] == h and s[2] == bpp for s in specs)

    total_bytes = 0
    for name in files:
        total_bytes += os.path.getsize(os.path.join(args.input, name))

    # 命名连续性：提取每帧末段数字
    nums = []
    for name in files:
        m = re.findall(r'(\d+)', os.path.splitext(name)[0])
        nums.append(int(m[-1]) if m else None)
    contiguous = None
    if all(n is not None for n in nums):
        contiguous = (nums == list(range(nums[0], nums[0] + len(nums))))

    print('COUNT       : %d' % len(files))
    print('FIRST_FRAME : %s' % files[0])
    print('LAST_FRAME  : %s' % files[-1])
    print('DIMENSIONS  : %d x %d' % (w, h))
    print('BPP         : %d' % bpp)
    print('TOP_DOWN    : %s' % top_down)
    print('SIZE_UNIFORM: %s' % ('YES' if size_ok else 'NO'))
    print('NAMING_SEQ  : %s' % ('CONTIGUOUS %d..%d' % (nums[0], nums[-1])
                                if contiguous else 'CHECK_MANUALLY'))
    print('RAW_TOTAL_MB: %.1f  (w*h*3*frames = %.1f)'
          % (total_bytes / 1048576.0, w * h * 3 * len(files) / 1048576.0))
    print('FILE_TOTAL_MB: %.1f' % (sum(os.path.getsize(os.path.join(args.input, f))
                                       for f in files) / 1048576.0))
    if errors:
        print('ERRORS      :')
        for e in errors[:10]:
            print('  ' + e)
    return 0


if __name__ == '__main__':
    sys.exit(main())

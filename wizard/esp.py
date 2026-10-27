# -*- coding: utf-8 -*-
"""wizard.esp — ESP（EFI 系统分区）发现、挂载、文件写入与备份。

安全约束：
  * 只写 \\EFI\\BootAnim，绝不触碰 \\EFI\\Microsoft。
  * 挂载使用的临时盘符用后立即卸载（mountvol /D）。
  * 每写完一个文件都回读 SHA256，不一致即失败。
"""
import hashlib
import os
import shutil
import string
import time

from . import ESP_DIR, EFI_NAME, ANIM_NAME, CONFIG_NAME
from . import paths, sysinfo

ESP_GPT_TYPE = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
WINDOWS_LOADER = os.path.join('EFI', 'Microsoft', 'Boot', 'bootmgfw.efi')
MIN_FREE_MB = 8.0          # 除 anim.bin 外还要留的余量（efi+config+索引+安全余量）


class EspError(Exception):
    pass


def _norm_drive(drive):
    """把 'T' / 'T:' / 'T:\\' 统一成 'T:'。"""
    d = (drive or '').strip().rstrip('\\')
    if len(d) == 1 and d.isalpha():
        d += ':'
    return d


class Esp(object):
    __slots__ = ('disk', 'part', 'letter', 'label', 'fs', 'size_mb', 'free_mb',
                 'volume_path', 'mounted_letter', 'mounted_by_us')

    def __init__(self, disk, part, letter, label, fs, size_mb, free_mb, volume_path):
        self.disk = disk
        self.part = part
        self.letter = _norm_drive(letter)
        self.label = label or ''
        self.fs = fs or ''
        self.size_mb = size_mb
        self.free_mb = free_mb
        self.volume_path = volume_path or ''
        self.mounted_letter = None
        self.mounted_by_us = False

    # ---- 展示 ----
    def describe(self):
        tag = self.mounted_letter or self.letter or '(无盘符)'
        return '磁盘%s 分区%s  %s  %s  %.0fMB 可用%.0fMB' % (
            self.disk, self.part, tag, self.fs or '?', self.size_mb, self.free_mb or 0)

    # ---- 路径 ----
    def root(self):
        letter = self.mounted_letter or self.letter
        if not letter:
            raise EspError('ESP 尚未挂载：%s' % self.describe())
        return letter.rstrip('\\') + '\\'

    def has_windows_loader(self):
        try:
            return os.path.isfile(os.path.join(self.root(), WINDOWS_LOADER))
        except (EspError, OSError):
            return False

    def has_bcd(self):
        try:
            return os.path.isfile(os.path.join(self.root(), 'EFI', 'Microsoft', 'Boot', 'BCD'))
        except (EspError, OSError):
            return False

    # ---- 挂载 ----
    def mount(self, logger=None):
        if self.letter:
            self.mounted_letter = self.letter
            self.refresh_free()
            return self.root()
        if self.mounted_letter:
            return self.root()
        if not self.volume_path:
            raise EspError('无法挂载：缺少卷路径（请以管理员身份运行）')
        candidates = [c for c in string.ascii_uppercase[3:] if not os.path.exists(c + ':')]
        if not candidates:
            raise EspError('没有空闲盘符用于挂载 ESP')
        last = ''
        for letter in candidates[:6]:
            rc, out, err = sysinfo.run(['mountvol', letter + ':', self.volume_path],
                                       logger=logger)
            if rc == 0 and os.path.exists(letter + ':\\'):
                self.mounted_letter = letter + ':'
                self.mounted_by_us = True
                self.refresh_free()
                if logger:
                    logger('[esp] 已挂载 %s -> %s' % (self.volume_path, letter + ':'))
                return self.root()
            last = (err or out or '').strip()
        raise EspError('挂载 ESP 失败：%s' % (last[:200] or '未知原因'))

    def unmount(self, logger=None):
        if self.mounted_by_us and self.mounted_letter:
            rc, out, err = sysinfo.run(['mountvol', self.mounted_letter + ':', '/D'],
                                       logger=logger)
            if logger:
                logger('[esp] 卸载 %s (%s)' % (self.mounted_letter + ':',
                                               'ok' if rc == 0 else 'fail'))
            self.mounted_letter = None
            self.mounted_by_us = False

    # ---- 空间 ----
    def refresh_free(self):
        try:
            usage = shutil.disk_usage(self.root())
            self.free_mb = usage.free / 1048576.0
            self.size_mb = usage.total / 1048576.0
        except (EspError, OSError):
            pass
        return self.free_mb


def discover(logger=None):
    """枚举所有候选 ESP（GPT EFI 分区 / System 类型小分区）。"""
    script = (
        "$out = @();"
        "foreach ($p in Get-Partition -ErrorAction SilentlyContinue) {"
        "  $g = '' + $p.GptType;"
        "  if ($g -eq '" + ESP_GPT_TYPE + "' -or ('' + $p.Type) -eq 'System') {"
        "    $v = Get-Volume -Partition $p -ErrorAction SilentlyContinue;"
        "    $letter = '' + $p.DriveLetter; if ($letter -eq \"`0\") { $letter = '' };"
        "    $out += [pscustomobject]@{"
        "      disk = [int]$p.DiskNumber; part = [int]$p.PartitionNumber;"
        "      letter = $letter;"
        "      label = ('' + $v.FileSystemLabel); fs = ('' + $v.FileSystem);"
        "      sizeMB = [math]::Round($p.Size/1MB,1);"
        "      freeMB = [math]::Round($v.SizeRemaining/1MB,1);"
        "      vpath = ('' + $v.Path) } } };"
        "@($out) | ConvertTo-Json -Compress")
    obj = sysinfo.run_ps_json(script, logger=logger)
    items = []
    if obj:
        items = obj if isinstance(obj, list) else [obj]
    esps = []
    for it in items:
        try:
            e = Esp(int(it.get('disk') or 0), int(it.get('part') or 0),
                    (it.get('letter') or '').strip().rstrip(':'),
                    (it.get('label') or '').strip(), (it.get('fs') or '').strip(),
                    float(it.get('sizeMB') or 0), float(it.get('freeMB') or 0),
                    (it.get('vpath') or '').strip())
        except (TypeError, ValueError):
            continue
        if e.size_mb and e.size_mb > 2048:      # ESP 不可能 >2GB
            continue
        esps.append(e)
    if logger:
        logger('[esp] 候选 %d 个' % len(esps))
        for e in esps:
            logger('  - ' + e.describe())
    return esps


def pick_windows_esp(logger=None, allow_mount=True):
    """选出承载 Windows 启动的那块 ESP（含 \\EFI\\Microsoft\\Boot\\bootmgfw.efi）。"""
    esps = discover(logger)
    if not esps:
        raise EspError('未找到任何 EFI 系统分区（ESP）。\n'
                       '请确认：① 系统以 UEFI 方式安装 ② 以管理员身份运行本工具')
    hits = []
    for e in esps:
        try:
            if allow_mount:
                e.mount(logger)
            if e.has_windows_loader():
                hits.append(e)
            elif e.mounted_by_us:
                e.unmount(logger)
        except EspError as ex:
            if logger:
                logger('[esp] 跳过 %s：%s' % (e.describe(), ex))
    if not hits:
        raise EspError('找到 %d 个 ESP，但没有任何一个包含 Windows 启动文件：\n%s'
                       % (len(esps), '\n'.join('  - ' + e.describe() for e in esps)))
    if len(hits) > 1 and logger:
        logger('[esp] 检测到 %d 个可用 ESP，默认使用第一个（GUI 中可切换）' % len(hits))
    chosen = hits[0]
    # 卸载未被选中的
    for e in hits[1:]:
        if e.mounted_by_us:
            e.unmount(logger)
    chosen.refresh_free()
    return chosen


# --------------------------------------------------------------------- #
# 文件操作（全部带 SHA256 校验）                                          #
# --------------------------------------------------------------------- #

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def esp_target_paths(esp):
    root = esp.root()
    base = os.path.join(root, ESP_DIR.strip('\\'))
    return {
        'dir': base,
        'efi': os.path.join(base, EFI_NAME),
        'anim': os.path.join(base, ANIM_NAME),
        'config': os.path.join(base, CONFIG_NAME),
    }


def ensure_dir(esp, logger=None):
    paths_ = esp_target_paths(esp)
    if not os.path.isdir(paths_['dir']):
        os.makedirs(paths_['dir'], exist_ok=True)
        if logger:
            logger('[esp] 创建目录 %s' % paths_['dir'])
    return paths_


def copy_verified(src, dst, logger=None):
    """复制 + 回读 SHA256 校验；返回 (src_sha, dst_sha)。"""
    src_sha = sha256_file(src)
    shutil.copyfile(src, dst)
    dst_sha = sha256_file(dst)
    if src_sha != dst_sha:
        raise EspError('写入校验失败：%s\n  src=%s\n  dst=%s' % (dst, src_sha, dst_sha))
    if logger:
        logger('[esp] 写入 %s (%d 字节, sha256=%s…)'
               % (os.path.basename(dst), os.path.getsize(dst), dst_sha[:16]))
    return src_sha, dst_sha


def backup_existing(esp, logger=None, backup_root=None):
    """把 ESP 上已有的 BootAnim 目录内容备份到本地；返回备份目录（可能为 None）。"""
    base = esp_target_paths(esp)['dir']
    if not os.path.isdir(base):
        return None
    files = [f for f in (EFI_NAME, ANIM_NAME, CONFIG_NAME)
             if os.path.isfile(os.path.join(base, f))]
    if not files:
        return None
    dest = os.path.join(backup_root or paths.new_backup_dir(), 'esp_BootAnim')
    os.makedirs(dest, exist_ok=True)
    for f in files:
        src = os.path.join(base, f)
        shutil.copyfile(src, os.path.join(dest, f))
        if logger:
            logger('[backup] %s -> %s (%d 字节, sha256=%s…)'
                   % (f, dest, os.path.getsize(src), sha256_file(src)[:16]))
    with open(os.path.join(dest, 'MANIFEST.txt'), 'w', encoding='utf-8') as fh:
        fh.write('备份时间: %s\nESP: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'),
                                              esp.describe()))
        for f in files:
            fh.write('%s  %s  %d bytes\n' % (sha256_file(os.path.join(dest, f)), f,
                                             os.path.getsize(os.path.join(dest, f))))
    if logger:
        logger('[backup] 完成：%s' % dest)
    return dest


def restore_backup(esp, backup_dir, logger=None):
    """从备份目录还原文件；返回还原的文件名列表。"""
    src_dir = os.path.join(backup_dir, 'esp_BootAnim')
    if not os.path.isdir(src_dir):
        raise EspError('备份不完整（缺少 esp_BootAnim）：%s' % backup_dir)
    base = esp_target_paths(esp)['dir']
    os.makedirs(base, exist_ok=True)
    done = []
    for f in (EFI_NAME, ANIM_NAME, CONFIG_NAME):
        src = os.path.join(src_dir, f)
        if os.path.isfile(src):
            copy_verified(src, os.path.join(base, f), logger)
            done.append(f)
    return done


def remove_agent_files(esp, logger=None):
    """删除 \\EFI\\BootAnim 下的我们三个文件（保留目录内其它内容）。"""
    base = esp_target_paths(esp)['dir']
    removed = []
    for f in (EFI_NAME, ANIM_NAME, CONFIG_NAME):
        p = os.path.join(base, f)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(f)
            if logger:
                logger('[esp] 删除 %s' % p)
    try:
        if os.path.isdir(base) and not os.listdir(base):
            os.rmdir(base)
            if logger:
                logger('[esp] 删除空目录 %s' % base)
    except OSError:
        pass
    return removed

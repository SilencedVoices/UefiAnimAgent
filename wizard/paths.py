# -*- coding: utf-8 -*-
"""wizard.paths — 资源定位 / 数据目录 / 日志。

三种运行形态必须都成立：
  * 源码运行:      python -m wizard
  * PyInstaller 单文件:  sys.frozen == True, 资源在 sys._MEIPASS
  * PyInstaller 单目录:  sys.frozen == True, 资源在 exe 同级 _internal
"""
import os
import sys
import time

from . import APP_NAME


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def app_dir():
    """exe 或源码包所在目录（用于找 assets、写便携数据）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    """只读资源（assets 里的 efi 等）。"""
    if is_frozen():
        base = getattr(sys, '_MEIPASS', app_dir())
        # 单文件: _MEIPASS/assets ; 单目录: _internal/assets 或 exe 同级 assets
        for cand in (os.path.join(base, 'assets'),
                     os.path.join(app_dir(), 'assets'),
                     os.path.join(base, 'wizard', 'assets')):
            p = os.path.join(cand, *parts)
            if os.path.exists(p):
                return p
        return os.path.join(base, 'assets', *parts)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', *parts)


def bundle_path(*parts):
    """打包进 exe 的任意相对资源（tests/fixtures 等）。"""
    if is_frozen():
        base = getattr(sys, '_MEIPASS', app_dir())
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def data_dir():
    """可写数据目录：%LOCALAPPDATA%\\UefiAnimWizard（备份/日志/配置）。"""
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def backup_dir():
    d = os.path.join(data_dir(), 'backup')
    os.makedirs(d, exist_ok=True)
    return d


def log_dir():
    d = os.path.join(data_dir(), 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def new_log_path(hint='wizard'):
    stamp = time.strftime('%Y%m%d_%H%M%S')
    return os.path.join(log_dir(), '%s_%s.log' % (hint, stamp))


def new_backup_dir():
    """每次安装一个独立备份目录：先试时间戳，撞名就加 -2/-3…（不覆盖历史备份）。"""
    stamp = time.strftime('%Y%m%d_%H%M%S')
    base = backup_dir()
    for i in range(1, 100):
        name = stamp if i == 1 else '%s-%d' % (stamp, i)
        d = os.path.join(base, name)
        try:
            os.makedirs(d, exist_ok=False)   # 已存在则抛错 → 换下一个名字
            return d
        except FileExistsError:
            continue
    d = os.path.join(base, '%s-%d' % (stamp, int(time.time())))
    os.makedirs(d, exist_ok=True)
    return d


class Logger(object):
    """同时写文件与回调（GUI 日志面板）的极简日志器。"""

    def __init__(self, path=None, sink=None):
        self.path = path or new_log_path()
        self.sink = sink
        self._fh = open(self.path, 'a', encoding='utf-8', newline='\n')
        self.write('==== %s session start %s ====' %
                   (APP_NAME, time.strftime('%Y-%m-%d %H:%M:%S')))

    def write(self, line):
        line = str(line)
        ts = time.strftime('%H:%M:%S')
        for raw in line.splitlines() or ['']:
            text = '[%s] %s' % (ts, raw)
            try:
                self._fh.write(text + '\n')
                self._fh.flush()
            except Exception:
                pass
            if self.sink:
                try:
                    self.sink(text)
                except Exception:
                    pass

    __call__ = write

    def close(self):
        try:
            self.write('==== session end ====')
            self._fh.close()
        except Exception:
            pass

# -*- coding: utf-8 -*-
"""wizard.bcd — BCD 固件启动项读写（bcdedit 封装 + 输出解析）。

为什么不用第三方库：bcdedit 是唯一受支持且可回滚的入口；这里只做两件事——
  1) 把 `bcdedit /enum firmware` 的文本解析成结构化条目；
  2) 以最小命令集完成"建条目 / 试用一次 / 设为默认 / 撤销 / 删除"。

解析要点（实测中文系统 bcdedit 输出）：
  * 元素名中英混杂：`identifier` 被本地化成"标识符"，而 displayorder / device /
    path / description / timeout 仍是英文；
  * 列表型元素（displayorder / bootsequence）的值按列续行，缩进多空格；
  * 块标题（如"固件应用程序(101fffff)"）单独成行，其后是分隔线。
因此识别"条目开始"只依赖：值形如 {...} 且键不是已知的 GUID 型字段（default 等）。

所有写操作都由上层（deploy）在"已备份 + 用户确认"之后调用。
"""
import os
import re

from . import BCD_DESCRIPTION, sysinfo

GUID_RE = re.compile(r'\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                     r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}')
SEP_RE = re.compile(r'^[-\u2014\u2500]{5,}\s*$')
KV_RE = re.compile(r'^(\S+)[ \t]{2,}(\S.*)?$')
BRACE_RE = re.compile(r'^\{[^}]{1,64}\}$')

FWBOOTMGR = '{fwbootmgr}'
BOOTMGR = '{bootmgr}'

AGENT_PATH_HINT = '\\efi\\bootanim\\bootanimation.efi'

# 值本身是 GUID、但不代表"新条目开始"的字段
GUID_FIELDS = {'displayorder', 'bootsequence', 'toolsdisplayorder', 'default',
               'resumeobject', 'inherit', 'objectguid', 'parent', 'device',
               'osdevice', 'path', 'identifier'}


class BcdError(Exception):
    pass


class Entry(object):
    __slots__ = ('guid', 'description', 'path', 'device', 'title')

    def __init__(self, guid, description='', path='', device='', title=''):
        self.guid = guid
        self.description = description
        self.path = path
        self.device = device
        self.title = title

    def is_agent(self):
        if (self.path or '').lower().endswith(AGENT_PATH_HINT):
            return True
        return (self.description or '').strip().lower() == BCD_DESCRIPTION.lower()

    def __repr__(self):
        return 'Entry(%s, %r, %r)' % (self.guid, self.description, self.path)


class Store(object):
    """一次 `bcdedit /enum firmware` 的解析结果。"""

    def __init__(self, entries, displayorder, bootsequence, timeout, raw):
        self.entries = entries
        self.displayorder = displayorder
        self.bootsequence = bootsequence
        self.timeout = timeout
        self.raw = raw

    def by_guid(self, guid):
        g = (guid or '').upper()
        for e in self.entries:
            if e.guid.upper() == g:
                return e
        return None

    def agent_entry(self):
        for e in self.entries:
            if e.is_agent():
                return e
        return None

    def agent_is_default(self):
        e = self.agent_entry()
        if not e or not self.displayorder:
            return False
        return self.displayorder[0].upper() == e.guid.upper()

    def agent_is_pending(self):
        e = self.agent_entry()
        if not e or not self.bootsequence:
            return False
        return self.bootsequence[0].upper() == e.guid.upper()


# --------------------------------------------------------------------- #
# 解析                                                                   #
# --------------------------------------------------------------------- #

def _parse_blocks(text):
    """把 bcdedit 输出切成块：[{title, fields{key:[values]}}]。"""
    lines = (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks = []
    cur = None
    pending_title = ''
    last_key = None

    for raw in lines:
        if not raw.strip():
            continue
        if SEP_RE.match(raw.strip()):
            if cur is not None:
                blocks.append(cur)
                cur = None
            last_key = None
            continue
        if raw[:1] in (' ', '\t'):
            tok = raw.strip()
            if cur is not None and last_key and GUID_RE.search(tok):
                cur['fields'].setdefault(last_key, []).append(tok)
            continue

        m = KV_RE.match(raw)
        if not m:
            # 块标题行
            pending_title = raw.strip()
            continue
        key, val = m.group(1), (m.group(2) or '').strip()
        kl = key.lower()
        is_ident = (kl == 'identifier' or not kl.isascii()) and bool(BRACE_RE.match(val))
        if is_ident:
            if cur is not None:
                blocks.append(cur)
            cur = {'title': pending_title, 'fields': {'identifier': [val]}}
            pending_title = ''
            last_key = 'identifier'
            continue
        if cur is None:
            cur = {'title': pending_title, 'fields': {}}
            pending_title = ''
        last_key = kl
        if kl in ('displayorder', 'bootsequence', 'toolsdisplayorder'):
            cur['fields'].setdefault(kl, [])
            if val:
                cur['fields'][kl].append(val)
        else:
            cur['fields'][kl] = [val] if val else []

    if cur is not None:
        blocks.append(cur)
    return blocks


def parse_enum(text):
    """解析 bcdedit /enum firmware 文本 → Store。"""
    blocks = _parse_blocks(text)
    entries = []
    displayorder, bootsequence, timeout = [], [], None
    for b in blocks:
        f = b['fields']
        ident = (f.get('identifier') or [''])[0]
        e = Entry(ident,
                  (f.get('description') or [''])[0],
                  (f.get('path') or [''])[0],
                  (f.get('device') or [''])[0],
                  b.get('title', ''))
        entries.append(e)
        if ident.upper() == FWBOOTMGR.upper():
            displayorder = [v for v in (f.get('displayorder') or []) if v]
            bootsequence = [v for v in (f.get('bootsequence') or []) if v]
            try:
                timeout = int((f.get('timeout') or [''])[0])
            except (TypeError, ValueError):
                timeout = None
    return Store(entries, displayorder, bootsequence, timeout, text)


# --------------------------------------------------------------------- #
# bcdedit 调用                                                           #
# --------------------------------------------------------------------- #

def _bcd(args, logger=None, check=True):
    rc, out, err = sysinfo.run(['bcdedit'] + list(args), timeout=45, logger=logger)
    if check and rc != 0:
        msg = (err or out or '').strip()
        if 'Access is denied' in msg or '拒绝访问' in msg:
            raise BcdError('读取/修改启动配置需要管理员权限（Access is denied）。请以管理员身份运行。')
        raise BcdError('bcdedit %s 失败（%d）：%s' % (' '.join(args), rc, msg[:300]))
    return rc, out, err


def read_store(logger=None):
    rc, out, err = _bcd(['/enum', 'firmware'], logger=logger, check=False)
    if rc != 0:
        msg = (err or out or '').strip()
        if 'Access is denied' in msg or '拒绝访问' in msg:
            raise BcdError('读取固件启动项需要管理员权限。请右键"以管理员身份运行"。')
        raise BcdError('bcdedit /enum firmware 失败（%d）：%s' % (rc, msg[:300]))
    return parse_enum(out)


def export_backup(path, logger=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _bcd(['/export', path], logger=logger)
    if not os.path.isfile(path) or os.path.getsize(path) < 512:
        raise BcdError('BCD 导出失败：%s' % path)
    return path


def import_backup(path, logger=None):
    if not os.path.isfile(path):
        raise BcdError('BCD 备份不存在：%s' % path)
    _bcd(['/import', path], logger=logger)
    return path


def create_agent_entry(logger=None, description=BCD_DESCRIPTION):
    """复制 {bootmgr} 生成新条目（继承 device，未激活）；返回新 GUID。"""
    rc, out, err = _bcd(['/copy', BOOTMGR, '/d', description], logger=logger)
    guids = GUID_RE.findall(out or '')
    if not guids:
        raise BcdError('无法从 bcdedit /copy 输出中解析新条目 GUID：%s' % (out or err)[:300])
    guid = guids[0]
    if logger:
        logger('[bcd] 新建启动项 %s（尚未激活）' % guid)
    return guid


def set_agent_path(guid, esp_letter, logger=None):
    """把启动项指向 ESP 上的播放器。"""
    path = '\\EFI\\BootAnim\\BootAnimation.efi'
    _bcd(['/set', guid, 'path', path], logger=logger)
    if esp_letter:
        _bcd(['/set', guid, 'device', 'partition=%s:' % esp_letter.rstrip(':'), ], logger=logger)
    return path


def set_description(guid, desc=BCD_DESCRIPTION, logger=None):
    _bcd(['/set', guid, 'description', desc], logger=logger)


def set_trial_boot(guid, logger=None):
    """只对下一次启动生效（失败自动回到原顺序）。"""
    _bcd(['/set', FWBOOTMGR, 'bootsequence', guid], logger=logger)


def set_default(guid, logger=None):
    """设为默认（固件启动顺序第一位）。"""
    _bcd(['/set', FWBOOTMGR, 'displayorder', guid, '/addfirst'], logger=logger)


def remove_from_displayorder(guid, logger=None):
    _bcd(['/set', FWBOOTMGR, 'displayorder', guid, '/remove'], logger=logger)


def clear_bootsequence(logger=None):
    _bcd(['/deletevalue', FWBOOTMGR, 'bootsequence'], logger=logger, check=False)


def delete_entry(guid, logger=None):
    """删除条目并从启动顺序中清理（/cleanup）。"""
    _bcd(['/delete', guid, '/cleanup'], logger=logger, check=False)


def bootmgr_device(store):
    e = store.by_guid(BOOTMGR)
    return (e.device if e else '') or ''

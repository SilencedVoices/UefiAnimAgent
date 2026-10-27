# -*- coding: utf-8 -*-
"""wizard.deploy — 体检 / 安装（试用）/ 固化 / 回滚。

设计原则（与 README 的"三条硬前提"一致）：
  1. 任何写操作前先备份：ESP 上已有文件 + BCD 导出，缺一不写。
  2. 安装默认只做"试一次"（bootsequence），失败自动回 Windows。
  3. 只有用户在看到一次成功后，才允许 `displayorder /addfirst` 固化。
  4. 只写 \\EFI\\BootAnim，绝不动 \\EFI\\Microsoft；每步写文件都回读 SHA256。
"""
import json
import os
import time

from . import (ANIM_NAME, APP_NAME, BCD_DESCRIPTION, CONFIG_NAME, EFI_NAME,
               paths, sysinfo)
from . import bcd, esp

STATE_NAME = 'state.json'


class DeployError(Exception):
    pass


# --------------------------------------------------------------------- #
# 状态（跨重启记录"试用中"）                                            #
# --------------------------------------------------------------------- #

def _state_path():
    return os.path.join(paths.data_dir(), STATE_NAME)


def save_state(**kw):
    st = load_state()
    st.update(kw)
    st['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(_state_path(), 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def load_state():
    try:
        with open(_state_path(), encoding='utf-8') as f:
            return json.load(f) or {}
    except (IOError, ValueError):
        return {}


def clear_state():
    try:
        os.remove(_state_path())
    except OSError:
        pass


# --------------------------------------------------------------------- #
# 体检                                                                   #
# --------------------------------------------------------------------- #

class Check(object):
    def __init__(self, level, title, detail='', fix=''):
        self.level = level          # ok | warn | error
        self.title = title
        self.detail = detail
        self.fix = fix

    def as_dict(self):
        return dict(level=self.level, title=self.title,
                    detail=self.detail, fix=self.fix)

    def __repr__(self):
        return '[%s] %s' % (self.level.upper(), self.title)


class Preflight(object):
    def __init__(self):
        self.checks = []
        self.admin = False
        self.uefi = None
        self.secure_boot = None
        self.bitlocker = None
        self.os_caption = ''
        self.resolution = None
        self.esp = None
        self.store = None
        self.agent = None
        self.free_mb = 0.0
        self.player_efi = ''

    # ---- 汇总 ----
    @property
    def errors(self):
        return [c for c in self.checks if c.level == 'error']

    @property
    def warnings(self):
        return [c for c in self.checks if c.level == 'warn']

    @property
    def ok(self):
        return not self.errors

    def add(self, level, title, detail='', fix=''):
        self.checks.append(Check(level, title, detail, fix))
        return self.checks[-1]

    def as_dict(self):
        return dict(ok=self.ok, admin=self.admin, uefi=self.uefi,
                    secure_boot=self.secure_boot, bitlocker=self.bitlocker,
                    os=self.os_caption, resolution=self.resolution,
                    esp=self.esp.describe() if self.esp else '',
                    esp_letter=(self.esp.mounted_letter or self.esp.letter)
                    if self.esp else '',
                    free_mb=self.free_mb,
                    agent_guid=self.agent.guid if self.agent else '',
                    agent_default=self.store.agent_is_default() if self.store else False,
                    agent_pending=self.store.agent_is_pending() if self.store else False,
                    player_efi=self.player_efi,
                    checks=[c.as_dict() for c in self.checks])


def _check_esp(pf, log, esp_letter=None):
    try:
        pf.esp = esp.pick_windows_esp(logger=log)
        letter = (pf.esp.mounted_letter or pf.esp.letter)
        pf.free_mb = pf.esp.refresh_free() or 0.0
        pf.add('ok', '找到 Windows 的 EFI 分区：%s（%s，可用 %.0f MB）'
               % (pf.esp.describe(), letter + ':', pf.free_mb))
        if esp_letter and pf.esp.mounted_letter and \
                esp_letter.upper() != pf.esp.mounted_letter.upper():
            pf.add('warn', 'ESP 盘符与预期不同',
                   '预期 %s:，实际 %s:' % (esp_letter, pf.esp.mounted_letter),
                   '如机器有多个 ESP（双系统），请确认选中的是 Windows 那个。')
        if pf.free_mb < esp.MIN_FREE_MB:
            pf.add('error', 'ESP 可用空间不足',
                   '可用 %.1f MB，至少需要 %.0f MB。' % (pf.free_mb, esp.MIN_FREE_MB),
                   '清理 ESP 上不必要的文件，或生成更小的动画（降分辨率/帧率）。')
    except esp.EspError as e:
        pf.add('error', '找不到可用的 EFI 系统分区', str(e),
               '确认系统是 UEFI 安装；若是双系统请先进入 Windows 所在的那块盘的系统。')


def player_efi_path():
    """播放器 efi：优先随包 assets，其次仓库里的构建产物。"""
    cands = [paths.resource_path(EFI_NAME)]
    root = paths.app_dir()
    cands += [
        os.path.join(root, 'assets', EFI_NAME),
        os.path.join(root, 'bin', EFI_NAME),
        os.path.join(root, 'Build', 'UefiAnimPkg', 'RELEASE_VS2022', 'X64', EFI_NAME),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ''


def preflight(logger=None, esp_letter=None):
    """只读检查，不修改任何东西。"""
    log = logger or (lambda s: None)
    pf = Preflight()

    # 1) 管理员
    pf.admin = sysinfo.is_admin()
    if pf.admin:
        pf.add('ok', '已获得管理员权限')
    else:
        pf.add('error', '当前不是管理员权限',
               '修改启动项和写 ESP 都必须管理员权限。',
               '关闭本程序，右键 exe → "以管理员身份运行"（或重新双击，UAC 里点"是"）。')

    # 2) UEFI / Secure Boot
    try:
        is_uefi, sb, note = sysinfo.secure_boot()
    except Exception as e:
        is_uefi, sb, note = None, None, str(e)
    pf.uefi, pf.secure_boot = is_uefi, sb
    if is_uefi is False:
        pf.add('error', '系统不是 UEFI 启动',
               '传统 BIOS(MBR) 启动无法使用固件启动项动画。', '')
    elif is_uefi is None:
        pf.add('warn', '无法确认 UEFI 状态', note or '', '')
    else:
        pf.add('ok', '系统以 UEFI 方式启动')

    if sb is True:
        pf.add('error', 'Secure Boot 已开启',
               '固件会拒绝加载未签名的播放器，动画不会出现。',
               '进 BIOS/UEFI 设置把 Secure Boot 设为 Disabled，保存重启后再运行本工具。\n'
               '（本工具不会、也无法替你改 Secure Boot。）')
    elif sb is False:
        pf.add('ok', 'Secure Boot 已关闭（可以加载未签名程序）')
    else:
        pf.add('warn', '无法读取 Secure Boot 状态', note or '', '')

    # 3) BitLocker
    try:
        bl, blnote = sysinfo.bitlocker()
    except Exception as e:
        bl, blnote = None, str(e)
    pf.bitlocker = bl
    if bl is True:
        pf.add('warn', '系统盘启用了 BitLocker 加密',
               '改动启动项有一定概率触发恢复界面；没有恢复密钥会进不去系统。',
               '先确认已保存 48 位恢复密钥（Microsoft 账户 → 设备 → BitLocker 密钥），'
               '或先在"管理 BitLocker"里暂停保护，装完再恢复。')
    elif bl is False:
        pf.add('ok', '未启用 BitLocker（无恢复密钥风险）')
    else:
        pf.add('warn', '无法确认 BitLocker 状态', blnote or '', '')

    # 4) 系统 / 分辨率
    pf.os_caption = sysinfo.os_caption()
    pf.resolution = sysinfo.display_resolution()
    if pf.resolution:
        w, h = pf.resolution
        pf.add('ok', '当前显示分辨率 %dx%d（动画会按此比例生成）' % (w, h))
    else:
        pf.add('warn', '未能读取显示分辨率', '将按 1920x1080 生成动画。', '')

    # 5) ESP
    if not pf.admin:
        pf.add('warn', '未读取 EFI 分区（需要管理员权限）',
               '以管理员身份运行时这里会显示 ESP 盘符、可用空间和已有文件。',
               '关闭本程序，右键 exe → "以管理员身份运行"。')
    else:
        _check_esp(pf, log, esp_letter)

    # 6) 播放器文件
    pf.player_efi = player_efi_path()
    if pf.player_efi:
        size = os.path.getsize(pf.player_efi)
        pf.add('ok', '播放器已就绪：%s（%d 字节）' % (os.path.basename(pf.player_efi), size))
    else:
        pf.add('error', '找不到播放器文件 BootAnimation.efi',
               '安装包不完整（assets 缺失）。', '重新下载完整压缩包，或用源码构建。')

    # 7) 现有启动项
    if pf.admin:
        try:
            pf.store = bcd.read_store(logger=log)
            pf.agent = pf.store.agent_entry()
            if pf.agent:
                state = ('已设为默认（重启就会看到动画）' if pf.store.agent_is_default()
                         else ('仅下一次启动生效（试用中）' if pf.store.agent_is_pending()
                               else '已安装但未激活'))
                pf.add('warn', '检测到已安装的动画启动项 %s' % pf.agent.guid,
                       '状态：%s' % state,
                       '直接再次安装会用新动画覆盖它；想先撤掉请用"回滚"。')
            else:
                pf.add('ok', '当前没有安装过动画启动项（首次安装）')
        except bcd.BcdError as e:
            pf.add('warn', '无法读取固件启动项', str(e),
                   '安装前会再次尝试；若持续失败，请确认杀毒软件没有拦截 bcdedit。')
    return pf


# --------------------------------------------------------------------- #
# 安装（备份 → 写文件 → 建条目 → 试用）                                 #
# --------------------------------------------------------------------- #

class InstallResult(object):
    def __init__(self):
        self.guid = ''
        self.backup_dir = ''
        self.esp_backup = ''      # 已有 BootAnim 文件的备份目录（可能为空）
        self.bcd_backup = ''      # BCD 导出文件
        self.files = []           # [(dst, sha256, bytes)]
        self.steps = []           # [(step, ok, detail)]
        self.reused_entry = False

    def step(self, name, ok=True, detail=''):
        self.steps.append((name, ok, detail))
        return ok

    def as_dict(self):
        return dict(guid=self.guid, backup_dir=self.backup_dir,
                    esp_backup=self.esp_backup, bcd_backup=self.bcd_backup,
                    files=[dict(path=p, sha256=s, size=n) for p, s, n in self.files],
                    steps=[dict(step=s, ok=o, detail=d) for s, o, d in self.steps],
                    reused_entry=self.reused_entry)


def install(pf, anim_dir, logger=None, trial=True, keep_backup=True):
    """把 anim_dir 里的 anim.bin/config.ini + 播放器写入 ESP 并挂上试用启动项。

    anim_dir: 打包输出目录（含 anim.bin、config.ini）。
    返回 InstallResult。任何一步失败都会抛异常，由上层提示（此时 ESP 可能已写入，
    但启动项尚未挂上 → 不会改变开机行为）。
    """
    log = logger or (lambda s: None)
    if not pf.ok:
        raise DeployError('体检未通过，不能安装：%s'
                          % '；'.join(c.title for c in pf.errors))
    if pf.esp is None:
        raise DeployError('缺少 ESP 信息，请重新体检。')

    anim_src = os.path.join(anim_dir, ANIM_NAME)
    cfg_src = os.path.join(anim_dir, CONFIG_NAME)
    if not os.path.isfile(anim_src):
        raise DeployError('找不到要安装的动画文件：%s' % anim_src)
    if not os.path.isfile(cfg_src):
        raise DeployError('找不到配置文件：%s' % cfg_src)
    if not pf.player_efi or not os.path.isfile(pf.player_efi):
        raise DeployError('找不到播放器 BootAnimation.efi（安装包不完整）。')

    anim_mb = os.path.getsize(anim_src) / 1048576.0
    need = anim_mb + esp.MIN_FREE_MB
    free = pf.esp.refresh_free() or 0.0
    if free < need:
        raise DeployError('ESP 空间不足：需要 %.1f MB（动画 %.1f + 余量 %.0f），'
                          '实际可用 %.1f MB。' % (need, anim_mb, esp.MIN_FREE_MB, free))

    res = InstallResult()
    res.backup_dir = paths.new_backup_dir()

    # ---- 1) 备份 ESP 上已有文件 ----
    res.esp_backup = esp.backup_existing(pf.esp, logger=log,
                                         backup_root=res.backup_dir) or ''
    res.step('备份 ESP 已有文件', True,
             res.esp_backup or '此前没有装过，无需备份')

    # ---- 2) 备份 BCD ----
    res.bcd_backup = os.path.join(res.backup_dir, 'BCD.export')
    bcd.export_backup(res.bcd_backup, logger=log)
    res.step('导出 BCD 备份', True, res.bcd_backup)

    # ---- 3) 写文件 ----
    tgt = esp.ensure_dir(pf.esp, logger=log)
    for src, dst in ((pf.player_efi, tgt['efi']),
                     (anim_src, tgt['anim']),
                     (cfg_src, tgt['config'])):
        sha, _ = esp.copy_verified(src, dst, logger=log)
        res.files.append((dst, sha, os.path.getsize(dst)))
    res.step('写入播放器与动画文件', True,
             '%d 个文件，共 %.2f MB' % (len(res.files),
                                       sum(n for _, _, n in res.files) / 1048576.0))

    # ---- 4) 启动项 ----
    st = bcd.read_store(logger=log)
    entry = st.agent_entry()
    if entry:
        res.guid = entry.guid
        res.reused_entry = True
        bcd.set_description(res.guid, BCD_DESCRIPTION, logger=log)
        log('[bcd] 复用已有启动项 %s' % res.guid)
    else:
        res.guid = bcd.create_agent_entry(logger=log)
    bcd.set_agent_path(res.guid, pf.esp.mounted_letter or pf.esp.letter, logger=log)
    res.step('创建/更新启动项', True, res.guid)

    # ---- 5) 试用：只下一次生效 ----
    if trial:
        bcd.clear_bootsequence(logger=log)
        bcd.set_trial_boot(res.guid, logger=log)
        res.step('设置"下一次启动试用"', True,
                 '重启一次即可看到动画；若失败固件会自己回到 Windows')
        save_state(guid=res.guid, stage='trial', esp=pf.esp.describe(),
                   backup=res.backup_dir, anim_dir=anim_dir)
    if not keep_backup:
        res.backup_dir = ''
    return res


# --------------------------------------------------------------------- #
# 固化 / 回滚                                                            #
# --------------------------------------------------------------------- #

def activate(guid, logger=None, esp_obj=None):
    """固化为默认启动项（以后每次开机都播放）。"""
    log = logger or (lambda s: None)
    st = bcd.read_store(logger=log)
    e = st.by_guid(guid) or st.agent_entry()
    if not e:
        raise DeployError('启动项 %s 不存在，无法固化。' % guid)
    bcd.clear_bootsequence(logger=log)
    bcd.set_default(e.guid, logger=log)
    save_state(guid=e.guid, stage='active')
    log('[bcd] 已设为默认启动项：%s' % e.guid)
    return e.guid


def rollback(guid='', logger=None, remove_files=False, esp_obj=None,
             restore_from=''):
    """撤销动画：恢复原启动顺序；可选还原/删除 ESP 文件。

    安全顺序：先摘掉启动项（立刻恢复开机行为），再动文件。
    """
    log = logger or (lambda s: None)
    done = []
    st = None
    try:
        st = bcd.read_store(logger=log)
    except bcd.BcdError as e:
        log('[bcd] 读取启动项失败：%s' % e)

    g = guid or (st.agent_entry().guid if st and st.agent_entry() else '')
    if st and st.bootsequence:
        bcd.clear_bootsequence(logger=log)
        done.append('清除"下一次启动"设置')
    if g:
        bcd.delete_entry(g, logger=log)
        done.append('删除启动项 %s' % g)
    if st and g and g.upper() in [x.upper() for x in st.displayorder]:
        bcd.remove_from_displayorder(g, logger=log)
        done.append('从启动顺序中移除')

    if remove_files and esp_obj:
        rm = esp.remove_agent_files(esp_obj, logger=log)
        if rm:
            done.append('删除 ESP 文件 %s' % '、'.join(rm))
    elif restore_from and esp_obj:
        files = esp.restore_backup(esp_obj, restore_from, logger=log)
        if files:
            done.append('还原 ESP 文件 %s' % '、'.join(files))

    save_state(guid='', stage='rolledback')
    log('[deploy] 回滚完成：%s' % ('；'.join(done) or '没有需要撤销的内容'))
    return done


def status(logger=None):
    """当前安装状态（GUI 首页显示）。"""
    st = {}
    try:
        store = bcd.read_store(logger=logger)
        agent = store.agent_entry()
        st.update(installed=bool(agent), guid=agent.guid if agent else '',
                  default=store.agent_is_default(),
                  pending=store.agent_is_pending(),
                  displayorder=store.displayorder)
    except bcd.BcdError as e:
        st.update(installed=None, error=str(e))
    st['state'] = load_state()
    try:
        e = esp.pick_windows_esp(logger=None)
        tgt = esp.esp_target_paths(e)
        st['esp'] = e.describe()
        st['esp_files'] = {k: os.path.isfile(tgt[k]) for k in ('efi', 'anim', 'config')}
        st['anim_mb'] = (os.path.getsize(tgt['anim']) / 1048576.0
                         if os.path.isfile(tgt['anim']) else 0.0)
        e.unmount(logger=None)
    except esp.EspError as e:
        st['esp'] = ''
        st['esp_error'] = str(e)
    return st


# --------------------------------------------------------------------- #
# 一键修复：开机看不到动画时的自诊                                       #
# --------------------------------------------------------------------- #

def diagnose(logger=None):
    """返回 [(level, text)]：常见"装了但看不到动画"的原因。"""
    log = logger or (lambda s: None)
    out = []
    pf = preflight(logger=log)
    for c in pf.checks:
        out.append((c.level, c.title + ('：' + c.detail if c.detail else '')))
    if pf.store:
        if pf.agent and pf.store.agent_is_default():
            out.append(('ok', '启动项已是默认，重启应当先播动画'))
        elif pf.agent and pf.store.agent_is_pending():
            out.append(('warn', '启动项只对"下一次启动"生效，若已重启过一次就会自动失效'))
        elif pf.agent:
            out.append(('warn', '启动项已创建但未激活（未设为默认，也不在下一次启动）'))
        else:
            out.append(('error', '没有找到动画启动项'))
    if pf.esp:
        tgt = esp.esp_target_paths(pf.esp)
        for key, name in (('efi', EFI_NAME), ('anim', ANIM_NAME), ('config', CONFIG_NAME)):
            if os.path.isfile(tgt[key]):
                out.append(('ok', 'ESP 上有 %s（%d 字节）'
                            % (name, os.path.getsize(tgt[key]))))
            else:
                out.append(('error', 'ESP 上缺少 %s' % name))
        if os.path.isfile(tgt['anim']):
            mb = os.path.getsize(tgt['anim']) / 1048576.0
            if mb > 200:
                out.append(('warn', '动画 %.0f MB 偏大，固件读取可能超时' % mb))
    return out

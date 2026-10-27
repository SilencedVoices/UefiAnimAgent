# -*- coding: utf-8 -*-
"""安装/固化/回滚的离线仿真测试。

把 ESP 换成临时目录、把 bcdedit 换成内存假实现，完整跑一遍：
体检 → 备份 → 写文件 → 建启动项 → 试用 → 固化 → 回滚。
真实机器不会被修改（不调用任何 bcdedit 写命令、不碰真 ESP）。

运行：python -m wizard --sim-install
"""
import os
import shutil
import tempfile

from .. import bcd, deploy, esp, paths


class FakeEsp(object):
    def __init__(self, root):
        self._root = root
        self.letter = 'Q:'
        self.mounted_letter = 'Q:'
        self.mounted_by_us = False
        self.free_mb = 500.0

    def root(self):
        return self._root

    def describe(self):
        return '仿真 ESP  Q:  500MB 可用480MB'

    def refresh_free(self):
        return self.free_mb

    def unmount(self, logger=None):
        pass


class FakeEntry(object):
    def __init__(self, guid, description='', path=''):
        self.guid = guid
        self.description = description
        self.path = path

    def is_agent(self):
        return self.description == deploy.BCD_DESCRIPTION


class FakeStore(object):
    def __init__(self):
        self.entries = []
        self.displayorder = ['{bootmgr}']
        self.bootsequence = ''

    def agent_entry(self):
        for e in self.entries:
            if e.is_agent():
                return e
        return None

    def by_guid(self, guid):
        for e in self.entries:
            if e.guid.upper() == guid.upper():
                return e
        return None

    def agent_is_default(self):
        a = self.agent_entry()
        return bool(a and self.displayorder and
                    self.displayorder[0].upper() == a.guid.upper())

    def agent_is_pending(self):
        a = self.agent_entry()
        return bool(a and self.bootsequence and
                    self.bootsequence.upper() == a.guid.upper())


class FakeBcd(object):
    """记录所有 bcdedit 写操作，便于断言顺序。"""

    def __init__(self):
        self.store = FakeStore()
        self.calls = []
        self._n = 0

    # ---- 被 deploy 调用的接口 ----
    def read_store(self, logger=None):
        return self.store

    def export_backup(self, path, logger=None):
        with open(path, 'w', encoding='utf-8') as f:
            f.write('fake-bcd-export\n')
        self.calls.append(('export_backup', path))
        return path

    def create_agent_entry(self, logger=None):
        self._n += 1
        guid = '{11111111-2222-3333-4444-%012d}' % self._n
        # 真机上 bcdedit /copy {bootmgr} /d "..." 会带上 description
        e = FakeEntry(guid, deploy.BCD_DESCRIPTION)
        self.store.entries.append(e)
        self.calls.append(('create_agent_entry', guid))
        return guid

    def set_description(self, guid, text, logger=None):
        e = self.store.by_guid(guid)
        if e:
            e.description = text
        self.calls.append(('set_description', guid, text))

    def set_agent_path(self, guid, esp_letter, logger=None):
        e = self.store.by_guid(guid)
        if e:
            e.path = '\\EFI\\BootAnim\\BootAnimation.efi'
        self.calls.append(('set_agent_path', guid, esp_letter))

    def clear_bootsequence(self, logger=None):
        self.store.bootsequence = ''
        self.calls.append(('clear_bootsequence',))

    def set_trial_boot(self, guid, logger=None):
        self.store.bootsequence = guid
        self.calls.append(('set_trial_boot', guid))

    def set_default(self, guid, logger=None):
        rest = [x for x in self.store.displayorder if x.upper() != guid.upper()]
        self.store.displayorder = [guid] + rest
        self.calls.append(('set_default', guid))

    def remove_from_displayorder(self, guid, logger=None):
        self.store.displayorder = [x for x in self.store.displayorder
                                   if x.upper() != guid.upper()]
        self.calls.append(('remove_from_displayorder', guid))

    def delete_entry(self, guid, logger=None):
        self.store.entries = [e for e in self.store.entries
                              if e.guid.upper() != guid.upper()]
        self.calls.append(('delete_entry', guid))


def run(logger=None):
    """执行仿真，返回 [(ok, 名称, 说明)]。"""
    log = logger or (lambda s: None)
    results = []

    def rec(ok, name, detail=''):
        results.append((bool(ok), name, detail))
        log('  %-22s %s  %s' % (name, 'PASS' if ok else 'FAIL', detail))

    tmp = tempfile.mkdtemp(prefix='wiz_sim_')
    esp_root = os.path.join(tmp, 'esp')
    os.makedirs(os.path.join(esp_root, 'EFI', 'Microsoft', 'Boot'))
    os.makedirs(os.path.join(esp_root, 'EFI', 'BootAnim'))
    with open(os.path.join(esp_root, 'EFI', 'Microsoft', 'Boot', 'bootmgfw.efi'),
              'wb') as f:                       # 模拟 Windows 启动文件（内容无关）
        f.write(b'MZ' + b'\0' * 512)
    old_anim = os.path.join(esp_root, 'EFI', 'BootAnim', 'anim.bin')
    with open(old_anim, 'wb') as f:             # 模拟"已经装过旧动画"
        f.write(b'OLD')

    anim_dir = os.path.join(tmp, 'anim')
    os.makedirs(anim_dir)

    fake = FakeBcd()
    saved_bcd = {k: getattr(bcd, k, None) for k in dir(bcd)
                 if not k.startswith('_') and callable(getattr(bcd, k, None))}
    saved_esp = {k: getattr(esp, k, None) for k in
                 ('pick_windows_esp', 'esp_target_paths')}

    try:
        # ---- 打桩 ----
        for name in ('read_store', 'export_backup', 'create_agent_entry',
                     'set_description', 'set_agent_path', 'clear_bootsequence',
                     'set_trial_boot', 'set_default', 'remove_from_displayorder',
                     'delete_entry'):
            setattr(bcd, name, getattr(fake, name))
        setattr(esp, 'pick_windows_esp',
                lambda logger=None, allow_mount=True: FakeEsp(esp_root))

        # 生成一个真实的动画包作为待安装内容
        from .. import pack
        pack.make_demo_frames(anim_dir + '_frames', 320, 180, 6, logger=log)
        pack.pack(anim_dir + '_frames', anim_dir, (320, 180), 12, 'QOI', logger=log)
        rec(os.path.isfile(os.path.join(anim_dir, 'anim.bin')), 'pack.demo',
            os.path.getsize(os.path.join(anim_dir, 'anim.bin')))

        # ---- 构造 pf（跳过真机体检） ----
        pf = deploy.Preflight()
        pf.admin = True
        pf.uefi, pf.secure_boot, pf.bitlocker = True, False, False
        pf.resolution = (320, 180)
        pf.os_caption = 'sim'
        pf.esp = FakeEsp(esp_root)
        pf.agent = None
        pf.player_efi = deploy.player_efi_path()
        rec(bool(pf.player_efi) and os.path.isfile(pf.player_efi), 'player.efi',
            os.path.basename(pf.player_efi or ''))
        if not pf.player_efi:
            return results

        # ---- 安装（试用） ----
        res = deploy.install(pf, anim_dir, logger=log, trial=True)
        rec(bool(res.guid), 'install.guid', res.guid)
        rec(os.path.isfile(os.path.join(esp_root, 'EFI', 'BootAnim', 'BootAnimation.efi')),
            'install.efi_written')
        rec(os.path.isfile(os.path.join(esp_root, 'EFI', 'BootAnim', 'config.ini')),
            'install.config_written')
        rec(os.path.getsize(old_anim) != 3, 'install.anim_replaced',
            os.path.getsize(old_anim))
        rec(bool(res.esp_backup) and os.path.isfile(
            os.path.join(res.esp_backup, 'anim.bin')), 'install.backup_anim')
        rec(os.path.isfile(res.bcd_backup), 'install.backup_bcd')
        rec(fake.store.bootsequence.upper() == res.guid.upper(), 'install.trial_only',
            'bootsequence=%s' % fake.store.bootsequence)
        rec(not fake.store.agent_is_default(), 'install.not_default_yet',
            'displayorder[0]=%s' % (fake.store.displayorder[0] if fake.store.displayorder else ''))
        st = deploy.load_state()
        rec(st.get('stage') == 'trial', 'install.state', st.get('stage'))

        # ---- 再次安装：复用条目，不重复建 ----
        n_before = len([c for c in fake.calls if c[0] == 'create_agent_entry'])
        res2 = deploy.install(pf, anim_dir, logger=log, trial=True)
        n_after = len([c for c in fake.calls if c[0] == 'create_agent_entry'])
        rec(res2.guid == res.guid and n_after == n_before, 'install.reuse_entry',
            '%s -> %s' % (n_before, n_after))

        # ---- 固化 ----
        g = deploy.activate(res.guid, logger=log)
        rec(fake.store.agent_is_default(), 'activate.default', g)
        rec(not fake.store.bootsequence, 'activate.cleared_trial')

        # ---- 回滚（不删文件） ----
        done = deploy.rollback(logger=log, esp_obj=FakeEsp(esp_root))
        rec(fake.store.agent_entry() is None, 'rollback.entry_removed')
        rec(os.path.isfile(os.path.join(esp_root, 'EFI', 'BootAnim', 'BootAnimation.efi')),
            'rollback.files_kept')
        rec(deploy.load_state().get('stage') == 'rolledback', 'rollback.state',
            '；'.join(done)[:60])

        # ---- 回滚（删文件） ----
        res3 = deploy.install(pf, anim_dir, logger=log, trial=True)
        deploy.rollback(guid=res3.guid, logger=log, remove_files=True,
                        esp_obj=FakeEsp(esp_root))
        rec(not os.path.isdir(os.path.join(esp_root, 'EFI', 'BootAnim')) or
            not os.listdir(os.path.join(esp_root, 'EFI', 'BootAnim')),
            'rollback.files_removed')
    finally:
        for k, v in saved_bcd.items():
            if v is not None:
                setattr(bcd, k, v)
        for k, v in saved_esp.items():
            if v is not None:
                setattr(esp, k, v)
        deploy.clear_state()
        shutil.rmtree(tmp, ignore_errors=True)
    return results

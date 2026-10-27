# -*- coding: utf-8 -*-
"""wizard.cli — 命令行入口（也供 exe 的 --selftest / --doctor 使用）。

GUI 版 exe 是 windowed（没有控制台），所以这里先用 AttachConsole 挂到
调用它的控制台；挂不上就把输出写日志并在末尾 MessageBox 提示。
"""
import os
import sys
import time
import traceback

from . import APP_NAME, APP_TITLE, __version__
from . import bcd, deploy, esp, pack, paths, sysinfo


# --------------------------------------------------------------------- #
# 控制台                                                               #
# --------------------------------------------------------------------- #

def attach_console():
    if os.name != 'nt':
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace',
                              buffering=1)
            sys.stderr = sys.stdout
    except Exception:
        pass


def _p(line=''):
    try:
        print(line)
        sys.stdout.flush()
    except Exception:
        pass


def _report_path(hint):
    return paths.new_log_path(hint)


# --------------------------------------------------------------------- #
# 自检                                                                   #
# --------------------------------------------------------------------- #

def cmd_selftest(argv):
    """无副作用自检：打包、BCD 解析、环境读取。不写 ESP、不改 BCD。"""
    t0 = time.time()
    results = []
    lines = []

    def rec(name, ok, detail=''):
        results.append((name, ok, detail))
        lines.append('  %-22s %s%s' % (name, 'PASS' if ok else 'FAIL',
                                       ('  ' + detail) if detail else ''))
        _p(lines[-1])

    _p('%s %s 自检开始' % (APP_NAME, __version__))

    # 1) 打包 + 容器格式
    try:
        r = pack.self_test()
        for k, v in r.items():
            rec('pack.' + k, bool(v))
    except Exception as e:
        rec('pack', False, str(e)[:120])

    # 2) BCD 解析（中文固件输出 fixture）
    try:
        fx = paths.bundle_path('tests', 'fixtures', 'bcd_firmware_zh.txt')
        text = open(fx, encoding='utf-8-sig').read()
        st = bcd.parse_enum(text)
        rec('bcd.parse.entries', len(st.entries) == 7, '%d 条' % len(st.entries))
        rec('bcd.parse.displayorder', len(st.displayorder) == 5,
            '%d 项' % len(st.displayorder))
        rec('bcd.parse.timeout', st.timeout == 2, str(st.timeout))
        rec('bcd.parse.agent', bool(st.agent_entry()) and st.agent_is_default())
    except Exception as e:
        rec('bcd.parse', False, str(e)[:120])

    # 3) 环境读取
    try:
        rec('env.admin', True, '是' if sysinfo.is_admin() else '否（自检不要求）')
        is_uefi, sb, note = sysinfo.secure_boot()
        rec('env.uefi', is_uefi is not None, 'UEFI=%s SB=%s %s'
            % (is_uefi, sb, note[:40]))
        res = sysinfo.display_resolution()
        rec('env.resolution', bool(res), str(res))
        rec('env.os', bool(sysinfo.os_caption()), sysinfo.os_caption()[:48])
    except Exception as e:
        rec('env', False, str(e)[:120])

    # 4) ESP 枚举（只读）
    try:
        esps = esp.discover()
        rec('esp.discover', True, '%d 个候选' % len(esps))
    except Exception as e:
        rec('esp.discover', False, str(e)[:120])

    # 5) 中英文案完整性（缺 key 的界面会显示成 key 本身）
    try:
        import wizard.i18n as i18n_mod
        miss = i18n_mod.missing_keys()
        rec('i18n.parity', not miss,
            '%d 条文案，缺 %s' % (i18n_mod.key_count(),
                                  ('、'.join(miss[:6]) if miss else '无')))
    except Exception as e:
        rec('i18n.parity', False, str(e)[:120])

    ok = all(x[1] for x in results)
    rp = _report_path('selftest')
    with open(rp, 'w', encoding='utf-8') as f:
        f.write('%s %s 自检报告  %s\n\n' % (APP_NAME, __version__,
                                          time.strftime('%Y-%m-%d %H:%M:%S')))
        f.write('\n'.join(lines) + '\n\n')
        f.write('总体: %s   耗时 %.1f 秒\n' % ('PASS' if ok else 'FAIL',
                                             time.time() - t0))
    _p('\n总体: %s（%.1f 秒）  报告: %s' % ('PASS' if ok else 'FAIL',
                                          time.time() - t0, rp))
    return 0 if ok else 1


# --------------------------------------------------------------------- #
# 体检 / 状态 / 诊断                                                     #
# --------------------------------------------------------------------- #

def _logf(s):
    """体检/安装时的日志：隐藏原始命令行，保留结果行。"""
    s = str(s)
    if s.startswith('$ ') or s.startswith('  ! ') or not s.strip():
        return
    _p('  ' + s)


def _print_checks(checks):
    icons = {'ok': '[ OK ]', 'warn': '[WARN]', 'error': '[FAIL]'}
    for c in checks:
        _p('%s %s' % (icons.get(c.level, '[????]'), c.title))
        if c.detail:
            _p('       %s' % c.detail)
        if c.fix:
            for ln in c.fix.splitlines():
                _p('       → %s' % ln)


def cmd_doctor(argv):
    _p('%s %s 体检（只读，不修改任何东西）'
       % (APP_NAME, __version__))
    pf = deploy.preflight(logger=_logf)
    _p('')
    _print_checks(pf.checks)
    _p('')
    if pf.ok:
        _p('结论：可以安装（%d 条提醒）' % len(pf.warnings))
        return 0
    _p('结论：有 %d 项必须处理，暂不能安装' % len(pf.errors))
    return 2


def cmd_status(argv):
    st = deploy.status()
    if st.get('installed') is None:
        _p('安装状态：无法读取（%s）' % (st.get('error') or '需要管理员权限'))
    else:
        _p('安装状态：%s' % ('已安装' if st.get('installed') else '未安装'))
    if st.get('guid'):
        _p('  启动项：%s' % st['guid'])
        _p('  默认启动：%s   仅下一次：%s' % (st.get('default'), st.get('pending')))
    if st.get('esp'):
        _p('  ESP：%s' % st['esp'])
        for k, v in (st.get('esp_files') or {}).items():
            _p('    文件 %-8s %s' % (k, '存在' if v else '缺失'))
        if st.get('anim_mb'):
            _p('    anim.bin：%.2f MB' % st['anim_mb'])
    if st.get('state'):
        _p('  本地状态：%s' % st['state'])
    return 0


def cmd_diagnose(argv):
    _p('%s 自诊：为什么开机没看到动画？' % APP_NAME)
    rows = deploy.diagnose(logger=_logf)
    icons = {'ok': '[ OK ]', 'warn': '[WARN]', 'error': '[FAIL]'}
    for lvl, text in rows:
        _p('%s %s' % (icons.get(lvl, '[????]'), text))
    return 0


# --------------------------------------------------------------------- #
# 安装 / 固化 / 回滚                                                     #
# --------------------------------------------------------------------- #

def _arg(argv, name, default=''):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    for a in argv:
        if a.startswith(name + '='):
            return a.split('=', 1)[1]
    return default


def cmd_install(argv):
    anim_dir = _arg(argv, '--dir', os.path.join(paths.data_dir(), 'anim'))
    trial = '--activate' not in argv
    import_dir = anim_dir
    pf = deploy.preflight(logger=_logf)
    if not pf.ok:
        _print_checks(pf.errors)
        return 2
    _p('安装：%s → %s' % (import_dir, pf.esp.describe()))
    r = deploy.install(pf, import_dir, logger=_logf, trial=trial)
    _p('')
    for name, ok, detail in r.steps:
        _p('%s %s%s' % ('[ OK ]' if ok else '[FAIL]', name,
                        ('  ' + str(detail)) if detail else ''))
    _p('')
    _p('启动项 GUID：%s' % r.guid)
    _p('备份目录：%s' % r.backup_dir)
    if trial:
        _p('现在重启一次即可看到动画；失败固件会自动回到 Windows。')
        _p('看到动画后再运行：UefiAnimWizard.exe --activate %s' % r.guid)
    return 0


def cmd_activate(argv):
    guid = argv[1] if len(argv) > 1 and not argv[1].startswith('-') else ''
    g = deploy.activate(guid, logger=_logf)
    _p('[ OK ] 已固化：%s（以后每次开机都播放）' % g)
    _p('想撤销：UefiAnimWizard.exe --rollback %s' % g)
    return 0


def cmd_rollback(argv):
    guid = _arg(argv, '--guid')
    restore = _arg(argv, '--restore')
    remove_files = '--remove-files' in argv
    e = None
    try:
        e = esp.pick_windows_esp()
    except esp.EspError as ex:
        _p('  (ESP 不可用：%s)' % ex)
    done = deploy.rollback(guid, logger=_logf,
                           remove_files=remove_files, esp_obj=e,
                           restore_from=restore)
    for d in done:
        _p('[ OK ] %s' % d)
    _p('已恢复原本的开机流程（重启后直接进 Windows）。')
    return 0


def cmd_sim(argv):
    """离线仿真：临时目录当 ESP + 内存假 BCD，跑完整安装/固化/回滚。"""
    from .tests import sim_install
    rp = _report_path('sim_install')
    lines = ['安装/固化/回滚 离线仿真（不会修改本机任何设置）', '']
    results = sim_install.run(
        logger=lambda s: lines.append(s) if s.startswith('  ') else None)
    bad = [r for r in results if not r[0]]
    lines.append('')
    lines.append('总体: %s（%d 项，失败 %d）'
                 % ('PASS' if not bad else 'FAIL', len(results), len(bad)))
    for ok, name, detail in bad:
        lines.append('  FAIL %s  %s' % (name, detail))
    body = '\n'.join(lines)
    try:
        with open(rp, 'w', encoding='utf-8') as f:
            f.write(body + '\n')
    except OSError:
        pass
    _p(body)
    _p('')
    _p('报告：%s' % rp)
    return 0 if not bad else 1


def usage():
    _p('%s %s' % (APP_TITLE, __version__))
    _p('')
    _p('用法：UefiAnimWizard.exe [命令]')
    _p('')
    _p('不带参数        打开图形向导（推荐）')
    _p('--selftest      无副作用自检（打包/解析/环境），写报告')
    _p('--sim-install   离线仿真安装/固化/回滚（临时目录 + 假 BCD，不动本机）')
    _p('--doctor        只读体检：管理员/UEFI/SecureBoot/BitLocker/ESP')
    _p('--status        显示当前安装状态')
    _p('--diagnose      开机看不到动画时的自诊')
    _p('--install --dir <目录> [--activate]   命令行安装（默认试用一次）')
    _p('--activate <GUID>                     把已有启动项固化为默认')
    _p('--rollback [--guid <GUID>] [--remove-files] [--restore <备份目录>]')
    _p('--version')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    attach_console()
    if not argv:
        from . import gui
        return gui.run()
    cmd = argv[0].lower()
    try:
        if cmd in ('--version', '-v'):
            _p('%s %s' % (APP_NAME, __version__))
            return 0
        if cmd in ('--selftest', '-t'):
            return cmd_selftest(argv)
        if cmd == '--sim-install':
            return cmd_sim(argv)
        if cmd in ('--doctor', '-d'):
            return cmd_doctor(argv)
        if cmd == '--status':
            return cmd_status(argv)
        if cmd == '--diagnose':
            return cmd_diagnose(argv)
        if cmd == '--install':
            return cmd_install(argv)
        if cmd == '--activate':
            return cmd_activate(argv)
        if cmd == '--rollback':
            return cmd_rollback(argv)
        if cmd in ('-h', '--help', '/?'):
            return usage()
        _p('未知参数：%s' % cmd)
        return usage()
    except (bcd.BcdError, deploy.DeployError, esp.EspError, pack.PackError) as e:
        _p('')
        _p('[失败] %s' % e)
        return 2
    except Exception:
        _p('')
        _p('[异常] 未预期错误：')
        for ln in traceback.format_exc().strip().splitlines()[-6:]:
            _p('  ' + ln)
        return 3

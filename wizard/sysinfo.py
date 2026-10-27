# -*- coding: utf-8 -*-
"""wizard.sysinfo — 系统能力探测（只读）。

所有探测都设计成"永不抛异常"：返回 (值, 说明)，说明用于直接呈现给新手用户。
"""
import ctypes
import json
import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------- #
# 子进程执行                                                             #
# --------------------------------------------------------------------- #

def _decode(data):
    """bcdedit 等系统工具会输出 UTF-16（重定向时）；逐个编码尝试。"""
    if not data:
        return ''
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        try:
            return data.decode('utf-16')
        except UnicodeDecodeError:
            pass
    if data.count(b'\x00') > max(2, len(data) // 4):
        for enc in ('utf-16-le', 'utf-16-be'):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                pass
    # 顺序很重要：bcdedit 等系统工具在中文系统上按 ANSI(CP936) 输出，
    # 而 PowerShell 输出我们强制成 UTF-8。先试 UTF-8（严格），失败再退 ANSI。
    for enc in ('utf-8-sig', 'mbcs', 'gbk', 'latin-1'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('latin-1', 'replace')


def run(args, timeout=60, logger=None, check=False):
    """执行命令，返回 (returncode, stdout, stderr)，永不抛（超时除外）。"""
    if logger:
        logger('$ ' + ' '.join(args) if isinstance(args, (list, tuple)) else '$ ' + args)
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired:
        if logger:
            logger('[TIMEOUT] %ss' % timeout)
        return 124, '', 'timeout after %ss' % timeout
    except OSError as e:
        if logger:
            logger('[OSERROR] %s' % e)
        return 127, '', str(e)
    out, err = _decode(p.stdout), _decode(p.stderr)
    if logger:
        for line in (out or '').splitlines():
            logger('  ' + line)
        for line in (err or '').splitlines():
            logger('  ! ' + line)
    if check and p.returncode != 0:
        raise RuntimeError('%s failed (%d): %s' % (args, p.returncode, (err or out).strip()[:400]))
    return p.returncode, out, err


def run_ps(script, timeout=60, logger=None):
    """执行 PowerShell 片段，返回 (rc, stdout, stderr)。输出强制为 UTF-8。"""
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                       'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    if not os.path.exists(exe):
        exe = 'powershell.exe'
    wrapped = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
               "$OutputEncoding=[Text.Encoding]::UTF8;" + script)
    return run([exe, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-Command', wrapped], timeout=timeout, logger=logger)


def run_ps_json(script, timeout=60, logger=None):
    """PowerShell → JSON 对象；失败返回 None。"""
    rc, out, err = run_ps(script, timeout=timeout, logger=logger)
    if rc != 0 or not out.strip():
        return None
    txt = out.strip()
    start = min([i for i in (txt.find('['), txt.find('{')) if i >= 0] or [len(txt)])
    txt = txt[start:]
    try:
        return json.loads(txt)
    except ValueError:
        return None


# --------------------------------------------------------------------- #
# 权限                                                                   #
# --------------------------------------------------------------------- #

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(argv=None, logger=None):
    """用 UAC 重新拉起自己（源码运行时重启 python 进程）。返回是否已发出请求。"""
    argv = list(argv if argv is not None else sys.argv[1:])
    if getattr(sys, 'frozen', False):
        exe = sys.executable
        params = subprocess.list2cmdline(argv)
    else:
        exe = sys.executable
        params = subprocess.list2cmdline(['-m', 'wizard'] + argv)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, None, 1)
        return rc > 32
    except Exception as e:
        if logger:
            logger('[elevate] %s' % e)
        return False


# --------------------------------------------------------------------- #
# 固件 / 安全启动 / BitLocker / 显示                                    #
# --------------------------------------------------------------------- #

def _sb_from_registry():
    """非管理员读不到 Confirm-SecureBootUEFI，但注册表通常可读。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r'SYSTEM\CurrentControlSet\Control\SecureBoot\State')
        try:
            val, _ = winreg.QueryValueEx(key, 'UEFISecureBootEnabled')
        finally:
            winreg.CloseKey(key)
        return bool(val)
    except Exception:
        return None


def secure_boot():
    """返回 (is_uefi, sb_enabled, note)。

    Confirm-SecureBootUEFI 只在 UEFI 固件上存在 → 能调用即证明 UEFI；
    非管理员时该 cmdlet 会拒绝访问，此时退回注册表 + ESP 特征判断。
    """
    rc, out, err = run_ps(
        "try { 'SB=' + (Confirm-SecureBootUEFI) } catch { 'SBERR=' + $_.Exception.Message }")
    out = (out or '').strip()
    if out.startswith('SB='):
        val = out[3:].strip().lower()
        return True, (val == 'true'), 'Confirm-SecureBootUEFI=%s' % val
    note = out or (err or '').strip()
    reg = _sb_from_registry()
    if reg is not None:
        return True, reg, '注册表 UEFISecureBootEnabled=%d' % (1 if reg else 0)
    # 回退：GPT 系统盘 + EFI 目录存在 → 大概率 UEFI
    efi_dir = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'Boot', 'EFI')
    if os.path.isdir(efi_dir):
        return True, None, 'Secure Boot 状态不可读（%s），按 UEFI 处理' % note[:120]
    return False, None, '疑似 Legacy BIOS（%s）' % note[:120]


def bitlocker(system_drive='C:'):
    """返回 (enabled(bool|None), note)。None = 未知/非专业版无此功能。"""
    obj = run_ps_json(
        "try { @(Get-BitLockerVolume -ErrorAction Stop | Select-Object MountPoint,"
        "VolumeStatus,ProtectionStatus,EncryptionPercentage) | ConvertTo-Json -Compress } "
        "catch { '' }")
    if obj:
        items = obj if isinstance(obj, list) else [obj]
        target = None
        for it in items:
            if str(it.get('MountPoint', '')).upper().startswith(system_drive.upper()):
                target = it
                break
        if target:
            status = str(target.get('VolumeStatus') or '')
            prot = target.get('ProtectionStatus')
            on = (prot == 1) or ('FullyEncrypted' in status) or ('EncryptionInProgress' in status)
            return on, 'C: %s / ProtectionStatus=%s' % (status or '?', prot)
    # 回退：CIM 加密卷类
    obj = run_ps_json(
        "try { @(Get-CimInstance -Namespace root\\cimv2\\security\\microsoftvolumeencryption "
        "-ClassName Win32_EncryptableVolume -ErrorAction Stop | Select-Object DriveLetter,"
        "ProtectionStatus,ConversionStatus) | ConvertTo-Json -Compress } catch { '' }")
    if obj:
        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if str(it.get('DriveLetter', '')).upper().startswith(system_drive.upper()):
                prot = it.get('ProtectionStatus')
                conv = it.get('ConversionStatus')
                on = (prot == 1) or (conv == 1)
                return on, 'CIM: ProtectionStatus=%s ConversionStatus=%s' % (prot, conv)
    return None, 'BitLocker 状态不可读（家庭版或未启用）'


def display_resolution():
    """返回 (w, h) 或 None。取当前显示模式的最大值（多屏时偏保守）。"""
    obj = run_ps_json(
        "@(Get-CimInstance Win32_VideoController | Where-Object { $_.CurrentHorizontalResolution } | "
        "Select-Object CurrentHorizontalResolution,CurrentVerticalResolution) | ConvertTo-Json -Compress")
    if not obj:
        return None
    items = obj if isinstance(obj, list) else [obj]
    best = None
    for it in items:
        try:
            w = int(it.get('CurrentHorizontalResolution') or 0)
            h = int(it.get('CurrentVerticalResolution') or 0)
        except (TypeError, ValueError):
            continue
        if w > 0 and h > 0 and (best is None or w * h > best[0] * best[1]):
            best = (w, h)
    return best


def os_caption():
    obj = run_ps_json("(Get-CimInstance Win32_OperatingSystem | "
                      "Select-Object Caption,Version,BuildNumber) | ConvertTo-Json -Compress")
    if isinstance(obj, dict):
        return '%s (%s.%s)' % (obj.get('Caption', '?'), obj.get('Version', '?'),
                               obj.get('BuildNumber', '?'))
    return '未知'


def computer_name():
    return os.environ.get('COMPUTERNAME', '?')

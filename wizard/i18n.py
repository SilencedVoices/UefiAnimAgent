# -*- coding: utf-8 -*-
"""wizard.i18n — 极简双语层（中文 / English）。

设计取舍：
  * 不引入 gettext（PyInstaller 下 .mo 资源定位易踩坑），用一张表 + t(key)。
  * 语言只影响界面文案与提示；日志、命令、BCD 输出保持原样（便于排错）。
  * 缺 key 时回退到中文，再回退到 key 本身——绝不因为翻译缺失而崩。
"""

LANGS = (('zh', '简体中文'), ('en', 'English'))
DEFAULT_LANG = 'zh'

_lang = DEFAULT_LANG

# key: (中文, English)
STRINGS = {
    # ---------------- 通用 ----------------
    'app.title': ('UEFI 开机动画向导', 'UEFI Boot Animation Wizard'),
    'app.version': ('版本 %s', 'version %s'),
    'btn.logs': ('打开日志', 'Open log folder'),
    'btn.settings': ('设置', 'Settings'),
    'btn.notice': ('用户须知', 'Read me'),
    'btn.close': ('关闭', 'Close'),
    'btn.apply': ('应用', 'Apply'),
    'btn.back': ('< 上一步', '< Back'),
    'btn.next': ('下一步 >', 'Next >'),
    'btn.finish': ('完成', 'Finish'),
    'admin.ok': ('管理员权限 ✓', 'Administrator ✓'),
    'admin.no': ('非管理员（无法安装）', 'Not administrator (cannot install)'),

    # ---------------- 步骤条 ----------------
    'step.1': ('1 安全须知', '1 Safety'),
    'step.2': ('2 环境体检', '2 Preflight'),
    'step.3': ('3 制作动画', '3 Build'),
    'step.4': ('4 安装试用', '4 Install'),
    'step.5': ('5 完成', '5 Finish'),

    # ---------------- 第 1 步 ----------------
    'p1.head': ('先看这三条，能省掉 90% 的麻烦',
                'Read these three first — they prevent 90% of problems'),
    'p1.intro': (
        '本工具会做两件事：\n'
        '  ① 往你的 EFI 系统分区（ESP）里写 3 个文件：播放器、动画、配置；\n'
        '  ② 在你的固件启动项里新增一条"UEFI 动画启动项"，指向这个播放器。\n'
        '它不会格式化磁盘、不会动 Windows 自己的引导文件（\\EFI\\Microsoft）。\n',
        'This tool does two things:\n'
        '  1) writes 3 files into your EFI System Partition (ESP): player, animation, config;\n'
        '  2) adds a firmware boot entry ("UEFI Animation Agent") that points at the player.\n'
        'It never formats a disk and never touches the Windows loader (\\EFI\\Microsoft).\n'),
    'p1.box': (' 三条硬前提 ', ' Three hard requirements '),
    'p1.r1.t': ('1. 必须关闭 Secure Boot（安全启动）',
                '1. Secure Boot must be OFF'),
    'p1.r1.d': (
        '固件只加载微软签名的引导程序，我们的播放器没有签名。\n'
        '代价：关掉后 Windows 11 的部分功能（如某些反作弊）可能报错；装完想开回来，动画就看不到了。\n'
        '怎么关：重启 → 开机按 Del/F2/F12 进 BIOS → Security/Boot 里把 Secure Boot 设 Disabled。',
        'Firmware only loads Microsoft-signed loaders; our player is unsigned.\n'
        'Cost: after turning it off, some Windows 11 features (e.g. certain anti-cheat) may complain;\n'
        'turning it back on later makes the animation disappear.\n'
        'How: reboot and press Del/F2/F12 into BIOS, then set Secure Boot to Disabled.'),
    'p1.r2.t': ('2. 如果开了 BitLocker，先准备好恢复密钥',
                '2. If BitLocker is on, have the recovery key ready'),
    'p1.r2.d': (
        '改启动顺序有一定概率触发 BitLocker 恢复界面。没有 48 位恢复密钥就只能重装。\n'
        '稳妥做法：先在"管理 BitLocker"里"暂停保护"，装完确认能开机再恢复。',
        'Changing the boot order can occasionally trigger the BitLocker recovery screen.\n'
        'Without the 48-digit recovery key you would have to reinstall Windows.\n'
        'Safe move: "Suspend protection" in Manage BitLocker, then resume after the reboot test.'),
    'p1.r3.t': ('3. 万一开不了机，要会进 WinRE 撤销',
                '3. Know how to undo it from WinRE'),
    'p1.r3.d': (
        '动画启动项失败时会自动回到 Windows（本工具默认"只试用一次"）。\n'
        '真出问题：连续强制关机 3 次进入恢复环境 → 疑难解答 → 命令提示符，执行\n'
        '    bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
        '第 5 步和日志目录里都有完整的回滚命令，可直接复制。',
        'If the animation entry fails, firmware falls back to Windows (this tool only trials once by default).\n'
        'If it really breaks: force power-off 3 times to reach Recovery → Troubleshoot → Command Prompt, then run\n'
        '    bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
        'Step 5 and the log folder contain the full rollback commands, ready to copy.'),

    # ---------------- 第 2 步 ----------------
    'p2.head': ('环境体检（只读，不会修改任何东西）',
                'Preflight (read-only, changes nothing)'),
    'p2.recheck': ('重新体检', 'Re-run'),
    'p2.col.level': ('结果', 'Result'),
    'p2.col.title': ('检查项', 'Check'),
    'p2.col.detail': ('说明', 'Detail'),
    'p2.idle': ('点"重新体检"开始。', 'Click "Re-run" to start.'),
    'p2.running': ('体检中…（读取固件启动项、磁盘与安全设置，只读不改）',
                   'Checking… (reads firmware entries, disks and security settings; read-only)'),
    'p2.need.admin': ('需要管理员权限才能继续：', 'Administrator rights are required to continue:'),
    'p2.relaunch': ('以管理员身份重启本程序', 'Restart as administrator'),
    'p2.relaunch.fail.t': ('提示', 'Notice'),
    'p2.relaunch.fail.b': ('无法自动提权，请关闭本程序后右键 exe → 以管理员身份运行。',
                           'Cannot elevate automatically. Close this app, right-click the exe and choose '
                           '"Run as administrator".'),
    'p2.mark.ok': ('通过', 'OK'),
    'p2.mark.warn': ('注意', 'Note'),
    'p2.mark.error': ('阻塞', 'Blocked'),
    'p2.nofix': ('没有需要处理的阻塞项。', 'Nothing blocking.'),
    'p2.pass': ('体检通过：%d 项通过，%d 条提醒。可以进入下一步。',
                'Passed: %d checks OK, %d notes. You may continue.'),
    'p2.fail': ('有 %d 项必须先处理，处理完点"重新体检"。',
                '%d item(s) must be fixed first, then click "Re-run".'),

    # ---------------- 第 3 步 ----------------
    'p3.head': ('制作动画（素材 → anim.bin）', 'Build the animation (frames → anim.bin)'),
    'p3.tip': ('支持：图片文件夹（PNG/BMP/JPG）、GIF/APNG 动图、视频（需 ffmpeg）、'
               '内置演示动画。动画会被等比缩放并补黑边，不会变形。',
               'Supports: image folder (PNG/BMP/JPG), GIF/APNG, video (needs ffmpeg) and a built-in demo. '
               'Frames are scaled proportionally with letterboxing — never stretched.'),
    'p3.src': ('素材来源', 'Source'),
    'p3.pickdir': ('选文件夹…', 'Folder…'),
    'p3.pickfile': ('选文件…', 'File…'),
    'p3.demo': ('用演示动画', 'Use demo'),
    'p3.out': ('输出目录', 'Output folder'),
    'p3.browse': ('浏览…', 'Browse…'),
    'p3.res': ('分辨率', 'Resolution'),
    'p3.fps': ('帧率', 'Frame rate'),
    'p3.fmt': ('格式', 'Format'),
    'p3.start': ('开始制作', 'Build'),
    'p3.busy': ('制作中…', 'Building…'),
    'p3.idle': ('还没有制作。选好素材后点"开始制作"。',
                'Nothing built yet. Pick a source, then click "Build".'),
    'p3.preview': (' 预览（首帧） ', ' Preview (first frame) '),
    'p3.preview.empty': ('（制作完成后这里显示首帧缩略图）',
                         '(the first frame appears here after building)'),
    'p3.preview.fail': ('（预览生成失败，不影响安装）', '(preview failed — install is unaffected)'),
    'p3.progress': ('制作中：%d/%d 帧', 'Building: %d/%d frames'),
    'p3.done': ('完成：%d 帧，%dx%d @%dfps，%s，%.2f MB，用时 %.1f 秒。\n保存在：%s',
                'Done: %d frames, %dx%d @%dfps, %s, %.2f MB in %.1fs.\nSaved to: %s'),
    'p3.demo.making': ('正在生成演示动画帧…', 'Generating demo frames…'),
    'p3.demo.ready': ('演示帧已生成：%s\n现在可以点"开始制作"。',
                      'Demo frames ready: %s\nNow click "Build".'),
    'p3.need.src.t': ('缺少素材', 'No source'),
    'p3.need.src.b': ('请先选择素材（文件夹/文件），或点"用演示动画"。',
                      'Pick a source (folder or file) first, or click "Use demo".'),
    'p3.filetypes': ('图片/动图/视频', 'Images / animation / video'),
    'p3.allfiles': ('所有文件', 'All files'),
    'p3.done.short': ('上一轮已生成动画，位于：%s', 'Animation built earlier at: %s'),

    # 下拉选项
    'res.auto': ('自动（按当前分辨率）', 'Automatic (current resolution)'),
    'res.1080': ('1920x1080（1080p）', '1920x1080 (1080p)'),
    'res.2k': ('2560x1440（2K）', '2560x1440 (1440p)'),
    'res.4k': ('3840x2160（4K）', '3840x2160 (4K)'),
    'res.720': ('1280x720（720p，体积最小）', '1280x720 (720p, smallest)'),
    'fmt.qoi': ('QOI（推荐，体积小）', 'QOI (recommended, small)'),
    'fmt.bmp': ('BMP（无损，仅限小动画）', 'BMP (lossless, small clips only)'),

    # ---------------- 第 4 步 ----------------
    'p4.head': ('安装（默认"只试用一次"）', 'Install (trial once by default)'),
    'p4.intro': ('安装会做这些事（每一步都有备份，可一键回滚）：',
                 'Installation does this (everything is backed up and can be rolled back):'),
    'p4.plan': (' 将要发生的变化 ', ' What will change '),
    'p4.l1': ('1. 备份 ESP 上已有的 \\EFI\\BootAnim 文件，并导出 BCD 到本地备份目录；',
              '1. Back up any existing \\EFI\\BootAnim files and export BCD to a local backup folder;'),
    'p4.l2': ('2. 写入 3 个文件到 ESP：BootAnimation.efi、anim.bin、config.ini；',
              '2. Write 3 files to the ESP: BootAnimation.efi, anim.bin, config.ini;'),
    'p4.l3': ('3. 在固件启动项里新增一条（或更新已有）"UEFI Animation Agent"条目；',
              '3. Add (or update) a firmware boot entry named "UEFI Animation Agent";'),
    'p4.l4': ('4. 设置"只下一次启动生效"——重启一次看效果，失败会自动回到 Windows。',
              '4. Make it apply to the next boot only — reboot to test; on failure firmware returns to Windows.'),
    'p4.need.pack': ('（先完成第 3 步制作动画）', '(finish step 3 first)'),
    'p4.agree': ('我已阅读用户须知与安全须知，理解需要关闭 Secure Boot，并已完成备份/暂停 BitLocker',
                 'I have read the notice and safety rules, understand Secure Boot must be off, '
                 'and have backed up / suspended BitLocker'),
    'p4.install': ('安装并试用一次', 'Install and trial once'),
    'p4.install.busy': ('安装中…', 'Installing…'),
    'p4.demo.install': ('以演示动画安装（不选素材）', 'Install with demo (no source needed)'),
    'p4.demo.ready': ('演示动画已生成（%.2f MB）。点"安装并试用一次"继续。',
                      'Demo animation built (%.2f MB). Click "Install and trial once" to continue.'),
    'p4.installing': ('安装中，请稍候…', 'Installing, please wait…'),
    'p4.done': ('安装完成！启动项 GUID：%s\n备份目录：%s\n\n'
                '现在请重启电脑：应当先看到动画，然后自动进入 Windows。\n'
                '看到动画后回到本程序，到第 5 步点"设为默认（固化）"。',
                'Installed. Boot entry GUID: %s\nBackup folder: %s\n\n'
                'Now reboot: you should see the animation, then Windows starts automatically.\n'
                'After that, come back and use step 5 to make it permanent.'),
    'p4.warn.agree.t': ('请先确认', 'Please confirm'),
    'p4.warn.agree.b': ('请先勾选"我已阅读用户须知与安全须知…"。',
                        'Please tick "I have read the notice and safety rules…" first.'),
    'p4.warn.anim.t': ('没有动画', 'No animation'),
    'p4.warn.anim.b': ('请先在第 3 步制作动画，或点"以演示动画安装"。',
                       'Build an animation in step 3 first, or click "Install with demo".'),
    'p4.warn.check.t': ('体检未通过', 'Preflight failed'),
    'p4.warn.check.b': ('请先在"环境体检"里让所有阻塞项变绿。',
                        'Fix all blocked items in "Preflight" first.'),
    'p4.confirm.t': ('确认安装', 'Confirm install'),
    'p4.confirm.b': ('即将写入 EFI 分区并新增启动项。\n\nESP：%s\n动画目录：%s\n\n'
                     '现在重启一次就会看到动画；如果看不到，固件会自动回到 Windows。\n\n继续？',
                     'About to write to the EFI partition and add a boot entry.\n\nESP: %s\nAnimation: %s\n\n'
                     'After one reboot you will see the animation; if it fails, firmware returns to Windows.\n\n'
                     'Continue?'),

    # ---------------- 第 5 步 ----------------
    'p5.head': ('固化或回滚', 'Make permanent or roll back'),
    'p5.reading': ('正在读取状态…', 'Reading state…'),
    'p5.activate': ('设为默认（每次开机都播放）', 'Make default (play on every boot)'),
    'p5.rollback': ('回滚（恢复原开机流程）', 'Roll back (restore original boot)'),
    'p5.refresh': ('刷新状态', 'Refresh'),
    'p5.trouble': (' 下次开机看不到动画？ ', " Didn't see the animation? "),
    'p5.trouble.body': (
        '1) 先点上方"刷新状态"，再点"回滚"，一切都会回到装之前；\n'
        '2) 仍进不了系统：连续强制关机 3 次 → 疑难解答 → 命令提示符，执行\n'
        '     bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
        '   （把第一条启动项换回 Windows 引导器）\n'
        '3) 详细命令和备份位置见日志目录里的 rollback 说明，或点底部"打开日志"。',
        '1) Click "Refresh" then "Roll back" — everything returns to the pre-install state;\n'
        '2) Still cannot boot: force power-off 3 times → Troubleshoot → Command Prompt, then run\n'
        '     bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'
        '   (puts the Windows loader back to first place)\n'
        '3) Detailed commands and backup locations are in the log folder — see "Open log folder".'),
    'p5.st.default': ('当前状态：已安装并设为默认（%s）—— 每次开机都会播放动画。',
                      'Status: installed and default (%s) — plays on every boot.'),
    'p5.st.pending': ('当前状态：已安装，仅"下一次启动"生效（%s）—— 重启看效果。',
                      'Status: installed, applies to the next boot only (%s) — reboot to test.'),
    'p5.st.inactive': ('当前状态：已安装但未激活（%s）—— 需要设为默认或重新试用。',
                       'Status: installed but inactive (%s) — make it default or trial again.'),
    'p5.st.unknown': ('无法读取启动项状态：%s', 'Cannot read boot entry state: %s'),
    'p5.st.none': ('当前状态：未安装（开机直接进 Windows）。',
                   'Status: not installed (boots straight into Windows).'),
    'p5.esp': ('ESP：%s', 'ESP: %s'),
    'p5.file.exists': ('存在', 'present'),
    'p5.file.missing': ('缺失', 'missing'),
    'p5.order': ('固件启动顺序：', 'Firmware boot order:'),
    'p5.confirm.act.t': ('确认固化', 'Confirm'),
    'p5.confirm.act.b': ('固化后每次开机都会先播放动画（约几秒）再进 Windows。\n\n'
                         '如果动画有问题，随时可以用"回滚"恢复。继续？',
                         'Once permanent, every boot plays the animation (a few seconds) before Windows.\n\n'
                         'You can always roll back. Continue?'),
    'p5.act.ok.t': ('已固化', 'Done'),
    'p5.act.ok.b': ('已设为默认启动项：%s\n以后每次开机都会播放动画。',
                    'Set as the default boot entry: %s\nEvery boot will play the animation.'),
    'p5.act.err.t': ('固化失败', 'Failed'),
    'p5.confirm.rb.t': ('确认回滚', 'Confirm rollback'),
    'p5.confirm.rb.b': ('将删除动画启动项、恢复原启动顺序，并删除 ESP 上的动画文件。\n'
                        '（如果之前备份过旧动画，会先还原备份。）\n\n继续？',
                        'This deletes the animation boot entry, restores the original boot order and removes '
                        'the animation files from the ESP.\n(If an older animation was backed up, it is restored '
                        'first.)\n\nContinue?'),
    'p5.rb.ok.t': ('已回滚', 'Rolled back'),
    'p5.rb.ok.b': ('回滚完成：\n%s\n\n重启后应当直接进入 Windows。',
                   'Rollback finished:\n%s\n\nA reboot should now go straight into Windows.'),
    'p5.rb.err.t': ('回滚失败', 'Rollback failed'),
    'p5.nochange': ('无变化', 'nothing changed'),

    # 首页横幅（检测到已安装）
    'banner.default': ('检测到：已安装并设为默认（%s）。想撤掉请到第 5 步"回滚"。',
                       'Detected: installed and set as default (%s). Use step 5 to roll back.'),
    'banner.pending': ('检测到：上次设置了"试用一次"（%s）。重启看效果，或到第 5 步回滚。',
                       'Detected: a one-time trial is pending (%s). Reboot to see it, or roll back in step 5.'),
    'banner.inactive': ('检测到：已安装但未激活（%s）。到第 5 步可固化或回滚。',
                        'Detected: installed but inactive (%s). Step 5 can make it permanent or roll back.'),

    # ---------------- 导航提示 ----------------
    'hint.checking': ('正在体检…', 'Checking…'),
    'hint.block': ('先处理体检里的阻塞项', 'Fix the blocked items in Preflight first'),
    'hint.pack': ('先制作动画（或选"以演示动画安装"）', 'Build an animation first (or use the demo install)'),
    'hint.install': ('点"安装并试用一次"完成后会自动进入下一步',
                     'Click "Install and trial once" — the wizard advances when done'),

    # ---------------- 错误标题 ----------------
    'err.pack.user': ('制作失败', 'Build failed'),
    'err.pack': ('制作异常', 'Build error'),
    'err.install.user': ('安装失败', 'Install failed'),
    'err.install': ('安装异常', 'Install error'),
    'err.check': ('体检异常', 'Preflight error'),
    'err.status': ('状态读取异常', 'Status error'),

    # ---------------- 设置对话框 ----------------
    'set.title': ('设置', 'Settings'),
    'set.lang': ('界面语言', 'Language'),
    'set.font': ('界面字体', 'Font'),
    'set.font.auto': ('自动（跟随系统）', 'Automatic (system default)'),
    'set.size': ('字号', 'Font size'),
    'set.note': ('更改后会立即重建界面；已完成的体检、动画与安装状态不会丢失。',
                 'Applying rebuilds the UI immediately; preflight, animation and install state are kept.'),
    'set.rebuild': ('已应用，界面已切换。', 'Applied — the UI has been rebuilt.'),
    'set.busy': ('请等当前任务完成后再改设置。', 'Wait for the running task to finish first.'),

    # ---------------- 用户须知 ----------------
    'notice.title': ('用户须知', 'Read me first'),
    'notice.version': ('须知版本 1', 'notice v1'),
    'notice.body': (
        '一、这是什么\n'
        '  一个在你电脑开机时（进入 Windows 之前）播放一小段动画的小工具。\n'
        '  它会往 EFI 系统分区写入 3 个小文件，并新增一条固件启动项。\n\n'
        '二、你必须自己做的三件事\n'
        '  1) 关闭 Secure Boot（否则未签名的播放器无法加载）；\n'
        '  2) 如果你开了 BitLocker，先暂停保护并确认自己手上有恢复密钥；\n'
        '  3) 知道怎么进 BIOS、怎么进 WinRE（连续强制关机 3 次）。\n\n'
        '三、风险与免责\n'
        '  · 修改启动项属于系统级操作。默认"只试用一次"，失败会自动回到 Windows；\n'
        '  · 但任何引导层改动都有极端情况下无法开机的可能，请自行评估并做好备份；\n'
        '  · 本软件按 MIT 协议"按原样"提供，作者不对数据丢失或系统损坏承担责任。\n\n'
        '四、隐私\n'
        '  · 本程序不联网、不上传任何数据；\n'
        '  · 日志与备份只保存在本机 %LOCALAPPDATA%\\UefiAnimWizard 下，可随时删除。\n\n'
        '五、怎么撤销\n'
        '  程序内第 5 步"回滚"一键恢复；命令行：\n'
        '    bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n',
        '1. What this is\n'
        '  A small tool that plays a short animation while your PC boots, before Windows.\n'
        '  It writes 3 small files to the EFI System Partition and adds one firmware boot entry.\n\n'
        '2. Three things you must do yourself\n'
        '  1) Turn Secure Boot off (otherwise the unsigned player cannot load);\n'
        '  2) If BitLocker is enabled, suspend protection and make sure you have the recovery key;\n'
        '  3) Know how to enter the BIOS and WinRE (force power-off 3 times).\n\n'
        '3. Risk and disclaimer\n'
        '  - Changing boot entries is a system-level operation. The default is trial-once and firmware\n'
        '    falls back to Windows on failure;\n'
        '  - Any boot-layer change can, in extreme cases, leave a machine unbootable — judge the risk\n'
        '    yourself and keep backups;\n'
        '  - This software is provided "as is" under the MIT license, without warranty of any kind.\n\n'
        '4. Privacy\n'
        '  - No network access, no telemetry, nothing uploaded;\n'
        '  - Logs and backups stay on this machine under %LOCALAPPDATA%\\UefiAnimWizard and can be\n'
        '    deleted at any time.\n\n'
        '5. How to undo\n'
        '  Use step 5 "Roll back" inside the app, or run:\n'
        '    bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst\n'),
    'notice.check': ('我已阅读并同意上述须知（理解关闭 Secure Boot 与备份的要求）',
                     'I have read and accept this notice (including Secure Boot and backup requirements)'),
    'notice.agree': ('同意并继续', 'Accept and continue'),
    'notice.quit': ('退出', 'Quit'),
    'notice.must': ('请先勾选"我已阅读并同意…"。', 'Please tick the acceptance box first.'),
}


def set_lang(code):
    global _lang
    code = (code or '').lower()[:2]
    _lang = code if code in dict(LANGS) else DEFAULT_LANG
    return _lang


def get_lang():
    return _lang


def is_en():
    return _lang == 'en'


def t(key, *args):
    """取当前语言文案；支持 % 占位符。缺 key 时回退中文→key。"""
    pair = STRINGS.get(key)
    if pair is None:
        return key
    text = pair[1] if _lang == 'en' else pair[0]
    if not text:
        text = pair[0]
    if args:
        try:
            return text % args
        except (TypeError, ValueError):
            return text
    return text


def key_count():
    return len(STRINGS)


def missing_keys():
    """中英文任一为空的 key（自检用；空英文会退化成中文，界面上就会中英混排）。"""
    out = []
    for k, pair in STRINGS.items():
        if not isinstance(pair, (tuple, list)) or len(pair) < 2:
            out.append(k)
        elif not (pair[0] or '').strip() or not (pair[1] or '').strip():
            out.append(k)
    return sorted(out)

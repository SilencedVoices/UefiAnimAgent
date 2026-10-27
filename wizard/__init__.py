# -*- coding: utf-8 -*-
"""UefiAnimWizard — UEFI 开机动画半自动向导（给愿意折腾的人用的安全工具）。

设计原则（与 DESIGN 一致）：
  1. 只做可逆操作：先备份，再写入；先"一次性试用"，确认后才"设为默认"。
  2. 绝不静默修改：每一条 bcdedit 写操作用户都能在界面里看到完整命令。
  3. 绝不碰 \\EFI\\Microsoft：只写 \\EFI\\BootAnim。
  4. 每一步失败都停手并给出回滚路径，不吞异常。
"""

__version__ = '1.0.0'
APP_NAME = 'UefiAnimWizard'
APP_TITLE = 'UEFI 开机动画向导'

# 部署布局（与播放器硬编码路径一致，勿改）
ESP_DIR = '\\EFI\\BootAnim'
ESP_DIR_POSIX = ESP_DIR.replace('\\', '/')
EFI_NAME = 'BootAnimation.efi'
ANIM_NAME = 'anim.bin'
CONFIG_NAME = 'config.ini'
DEFAULT_CHAINLOAD = '\\EFI\\Microsoft\\Boot\\bootmgfw.efi'
BCD_DESCRIPTION = 'UEFI Animation Agent'

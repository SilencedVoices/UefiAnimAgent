# UEFI Boot Animation Agent — 真机部署方案评审（P5）

> 基线：agent_design_plan.md v1.0（冻结） + vm_test_report.txt（P4 T1-T8 全 PASS）
> 性质：**评审与预检产物，本阶段未写入任何真机 ESP / 未修改 BCD / 未重启**
> 生成：2026-10-16 · <主机名> · 管理员 DSH 会话

---

## 1. 真机环境取证（全部只读）

| 项 | 实测值 | 结论 |
|---|---|---|
| ESP 挂载 | `S:` FAT32，总 299.4 MB，**剩余 254.1 MB** | 与设计 §2.4 预算一致；部署仅需 ~12 MB ✅ |
| bootmgfw.efi | `S:\EFI\Microsoft\Boot\bootmgfw.efi` = **1,604,016 B**（2026-06-29） | 可读，链式加载目标在位 |
| Secure Boot | `Confirm-SecureBootUEFI = False`；注册表 UEFISecureBootEnabled=0 | **SB 关闭** |
| SB 密钥 | PK / KEK / db 均**未部署**（0xC0000100）；dbx 1B 占位 | 未签名 Agent 可直接加载 |
| BCD 固件层 | `{fwbootmgr}` displayorder：①`{bootmgr}` ②bootmgfw@恢复分区 ③可移动介质 OS 项 ④SSD 固件项 ⑤系统盘 bootmgfw，timeout=2 | `{bootmgr}` 第一顺位 |
| BCD 系统层 | `{bootmgr}`→S:\EFI\MICROSOFT\BOOT\BOOTMGFW.EFI，default `{current}`（Windows 10），displayorder 含 第三方 WinPE 项（可移动介质上的 .wim），nointegritychecks=Yes（既有状态） | 已有自定义项，不可扰动 |
| 恢复项 | `{WinRE 项}`（指向恢复分区） | 标准在位 |

## 2. 备份清单（<项目根>\p5\esp_backup\）

| 文件 | 大小 | SHA256（前 24） | 用途 |
|---|---|---|---|
| bootmgfw.efi.backup | 1,604,016 B | D1F7E351EF06E644… | 链式加载目标还原 |
| BCD.export | 32,768 B | 801708155059D790… | `bcdedit /import` 整店还原 |
| bcd_firmware_before.txt | 3,974 B | 93B820566722FFD8… | 固件层快照（对照） |
| bcd_all_before.txt | 12,676 B | A96CFEB8842DA025… | 全量快照（对照） |
| backup_sha256.json | 390 B | — | 完整哈希核验清单 |

注：BCD 原文件被运行中系统独占锁定（Copy-Item/robocopy /B 均确认），改用
`bcdedit /export` 获得等价可还原备份——回滚能力不受影响。

## 3. 签名策略（P4 实证 + 本机状态推导）

- **免签名直接部署**：SB 关闭且无密钥 → 与 P4 标准 OVMF 路径同效，T1-T8
  行为可直接迁移。
- 纪律：部署后**请勿在 BIOS 开启 Secure Boot**（开启即拒载未签名 Agent，
  动画失效但不影响进 Windows——P4 T2 已实证拒载行为、T8 实证最坏路径
  安全）。
- P4 测试证书链（vm\keys\）仅 VM 演练用，真机不采用。

## 4. 部署方案（按执行顺序；由 deploy_esp.ps1 承载，需 P6 单独授权执行）

| # | 操作 | 命令/动作 | 预期 |
|---|---|---|---|
| 1 | 预检 | 脚本自动：管理员/挂载/FS=FAT32/剩余≥30MB/源文件在位/无重名启动项 | 全绿才继续 |
| 2 | 建目录 | `mkdir S:\EFI\BootAnim` | 新目录，不触碰 \EFI\Microsoft\ |
| 3 | 复制三件套 | BootAnimation.efi + anim.bin + config.ini → S:\EFI\BootAnim\ | ~12 MB，复制后大小核验 |
| 4 | 克隆启动项 | `bcdedit /copy {bootmgr} /d "UEFI Animation Agent"` | 返回新 {GUID}（device 继承 partition=S:） |
| 5 | 改路径 | `bcdedit /set {GUID} path \EFI\BootAnim\BootAnimation.efi` | 操作成功完成 |
| 6 | **手动确认** | 用户执行 `bcdedit /set {fwbootmgr} displayorder {GUID} /addfirst` | 生效前启动行为零变化 |
| 7 | 验证 | 重启 → 动画播放 → Esc 可跳过 → 自动进 Windows | 对应 P4 T1/T6/T4 |

**冲突面分析**：新 GUID 条目指向 S:\EFI\BootAnim\（非 \EFI\BOOT\ 可移动介质
路径）→ 与 E: 盘 "UEFI OS"、第三方 WinPE WinPE、WinRE 零交集；不修改任何既有
条目；第 6 步前 {bootmgr} 仍为第一顺位。

## 5. 验收要点（部署后，对应 P4 用例）

1. 动画播放 ~4.5s（109 帧 @24fps，2K 或降级模式均合格）
2. Esc 立即跳过 → chainload bootmgfw → 正常进 Windows
3. 删除 anim.bin → 直接 chainload 不阻塞（T8）
4. 动画结束自然 chainload（T4）

## 6. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Agent 崩溃/黑屏 | 低（P4 8/8） | 中 | 最坏路径=直接 chainload；手动 addfirst 前零风险 |
| 误开 Secure Boot | 人为 | 低 | 动画失效，不影响进系统；回滚=关 SB |
| anim.bin 损坏 | 低 | 低 | T8 兜底直跳；重跑 make_anim.py 即可 |
| ESP 空间 | 极低 | 低 | 254MB 剩余 vs 需求 12MB；脚本硬检 ≥30MB |
| BCD 意外损坏 | 极低 | 高 | BCD.export 整店还原 + WinRE + 第三方 WinPE 三重救援 |

## 7. 明确不做（本阶段）

- ❌ 不执行 deploy_esp.ps1（仅生成）
- ❌ 不执行任何 bcdedit /set /delete /copy 写操作
- ❌ 不写入 S:\ 任何内容（仅读取备份）
- ❌ 不重启、不注销、不改进程

执行授权与实际部署 → P6（独立确认）。

# UEFI Boot Animation Agent — 回滚计划（P5）

> 适用：真机部署 deploy_esp.ps1 执行之后任何阶段
> 原则：**先保进 Windows，再清理痕迹**；所有方式互为备份，按代价从低到高
> 备份资产：<项目根>\p5\esp_backup\（bootmgfw.efi.backup + BCD.export + 哈希清单）

---

## 0. 回滚触发条件（任一命中即回滚）

| 现象 | 判定 |
|---|---|
| 开机黑屏 >30s 且无动画无 LOGO | Agent 前置路径故障 |
| 动画播完但反复重启进不了 Windows | chainload 链路异常 |
| 固件卡死 / 键盘无响应 / 循环停留在启动菜单 | 固件层不兼容 |
| Esc 无法跳过且无法进入 Windows | 输入路径 + chainload 双故障 |

> 注：单纯"动画效果不对（分辨率/帧率/花屏）"不触发回滚，用 P6 调参即可。

## 1. 回滚方式一：UEFI 设置界面（最优先，无需任何介质）

1. 开机狂按 `F2`/`Del`（本机为 AMI/Insyde 常见键位，开机 LOGO 有提示）
2. 进入 `Boot` → `Boot Priority / 启动顺序`
3. 将 **Windows Boot Manager** 调回第一启动项
   （或将 "UEFI Animation Agent" 条目禁用/删除）
4. `F10` 保存重启 → 直接进 Windows
5. 进系统后清理（可选）：
   ```powershell
   # 管理员 PowerShell —— 删除 Agent 启动项与文件
   bcdedit /enum firmware                 # 找到 "UEFI Animation Agent" 的 {GUID}
   bcdedit /delete {该GUID} /cleanup
   Remove-Item S:\EFI\BootAnim -Recurse -Force
   ```

## 2. 回滚方式二：WinRE 命令行修 BCD（方式一进不去时）

### 2.1 进入 WinRE
强制断电 2 次触发"自动修复" → `高级选项` → `疑难解答` → `命令提示符`
（或开机 F9/F12 选 "Windows Recovery Environment"）

### 2.2 修复命令（X:\ 为 WinRE 盘符，实际 ESP 可能不是 S:，先确认）
```cmd
:: 定位 ESP 分区（找 FAT32 ~100-300MB 的分区）
diskpart
  list disk
  list volume
  select volume <ESP编号>
  assign letter=S
  exit

:: 恢复 Windows Boot Manager 第一顺位（最小修复）
bcdedit /store S:\EFI\Microsoft\Boot\BCD /set {fwbootmgr} displayorder {bootmgr} /addfirst

:: 删除 Agent 启动项
bcdedit /store S:\EFI\Microsoft\Boot\BCD /enum firmware
bcdedit /store S:\EFI\Microsoft\Boot\BCD /delete {Agent的GUID} /cleanup
```

### 2.3 也可借用本机已有的 第三方 WinPE WinPE
本机 BCD 已含 第三方 WinPE（可移动介质上的 .wim）启动项，开机启动菜单
选择它进入 PE，用 PE 自带的引导修复工具/命令行执行 2.2 同样命令。

## 3. 回滚方式三：从 esp_backup 整店还原（BCD 大面积损坏时）

备份位于 `<项目根>\p5\esp_backup\`（需先进入 WinRE/PE 才能访问 C:）。

```cmd
:: 3.1 校验备份完整性（可选，哈希应与 backup_sha256.json 一致）
certutil -hashfile <项目根>\p5\esp_backup\BCD.export SHA256
certutil -hashfile <项目根>\p5\esp_backup\bootmgfw.efi.backup SHA256

:: 3.2 还原 BCD 整店（bcdedit /import，会重写 S:\EFI\Microsoft\Boot\BCD）
::     /clean = 清空现存所有启动项后导入（彻底但会丢 第三方 WinPE 项，
::     不加 /clean = 合并导入，推荐先不加）
bcdedit /import <项目根>\p5\esp_backup\BCD.export
:: 如系统仍不能引导，再用彻底版：
:: bcdedit /import <项目根>\p5\esp_backup\BCD.export /clean

:: 3.3 还原 bootmgfw.efi（仅当文件被破坏时才需要；正常部署不会触碰它）
copy /Y <项目根>\p5\esp_backup\bootmgfw.efi.backup S:\EFI\Microsoft\Boot\bootmgfw.efi

:: 3.4 重建启动项兜底（import 后仍缺失时）
bcdboot C:\Windows /s S: /f UEFI
```

## 4. 回滚 U 盘制作（预防性，部署前建议完成其一）

### 方案 A：官方 Windows 安装 U 盘（推荐，8GB+）
1. 另找一台 Windows 机器访问 microsoft.com/software-download/windows10
2. 下载 MediaCreationTool → 插 U 盘 → 制作安装介质
3. 用法：F12 选 U 盘启动 → `修复计算机` → `疑难解答` → `命令提示符`
   → 执行第 2/3 节命令

### 方案 B：Rufus + ISO
1. 下载 Windows 10/11 ISO + Rufus（rufus.ie）
2. GPT + UEFI(非 CSM) 模式写入 U 盘
3. 用法同方案 A

### 方案 C：本机现有 第三方 WinPE（零制作成本）
本机 BCD 已含该启动项，开机菜单直接可用（见 2.3）——**建议作为快速通道**，
官方安装盘作为终极兜底。

## 5. 回滚后验证清单

- [ ] 重启直接进 Windows（无动画、无启动菜单停留）
- [ ] `bcdedit /enum firmware` 无 "UEFI Animation Agent" 残留
- [ ] `S:\EFI\BootAnim\` 已删除（或明确保留待 P6 修复）
- [ ] 第三方 WinPE 与 WinRE 条目完好（对照 bcd_all_before.txt）
- [ ] bcdedit /enum {fwbootmgr} displayorder 首位 = {bootmgr}

## 6. 一句话版

> 系统能进 → 方式一；进不去 → WinRE/大白菜 PE 救；BCD 烂了 → import 备份；
> 全程有 SHA256 可核验，三重救援互为冗余。

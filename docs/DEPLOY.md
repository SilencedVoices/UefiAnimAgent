# 真机部署指南（UEFI x64 + Windows 双系统/单系统通用）

> ⚠️ 部署动 ESP 与 BCD 前请先备份：`bcdedit /export C:\BCD_backup`，
> 并确保你有一套可引导的救援手段（WinRE / 安装 U 盘 / PE）。

## 0. 前提检查

```powershell
# 管理员 PowerShell
Confirm-SecureBootUEFI        # False 才可直接用未签名 efi（或自行签名）
mountvol S: /S                # 挂载 ESP（本机若多 ESP，用卷 GUID 精确挂载）
```

## 1. 复制文件（仅新建目录，不动 \EFI\Microsoft）

```powershell
New-Item -ItemType Directory S:\EFI\BootAnim -Force
Copy-Item BootAnimation.efi S:\EFI\BootAnim\
Copy-Item anim.bin        S:\EFI\BootAnim\
Copy-Item config.ini      S:\EFI\BootAnim\
Get-FileHash S:\EFI\BootAnim\* -Algorithm SHA256   # 与源核对
```

## 2. 创建启动项（不改变现有顺序）

```powershell
$bcdOut = bcdedit /copy "{bootmgr}" /d "UEFI Animation Agent"
# 输出形如：The entry was successfully copied to {xxxxxxxx-....}
# 取其中 GUID，记为 {GUID}：
bcdedit /set "{GUID}" path \EFI\BootAnim\BootAnimation.efi
bcdedit /enum firmware        # 此刻新条目在末位 = 未激活，重启无影响
```

## 3. 激活（下一次重启生效）

```powershell
bcdedit /set "{fwbootmgr}" displayorder "{GUID}" /addfirst
bcdedit /enum firmware        # 确认 displayorder 首位为新 GUID
```

## 4. 验证

重启观察四项：①动画画幅正常（无拉伸/无异常裁切）②时长符合帧数/帧率
③播放中按 Esc 立即跳过 ④播放完/跳过后自动进入 Windows。

## 回滚（任选其一）

```powershell
# 方式一：删除启动项 + 删文件（最干净）
bcdedit /delete "{GUID}" /cleanup
Remove-Item S:\EFI\BootAnim -Recurse -Force

# 方式二：不进系统时——UEFI 固件设置 → Boot 顺序把 Windows Boot Manager 调回第一

# 方式三：BCD 整店还原（重大异常时）
bcdedit /import C:\BCD_backup /clean
```

## 常见问题

- **画面被裁切**：anim.bin 尺寸大于显示模式。用打包工具按目标机分辨率重打。
- **拉伸变形**：config `Resolution=auto` 在固件虚报模式时可能发生，
  显式指定 `Resolution = <显示器原生WxH>`。
- **Esc 不跳**：极老固件无 ConIn 键盘翻译；可设 `SkipKey = 0xFFFF` 任意键。
- **动画飞快/极慢**：该机 TSC 校准异常（罕见），属已知限制。

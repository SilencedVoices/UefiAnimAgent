# UEFI Boot Animation Agent（UefiAnimAgent）

> 在 UEFI 阶段播放开机动画，播完自动链式引导 Windows 的引导代理。纯 EDK2 实现，无运行时依赖；另附一个五步图形向导，帮你把动画装进 ESP。

[English summary](#english-summary) 在文末。

## 这个项目是怎么来的

电脑新手一枚。某天突发奇想，用 AE 做了一段"无限符号 → DeepSeek 标志"的矢量线条动画，于是起了改为开机动画的念头。

想法简单，做起来有坑：UEFI 阶段没有现成的播放器，只能让 AI 用 EDK2 从零写一个引导代理——固件 GOP 的缓冲、帧率时基、键盘输入、Secure Boot 签名，最后在虚拟机上跑通全部用例，又在真机上完整跑通：开机、播放、自动进入 Windows，Esc 随时可跳过。

本人的审美和技术水平都有限，附带的自制开机动画比较粗糙，望各位多多包容；项目后续也会继续改进。

## 特性

- **UAN1 容器**：单文件动画包（头 + 索引 + 帧数据），QOI 与 BMP RAW24 双格式
- **双缓冲渲染**：BackBuffer 合帧 + 行级投送防撕裂，`PixelBltOnly` 固件自动降级 Blt
- **高精度时基**：Stall 粗睡 + RDTSC 细自旋，120fps（8,333µs/帧）仍保持 µs 级帧距
- **输入双通道**：ConIn + SimpleTextInputEx，Esc 以 ScanCode(0x0017) 或 UnicodeChar(0x1B) 到达均可命中
- **显示策略**：显式分辨率优先；auto 时沿用当前模式（≥720p 且 16:9，零 SetMode），避免拉伸
- **打包工具**：tkinter GUI，PNG/BMP 混合、等比缩放居中黑边、1080p–4K、24–120fps
- **链式加载**：播完或跳过后加载 `\EFI\Microsoft\Boot\bootmgfw.efi`，不驻留内存

## 仓库结构

```
UefiAnimPkg/   EDK2 包（.dsc/.dec + Application 源码 10 模块）
tools/         打包与辅助脚本（GUI / CLI / BMP 扫描）
wizard/        图形安装向导（Python + tkinter，打包成单文件 exe）
docs/          设计文档 / VM 测试报告 / 真机部署与回滚指南 / 构建指南 / 使用指南
example/       config.ini 示例
```

> 预编译 `BootAnimation.efi` 与示例动画包 `anim.bin` 不入 Git 仓库，
> 通过 GitHub Release 分发（Release 页获取，或按 [docs/BUILDING.md](docs/BUILDING.md) 自行编译）。

## 快速开始

**只想装个动画**（最省事）：下载 Release 里的 `UefiAnimWizard.exe` 双击运行，跟着五步向导走。详见[图形化向导](#图形化向导新手推荐)。

**想自己打包动画**（GUI 或 CLI 任选其一）：

```powershell
python tools\AnimPackerGUI.py
python tools\make_anim.py --input <帧目录> --output anim.bin --format QOI --fps 24
```

**先在虚拟机试放**（强烈建议先于真机）：

```powershell
# esp\EFI\BootAnim\{BootAnimation.efi, anim.bin, config.ini}
# 另放 esp\EFI\BOOT\BOOTX64.EFI = BootAnimation.efi
qemu-system-x86_64 -machine q35 -m 512 -bios OVMF.fd -vga std `
  -drive file=fat:rw:esp,format=raw,media=disk
```

**真机部署**：见 [docs/DEPLOY.md](docs/DEPLOY.md)（BCD 条目创建与一键回滚）。

## 图形化向导（新手推荐）

不想手敲 `bcdedit`？仓库自带 `wizard/`——一个五步图形向导，打包成单个 exe：

```
安全须知 → 环境体检 → 制作动画 → 安装（只试一次）→ 固化 / 回滚
```

```powershell
pip install pillow pyinstaller
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
.\dist\UefiAnimWizard.exe
```

- 用户手册：[docs/使用指南.md](docs/使用指南.md)（Secure Boot / BitLocker 前提、WinRE 救砖命令、FAQ）
- 开发者文档：[wizard/README.md](wizard/README.md)（安全模型、构建、自检与离线仿真）
- 安全默认：只写 `\EFI\BootAnim`；**默认 `{fwbootmgr} bootsequence` 只试一次**，失败由固件自动回 Windows；
  写文件前先备份 ESP 文件并 `bcdedit /export`，每个文件回读 SHA256。
- 双语界面 + 可换字体：标题栏"设置"里切简体中文 / English，字号 10~28（默认 12，按 DPI 换算成像素）；首次启动先弹"用户须知"（风险/免责/隐私/撤销），勾选同意后才能安装
- 窗口自适应：字号变大时按控件实际需求撑大窗口，超过屏幕可用区域（任务栏以外）直接最大化；窗口大小会记住，下次启动沿用
- 体检结果用"可滚动 + 自动换行"的标签列表渲染（不用 Treeview：它的行高不吃字体、单元格不换行，大字号下必然重叠/截断）
- 第 3 步可直接"选成品 anim.bin"：按文件头里的宽高/帧率补写 config.ini，跳过制作直接安装
- 自检：`UefiAnimWizard.exe --selftest`（17 项，含中英文案完整性）、`--sim-install`（18 项安装/固化/回滚仿真，不动本机）

## 构建

见 [docs/BUILDING.md](docs/BUILDING.md)。一句话版（Windows + VS2022 + EDK2）：

```bat
cd <edk2> && edksetup.bat
build -p UefiAnimPkg\UefiAnimPkg.dsc -a X64 -t VS2022 -b RELEASE
```

## 配置（config.ini）

```ini
[anim]
Resolution = 1920x1080   ; auto 或 WxH（推荐与 anim.bin 同尺寸）
Fps        = 24          ; 1..120；缺省回落 anim.bin 的 fps_hint
AnimPath   = anim.bin    ; 相对 \EFI\BootAnim\
SkipKey    = 0x1b        ; EFI 扫描码或 0xFFFF=任意键；回车恒可跳过
Chainload  = \EFI\Microsoft\Boot\bootmgfw.efi
```

## UAN1 容器格式（小端）

```
0x00 4B magic 'UAN1'   0x04 2B version=1   0x06 2B format(0=BMP_RAW24, 1=QOI)
0x08 2B frame_count    0x0A 2B fps_hint    0x0C 4B width    0x10 4B height
0x14 4B index_offset(=0x18)
0x18 N*8B index[{u32 offset, u32 size}]    之后为帧数据
BMP_RAW24 帧 = top-down BGR 行数据    QOI 帧 = 完整 QOI 图像
```

## 安全与限制

- 产物**未签名**：需关闭 Secure Boot，或用自有 PK/KEK/db 自行签名
- 渲染为**居中留黑不缩放**：anim.bin 尺寸 ≠ 显示模式时居中裁切/留边
- 极少数校准异常固件走纯 Stall 退化模式，高帧率可能 ±10% 播放误差
- BMP RAW24 仅限 ≤72 帧且 ≤1080p，更大请用 QOI

## English summary

A pure-EDK2 UEFI boot animation agent: plays a UAN1-packed QOI/BMP frame
sequence with double-buffered GOP rendering and TSC-based pacing (24–120fps),
Esc-to-skip, then chain-loads the Windows Boot Manager. Includes a
tkinter+Pillow GUI packer (1080p–4K, letterbox normalization), a CLI packer,
and a five-step installation wizard (Python + tkinter, shipped as a single exe).
Prebuilt binaries and sample packs ship via GitHub Releases.
See docs/BUILDING.md and docs/DEPLOY.md.

## 许可证

MIT（见 [LICENSE](LICENSE)）。

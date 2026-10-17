# UEFI Boot Animation Agent（UefiAnimAgent）

[English summary](#english-summary) 在文末。

在 UEFI 阶段播放开机动画的引导代理：GOP 双缓冲渲染 QOI/BMP 帧序列，
TSC 高精度帧率控制（24–120fps），Esc 跳过，播放完自动链式加载
Windows Boot Manager（或任意 EFI 程序）。纯 EDK2 实现，无运行时依赖。

## 特性

- **UAN1 容器**：单文件动画包（头 + 索引 + 帧数据），支持 QOI 与 BMP RAW24
- **双缓冲渲染**：BackBuffer 合帧 + 行级投送，规避撕裂；`PixelBltOnly`
  固件自动降级到 Blt 路径
- **高精度时基**：Stall 粗睡 + RDTSC 细自旋两段式等待，120fps（8,333µs/帧）
  下仍保持 µs 级帧距；自旋带 2×帧间隔防死锁护栏
- **输入双通道**：gST->ConIn 主 + SimpleTextInputEx 备，Esc 以
  ScanCode(0x0017) 或 UnicodeChar(0x1B) 任一形态到达均可命中
- **显示策略**：config 显式分辨率优先；auto 时沿用可用当前模式
  （≥720p 且 16:9 族，零 SetMode），避免"固件报 2K、屏幕原生 1080p"的拉伸
- **打包工具**：tkinter GUI（PNG/BMP 混合、等比缩放居中黑边、
  1080p–4K、24–120fps、进度条、Pillow 缺失自动安装）
- **链式加载**：动画结束（或跳过）后加载 `\EFI\Microsoft\Boot\bootmgfw.efi`
  并转交控制权，不驻留内存

## 仓库结构

```
UefiAnimPkg/        EDK2 包（.dsc/.dec + Application 源码 10 模块）
tools/              打包与辅助脚本（GUI / CLI / BMP 扫描）
docs/               设计文档 / VM 测试报告 / 真机部署与回滚指南 / 构建指南
example/            config.ini 示例
```

> 预编译 BootAnimation.efi 与示例动画包 anim.bin 不入 Git 仓库，
> 通过 GitHub Release 附件分发（Release 页获取，或按 docs/BUILDING.md 自行编译）。

## 快速开始

1. **打包动画**（任选其一）：

   ```powershell
   # GUI：双击 tools/AnimPackerGUI.py，选帧目录 → 一键打包
   python tools\AnimPackerGUI.py

   # CLI：
   python tools\make_anim.py --input <帧目录> --output anim.bin --format QOI --fps 24
   ```

2. **虚拟机试放**（强烈建议先于真机）：

   ```powershell
   # OVMF + 虚拟 FAT ESP：esp\EFI\BootAnim\{BootAnimation.efi, anim.bin, config.ini}
   # 另放 esp\EFI\BOOT\BOOTX64.EFI = BootAnimation.efi
   qemu-system-x86_64 -machine q35 -m 512 -bios OVMF.fd -vga std `
     -drive file=fat:rw:esp,format=raw,media=disk
   ```

3. **真机部署**：见 [docs/DEPLOY.md](docs/DEPLOY.md)（含 BCD 条目创建与回滚）。

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
- 渲染策略为**居中留黑不缩放**：anim.bin 尺寸 ≠ 显示模式时居中裁切/留边，
  建议打包时选与目标机器一致的分辨率
- 极少数校准异常固件走纯 Stall 退化模式，高帧率可能 ±10% 播放误差
- BMP RAW24 仅限 ≤72 帧且 ≤1080p（体积规则），更大请用 QOI

## English summary

UEFI-stage boot animation agent in pure EDK2: plays a UAN1 packed
QOI/BMP sequence via double-buffered GOP rendering with TSC-based
high-precision pacing (24–120fps), Esc-to-skip (both ScanCode and
Unicode forms), then chain-loads the Windows Boot Manager. Ships a
tkinter+Pillow GUI packer (1080p–4K, letterbox normalization) and a
CLI packer. Prebuilt binaries and sample animation packs are
distributed via GitHub Releases. See docs/BUILDING.md and docs/DEPLOY.md.

## 许可证

MIT（见 [LICENSE](LICENSE)）。

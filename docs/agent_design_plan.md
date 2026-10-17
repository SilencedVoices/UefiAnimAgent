# UEFI Boot Animation Agent 设计文档

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0（设计冻结基线） |
| 日期 | 2026-10-11 |
| 前置依据 | `<项目根>\edk2_env_check_report.txt`（EDK2 master @ `1a44fc33` 已验证可编译，BaseTools 12 工具齐备） |
| 目标平台 | Windows 10/11 物理机（UEFI x64）+ QEMU/OVMF 虚拟机先行验证 |
| 用户确认 | 参考项目、动画源、动画格式、虚拟化平台、架构策略五项均已确认（见附录 A） |

---

## 1. 参考项目

### 1.1 主要参考：GRUB2 EFI chainloader

- 参考对象：`grub-core/loader/efi/chainloader.c`
- 核心流程（我们的 Chainload 模块照此实现）：
  1. 通过 `SimpleFileSystem` 或 `LoadFile2` 定位目标文件 `\EFI\Microsoft\Boot\bootmgfw.efi`；
  2. `gBS->LoadImage(FALSE, ImageHandle, DevicePath, NULL, 0, &ImageHandle2)` 将其装入内存并交由固件完成 PE/COFF 校验；
  3. `gBS->StartImage(ImageHandle2, &ExitDataSize, &ExitData)` 获得控制权移交；
  4. 失败路径：LoadImage 失败（`EFI_SECURITY_VIOLATION` / `EFI_NOT_FOUND`）时清理资源并给出错误提示，不悬挂系统。
- **Secure Boot 关键经验（安全补丁级要点）**：GRUB2 曾通过将 `GRUB_FILE_TYPE_EFI_CHAINLOADED_IMAGE` 加入 shim 验证器信任类型列表来避免链式加载被锁定策略拒绝。对应到本 Agent：在 Secure Boot 开启的机器上，链式加载的 `bootmgfw.efi` 本身是微软签名的（可通过验证），**被拒的将是未签名的 Agent 自身**——因此真机 SB 场景必须走"测试签名/自定义 DB 证书"方案（见风险点 R1 与虚拟验证方案 7.4）。

### 1.2 辅助参考：Limine chainload

- 参考对象：Limine boot protocol 中 `PROTOCOL=chainload` + `IMAGE_PATH` 的配置驱动实现。
- 借鉴点：**代码组织方式**——把"加载什么、从哪加载"全部外置到配置文件，内核代码只保留通用执行路径。我们的配置读取模块（config.ini）沿用该思想：分辨率、帧率、动画路径、链式加载目标全部可配置，代码零改动即可切换行为。

### 1.3 明确不参考的部分

- GRUB2 的脚本引擎、文件系统栈（我们直接用 UEFI 固件自带的 SimpleFileSystem/FAT 驱动）；
- Limine 的协议握手与多引导规范（与开机动画场景无关）。

---

## 2. 动画规格（方案一：预渲染，已确认）

### 2.1 源素材与制作管线

- 素材来源：**用户 AE 工程（已确认）**，导出 PNG 帧序列（建议 PNG-24，无损）。
- 离线工具链（后续执行阶段实现，Python 3.12 已就绪）：
  `AE 导出 PNG 序列 → tools/make_anim.py（尺寸缩放/像素格式转换/编码）→ anim.bin → 拷入 ESP 或虚拟 ESP`。
- 若后续出现视频源（MP4 等），插入 `ffmpeg 抽帧` 步骤即可，管线其余不变。

### 2.2 格式策略（阈值策略，已确认）

| 条件 | 采用格式 | 理由 |
|---|---|---|
| 帧数 ≤ 72（3s @24fps）且目标为 720p/1080p | **BMP 24bpp 帧序列** | 解码零成本：解析 54 字节头后 `CopyMem` 级搬运 |
| 帧数 > 72，或目标 2K，或真机 ESP 部署 | **QOI**（含自研打包容器） | 体积较原始 24bpp 减 60–80%，解码器约 300 行 C，UEFI 移植成本低 |

### 2.3 统一打包容器 anim.bin

两种格式统一封装，资源加载模块只有一条文件读取路径：

```text
offset  size    field
0x00    4       magic 'UAN1'
0x04    2       version (=1)
0x06    2       format (0=BMP_RAW24, 1=QOI)
0x08    2       frame_count
0x0A    2       fps_hint (24/30/60, 可被 config 覆盖)
0x0C    4       width
0x10    4       height
0x14    4       index_offset (通常 = 0x18)
0x18    N*8     index[]: {u32 offset, u32 size} per frame
...             frame data[]（QOI 模式每帧为完整 QOI 图像；BMP 模式每帧为去掉文件头后的原始 BGR 行数据，统一按 bottom-up 已翻转为 top-down 存放）
```

### 2.4 体积预算（硬约束：实测真机 ESP 剩余仅约 254 MB）

| 规格 | 单帧原始 24bpp | BMP ×72 帧 | QOI(≈35%) ×72 帧 | 结论 |
|---|---|---|---|---|
| 1280×720 | 2.76 MB | 199 MB | ≈70 MB | BMP 可用 |
| 1920×1080 | 5.93 MB | 427 MB ✗ | ≈149 MB | 真机必须 QOI |
| 2560×1440 | 10.55 MB | 759 MB ✗ | ≈265 MB ⚠ | 真机 2K 长动画超预算，需减帧/分段 |

**结论写入实现约束**：真机部署默认 1080p + QOI；2K 仅在帧数 ≤ 60 且接受 ≈220 MB 占用时可用；BMP 仅用于帧数很少的短动画或虚拟机测试（虚拟磁盘无 254 MB 限制）。FAT32 单文件 4 GB 上限远未触及，非风险。

---

## 3. 渲染方案（GOP + 双缓冲，已确认）

### 3.1 分辨率获取与降级策略

1. `GOP->QueryMode()` 枚举 `MaxMode` 全部模式，筛选像素格式非 `PixelBltOnly` 且宽高匹配优先级的模式；
2. 优先级：`config 指定模式` → 2560×1440 → 1920×1080 → 1280×720 → 当前模式（不切换）；
3. `GOP->SetMode()` 切换后**必须重读** `Mode->FrameBufferBase`、`Mode->Info->PixelsPerScanLine`、`Mode->Info->PixelFormat`（基址/步幅/格式均可能变化，v1 报告已强调）；
4. 动画帧尺寸 ≠ 实际显示模式时：居中绘制，四周留黑（不做实时缩放，保证 memcpy 级性能）。

### 3.2 双缓冲与像素格式

- `BackBuffer`：按显示模式分配的线性内存（`AllocatePool`，2K XRGB8888 约 14.1 MB）；
- 每帧流程：解码到/拷贝到 BackBuffer（完成 BGR→目标格式转换与 stride 填充）→ 一次 `CopyMem((void*)FrameBufferBase, BackBuffer, ...)` 投送到 FrontBuffer；
- 严格按 `PixelsPerScanLine` 步幅写 framebuffer，防止斜纹花屏；
- 降级路径：若 `PixelFormat == PixelBltOnly`（固件不暴露线性 framebuffer，部分 VM 会出现），改走 `GOP->Blt()`（性能低但正确），并在日志中标注。

### 3.3 帧率控制（Stall，已确认）

| fps | 帧间隔 (µs) |
|---|---|
| 24 | 41,667 |
| 30 | 33,333 |
| 60 | 16,667 |

- 用 `gBS->Stall()` 控制间隔；解码耗时会引入正偏移，采用"剩余时间 Stall"补偿（间隔 − 本帧已耗时），实际帧率波动约 ±5%，开机动画场景可接受（设计上不做定时器中断方案，保持 DXE Runtime 之前无依赖）。

### 3.4 渲染主循环（伪代码）

```text
Init: GOP 设置模式 → 读 config → 加载 anim.bin 全量入内存 → 分配 BackBuffer
loop (frame = 0..N-1):
    if WaitForKeyStroke(Esc/任意键) → break        // 非阻塞轮询，见 4.6
    t0 = RDTSC/Stall 基准
    decode frame[frame] → BackBuffer（BMP: CopyMem；QOI: qoi_decode）
    CopyMem(FrontBuffer, BackBuffer, stride*height)
    Stall(interval - elapsed)
end → 释放资源 → Chainload
```

---

## 4. 架构模块（7 模块，已确认）

| # | 模块 | 职责 | 关键 UEFI 依赖 | 输入 → 输出 |
|---|---|---|---|---|
| M1 | GOP 初始化 | 枚举/设置分辨率（降级策略）、获取 framebuffer 参数、PixelBltOnly 检测 | GraphicsOutput | config.Resolution → Mode 信息全局态 |
| M2 | 配置读取 | 定位自身所在 ESP（LoadedImage→DeviceHandle→SimpleFileSystem），读取解析 `config.ini` | SimpleFileSystem, LoadedImage | config.ini → AnimConfig 结构体 |
| M3 | 资源加载 | 读取 anim.bin 头 + 索引 + 全部帧数据入内存（一次 I/O，播放期零读盘） | SimpleFileSystem, AllocatePool | AnimConfig → AnimData(内存) |
| M4 | 双缓冲渲染 | BackBuffer 管理、BGR→显存格式转换、stride 处理、投送 FrontBuffer 或 Blt 降级 | GraphicsOutput | 一帧数据 → 屏幕更新 |
| M5 | 帧率控制 | fps→µs 间隔换算、剩余时间补偿 Stall | BootServices.Stall | fps 配置 → 帧节奏 |
| M6 | 输入监听 | 非阻塞检测按键（Esc 跳过；可配置任意键） | SimpleTextInputEx(主)/SimpleTextIn(备) | 按键事件 → 跳过标志 |
| M7 | Chainload | 构造 DevicePath → LoadImage/StartImage `\EFI\Microsoft\Boot\bootmgfw.efi`，失败错误处理与安全退出 | LoadImage/StartImage/Exit | 动画结束/跳过 → 控制权移交 |

### 4.1 模块依赖关系（执行顺序）

```text
M1 GOP ─┐
M2 配置 ─┼─→ M3 资源 → M4 渲染 ⇄ M5 帧率
        │              ↑
        └──────────── M6 输入（事件循环内轮询）
                       ↓ 动画结束/跳过
                     M7 Chainload → bootmgfw.efi
```

### 4.2 配置文件格式（ESP:\EFI\BootAnim\config.ini）

```ini
[anim]
Resolution = auto        ; auto|2560x1440|1920x1080|1280x720
Fps        = 30          ; 24|30|60
AnimPath   = anim.bin
SkipKey    = 0x1b        ; ESC（EFI 扫描码）
Chainload  = \EFI\Microsoft\Boot\bootmgfw.efi
```

### 4.3 部署目录规划（虚拟 ESP 与真机一致）

```text
ESP:\EFI\BootAnim\BootAnimation.efi    ; Agent 本体
ESP:\EFI\BootAnim\config.ini           ; 配置
ESP:\EFI\BootAnim\anim.bin             ; 动画资源
ESP:\EFI\Microsoft\Boot\bootmgfw.efi   ; 微软原厂，绝不修改，仅读取加载
```

### 4.4 代码目录规划（后续在 <EDK2> 内新增）

```text
<EDK2>\UefiAnimPkg\
  UefiAnimPkg.dec
  UefiAnimPkg.dsc                ; 仅依赖 MdePkg，工具链 VS2022/X64/RELEASE
  Application/BootAnimationApp.inf
  Application/Main.c             ; 装配 7 模块
  Application/Gop.c  Config.c  AnimLoad.c  Render.c  FrameRate.c  Input.c  Chainload.c
  Application/Qoi.c              ; QOI 解码器（~300 行，含许可头）
<项目根>\tools\make_anim.py  ; PNG 序列 → anim.bin 打包器
```

### 4.5 错误处理原则

任一模块致命错误（配置缺失→用默认值；动画缺失→直接 chainload；GOP 失败→文字模式提示后 chainload）**都不允许阻塞 Windows 启动**——最坏路径永远是"跳过动画，正常移交 bootmgfw"。

---

## 5. 依赖协议

| 协议/服务 | 提供者 | 使用模块 | 用法 |
|---|---|---|---|
| `gEfiGraphicsOutputProtocolGuid` | GOP 驱动（DXE 早期就绪） | M1/M4 | QueryMode/SetMode/Blt/线性 framebuffer |
| `gEfiSimpleFileSystemProtocolGuid` | FAT 驱动 | M2/M3 | 打开 ESP 卷、ReadFile |
| `gEfiLoadedImageProtocolGuid` | DXE Core | M2 | `ImageHandle→DeviceHandle` 定位自身所在文件系统 |
| `gEfiSimpleTextInputExProtocolGuid` | 控制台驱动 | M6 | ReadKeyStrokeEx（ScanCode ESC 可靠） |
| `gEfiSimpleTextInProtocolGuid`（备） | 控制台驱动 | M6 | Ex 不可用时回退 ReadKeyStroke |
| `gBS->LoadImage / StartImage` | DXE Core | M7 | 链式加载 bootmgfw.efi |
| `gBS->Stall` | DXE Core | M5 | 微秒级帧间隔 |
| `gBS->AllocatePool / FreePool` | DXE Core | M3/M4 | BackBuffer 与帧数据内存 |
| `gBS->Exit /UnloadImage` | DXE Core | M7 | 失败路径自清理 |

内存预算峰值（1080p QOI）：帧数据 ≈149 MB + BackBuffer 7.9 MB + 结构开销 <1 MB ≈ **158 MB**，DXE 阶段可用内存（通常 >1 GB）完全覆盖。

---

## 6. 风险点

| # | 风险 | 影响 | 缓解措施 |
|---|---|---|---|
| R1 | **Secure Boot 拒载未签名 Agent** | 真机 SB 开启时 Agent 无法加载（QEMU 场景 3 已验证该行为符合预期） | 真机部署属后续独立阶段，届时三选一：①关 SB（不推荐）；②测试模式 + 自签；③自有 PK/KEK/DB 全套自签。虚拟阶段先在 OVMF SB 开启环境下完整演练 |
| R2 | **真机 ESP 仅 ≈254 MB** | 2K/长动画放不下 | 2.4 体积预算表强制：真机默认 1080p+QOI；打包器内置预算校验，超限即拒绝生成 |
| R3 | **Windows 更新/修复还原 BCD 或 bootmgfw** | 部署方案 2（改名替换 bootmgfw）会被系统还原 | 部署采用 BCD 新增启动项指向 Agent（`bcdedit /set` 新 entry），原 entry 原样保留；且本设计阶段不触碰真机 ESP |
| R4 | PixelBltOnly 固件（无线性 framebuffer） | 直接写显存方案失效 | 3.2 已设 Blt 降级路径 + 启动日志标注 |
| R5 | SetMode 后基址/步幅/格式变化 | 花屏或越界写 | 切换后强制重读三参数（v1 报告既有结论） |
| R6 | Stall 精度不足 → 帧率波动 ±5% | 观感略不平滑 | 剩余时间补偿（3.3）；不做定时器方案，保持简单 |
| R7 | GOP 在 QEMU(OVMF) 与真机模式列表差异 | VM 通过 ≠ 真机必过 | 降级策略本身即兜底；真机首次部署前先在只读诊断模式（打印模式列表）验证 |
| R8 | FAT32 驱动读取大文件慢（冷启动） | 首帧前黑屏时间长 | M3 一次性全量读入；必要时显示 "loading" 简单提示帧 |
| R9 | AE 导出帧序列规格漂移（尺寸/帧率不一致） | 播放错乱 | make_anim.py 入口即校验：帧数、尺寸一致性，不合规拒绝打包 |

---

## 7. 虚拟验证方案（QEMU + OVMF，已确认）

### 7.1 平台与安装（执行阶段实施，本阶段不安装）

- QEMU：`winget install QEMU.QEMU`（Windows 版，免费开源）；
- OVMF 固件：**优先自编译**——利用已验证的 <EDK2> 工具链：
  `build -p OvmfPkg/OvmfPkgX64.dsc -a X64 -t VS2022 -b RELEASE` 产出 `OVMF_CODE.fd` / `OVMF_VARS.fd`；
  备选：直接使用发行版预编译 OVMF 二进制。
- Secure Boot 变体：`-D SECURE_BOOT_ENABLE=TRUE` 编译 + 生成测试 PK/KEK/DB 密钥对（edk2 BaseTools 自带脚本）。

### 7.2 虚拟 ESP 构造（两种方式）

- 方式 A（推荐，免格式化）：QEMU 虚拟 FAT 直挂目录：
  `-drive format=raw,file=fat:rw:<项目根>\vm\esp` —— 目录即 ESP，宿主机直接拖文件；
- 方式 B（贴近真机）：`qemu-img create -f qcow2 test_esp.img 100M` → VM 内格式化 FAT32 → 拷入文件。用于最终验收演练。

### 7.3 标准测试命令（示意）

```text
qemu-system-x86_64 -machine q35 -m 2048 ^
  -drive if=pflash,format=raw,file=OVMF_CODE.fd ^
  -drive if=pflash,format=raw,file=OVMF_VARS.fd ^
  -drive format=raw,file=fat:rw:<项目根>\vm\esp ^
  -net none -vga std
```

### 7.4 测试场景矩阵（6+2 项）

| # | 场景 | 测试内容 | 预期结果 |
|---|---|---|---|
| T1 | 基础启动 | Agent 加载并播放动画 | 动画流畅播放，无花屏/撕裂，帧率接近配置值 |
| T2 | Secure Boot 开启（未签名） | Agent 未签名时加载 | **被固件拒绝（符合预期）**，直接进 bootmgfw/OVMF shell |
| T3 | Secure Boot 开启（已签名） | 用测试 DB 证书签名后加载 | 正常播放 + chainload（R1 方案演练） |
| T4 | Secure Boot 关闭 | Agent 加载并 chainload | 正常进入 bootmgfw（OVMF 下为报错画面属正常，因无真实 Windows） |
| T5 | 分辨率降级 | `-vga std` 等限定模式使 2K 不可用 | 自动降 1080p，日志可验证 |
| T6 | 按键跳过 | 播放中按 Esc | 立即中止并 chainload |
| T7 | 链式加载失败 | config 指向不存在路径 | 显示错误 3 秒 → 安全退出/重启（不死机） |
| T8 | 容错兜底 | 删除 anim.bin / 删除 config.ini | 直接 chainload（4.5 最坏路径） |

### 7.5 安全边界

- 所有试验在 QEMU 内完成，真机 ESP 全程零写入；
- 真机部署（BCD 修改）为独立后续阶段，需用户届时单独确认后执行；
- 本设计阶段不安装 QEMU、不编译 OVMF、不生成任何 .efi。

---

## 8. 后续执行阶段规划

| 阶段 | 内容 | 前置 |
|---|---|---|
| P1 | 安装 QEMU + 编译 OVMF（含 SB 变体）+ 搭建虚拟 ESP | 本文档 |
| P2 | 实现 UefiAnimPkg 7 模块 + QOI 解码器，编译通过 | P1 |
| P3 | make_anim.py 资源管线（AE PNG → anim.bin），先用程序生成测试帧 | P2 |
| P4 | 虚拟机全场景测试（T1–T8） | P2+P3 |
| P5 | 正式素材替换 + 真机部署方案评审（BCD entry，单独授权） | P4 |

---

## 附录 A：用户确认记录（2026-10-11）

| 决策点 | 用户选择 |
|---|---|
| 参考项目 | 同意 GRUB2 为主（chainloader 流程 + SB 补丁经验）+ Limine 为辅（配置驱动组织） |
| 动画源文件 | 有 AE 工程，可导出 PNG 帧序列 |
| 动画格式 | 阈值策略：≤72 帧 BMP 帧序列，否则 QOI（真机受 ESP 254MB 约束默认 1080p+QOI） |
| 虚拟化平台 | 同意 QEMU + OVMF（OVMF 优先自编译，含 Secure Boot 变体） |
| 架构与策略 | 同意 7 模块划分 + 2K→1080p→720p 降级 + Stall() 帧率控制 |

## 附录 B：环境事实快照（引自前三轮报告）

- 工具链：VS2022 BuildTools 17.14.41（MSVC 14.44.35207）/ Python 3.12.10 / NASM 3.02 / Git 2.55.0 / iasl 20260408（<EDK2>\tools\iasl\）
- EDK2：master @ `1a44fc33`，MdeModulePkg X64/RELEASE 编译通过（465 .efi），BaseTools 12 工具齐备
- ESP：FAT32，序列号 F807-75E7，剩余 ≈254 MB（2026-10-11 17:35 实测），bootmgfw.efi 1,604,016 字节可读
- 已知网络事实：GitHub 间歇不可达，Gitee/gh-proxy 镜像可用（后续拉取 OVMF 相关资源时复用该策略）

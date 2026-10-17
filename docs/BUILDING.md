# 构建指南（UefiAnimPkg）

## Windows（VS2022，已在真实环境验证）

前置：EDK2-stable 检出、Visual Studio 2022（含 C++ 桌面开发）、Python 3.8+、
NASM（EDK2 BaseTools 编译需要）。

```bat
:: 1. 将 UefiAnimPkg/ 放入 EDK2 检出根目录
:: 2. 初始化构建环境
cd /d <edk2>
set EDK_TOOLS_PATH=%CD%\BaseTools
set PYTHON_COMMAND=python
edksetup.bat

:: 3. 编译（X64 RELEASE）
build -p UefiAnimPkg\UefiAnimPkg.dsc -a X64 -t VS2022 -b RELEASE
```

产物：`Build\UefiAnimPkg\RELEASE_VS2022\X64\BootAnimation.efi`（约 15KB）。

常用变体：

```bat
build -p UefiAnimPkg\UefiAnimPkg.dsc -a X64 -t VS2022 -b DEBUG   :: 调试版
```

## Linux（GCC，未在本项目验证）

```bash
cd <edk2>
make -C BaseTools
source edksetup.sh
build -p UefiAnimPkg/UefiAnimPkg.dsc -a X64 -t GCC5 -b RELEASE
```

如编译器报 intrinsics 相关错误，请优先确认 NASM 已安装且在 PATH。

## 模块一览

| 文件 | 职责 |
|---|---|
| Main.c | 主流程：配置→显示→动画→链式加载 |
| Gop.c | GOP 枚举/模式策略（显式优先、auto 零 SetMode 沿用） |
| Render.c | 双缓冲渲染（居中留黑不缩放、Blt 降级） |
| Qoi.c | QOI 解码器（子集实现，覆盖打包器输出） |
| AnimLoad.c | UAN1 容器解析与帧装载 |
| Config.c | config.ini 解析（极简 INI，缺省兜底） |
| FrameRate.c | TSC 校准 + 两段式高精度帧距控制 |
| Input.c | 双通道按键轮询与跳过判定 |
| Chainload.c | LoadImage/StartImage 链式加载 bootmgfw |

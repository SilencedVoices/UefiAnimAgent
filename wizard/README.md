# UefiAnimWizard — 图形化安装向导（内含打包功能）

给"愿意折腾、但不想手敲 bcdedit"的人用的**半自动**工具：
选素材 → 生成动画 → 一键装到 ESP → **只试一次** → 看到效果再决定固化或回滚。

> 它不是"零交互一键破解 Secure Boot 的傻瓜软件"：Secure Boot 必须用户自己去 BIOS 关，
> BitLocker 恢复密钥必须自己先确认，这两件事任何软件都替不了。

## 给用户看的文档

- [`../docs/使用指南.md`](../docs/使用指南.md) — 图形界面逐步图解、WinRE 救砖、常见问题、安全说明

## 目录结构

```
run_wizard.py           PyInstaller 打包入口（独立文件，避免相对导入问题）
build_exe.ps1           构建脚本（自检 → PyInstaller 单文件 → 版本信息/图标/manifest）
wizard/
  __init__.py           版本号、应用名、ESP 路径常量
  paths.py              资源定位（源码 / _MEIPASS / 单目录）、数据目录、Logger
  sysinfo.py            管理员、UEFI/SecureBoot、BitLocker、分辨率、bcdedit 文本解码
  esp.py                ESP 发现、临时盘符挂载、SHA256 校验写入、备份/还原
  bcd.py                固件启动项解析与增删改（bcdedit 封装）
  pack.py               素材 → anim.bin + config.ini（自包含，不依赖 tools/）
  deploy.py             体检 / 安装（试用）/ 固化 / 回滚 / 状态 / 自诊
  i18n.py               中英文文案表（zh/en）+ 字体/语言设置
  cli.py                命令行入口与 --selftest / --doctor / --sim-install
  gui.py                tkinter 五步向导（含设置对话框与用户须知）
  assets/               BootAnimation.efi（播放器）、图标、exe 版本信息
  tests/                自检与离线仿真（fixtures 里有中文 bcdedit 输出样本）
```

## 命令行

```powershell
python -m wizard                      # 打开图形向导
python -m wizard --selftest           # 无副作用自检（打包 + 解析 + 环境），写报告
python -m wizard --sim-install        # 离线仿真：临时目录当 ESP + 假 BCD，跑安装/固化/回滚
python -m wizard --doctor             # 只读体检（可非管理员运行，会提示受限项）
python -m wizard --status             # 当前安装状态
python -m wizard --diagnose           # 开机看不到动画时的自诊
python -m wizard --install --dir <目录> [--activate]
python -m wizard --rollback [--guid <GUID>] [--remove-files] [--restore <备份目录>]
```

## 构建 exe

```powershell
pip install pillow pyinstaller
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
.\dist\UefiAnimWizard.exe --selftest
```

产物：`dist\UefiAnimWizard.exe`（约 18 MB，单文件，内嵌 `requireAdministrator` 清单，
双击即请求管理员；图标与版本信息已嵌入）。

## 安全模型（代码层面的硬约束）

| 约束 | 实现位置 |
| --- | --- |
| 只写 `\EFI\BootAnim\`，绝不碰 `\EFI\Microsoft\` | `esp.esp_target_paths()` 是唯一路径来源 |
| 任何写操作前先备份（ESP 文件 + BCD 导出），缺一不写 | `deploy.install()` 步骤 1/2 在步骤 3 之前 |
| 默认只"试一次"：`{fwbootmgr} bootsequence`，失败固件自动回 Windows | `bcd.set_trial_boot()` |
| 只有用户看到成功后才允许 `displayorder /addfirst` 固化 | `deploy.activate()` 仅由"完成"页按钮调用 |
| 每个文件写完回读 SHA256，不一致立即失败 | `esp.copy_verified()` |
| ESP 无盘符时拒绝安装（避免 BCD `partition=X:` 依赖临时盘符） | `esp.pick_windows_esp()` / `deploy` 前置 |
| 临时挂载的盘符用后立即 `mountvol /D` | `esp.Esp.unmount()` |
| 首次启动必须先勾选用户须知（风险/免责/隐私/撤销），记录在 settings.json | `gui._first_run_notice()` / `NOTICE_VERSION` |

回滚永远先摘启动项（立刻恢复开机行为），再决定动不动文件。

## 已内置的"踩坑经验"

- **bcdedit 输出不是 UTF-16**：中文系统上是 CP936 + CRLF；且 `identifier` 被本地化成"标识符"，
  而 `displayorder`/`device`/`path` 仍是英文。`bcd._parse_blocks()` 同时兼容两种键名。
- **盘符归一化**：`Get-Partition` 给的 `DriveLetter` 是字符 `T`，不是 `T:`，
  曾导致 `root()` 拼出 `T\EFI\...` 而判定"找不到 Windows EFI 分区"。`esp._norm_drive()` 统一处理。
- **Tk 不是线程安全的**：工作线程只允许 `queue.put()`，控件更新一律回到主线程（`gui._log_append`）。
- **PowerShell 单元素管道会退化成字符串**：`$cand[0]` 取到的是首字符 `C`，
  `build_exe.ps1` 里用 `@(@(...) | Where-Object ...)` 包一层。
- **带中文的 .ps1 存盘必须 UTF-8 BOM**，否则 Windows PowerShell 5.1 按 ANSI 解析直接语法报错。

## 界面语言与字体

- 文案集中在 `i18n.py`：`LANGS = (('zh','简体中文'),('en','English'))`，`t('key', *args)` 取当前语言，`set_lang(code)` 切换；新增文案必须两种语言都写，否则自检的 `i18n.parity` 会失败。
- 字体/语言/须知状态保存在 `%LOCALAPPDATA%\\UefiAnimWizard\\settings.json`；`paths.load_settings()/save_settings()` 读写，换语言只调 `gui._rebuild()` 重建界面（不动已完成状态）。
- 字号放大后用 `gui._fit_window()` 按各块 `winfo_reqheight` 之和撑高窗口，避免底部按钮被裁。

## 测试覆盖

`--selftest`（17 项）：QOI 打包、容器头/索引/体积、config 生成、BMP 阈值拒绝、空素材拒绝、
bcdedit 中文样本解析（条目数/displayorder/timeout/agent 识别）、环境（管理员/UEFI/SB/分辨率/OS）、ESP 发现、
中英文案完整性（154 条，缺一条就 FAIL）。

`--sim-install`（18 项）：真实调用 `deploy.install/activate/rollback`，但 ESP 指向临时目录、
bcdedit 换成内存假实现，验证——写文件并校验 SHA256、备份 anim/bcd、只设 bootsequence 不改
displayorder、重复安装复用条目、固化后清 trial、回滚删条目/保留文件、回滚删文件。

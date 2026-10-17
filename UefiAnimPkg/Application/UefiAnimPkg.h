/** @file
  UefiAnimPkg.h — UEFI 开机动画 Agent 公共类型与常量
  依据：C:\uefi_anim\agent_design_plan.md v1.0（§2.3 容器格式 / §4.2 配置结构）
  模块：M1 GOP / M2 Config / M3 AnimLoad / M4 Render / M5 FrameRate /
        M6 Input / M7 Chainload + Qoi 解码器
  版权：MIT（随项目发布）
**/

#ifndef UEFI_ANIM_PKG_H_
#define UEFI_ANIM_PKG_H_

#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/DevicePathLib.h>
#include <Protocol/GraphicsOutput.h>
#include <Protocol/SimpleFileSystem.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/SimpleTextIn.h>
#include <Protocol/SimpleTextInEx.h>
#include <Guid/FileInfo.h>

/* ------------------------------------------------------------------ */
/* 常量                                                                 */
/* ------------------------------------------------------------------ */

#define ANIM_MAX_PATH               128

/* 帧率（§3.3）*/
#define ANIM_FPS_MIN                1
#define ANIM_FPS_MAX                120
#define ANIM_DEFAULT_FPS            30

/* 跳过键：EFI 扫描码 SCAN_ESC = 0x0017（MdePkg SimpleTextIn.h 实证）；
   config.ini 中写 0x1b 亦按 ASCII ESC 兼容。P6 修复：旧值 0x000B 实为 SCAN_F1，
   真机固件 Esc 只填 ScanCode 时导致无法跳过（QEMU 走 UnicodeChar=0x1B 掩盖）。*/
#define ANIM_DEFAULT_SKIP_SCANCODE  0x0017
#define ANIM_ASCII_ESC              0x1B
#define ANIM_ANY_KEY                0xFFFF

/* 固定路径（除链式加载目标外全部相对 \EFI\BootAnim\，不硬编码盘符）*/
#define ANIM_DIR_PREFIX             L"\\EFI\\BootAnim\\"
#define ANIM_CONFIG_FILE            L"\\EFI\\BootAnim\\config.ini"
#define ANIM_CHAINLOAD_DEFAULT      L"\\EFI\\Microsoft\\Boot\\bootmgfw.efi"

/* 显示降级优先级（§3.1：config 指定 → 2560x1440 → 1920x1080 → 1280x720 → 当前）*/
#define ANIM_FALLBACK_MODE_COUNT    3

/* ------------------------------------------------------------------ */
/* anim.bin 统一容器（§2.3，头 0x18 字节，小端，packed）                  */
/* ------------------------------------------------------------------ */

#pragma pack(1)
typedef struct {
  UINT8   Magic[4];        /* 'U','A','N','1'                                        */
  UINT16  Version;         /* = 1                                                    */
  UINT16  Format;          /* 0 = BMP_RAW24（top-down BGR 行数据）1 = QOI（整图）    */
  UINT16  FrameCount;      /* 帧数                                                   */
  UINT16  FpsHint;         /* 24/30/60，可被 config.ini 覆盖                          */
  UINT32  Width;           /* 动画像素宽                                              */
  UINT32  Height;          /* 动画像素高                                              */
  UINT32  IndexOffset;     /* 帧索引在文件内偏移，通常 = 0x18                         */
} ANIM_HEADER;

typedef struct {
  UINT32  Offset;          /* 帧数据在文件内偏移                                      */
  UINT32  Size;            /* 帧数据字节数                                            */
} ANIM_FRAME_INDEX;
#pragma pack()

/* 4+2+2+2+2+4+4+4 = 24 = 0x18，静态断言由 AnimLoad 在运行时核对 */

#define ANIM_FORMAT_BMP_RAW24       0
#define ANIM_FORMAT_QOI             1
#define ANIM_VERSION_V1             1

/* ------------------------------------------------------------------ */
/* 配置结构（§4.2，M2 输出）                                             */
/* ------------------------------------------------------------------ */

typedef struct {
  UINT32    Fps;                       /* 0 = 未指定 → 沿用 anim.bin FpsHint → 30   */
  UINT32    ReqWidth;                  /* 0 = auto（走降级链）                       */
  UINT32    ReqHeight;
  UINT16    SkipScanCode;              /* EFI 扫描码；ANIM_ANY_KEY = 任意键跳过      */
  CHAR16    AnimPath[ANIM_MAX_PATH];   /* 相对 \EFI\BootAnim\ 或以 '\' 开头的绝对路径 */
  CHAR16    ChainloadPath[ANIM_MAX_PATH];
  BOOLEAN   ConfigLoaded;              /* config.ini 是否成功读取（缺失→默认值）     */
} ANIM_CONFIG;

/* ------------------------------------------------------------------ */
/* 文件系统上下文（M2 定位自身 ESP，M3/M7 复用同一卷）                     */
/* ------------------------------------------------------------------ */

typedef struct {
  EFI_SIMPLE_FILE_SYSTEM_PROTOCOL  *Fs;
  EFI_FILE_PROTOCOL                *Root;   /* 卷根，全模块共用，Main 统一关闭       */
} ANIM_FS;

/* ------------------------------------------------------------------ */
/* 动画数据（M3 输出：anim.bin 全量入内存，播放期零读盘）                  */
/* ------------------------------------------------------------------ */

typedef struct {
  ANIM_HEADER         Header;
  ANIM_FRAME_INDEX   *Index;       /* 指向 FileBuffer 内部（IndexOwned=FALSE）       */
  UINT8              *FileBuffer;  /* anim.bin 全量缓冲                                */
  UINTN               FileSize;
  BOOLEAN             IndexOwned;   /* 预留：索引独立分配时需单独 FreePool            */
} ANIM_DATA;

/* ------------------------------------------------------------------ */
/* 显示状态（M1 输出，M4 消费；§3.1/§3.2）                                */
/* ------------------------------------------------------------------ */

typedef struct {
  EFI_GRAPHICS_OUTPUT_PROTOCOL    *Gop;
  UINT32    ModeIndex;            /* 当前模式号                                        */
  UINT32    Width, Height;        /* Info->Horizontal/VerticalResolution               */
  UINT32    Stride;               /* = Info->PixelsPerScanLine（像素），防斜纹关键      */
  EFI_GRAPHICS_PIXEL_FORMAT  Format;
  BOOLEAN   BltOnly;              /* TRUE = 无线性 framebuffer，渲染走 Blt() 降级      */
  UINT8    *FrameBase;            /* 线性 framebuffer 基址（BltOnly 时 NULL）          */
  UINT32    FrameBpp;             /* 线性模式每像素字节（BltOnly 时 0）                */
  UINT32    RMask, GMask, BMask;  /* PixelBitMask 格式的通道掩码                       */
  UINT8     RShift, GShift, BShift; /* 掩码推导的通道移位（目标像素组装用）             */
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL  *Back; /* BackBuffer，B,G,R,X 顺序（与 Blt 兼容）      */
  UINT8                           *Scratch; /* QOI 解码暂存（W*H*4 RGBA，M4 按需分配） */
} ANIM_DISPLAY;

/* ------------------------------------------------------------------ */
/* 帧率控制（M5：TSC 校准 + 剩余时间 Stall 补偿，§3.3）                   */
/* ------------------------------------------------------------------ */

typedef struct {
  UINT64    TscPerUs;      /* 每微秒 TSC 计数；0 = 校准出界 → 纯 Stall 退化模式（P7）   */
  UINT64    IntervalUs;    /* 帧间隔 µs = 1,000,000 / fps（24/30/60/120 档 §3.3）     */
} ANIM_TIMER;

/* ------------------------------------------------------------------ */
/* 小工具（避免 CRT / PrintLib 依赖）                                    */
/* ------------------------------------------------------------------ */

/** CHAR16 串长（自实现，零库依赖）*/
STATIC
UINTN
AnimStrLen (
  IN CONST CHAR16  *S
  )
{
  UINTN N;

  N = 0;
  if (S != NULL) {
    while (S[N] != L'\0') {
      N++;
    }
  }
  return N;
}

/** 控制台文本输出（不依赖 PrintLib；GOP 失败时的文字提示用）*/
STATIC
VOID
AnimPrint (
  IN CONST CHAR16  *S
  )
{
  if ((S != NULL) && (gST != NULL) && (gST->ConOut != NULL)) {
    gST->ConOut->OutputString (gST->ConOut, (CHAR16 *)S);
  }
}

#endif /* UEFI_ANIM_PKG_H_ */

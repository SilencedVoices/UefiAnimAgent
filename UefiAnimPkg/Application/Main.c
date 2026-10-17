/** @file Main.c — UEFI 开机动画 Agent 主程序
  依据：设计文档 §3.4（主循环伪代码）、§4.1（模块装配顺序）、§4.5（最坏路径原则）
  装配：M1 GOP → M2 配置 → M3 资源 → M4 渲染 ⇄ M5 帧率 ⇄ M6 输入 → M7 Chainload
  铁律：任何模块失败都不阻塞 Windows 启动——最坏路径永远是"跳过动画，正常移交 bootmgfw"。
**/

#include "UefiAnimPkg.h"
#include "Gop.h"
#include "Config.h"
#include "AnimLoad.h"
#include "Render.h"
#include "FrameRate.h"
#include "Input.h"
#include "Chainload.h"

/* ------------------------------------------------------------------ */
/* 小工具：16 进制状态码打印（不依赖 PrintLib）                          */
/* ------------------------------------------------------------------ */

STATIC
VOID
AnimPrintHex32 (
  IN UINT32  Value
  )
{
  CHAR16   Buf[11];
  UINT32   Nib;
  INT32    I;

  Buf[0]  = L'0';
  Buf[1]  = L'x';
  Buf[10] = L'\0';
  for (I = 9; I >= 2; I--) {
    Nib = Value & 0xF;
    Buf[I] = (CHAR16)((Nib < 10) ? (L'0' + Nib) : (L'A' + Nib - 10));
    Value >>= 4;
  }
  AnimPrint (Buf);
}

/* ------------------------------------------------------------------ */
/* 清理路径：M4/M3 资源 + 卷根                                          */
/* ------------------------------------------------------------------ */

STATIC
VOID
AnimCleanupAll (
  IN OUT ANIM_DISPLAY  *Display,
  IN OUT ANIM_DATA     *Data,
  IN OUT ANIM_FS       *Fs
  )
{
  AnimRenderCleanup (Display);
  AnimFreeAnimation (Data);
  if ((Fs != NULL) && (Fs->Root != NULL)) {
    Fs->Root->Close (Fs->Root);
    Fs->Root = NULL;
  }
}

/* ------------------------------------------------------------------ */
/* 入口                                                                 */
/* ------------------------------------------------------------------ */

EFI_STATUS
EFIAPI
UefiMain (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS     Status;
  ANIM_CONFIG    Config;
  ANIM_FS        Fs;
  ANIM_DISPLAY   Display;
  ANIM_DATA      Data;
  ANIM_TIMER     Timer;
  ANIM_INPUT     Input;
  UINT32         Fps;
  UINT32         Frame;
  UINT64         FrameTsc;
  BOOLEAN        AnimReady;

  /* 文本光标关闭，避免动画上闪标 */
  if ((gST->ConOut != NULL)) {
    gST->ConOut->EnableCursor (gST->ConOut, FALSE);
  }

  /* ---- M2 配置（失败 → 默认值，不阻塞）---- */
  Status = AnimConfigLoad (ImageHandle, &Fs, &Config);
  if (EFI_ERROR (Status)) {
    ZeroMem (&Fs, sizeof (Fs));
  }

  AnimReady = FALSE;

  /* ---- M1 GOP（失败 → 文字提示后直接 chainload，§4.5）---- */
  ZeroMem (&Display, sizeof (Display));
  Status = AnimDisplayInit (&Config, &Display);
  if (EFI_ERROR (Status)) {
    AnimPrint (L"\r\n [BootAnim] GOP not available, skip animation.\r\n");
    goto Chainload;
  }

  /* ---- M3 资源（缺 anim.bin → 直接 chainload，§4.5 / T8）---- */
  ZeroMem (&Data, sizeof (Data));
  Status = AnimLoadAnimation (&Fs, &Config, &Data);
  if (EFI_ERROR (Status)) {
    goto Cleanup;
  }

  /* ---- M4 BackBuffer / QOI Scratch ---- */
  Status = AnimRenderInit (&Display, &Data);
  if (EFI_ERROR (Status)) {
    goto Cleanup;
  }

  /* ---- M5 帧率：config 优先 → FpsHint → 30（§2.3）---- */
  Fps = Config.Fps;
  if (Fps == 0) {
    Fps = Data.Header.FpsHint;
  }
  if ((Fps < ANIM_FPS_MIN) || (Fps > ANIM_FPS_MAX)) {
    Fps = ANIM_DEFAULT_FPS;
  }
  AnimTimerInit (&Timer, Fps);

  /* ---- M6 输入 ---- */
  AnimInputInit (&Input, Config.SkipScanCode);

  AnimReady = TRUE;

  /* ---- 主循环（§3.4）---- */
  for (Frame = 0; Frame < Data.Header.FrameCount; Frame++) {
    if (AnimCheckSkip (&Input)) {
      break;    /* ESC/回车 → 立即中止（T6）*/
    }
    FrameTsc = AnimTimerNow ();
    Status = AnimRenderFrame (&Display, &Data, Frame);
    if (EFI_ERROR (Status)) {
      break;    /* 解码/渲染失败 → 中止动画，保底 chainload */
    }
    AnimFrameWait (&Timer, FrameTsc);   /* 剩余时间 Stall 补偿（§3.3）*/
  }

Cleanup:
  /* 释放全部大块资源（bootmgfw 需要内存），错误码不改变走向 */
  AnimCleanupAll (&Display, &Data, &Fs);
  (VOID)AnimReady;

Chainload:
  /* ---- M7：最坏路径永远是移交 bootmgfw（§4.5）---- */
  Status = AnimChainload (ImageHandle, &Config);
  if (EFI_ERROR (Status)) {
    /* T7：错误提示 3 秒 → 安全退出（返回启动管理器继续下一项，不悬挂）*/
    AnimPrint (L"\r\n [BootAnim] chainload failed, status = ");
    AnimPrintHex32 ((UINT32)Status);
    AnimPrint (L"\r\n [BootAnim] target: \\EFI\\Microsoft\\Boot\\bootmgfw.efi\r\n");
    gBS->Stall (3 * 1000000);
  }
  return EFI_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* CRT 兼容 shim：MSVC /O1 优化会对块拷贝生成 memcpy 调用，              */
/* 而 edk2 以 /NODEFAULTLIB 链接（无 CRT）。此处以 BaseMemoryLib 实现。  */
/* ------------------------------------------------------------------ */

VOID *
memcpy (
  OUT VOID       *Dest,
  IN  CONST VOID *Src,
  IN  UINTN       Count
  )
{
  CopyMem (Dest, (VOID *)Src, Count);
  return Dest;
}

VOID *
memset (
  OUT VOID  *Dest,
  IN  INT32  Value,
  IN  UINTN  Count
  )
{
  SetMem (Dest, Count, (UINT8)Value);
  return Dest;
}

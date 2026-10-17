/** @file Gop.c — M1 GOP 初始化与降级策略实现
  依据：设计文档 §3.1（模式枚举/降级）、§3.2（BltOnly 检测）、R4/R5（重读三参数）
**/

#include "Gop.h"

/* ------------------------------------------------------------------ */
/* 掩码工具：求掩码最低置位（通道移位量）与最高置位（位宽）               */
/* ------------------------------------------------------------------ */

STATIC
UINT8
AnimMaskShift (
  IN UINT32  Mask
  )
{
  UINT8  Idx;

  if (Mask == 0) {
    return 0;             /* 通道不存在，保守返回 0 */
  }
  for (Idx = 0; Idx < 32; Idx++) {
    if ((Mask & (((UINT32)1) << Idx)) != 0) {
      return Idx;
    }
  }
  return 0;
}

STATIC
UINT8
AnimMaskHighBit (
  IN UINT32  Mask
  )
{
  UINT8  Idx;
  UINT8  High;

  High = 0;
  if (Mask == 0) {
    return 0;
  }
  for (Idx = 0; Idx < 32; Idx++) {
    if ((Mask & (((UINT32)1) << Idx)) != 0) {
      High = Idx;
    }
  }
  return (UINT8)(High + 1);
}

/* ------------------------------------------------------------------ */
/* 从 Gop->Mode 重读全部显示参数（SetMode 后必须调用，§3.1-3）          */
/* ------------------------------------------------------------------ */

STATIC
VOID
AnimDisplayFromMode (
  IN OUT ANIM_DISPLAY  *Display
  )
{
  EFI_GRAPHICS_OUTPUT_MODE_INFORMATION  *Info;
  UINT32                                 All;
  UINT8                                  High;

  Info = Display->Gop->Mode->Info;

  Display->Width     = Info->HorizontalResolution;
  Display->Height    = Info->VerticalResolution;
  Display->Stride    = Info->PixelsPerScanLine;   /* 像素步幅，§3.2 防斜纹关键 */
  Display->Format    = Info->PixelFormat;
  Display->ModeIndex = Display->Gop->Mode->Mode;

  Display->BltOnly   = (BOOLEAN)(Display->Format == PixelBltOnly);
  Display->FrameBase = (UINT8 *)(UINTN)Display->Gop->Mode->FrameBufferBase;

  Display->RMask  = Info->PixelInformation.RedMask;
  Display->GMask  = Info->PixelInformation.GreenMask;
  Display->BMask  = Info->PixelInformation.BlueMask;
  Display->RShift = AnimMaskShift (Display->RMask);
  Display->GShift = AnimMaskShift (Display->GMask);
  Display->BShift = AnimMaskShift (Display->BMask);

  Display->FrameBpp = 0;
  if (!Display->BltOnly) {
    switch (Display->Format) {
      case PixelRedGreenBlueReserved8BitPerColor:   /* R,G,B,X 各 8bit */
      case PixelBlueGreenRedReserved8BitPerColor:   /* B,G,R,X 各 8bit */
        Display->FrameBpp = 4;
        break;
      case PixelBitMask:
        All  = Display->RMask | Display->GMask | Display->BMask;
        High = AnimMaskHighBit (All);
        Display->FrameBpp = (UINT32)((High + 7) / 8);
        break;
      default:
        break;
    }
  }

  if (Display->BltOnly) {
    Display->FrameBase = NULL;    /* Blt 降级路径（§3.2 / R4）*/
  }
}

/* ------------------------------------------------------------------ */
/* 模式探测：宽高匹配且非 PixelBltOnly                                  */
/* ------------------------------------------------------------------ */

STATIC
BOOLEAN
AnimModeMatches (
  IN EFI_GRAPHICS_OUTPUT_PROTOCOL  *Gop,
  IN UINT32                         ModeIndex,
  IN UINT32                         Width,
  IN UINT32                         Height
  )
{
  EFI_GRAPHICS_OUTPUT_MODE_INFORMATION  *Info;
  UINTN                                  Size;
  EFI_STATUS                             Status;
  BOOLEAN                                Ok;

  Info   = NULL;
  Status = Gop->QueryMode (Gop, ModeIndex, &Size, &Info);
  if (EFI_ERROR (Status) || (Info == NULL)) {
    return FALSE;
  }
  Ok = (BOOLEAN)((Info->HorizontalResolution == Width) &&
                 (Info->VerticalResolution  == Height) &&
                 (Info->PixelFormat != PixelBltOnly));
  gBS->FreePool (Info);
  return Ok;
}

/* ------------------------------------------------------------------ */
/* 当前模式可用性（P6 画幅修复）：
   ≥1280x720 且宽高比属 16:9 族（容差 ~3%，兼容 1366x768 等微差比例）。
   4:3 / 16:10 / 过小模式视为不理想，才值得冒险 SetMode。               */
/* ------------------------------------------------------------------ */

STATIC
BOOLEAN
AnimModeIsUsable (
  IN UINT32  Width,
  IN UINT32  Height
  )
{
  UINT64  Diff;

  if ((Width < 1280) || (Height < 720)) {
    return FALSE;
  }
  Diff = (UINT64)Width * 9;
  if (Diff >= (UINT64)Height * 16) {
    Diff -= (UINT64)Height * 16;
  } else {
    Diff = (UINT64)Height * 16 - Diff;
  }
  return (BOOLEAN)(Diff * 32 <= (UINT64)Height * 16);
}

/* ------------------------------------------------------------------ */
/* M1 入口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimDisplayInit (
  IN  ANIM_CONFIG   *Config,
  OUT ANIM_DISPLAY  *Display
  )
{
  EFI_STATUS                     Status;
  UINT32                         Prefs[ANIM_FALLBACK_MODE_COUNT + 1][2];
  UINT32                         Fallback[ANIM_FALLBACK_MODE_COUNT][2];
  UINT32                         PrefCount;
  UINT32                         P;
  UINT32                         Mode;
  UINT32                         MaxMode;
  BOOLEAN                        Found;
  EFI_GRAPHICS_OUTPUT_PROTOCOL  *Gop;

  ZeroMem (Display, sizeof (*Display));

  Gop = NULL;
  Status = gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop);
  if (EFI_ERROR (Status) || (Gop == NULL)) {
    return (EFI_ERROR (Status) ? Status : EFI_NOT_FOUND);
  }
  Display->Gop = Gop;

  /* 降级优先级表（§3.1）：config 指定 → 2560x1440 → 1920x1080 → 1280x720 */
  Fallback[0][0] = 2560; Fallback[0][1] = 1440;
  Fallback[1][0] = 1920; Fallback[1][1] = 1080;
  Fallback[2][0] = 1280; Fallback[2][1] = 720;

  PrefCount = 0;
  if ((Config->ReqWidth != 0) && (Config->ReqHeight != 0)) {
    Prefs[PrefCount][0] = Config->ReqWidth;
    Prefs[PrefCount][1] = Config->ReqHeight;
    PrefCount++;
  }
  for (P = 0; P < ANIM_FALLBACK_MODE_COUNT; P++) {
    /* 去重：config 已指定的尺寸不重复加入 */
    if ((PrefCount > 0) &&
        (Prefs[0][0] == Fallback[P][0]) && (Prefs[0][1] == Fallback[P][1])) {
      continue;
    }
    Prefs[PrefCount][0] = Fallback[P][0];
    Prefs[PrefCount][1] = Fallback[P][1];
    PrefCount++;
  }

  MaxMode = Gop->Mode->MaxMode;
  Found   = FALSE;

  /* P6 画幅修复策略（按优先级）：
     1) config 显式指定（非 auto）→ 精确匹配优先，失败走降级表（下方流程）
     2) auto 且当前模式可用（≥720p + 16:9 族 + 非 BltOnly）→ 沿用当前模式，
        零 SetMode——避免"固件报告 2K 可设但显示器原生 1080p"的拉伸错位
     3) auto 且当前模式不理想（4:3 / 16:10 / 过小）→ 才走降级表尝试切换 */
  if ((Config->ReqWidth == 0) || (Config->ReqHeight == 0)) {
    EFI_GRAPHICS_OUTPUT_MODE_INFORMATION  *CurInfo;

    CurInfo = Gop->Mode->Info;
    if ((CurInfo->PixelFormat != PixelBltOnly) &&
        AnimModeIsUsable (CurInfo->HorizontalResolution,
                          CurInfo->VerticalResolution)) {
      Found = TRUE;    /* 沿用当前模式：跳过全部 SetMode 尝试 */
    }
  }

  for (P = 0; (P < PrefCount) && !Found; P++) {
    for (Mode = 0; Mode < MaxMode; Mode++) {
      if (AnimModeMatches (Gop, Mode, Prefs[P][0], Prefs[P][1])) {
        Status = Gop->SetMode (Gop, Mode);
        if (!EFI_ERROR (Status)) {
          Found = TRUE;
        }
        break;   /* 该尺寸只试首个匹配模式；SetMode 失败则进入下一优先级 */
      }
    }
  }

  /* 全部优先级失败：保持当前模式不切换（§3.1 最后一级），并重读参数 */
  AnimDisplayFromMode (Display);

  if ((Display->Width == 0) || (Display->Height == 0) || (Display->Stride == 0)) {
    return EFI_UNSUPPORTED;
  }
  if (!Display->BltOnly && (Display->FrameBase == NULL)) {
    return EFI_UNSUPPORTED;
  }
  return EFI_SUCCESS;
}

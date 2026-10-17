/** @file Render.c — M4 双缓冲渲染实现
  依据：设计文档 §3.2（BackBuffer / CopyMem 投送 / Blt 降级）、§3.1-4（居中留黑不缩放）
  像素约定：Back 为 EFI_GRAPHICS_OUTPUT_BLT_PIXEL 序（B,G,R,X），与 Blt() 输入一致。
**/

#include "Render.h"
#include "Qoi.h"

/* ------------------------------------------------------------------ */
/* 居中/裁剪几何                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
  UINT32  DstX0;    /* 目标起始列（Back 内）*/
  UINT32  DstY0;    /* 目标起始行           */
  UINT32  SrcX0;    /* 源起始列             */
  UINT32  SrcY0;    /* 源起始行             */
  UINT32  Width;    /* 拷贝宽               */
  UINT32  Height;   /* 拷贝高               */
} ANIM_BLIT_GEOM;

STATIC
VOID
AnimCalcGeom (
  IN  UINT32           DstWidth,
  IN  UINT32           DstHeight,
  IN  UINT32           SrcWidth,
  IN  UINT32           SrcHeight,
  OUT ANIM_BLIT_GEOM  *Geom
  )
{
  if (SrcWidth <= DstWidth) {
    Geom->DstX0 = (DstWidth - SrcWidth) / 2;
    Geom->SrcX0 = 0;
    Geom->Width = SrcWidth;
  } else {
    Geom->DstX0 = 0;
    Geom->SrcX0 = (SrcWidth - DstWidth) / 2;
    Geom->Width = DstWidth;
  }
  if (SrcHeight <= DstHeight) {
    Geom->DstY0 = (DstHeight - SrcHeight) / 2;
    Geom->SrcY0 = 0;
    Geom->Height = SrcHeight;
  } else {
    Geom->DstY0 = 0;
    Geom->SrcY0 = (SrcHeight - DstHeight) / 2;
    Geom->Height = DstHeight;
  }
}

/* ------------------------------------------------------------------ */
/* 源 → Back 转换（BMP 的 BGR24 行 / QOI 的 RGBA32 行）                  */
/* ------------------------------------------------------------------ */

/* BMP_RAW24：源为 top-down BGR 三元组（§2.3）*/
STATIC
VOID
AnimBltFromBgr (
  IN OUT ANIM_DISPLAY                *Display,
  IN CONST UINT8                     *SrcBgr,
  IN UINT32                           SrcWidth,
  IN UINT32                           SrcHeight
  )
{
  ANIM_BLIT_GEOM  Geom;
  UINT32          Row;
  UINT32          Col;

  AnimCalcGeom (Display->Width, Display->Height, SrcWidth, SrcHeight, &Geom);

  ZeroMem (Display->Back, (UINTN)Display->Stride * Display->Height * sizeof (EFI_GRAPHICS_OUTPUT_BLT_PIXEL));

  for (Row = 0; Row < Geom.Height; Row++) {
    EFI_GRAPHICS_OUTPUT_BLT_PIXEL  *Dst;
    CONST UINT8                    *Src;

    Dst = &Display->Back[(UINTN)(Geom.DstY0 + Row) * Display->Stride + Geom.DstX0];
    Src = &SrcBgr[((UINTN)(Geom.SrcY0 + Row) * SrcWidth + Geom.SrcX0) * 3];
    for (Col = 0; Col < Geom.Width; Col++) {
      Dst[Col].Blue     = Src[Col * 3 + 0];
      Dst[Col].Green    = Src[Col * 3 + 1];
      Dst[Col].Red      = Src[Col * 3 + 2];
      Dst[Col].Reserved = 0;
    }
  }
}

/* QOI：Scratch 为 RGBA 四元组（解码器输出）*/
STATIC
VOID
AnimBltFromRgba (
  IN OUT ANIM_DISPLAY                *Display,
  IN CONST UINT8                     *SrcRgba,
  IN UINT32                           SrcWidth,
  IN UINT32                           SrcHeight
  )
{
  ANIM_BLIT_GEOM  Geom;
  UINT32          Row;
  UINT32          Col;

  AnimCalcGeom (Display->Width, Display->Height, SrcWidth, SrcHeight, &Geom);

  ZeroMem (Display->Back, (UINTN)Display->Stride * Display->Height * sizeof (EFI_GRAPHICS_OUTPUT_BLT_PIXEL));

  for (Row = 0; Row < Geom.Height; Row++) {
    EFI_GRAPHICS_OUTPUT_BLT_PIXEL  *Dst;
    CONST UINT8                    *Src;

    Dst = &Display->Back[(UINTN)(Geom.DstY0 + Row) * Display->Stride + Geom.DstX0];
    Src = &SrcRgba[((UINTN)(Geom.SrcY0 + Row) * SrcWidth + Geom.SrcX0) * 4];
    for (Col = 0; Col < Geom.Width; Col++) {
      Dst[Col].Blue     = Src[Col * 4 + 2];
      Dst[Col].Green    = Src[Col * 4 + 1];
      Dst[Col].Red      = Src[Col * 4 + 0];
      Dst[Col].Reserved = 0;
    }
  }
}

/* ------------------------------------------------------------------ */
/* Back → 显存投送（§3.2）                                              */
/* ------------------------------------------------------------------ */

STATIC
EFI_STATUS
AnimPresent (
  IN OUT ANIM_DISPLAY  *Display
  )
{
  UINT64  CopyBytes;

  if (Display->BltOnly) {
    /* 降级路径（§3.2 / R4）：Blt 输入即 BLT 像素序，Back 直用 */
    return Display->Gop->Blt (
                           Display->Gop,
                           Display->Back,
                           EfiBltBufferToVideo,
                           0, 0, 0, 0,
                           Display->Width, Display->Height,
                           0
                           );
  }

  switch (Display->Format) {
    case PixelBlueGreenRedReserved8BitPerColor:
      /* 显存布局 B,G,R,X 与 Back 完全一致：一次 CopyMem（§3.2）*/
      CopyBytes = (UINT64)Display->Stride * Display->Height * 4;
      CopyMem ((VOID *)Display->FrameBase, Display->Back, (UINTN)CopyBytes);
      return EFI_SUCCESS;

    case PixelRedGreenBlueReserved8BitPerColor: {
      /* R,G,B,X：就地交换 Back 的 R/B 通道后 CopyMem。
         Back 下一帧会被完整重写（ZeroMem+填充），就地破坏无副作用。*/
      UINT32  PixelCount;
      UINT32  I;

      PixelCount = Display->Stride * Display->Height;
      for (I = 0; I < PixelCount; I++) {
        UINT8  T;

        T = Display->Back[I].Blue;
        Display->Back[I].Blue = Display->Back[I].Red;
        Display->Back[I].Red  = T;
      }
      CopyBytes = (UINT64)Display->Stride * Display->Height * 4;
      CopyMem ((VOID *)Display->FrameBase, Display->Back, (UINTN)CopyBytes);
      return EFI_SUCCESS;
    }

    case PixelBitMask: {
      /* 通用掩码路径：逐像素组装，小端写 FrameBpp 字节 */
      UINT32  Row;
      UINT32  Col;

      for (Row = 0; Row < Display->Height; Row++) {
        UINT8  *DstRow;

        DstRow = Display->FrameBase + (UINTN)Row * Display->Stride * Display->FrameBpp;
        for (Col = 0; Col < Display->Width; Col++) {
          EFI_GRAPHICS_OUTPUT_BLT_PIXEL  *Px;
          UINT32                          Val;
          UINT8                           B;

          Px  = &Display->Back[(UINTN)Row * Display->Stride + Col];
          Val = ((((UINT32)Px->Red)   << Display->RShift) & Display->RMask) |
                ((((UINT32)Px->Green) << Display->GShift) & Display->GMask) |
                ((((UINT32)Px->Blue)  << Display->BShift) & Display->BMask);
          for (B = 0; B < (UINT8)Display->FrameBpp; B++) {
            DstRow[(UINTN)Col * Display->FrameBpp + B] = (UINT8)((Val >> (B * 8)) & 0xFF);
          }
        }
      }
      return EFI_SUCCESS;
    }

    default:
      return EFI_UNSUPPORTED;
  }
}

/* ------------------------------------------------------------------ */
/* M4 接口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimRenderInit (
  IN OUT ANIM_DISPLAY  *Display,
  IN ANIM_DATA         *Data
  )
{
  EFI_STATUS  Status;
  UINT64      Pixels;

  Pixels = (UINT64)Display->Stride * Display->Height;

  Display->Back = NULL;
  Status = gBS->AllocatePool (
                  EfiBootServicesData,
                  (UINTN)(Pixels * sizeof (EFI_GRAPHICS_OUTPUT_BLT_PIXEL)),
                  (VOID **)&Display->Back
                  );
  if (EFI_ERROR (Status)) {
    return Status;
  }

  Display->Scratch = NULL;
  if (Data->Header.Format == ANIM_FORMAT_QOI) {
    Status = gBS->AllocatePool (
                    EfiBootServicesData,
                    (UINTN)((UINT64)Data->Header.Width * Data->Header.Height * 4),
                    (VOID **)&Display->Scratch
                    );
    if (EFI_ERROR (Status)) {
      gBS->FreePool (Display->Back);
      Display->Back = NULL;
      return Status;
    }
  }
  return EFI_SUCCESS;
}

VOID
AnimRenderCleanup (
  IN OUT ANIM_DISPLAY  *Display
  )
{
  if (Display->Back != NULL) {
    gBS->FreePool (Display->Back);
    Display->Back = NULL;
  }
  if (Display->Scratch != NULL) {
    gBS->FreePool (Display->Scratch);
    Display->Scratch = NULL;
  }
}

EFI_STATUS
AnimRenderFrame (
  IN OUT ANIM_DISPLAY  *Display,
  IN ANIM_DATA         *Data,
  IN UINT32             FrameIndex
  )
{
  EFI_STATUS   Status;
  CONST UINT8 *FrameData;
  UINTN        FrameSize;

  if ((Display->Back == NULL) || (FrameIndex >= Data->Header.FrameCount)) {
    return EFI_INVALID_PARAMETER;
  }

  FrameData = Data->FileBuffer + Data->Index[FrameIndex].Offset;
  FrameSize = Data->Index[FrameIndex].Size;

  if (Data->Header.Format == ANIM_FORMAT_BMP_RAW24) {
    AnimBltFromBgr (Display, FrameData, Data->Header.Width, Data->Header.Height);
  } else {
    if (Display->Scratch == NULL) {
      return EFI_NOT_READY;
    }
    Status = QoiDecode (
               FrameData,
               FrameSize,
               Display->Scratch,
               (UINTN)((UINT64)Data->Header.Width * Data->Header.Height * 4)
               );
    if (EFI_ERROR (Status)) {
      return Status;
    }
    AnimBltFromRgba (Display, Display->Scratch, Data->Header.Width, Data->Header.Height);
  }

  return AnimPresent (Display);
}

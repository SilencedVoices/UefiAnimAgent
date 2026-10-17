/** @file Qoi.c — QOI 解码器实现（UEFI 纯 C，约 300 行）
  格式规范：https://qoiformat.org —— "Quite OK Image" by Dominic Szablewski
  （规范文本为公共领域；本实现按规范独立编写，随项目以 MIT 许可发布）
  支持全部 opcode：QOI_OP_RGB / QOI_OP_RGBA / QOI_OP_INDEX / QOI_OP_DIFF /
                   QOI_OP_LUMA / QOI_OP_RUN（设计文档 §2.3）
  通道：channels=3 时 A 恒 255；输出统一 RGBA8888。
**/

#include "Qoi.h"

/* ------------------------------------------------------------------ */
/* 常量                                                                 */
/* ------------------------------------------------------------------ */

#define QOI_HEADER_SIZE    14
#define QOI_INDEX_SIZE     64

/* 单字节 opcode */
#define QOI_OP_RGB         0xFE
#define QOI_OP_RGBA        0xFF

/* 2-bit tag opcode */
#define QOI_OP_INDEX       0x00
#define QOI_OP_DIFF        0x40
#define QOI_OP_LUMA        0x80
#define QOI_OP_RUN         0xC0
#define QOI_TAG_MASK       0xC0

/* 尺寸护栏（与 AnimLoad 一致，防异常头巨量分配判断）*/
#define QOI_MAX_DIM        8192

/* ------------------------------------------------------------------ */
/* 内部类型                                                             */
/* ------------------------------------------------------------------ */

#pragma pack(1)
typedef struct {
  UINT8  R;
  UINT8  G;
  UINT8  B;
  UINT8  A;
} QOI_RGBA;
#pragma pack()

/* ------------------------------------------------------------------ */
/* 小工具：大端读取与索引哈希                                            */
/* ------------------------------------------------------------------ */

STATIC
UINT32
QoiBe32 (
  IN CONST UINT8  *P
  )
{
  return ((UINT32)P[0] << 24) | ((UINT32)P[1] << 16) |
         ((UINT32)P[2] << 8)  |  (UINT32)P[3];
}

STATIC
UINT8
QoiIndexHash (
  IN CONST QOI_RGBA  *Px
  )
{
  return (UINT8)(((UINT32)Px->R * 3 + (UINT32)Px->G * 5 +
                  (UINT32)Px->B * 7 + (UINT32)Px->A * 11) % QOI_INDEX_SIZE);
}

/* ------------------------------------------------------------------ */
/* 头解析                                                               */
/* ------------------------------------------------------------------ */

EFI_STATUS
QoiInfo (
  IN  CONST UINT8  *Data,
  IN  UINTN         Size,
  OUT UINT32       *Width,
  OUT UINT32       *Height,
  OUT UINT8        *Channels
  )
{
  UINT32  W;
  UINT32  H;

  if ((Data == NULL) || (Size < QOI_HEADER_SIZE)) {
    return EFI_VOLUME_CORRUPTED;
  }
  if ((Data[0] != 'q') || (Data[1] != 'o') ||
      (Data[2] != 'i') || (Data[3] != 'f')) {
    return EFI_VOLUME_CORRUPTED;
  }

  W = QoiBe32 (&Data[4]);
  H = QoiBe32 (&Data[8]);
  if ((W == 0) || (H == 0) || (W > QOI_MAX_DIM) || (H > QOI_MAX_DIM)) {
    return EFI_VOLUME_CORRUPTED;
  }
  if ((Data[12] != 3) && (Data[12] != 4)) {
    return EFI_UNSUPPORTED;    /* channels 仅 3/4 */
  }

  if (Width != NULL) {
    *Width = W;
  }
  if (Height != NULL) {
    *Height = H;
  }
  if (Channels != NULL) {
    *Channels = Data[12];
  }
  return EFI_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* 解码主循环                                                           */
/* ------------------------------------------------------------------ */

EFI_STATUS
QoiDecode (
  IN  CONST UINT8  *Data,
  IN  UINTN         Size,
  OUT UINT8        *RgbaOut,
  IN  UINTN         OutBytes
  )
{
  EFI_STATUS  Status;
  UINT32      Width;
  UINT32      Height;
  UINT8       Channels;
  UINT64      PixelCount;
  UINT64      Need;
  QOI_RGBA    Index[QOI_INDEX_SIZE];
  QOI_RGBA    Px;
  UINTN       Pos;
  UINT64      PxIndex;
  UINT8       B;

  if ((Data == NULL) || (RgbaOut == NULL)) {
    return EFI_INVALID_PARAMETER;
  }

  /* ---- 头 ---- */
  Status = QoiInfo (Data, Size, &Width, &Height, &Channels);
  if (EFI_ERROR (Status)) {
    return Status;
  }

  PixelCount = (UINT64)Width * (UINT64)Height;
  Need       = PixelCount * 4;
  if (Need != (UINT64)OutBytes) {
    /* 与 anim.bin 头声明的动画尺寸不一致：宁可拒帧不做越界写 */
    return (Need > (UINT64)OutBytes) ? EFI_BAD_BUFFER_SIZE : EFI_VOLUME_CORRUPTED;
  }

  /* ---- 状态 ---- */
  ZeroMem (Index, sizeof (Index));
  Px.R = 0;
  Px.G = 0;
  Px.B = 0;
  Px.A = 255;

  Pos     = QOI_HEADER_SIZE;
  PxIndex = 0;

  while (PxIndex < PixelCount) {
    if (Pos >= Size) {
      return EFI_VOLUME_CORRUPTED;    /* 输入截断 */
    }
    B = Data[Pos++];

    if (B == QOI_OP_RGB) {
      /* ---- QOI_OP_RGB：R,G,B 三字节，A 保持 ---- */
      if (Pos + 3 > Size) {
        return EFI_VOLUME_CORRUPTED;
      }
      Px.R = Data[Pos + 0];
      Px.G = Data[Pos + 1];
      Px.B = Data[Pos + 2];
      Pos += 3;
      Index[QoiIndexHash (&Px)] = Px;

    } else if (B == QOI_OP_RGBA) {
      /* ---- QOI_OP_RGBA：R,G,B,A 四字节 ---- */
      if (Pos + 4 > Size) {
        return EFI_VOLUME_CORRUPTED;
      }
      Px.R = Data[Pos + 0];
      Px.G = Data[Pos + 1];
      Px.B = Data[Pos + 2];
      Px.A = Data[Pos + 3];
      Pos += 4;
      Index[QoiIndexHash (&Px)] = Px;

    } else if ((B & QOI_TAG_MASK) == QOI_OP_INDEX) {
      /* ---- QOI_OP_INDEX：64 项运行数组直取 ---- */
      Px = Index[B & 0x3F];

    } else if ((B & QOI_TAG_MASK) == QOI_OP_DIFF) {
      /* ---- QOI_OP_DIFF：2-bit 双补码增量，模 256 回绕（UINT8 自然回绕）---- */
      Px.R = (UINT8)(Px.R + (((B >> 4) & 0x03) - 2));
      Px.G = (UINT8)(Px.G + (((B >> 2) & 0x03) - 2));
      Px.B = (UINT8)(Px.B + ((B & 0x03) - 2));
      Index[QoiIndexHash (&Px)] = Px;

    } else if ((B & QOI_TAG_MASK) == QOI_OP_LUMA) {
      /* ---- QOI_OP_LUMA：绿差 6-bit + 红蓝相对绿差 4-bit ---- */
      UINT8  B2;
      INT8   Dg;
      INT8   DrDg;
      INT8   DbDg;

      if (Pos + 1 >= Size) {
        return EFI_VOLUME_CORRUPTED;
      }
      B2   = Data[Pos++];
      Dg   = (INT8)((B & 0x3F) - 32);
      DrDg = (INT8)(((B2 >> 4) & 0x0F) - 8);
      DbDg = (INT8)((B2 & 0x0F) - 8);
      Px.G = (UINT8)(Px.G + Dg);
      Px.R = (UINT8)(Px.R + Dg + DrDg);
      Px.B = (UINT8)(Px.B + Dg + DbDg);
      Index[QoiIndexHash (&Px)] = Px;

    } else {
      /* ---- QOI_OP_RUN：重复前一像素 1..62 次 ---- */
      UINT32  Run;
      UINT32  I;

      Run = ((UINT32)(B & 0x3F)) + 1;
      if ((UINT64)Run > (PixelCount - PxIndex)) {
        Run = (UINT32)(PixelCount - PxIndex);   /* 容错截断（写缓冲前钳制）*/
      }
      for (I = 0; I < Run; I++) {
        RgbaOut[(UINTN)PxIndex * 4 + 0] = Px.R;
        RgbaOut[(UINTN)PxIndex * 4 + 1] = Px.G;
        RgbaOut[(UINTN)PxIndex * 4 + 2] = Px.B;
        RgbaOut[(UINTN)PxIndex * 4 + 3] = Px.A;
        PxIndex++;
      }
      continue;    /* RUN 已写像素，跳过公共写入 */
    }

    /* ---- 公共写入当前像素 ---- */
    RgbaOut[(UINTN)PxIndex * 4 + 0] = Px.R;
    RgbaOut[(UINTN)PxIndex * 4 + 1] = Px.G;
    RgbaOut[(UINTN)PxIndex * 4 + 2] = Px.B;
    RgbaOut[(UINTN)PxIndex * 4 + 3] = Px.A;
    PxIndex++;
  }

  return EFI_SUCCESS;
}

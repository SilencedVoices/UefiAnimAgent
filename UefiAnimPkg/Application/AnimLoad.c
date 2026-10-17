/** @file AnimLoad.c — M3 资源加载实现
  依据：设计文档 §2.3（容器格式与校验）、§4 M3（一次 I/O 全量入内存）、§4.5（错误路径）
**/

#include "AnimLoad.h"

/* 尺寸护栏：防异常头导致巨量计算/越界 */
#define ANIM_MAX_DIM        8192
#define ANIM_MAX_FRAMES     65535   /* FrameCount 为 UINT16，天然上限 */

/* ------------------------------------------------------------------ */
/* 文件全量读取                                                         */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimReadFile (
  IN  EFI_FILE_PROTOCOL  *Root,
  IN  CONST CHAR16       *Path,
  OUT UINT8             **Buffer,
  OUT UINTN              *Size
  )
{
  EFI_STATUS       Status;
  EFI_FILE_PROTOCOL  *File;
  EFI_FILE_INFO    *Info;
  UINTN            InfoSize;
  UINT64           FileSize;
  UINT8            *Buf;

  if ((Root == NULL) || (Path == NULL) || (Buffer == NULL) || (Size == NULL)) {
    return EFI_INVALID_PARAMETER;
  }
  *Buffer = NULL;
  *Size   = 0;

  File = NULL;
  Status = Root->Open (Root, &File, (CHAR16 *)Path, EFI_FILE_MODE_READ, 0);
  if (EFI_ERROR (Status) || (File == NULL)) {
    return Status;
  }

  /* 第一次 GetInfo 取大小 */
  InfoSize = 0;
  Status = File->GetInfo (File, &gEfiFileInfoGuid, &InfoSize, NULL);
  if (EFI_ERROR (Status) && (Status != EFI_BUFFER_TOO_SMALL)) {
    File->Close (File);
    return Status;
  }
  Info = NULL;
  Status = gBS->AllocatePool (EfiBootServicesData, InfoSize, (VOID **)&Info);
  if (EFI_ERROR (Status)) {
    File->Close (File);
    return Status;
  }
  Status = File->GetInfo (File, &gEfiFileInfoGuid, &InfoSize, Info);
  if (EFI_ERROR (Status)) {
    gBS->FreePool (Info);
    File->Close (File);
    return Status;
  }
  FileSize = Info->FileSize;
  gBS->FreePool (Info);

  if (FileSize == 0) {
    File->Close (File);
    return EFI_UNSUPPORTED;
  }

  Buf = NULL;
  Status = gBS->AllocatePool (EfiBootServicesData, (UINTN)FileSize, (VOID **)&Buf);
  if (EFI_ERROR (Status)) {
    File->Close (File);
    return Status;
  }

  /* 保险起见回到文件头再读 */
  Status = File->SetPosition (File, 0);
  if (EFI_ERROR (Status)) {
    gBS->FreePool (Buf);
    File->Close (File);
    return Status;
  }

  InfoSize = (UINTN)FileSize;
  Status = File->Read (File, &InfoSize, Buf);
  File->Close (File);
  if (EFI_ERROR (Status)) {
    gBS->FreePool (Buf);
    return Status;
  }
  if (InfoSize != (UINTN)FileSize) {
    /* 短读：容错接受实际读取量（FAT 驱动个别实现），但不得为 0 */
    if (InfoSize == 0) {
      gBS->FreePool (Buf);
      return EFI_ABORTED;
    }
  }

  *Buffer = Buf;
  *Size   = InfoSize;
  return EFI_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* anim.bin 校验与装配                                                  */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimLoadAnimation (
  IN  ANIM_FS      *Fs,
  IN  ANIM_CONFIG  *Cfg,
  OUT ANIM_DATA    *Data
  )
{
  EFI_STATUS  Status;
  CHAR16      FullPath[ANIM_MAX_PATH * 2];
  UINT8      *Buf;
  UINTN       Size;
  UINTN       I;
  UINT32      PrefixLen;
  UINT64      Need;

  ZeroMem (Data, sizeof (*Data));

  if ((Fs == NULL) || (Fs->Root == NULL) || (Cfg == NULL)) {
    return EFI_NOT_FOUND;
  }

  /* 组路径：相对路径 → \EFI\BootAnim\ 前缀；绝对路径直用 */
  PrefixLen = 0;
  if (Cfg->AnimPath[0] != L'\\') {
    while ((PrefixLen < ANIM_MAX_PATH * 2 - 1) && (ANIM_DIR_PREFIX[PrefixLen] != L'\0')) {
      FullPath[PrefixLen] = ANIM_DIR_PREFIX[PrefixLen];
      PrefixLen++;
    }
  }
  for (I = 0; (PrefixLen + I < ANIM_MAX_PATH * 2 - 1) && (Cfg->AnimPath[I] != L'\0'); I++) {
    FullPath[PrefixLen + I] = Cfg->AnimPath[I];
  }
  FullPath[PrefixLen + I] = L'\0';

  Buf  = NULL;
  Size = 0;
  Status = AnimReadFile (Fs->Root, FullPath, &Buf, &Size);
  if (EFI_ERROR (Status) || (Buf == NULL)) {
    return Status;    /* 缺文件 → §4.5/T8 直接 chainload */
  }

  Data->FileBuffer = Buf;
  Data->FileSize   = Size;

  /* ---- 头校验 ---- */
  if (Size < sizeof (ANIM_HEADER)) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }
  CopyMem (&Data->Header, Buf, sizeof (ANIM_HEADER));

  if ((Data->Header.Magic[0] != 'U') || (Data->Header.Magic[1] != 'A') ||
      (Data->Header.Magic[2] != 'N') || (Data->Header.Magic[3] != '1')) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }
  if (Data->Header.Version != ANIM_VERSION_V1) {
    AnimFreeAnimation (Data);
    return EFI_INCOMPATIBLE_VERSION;
  }
  if ((Data->Header.Format != ANIM_FORMAT_BMP_RAW24) &&
      (Data->Header.Format != ANIM_FORMAT_QOI)) {
    AnimFreeAnimation (Data);
    return EFI_UNSUPPORTED;
  }
  if ((Data->Header.FrameCount == 0) || (Data->Header.FrameCount > ANIM_MAX_FRAMES)) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }
  if ((Data->Header.Width == 0) || (Data->Header.Height == 0) ||
      (Data->Header.Width  > ANIM_MAX_DIM) || (Data->Header.Height > ANIM_MAX_DIM)) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }

  /* ---- 索引校验（64 位算式防溢出）---- */
  if (Data->Header.IndexOffset < sizeof (ANIM_HEADER)) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }
  Need = (UINT64)Data->Header.IndexOffset +
         (UINT64)Data->Header.FrameCount * sizeof (ANIM_FRAME_INDEX);
  if (Need > (UINT64)Size) {
    AnimFreeAnimation (Data);
    return EFI_VOLUME_CORRUPTED;
  }
  Data->Index     = (ANIM_FRAME_INDEX *)(Buf + Data->Header.IndexOffset);
  Data->IndexOwned = FALSE;

  /* ---- 每帧边界校验 ---- */
  for (I = 0; I < Data->Header.FrameCount; I++) {
    UINT64  Off;
    UINT64  Sz;

    Off = Data->Index[I].Offset;
    Sz  = Data->Index[I].Size;
    if ((Off + Sz) > (UINT64)Size) {
      AnimFreeAnimation (Data);
      return EFI_VOLUME_CORRUPTED;
    }
    if (Sz == 0) {
      AnimFreeAnimation (Data);
      return EFI_VOLUME_CORRUPTED;
    }
    if (Data->Header.Format == ANIM_FORMAT_BMP_RAW24) {
      /* 契约：BMP 帧为去头后的 top-down BGR 行数据（§2.3）*/
      if (Sz != (UINT64)Data->Header.Width * Data->Header.Height * 3) {
        AnimFreeAnimation (Data);
        return EFI_VOLUME_CORRUPTED;
      }
    }
  }

  return EFI_SUCCESS;
}

VOID
AnimFreeAnimation (
  IN OUT ANIM_DATA  *Data
  )
{
  if (Data == NULL) {
    return;
  }
  if (Data->FileBuffer != NULL) {
    gBS->FreePool (Data->FileBuffer);
    Data->FileBuffer = NULL;
  }
  if (Data->IndexOwned && (Data->Index != NULL)) {
    gBS->FreePool (Data->Index);
  }
  Data->Index   = NULL;
  Data->FileSize = 0;
}

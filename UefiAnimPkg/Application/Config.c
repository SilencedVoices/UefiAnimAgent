/** @file Config.c — M2 配置读取实现
  依据：设计文档 §4.2（config.ini 格式）、§4.5（缺配置→默认值兜底）
  解析器为极简 INI：Key = Value，支持 ; 与 # 注释、[节] 忽略、大小写不敏感键名。
**/

#include "Config.h"
#include "AnimLoad.h"

/* ------------------------------------------------------------------ */
/* 默认值（§4.2）                                                       */
/* ------------------------------------------------------------------ */

STATIC
VOID
AnimConfigDefaults (
  OUT ANIM_CONFIG  *Config
  )
{
  CONST CHAR16  *DefAnim;
  CONST CHAR16  *DefChain;
  UINTN         I;

  ZeroMem (Config, sizeof (*Config));

  Config->Fps          = 0;                          /* 0 = 沿用 anim.bin FpsHint */
  Config->ReqWidth     = 0;                          /* auto → 降级链             */
  Config->ReqHeight    = 0;
  Config->SkipScanCode = ANIM_DEFAULT_SKIP_SCANCODE; /* EFI 扫描码 SCAN_ESC = 0x0017 */

  DefAnim  = L"anim.bin";
  DefChain = ANIM_CHAINLOAD_DEFAULT;
  for (I = 0; (I < ANIM_MAX_PATH - 1) && (DefAnim[I] != L'\0'); I++) {
    Config->AnimPath[I] = DefAnim[I];
  }
  Config->AnimPath[I] = L'\0';

  for (I = 0; (I < ANIM_MAX_PATH - 1) && (DefChain[I] != L'\0'); I++) {
    Config->ChainloadPath[I] = DefChain[I];
  }
  Config->ChainloadPath[I] = L'\0';

  Config->ConfigLoaded = FALSE;
}

/* ------------------------------------------------------------------ */
/* 打开自身所在 ESP（LoadedImage→DeviceHandle→SimpleFileSystem）          */
/* ------------------------------------------------------------------ */

STATIC
EFI_STATUS
AnimFsOpen (
  IN  EFI_HANDLE  ImageHandle,
  OUT ANIM_FS     *Fs
  )
{
  EFI_STATUS                         Status;
  EFI_LOADED_IMAGE_PROTOCOL         *LoadedImage;
  EFI_HANDLE                        *Handles;
  UINTN                              HandleCount;

  ZeroMem (Fs, sizeof (*Fs));

  LoadedImage = NULL;
  Status = gBS->HandleProtocol (
                  ImageHandle,
                  &gEfiLoadedImageProtocolGuid,
                  (VOID **)&LoadedImage
                  );
  if (EFI_ERROR (Status) || (LoadedImage == NULL)) {
    return (EFI_ERROR (Status) ? Status : EFI_NOT_FOUND);
  }

  Status = gBS->HandleProtocol (
                  LoadedImage->DeviceHandle,
                  &gEfiSimpleFileSystemProtocolGuid,
                  (VOID **)&Fs->Fs
                  );
  if (EFI_ERROR (Status) || (Fs->Fs == NULL)) {
    /* 回退：取第一个 SimpleFileSystem 卷（多卷 ESP 场景的保守兜底）*/
    Fs->Fs      = NULL;
    Handles     = NULL;
    HandleCount = 0;
    Status = gBS->LocateHandleBuffer (
                    ByProtocol,
                    &gEfiSimpleFileSystemProtocolGuid,
                    NULL,
                    &HandleCount,
                    &Handles
                    );
    if (EFI_ERROR (Status) || (HandleCount == 0)) {
      if (Handles != NULL) {
        gBS->FreePool (Handles);
      }
      return (EFI_ERROR (Status) ? Status : EFI_NOT_FOUND);
    }
    Status = gBS->HandleProtocol (
                    Handles[0],
                    &gEfiSimpleFileSystemProtocolGuid,
                    (VOID **)&Fs->Fs
                    );
    gBS->FreePool (Handles);
    if (EFI_ERROR (Status) || (Fs->Fs == NULL)) {
      return EFI_NOT_FOUND;
    }
  }

  Fs->Root = NULL;
  Status = Fs->Fs->OpenVolume (Fs->Fs, &Fs->Root);
  if (EFI_ERROR (Status) || (Fs->Root == NULL)) {
    return (EFI_ERROR (Status) ? Status : EFI_UNSUPPORTED);
  }
  return EFI_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* ASCII 小工具（零 CRT 依赖）                                          */
/* ------------------------------------------------------------------ */

STATIC
CHAR8
AnimAsciiLower (
  IN CHAR8  Ch
  )
{
  if ((Ch >= 'A') && (Ch <= 'Z')) {
    return (CHAR8)(Ch + ('a' - 'A'));
  }
  return Ch;
}

STATIC
BOOLEAN
AnimKeyEq (
  IN CONST CHAR8  *Key,
  IN CONST CHAR8  *Name
  )
{
  UINTN  I;

  for (I = 0; Key[I] != '\0' && Name[I] != '\0'; I++) {
    if (AnimAsciiLower (Key[I]) != AnimAsciiLower (Name[I])) {
      return FALSE;
    }
  }
  return (BOOLEAN)(Key[I] == '\0' && Name[I] == '\0');
}

STATIC
UINTN
AnimStrLenA (
  IN CONST CHAR8  *S
  )
{
  UINTN  N;

  N = 0;
  if (S != NULL) {
    while (S[N] != '\0') {
      N++;
    }
  }
  return N;
}

/* "0x1b" / "27" → UINT32；非法返回 FailValue */
STATIC
UINT32
AnimParseInt (
  IN CONST CHAR8  *S,
  IN UINT32       FailValue
  )
{
  UINT32   Value;
  BOOLEAN  Hex;
  UINTN    I;

  if ((S == NULL) || (S[0] == '\0')) {
    return FailValue;
  }
  Hex = FALSE;
  I   = 0;
  if ((S[0] == '0') && (S[1] == 'x' || S[1] == 'X')) {
    Hex = TRUE;
    I   = 2;
    if (S[I] == '\0') {
      return FailValue;
    }
  }
  Value = 0;
  for (; S[I] != '\0'; I++) {
    CHAR8  C;
    UINT32 Digit;

    C = S[I];
    if ((C >= '0') && (C <= '9')) {
      Digit = (UINT32)(C - '0');
    } else if (Hex && (C >= 'a') && (C <= 'f')) {
      Digit = (UINT32)(C - 'a' + 10);
    } else if (Hex && (C >= 'A') && (C <= 'F')) {
      Digit = (UINT32)(C - 'A' + 10);
    } else {
      return FailValue;
    }
    if (Value > (0xFFFFFFFF - Digit) / (Hex ? 16 : 10)) {
      return FailValue;    /* 溢出保护 */
    }
    Value = Value * (Hex ? 16 : 10) + Digit;
  }
  return Value;
}

/* "2560x1440" → W/H；非法返回 FALSE */
STATIC
BOOLEAN
AnimParseRes (
  IN CONST CHAR8  *S,
  OUT UINT32      *W,
  OUT UINT32      *H
  )
{
  UINTN  I;
  UINTN  XPos;

  if ((S == NULL) || (S[0] == '\0')) {
    return FALSE;
  }
  XPos = (UINTN)-1;
  for (I = 0; S[I] != '\0'; I++) {
    if (S[I] == 'x' || S[I] == 'X') {
      XPos = I;
      break;
    }
    if ((S[I] < '0') || (S[I] > '9')) {
      return FALSE;
    }
  }
  if ((XPos == (UINTN)-1) || (XPos == 0) || (S[XPos + 1] == '\0')) {
    return FALSE;
  }

  /* 手工转宽（截断到 5 位内），避免改 S 内容 */
  {
    CHAR8  Buf[8];
    UINTN  N;

    N = XPos;
    if (N >= sizeof (Buf)) {
      return FALSE;
    }
    for (I = 0; I < N; I++) {
      Buf[I] = S[I];
    }
    Buf[N] = '\0';
    *W = AnimParseInt (Buf, 0);
    if (*W == 0) {
      return FALSE;
    }

    if (AnimStrLenA (&S[XPos + 1]) >= sizeof (Buf)) {
      return FALSE;
    }
    for (I = 0; S[XPos + 1 + I] != '\0'; I++) {
      Buf[I] = S[XPos + 1 + I];
    }
    Buf[I] = '\0';
    *H = AnimParseInt (Buf, 0);
    if (*H == 0) {
      return FALSE;
    }
  }
  return TRUE;
}

/* ASCII → CHAR16 拷贝（带长度上限）*/
STATIC
VOID
AnimAsciiToUnicode (
  IN CONST CHAR8  *Src,
  OUT CHAR16      *Dst,
  IN UINTN        MaxChars
  )
{
  UINTN  I;

  if ((Src == NULL) || (Dst == NULL) || (MaxChars == 0)) {
    return;
  }
  for (I = 0; (I < MaxChars - 1) && (Src[I] != '\0'); I++) {
    Dst[I] = (CHAR16)(UINT8)Src[I];
  }
  Dst[I] = L'\0';
}

/* ------------------------------------------------------------------ */
/* INI 解析主体                                                         */
/* ------------------------------------------------------------------ */

STATIC
VOID
AnimParseIni (
  IN CONST UINT8  *Text,
  IN UINTN        Size,
  IN OUT ANIM_CONFIG  *Config
  )
{
  UINTN        Pos;
  CHAR8       *Editable;
  UINT8       *Buf;

  /* 拷入可写缓冲并补 NUL，保证行处理安全 */
  Buf = NULL;
  if (gBS->AllocatePool (EfiBootServicesData, Size + 1, (VOID **)&Buf) != EFI_SUCCESS) {
    return;
  }
  CopyMem (Buf, Text, Size);
  Buf[Size] = 0;
  Editable = (CHAR8 *)Buf;

  Pos = 0;
  while (Pos < Size) {
    CHAR8  *LineStart;
    CHAR8  *LineEnd;
    CHAR8  *Eq;
    CHAR8  *Key;
    CHAR8  *Val;
    CHAR8  *Pch;

    LineStart = &Editable[Pos];

    /* 找行尾 */
    LineEnd = LineStart;
    while ((*LineEnd != '\0') && (*LineEnd != '\n')) {
      LineEnd++;
    }

    /* 截断为 C 串 */
    Pch = LineEnd;
    if (*Pch == '\n') {
      *Pch = '\0';
    }
    if ((Pch > LineStart) && (*(Pch - 1) == '\r')) {
      *(Pch - 1) = '\0';
    }

    /* 推进 Pos 到下一行 */
    Pos += (UINTN)(LineEnd - LineStart) + 1;

    /* 去首部空白 */
    Key = LineStart;
    while ((*Key == ' ') || (*Key == '\t')) {
      Key++;
    }
    if ((*Key == '\0') || (*Key == ';') || (*Key == '#') || (*Key == '[')) {
      continue;    /* 空行 / 注释 / 节名 */
    }

    /* 找 '=' 并切分 */
    Eq = Key;
    while ((*Eq != '\0') && (*Eq != '=')) {
      Eq++;
    }
    if (*Eq != '=') {
      continue;
    }
    *Eq = '\0';
    Val = Eq + 1;

    /* 去尾部空白（Key）*/
    Pch = Eq;
    while ((Pch > Key) && ((*(Pch - 1) == ' ') || (*(Pch - 1) == '\t'))) {
      Pch--;
    }
    *Pch = '\0';

    /* 去首尾空白（Val）*/
    while ((*Val == ' ') || (*Val == '\t')) {
      Val++;
    }
    Pch = Val;
    while (*Pch != '\0') {
      Pch++;
    }
    while ((Pch > Val) && ((*(Pch - 1) == ' ') || (*(Pch - 1) == '\t'))) {
      Pch--;
    }
    *Pch = '\0';

    /* 键分发（大小写不敏感）*/
    if (AnimKeyEq (Key, "resolution")) {
      if (Val[0] == 'a' || Val[0] == 'A') {    /* auto */
        Config->ReqWidth  = 0;
        Config->ReqHeight = 0;
      } else if (AnimParseRes (Val, &Config->ReqWidth, &Config->ReqHeight)) {
        /* 已写入 */
      } else {
        Config->ReqWidth  = 0;
        Config->ReqHeight = 0;
      }
    } else if (AnimKeyEq (Key, "fps")) {
      UINT32  Fps;

      Fps = AnimParseInt (Val, 0);
      if ((Fps >= ANIM_FPS_MIN) && (Fps <= ANIM_FPS_MAX)) {
        Config->Fps = Fps;
      } else {
        Config->Fps = 0;    /* 非法 → 回落 FpsHint */
      }
    } else if (AnimKeyEq (Key, "animpath")) {
      AnimAsciiToUnicode (Val, Config->AnimPath, ANIM_MAX_PATH);
    } else if (AnimKeyEq (Key, "skipkey")) {
      UINT32  KeyVal;

      KeyVal = AnimParseInt (Val, 0xFFFFFFFF);
      if (KeyVal == 0x1B) {
        /* 设计文档示例写法：0x1b = ASCII ESC → 映射为 EFI 扫描码 ESC */
        Config->SkipScanCode = ANIM_DEFAULT_SKIP_SCANCODE;
      } else if (KeyVal == 0xFFFF) {
        Config->SkipScanCode = ANIM_ANY_KEY;
      } else if ((KeyVal > 0) && (KeyVal <= 0xFFFF)) {
        Config->SkipScanCode = (UINT16)KeyVal;
      } else {
        Config->SkipScanCode = ANIM_DEFAULT_SKIP_SCANCODE;
      }
    } else if (AnimKeyEq (Key, "chainload")) {
      AnimAsciiToUnicode (Val, Config->ChainloadPath, ANIM_MAX_PATH);
      if (Config->ChainloadPath[0] != L'\\') {
        /* 链式加载目标必须是绝对路径（任务约束）→ 恢复默认 */
        CONST CHAR16  *DefChain;
        UINTN         I2;

        DefChain = ANIM_CHAINLOAD_DEFAULT;
        for (I2 = 0; (I2 < ANIM_MAX_PATH - 1) && (DefChain[I2] != L'\0'); I2++) {
          Config->ChainloadPath[I2] = DefChain[I2];
        }
        Config->ChainloadPath[I2] = L'\0';
      }
    }
    /* 未知键忽略（向前兼容）*/
  }

  gBS->FreePool (Buf);
}

/* ------------------------------------------------------------------ */
/* M2 入口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimConfigLoad (
  IN  EFI_HANDLE    ImageHandle,
  OUT ANIM_FS       *Fs,
  OUT ANIM_CONFIG   *Config
  )
{
  EFI_STATUS  Status;
  UINT8      *Buf;
  UINTN       Size;

  AnimConfigDefaults (Config);

  Status = AnimFsOpen (ImageHandle, Fs);
  if (EFI_ERROR (Status)) {
    ZeroMem (Fs, sizeof (*Fs));
    return Status;
  }

  /* config.ini 可选：读不到 → 默认值（§4.5 / T8）*/
  Buf  = NULL;
  Size = 0;
  Status = AnimReadFile (Fs->Root, ANIM_CONFIG_FILE, &Buf, &Size);
  if (EFI_ERROR (Status) || (Buf == NULL) || (Size == 0)) {
    if (Buf != NULL) {
      gBS->FreePool (Buf);
    }
    return EFI_SUCCESS;
  }

  AnimParseIni (Buf, Size, Config);
  gBS->FreePool (Buf);

  Config->ConfigLoaded = TRUE;
  return EFI_SUCCESS;
}

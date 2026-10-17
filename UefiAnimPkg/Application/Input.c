/** @file Input.c — M6 输入监听实现
  依据：设计文档 §4 M6（SimpleTextInputEx 主 / SimpleTextIn 备）、§4.2（SkipKey）
**/

#include "Input.h"

/* ------------------------------------------------------------------ */
/* 单键判定                                                             */
/* ------------------------------------------------------------------ */

STATIC
BOOLEAN
AnimKeyIsSkip (
  IN ANIM_INPUT        *Ctx,
  IN EFI_INPUT_KEY     *Key
  )
{
  if (Ctx->SkipScanCode == ANIM_ANY_KEY) {
    return (BOOLEAN)((Key->ScanCode != 0) || (Key->UnicodeChar != 0));
  }
  /* P6 修复：EFI 标准扫描码 SCAN_ESC=0x0017（MdePkg SimpleTextIn.h 实证）。
     真机固件 Esc 常只填 ScanCode（UnicodeChar=0），QEMU 反之只填 UnicodeChar，
     两种形态都必须命中；无论 config 如何配置，SCAN_ESC 恒为跳过键。*/
  if (Key->ScanCode == SCAN_ESC) {
    return TRUE;
  }
  if ((Key->ScanCode != 0) && (Key->ScanCode == Ctx->SkipScanCode)) {
    return TRUE;
  }
  if (Key->UnicodeChar == ANIM_ASCII_ESC) {
    return TRUE;    /* ASCII ESC（0x1b），兼容 config.ini SkipKey=0x1b 写法 */
  }
  if ((Key->UnicodeChar == L'\r') || (Key->UnicodeChar == L'\n')) {
    return TRUE;    /* 回车跳过（§3.4 "Esc/任意键" 的安全解释）*/
  }
  return FALSE;
}

/* ------------------------------------------------------------------ */
/* M6 接口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimInputInit (
  OUT ANIM_INPUT  *Ctx,
  IN  UINT16       SkipScanCode
  )
{
  EFI_STATUS  Status;

  if (Ctx == NULL) {
    return EFI_INVALID_PARAMETER;
  }
  ZeroMem (Ctx, sizeof (*Ctx));
  Ctx->SkipScanCode = SkipScanCode;

  Ctx->TextInEx = NULL;
  Status = gBS->LocateProtocol (&gEfiSimpleTextInputExProtocolGuid, NULL, (VOID **)&Ctx->TextInEx);
  if (EFI_ERROR (Status)) {
    Ctx->TextInEx = NULL;    /* 备选路径兜底 */
  }

  Ctx->TextIn = gST->ConIn;

  /* 排空残留按键，忽略失败（个别控制台驱动 Reset 受限）*/
  if (Ctx->TextInEx != NULL) {
    Ctx->TextInEx->Reset (Ctx->TextInEx, TRUE);
  }
  if (Ctx->TextIn != NULL) {
    Ctx->TextIn->Reset (Ctx->TextIn, TRUE);
  }
  return EFI_SUCCESS;
}

BOOLEAN
AnimCheckSkip (
  IN ANIM_INPUT  *Ctx
  )
{
  EFI_STATUS    Status;
  EFI_INPUT_KEY Key;
  EFI_KEY_DATA  KeyData;

  if (Ctx == NULL) {
    return FALSE;
  }

  /* 循环排空所有待读按键：读到即判，未命中继续读，无键可读返回 FALSE。
     P6 修复：主路径改为 gST->ConIn（控制台聚合输入，真机最可靠），
     SimpleTextInputEx 降为增强备选——真机固件 LocateProtocol 可能返回
     非聚合设备实例（QEMU 仅 ConSplitter 一个故 VM 测试未暴露）；
     旧实现 TextInEx NOT_READY 即返回，真机上永远读不到键。*/
  for (;;) {
    BOOLEAN  GotKey;

    GotKey = FALSE;
    if (Ctx->TextIn != NULL) {
      Status = Ctx->TextIn->ReadKeyStroke (Ctx->TextIn, &Key);
      if (Status == EFI_SUCCESS) {
        GotKey = TRUE;
      }
    }
    if (!GotKey && Ctx->TextInEx != NULL) {
      Status = Ctx->TextInEx->ReadKeyStrokeEx (Ctx->TextInEx, &KeyData);
      if (Status == EFI_SUCCESS) {
        Key     = KeyData.Key;
        GotKey  = TRUE;
      }
    }
    if (!GotKey) {
      return FALSE;
    }

    if (AnimKeyIsSkip (Ctx, &Key)) {
      return TRUE;
    }
    /* 未命中的键被丢弃，继续排空 */
  }
}

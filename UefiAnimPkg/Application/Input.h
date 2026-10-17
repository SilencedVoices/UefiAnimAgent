/** @file Input.h — M6 输入监听（设计文档 §4 M6 / §4.2 SkipKey）*/

#ifndef INPUT_H_
#define INPUT_H_

#include "UefiAnimPkg.h"

typedef struct {
  EFI_SIMPLE_TEXT_INPUT_EX_PROTOCOL  *TextInEx;    /* 主选，可为 NULL */
  EFI_SIMPLE_TEXT_INPUT_PROTOCOL      *TextIn;     /* 备选（gST->ConIn）*/
  UINT16                              SkipScanCode; /* 跳过键（EFI 扫描码 / ANIM_ANY_KEY）*/
} ANIM_INPUT;

/**
  初始化输入：优先 SimpleTextInputEx，备选 SimpleTextIn；
  Reset(TRUE) 排空启动期残留按键（避免固件日志按键误触发跳过）。
**/
EFI_STATUS
AnimInputInit (
  OUT ANIM_INPUT  *Ctx,
  IN  UINT16       SkipScanCode
  );

/**
  非阻塞轮询一次：排空当前待读按键并判定是否命中跳过键。
  判定：配置扫描码（默认 SCAN_ESC=0x0017）或 ASCII ESC(0x1B)，回车/换行亦视为跳过
  （§3.4 伪代码 "Esc/任意键" 的安全解释）；SkipScanCode==ANIM_ANY_KEY 时任意键跳过。

  @retval TRUE   命中跳过键
  @retval FALSE  无按键或未命中
**/
BOOLEAN
AnimCheckSkip (
  IN ANIM_INPUT  *Ctx
  );

#endif /* INPUT_H_ */

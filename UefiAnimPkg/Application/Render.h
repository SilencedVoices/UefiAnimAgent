/** @file Render.h — M4 双缓冲渲染（设计文档 §3.2 / §4 M4）*/

#ifndef RENDER_H_
#define RENDER_H_

#include "UefiAnimPkg.h"

/**
  渲染初始化：分配 BackBuffer（Stride*Height 个 BLT 像素），
  QOI 模式额外分配 W*H*4 RGBA 暂存（Scratch）。

  @retval EFI_SUCCESS 及错误码（内存不足）
**/
EFI_STATUS
AnimRenderInit (
  IN OUT ANIM_DISPLAY  *Display,
  IN ANIM_DATA         *Data
  );

/** 释放 BackBuffer / Scratch（幂等）。*/
VOID
AnimRenderCleanup (
  IN OUT ANIM_DISPLAY  *Display
  );

/**
  渲染并投送一帧：
  BMP 帧 → BGR 行数据直接转 Back；QOI 帧 → QoiDecode 到 Scratch 再转 Back；
  居中绘制、越界裁剪（§3.1-4，不缩放）；随后按像素格式投送
  FrontBuffer（CopyMem / 字节组装）或 Blt() 降级（§3.2）。

  @retval EFI_SUCCESS 及错误码（解码失败等）
**/
EFI_STATUS
AnimRenderFrame (
  IN OUT ANIM_DISPLAY  *Display,
  IN ANIM_DATA         *Data,
  IN UINT32             FrameIndex
  );

#endif /* RENDER_H_ */

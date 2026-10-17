/** @file AnimLoad.h — M3 资源加载（设计文档 §2.3 / §4 M3）*/

#ifndef ANIMLOAD_H_
#define ANIMLOAD_H_

#include "UefiAnimPkg.h"

/**
  读取任一文件全量入内存（config.ini 与 anim.bin 共用路径）。
  调用方负责 gBS->FreePool(*Buffer)。

  @param[in]  Root    卷根（来自 M2 的 ANIM_FS）
  @param[in]  Path    绝对路径（以 '\' 开头）
  @param[out] Buffer  成功时分配并填充；失败时 *Buffer=NULL
  @param[out] Size    文件字节数

  @retval EFI_SUCCESS 及错误码
**/
EFI_STATUS
AnimReadFile (
  IN  EFI_FILE_PROTOCOL  *Root,
  IN  CONST CHAR16       *Path,
  OUT UINT8             **Buffer,
  OUT UINTN              *Size
  );

/**
  加载并校验 anim.bin：头 + 索引 + 全部帧数据一次读入内存（播放期零读盘，§4 M3）。
  校验（§2.3）：magic 'UAN1'、version=1、format 合法、索引与每帧 offset+size
  均落在文件内、BMP 模式帧尺寸 = W*H*3。

  @param[in]  Fs    卷上下文
  @param[in]  Cfg   配置（AnimPath 相对 \EFI\BootAnim\ 或绝对）
  @param[out] Data  成功时填充；失败时内部已清理为全零

  @retval EFI_SUCCESS 及错误码（缺文件等 → 调用方按 §4.5 直接 chainload）
**/
EFI_STATUS
AnimLoadAnimation (
  IN  ANIM_FS     *Fs,
  IN  ANIM_CONFIG *Cfg,
  OUT ANIM_DATA   *Data
  );

/** 释放 anim.bin 缓冲（幂等）。*/
VOID
AnimFreeAnimation (
  IN OUT ANIM_DATA  *Data
  );

#endif /* ANIMLOAD_H_ */

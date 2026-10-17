/** @file Qoi.h — QOI 解码器（设计文档 §2.3 / §2.2）
  格式规范：https://qoiformat.org（QOI Format, public domain specification）
  输出：RGBA8888（每像素 4 字节 R,G,B,A），供 Render 转换 BLT 像素。
**/

#ifndef QOI_H_
#define QOI_H_

#include "UefiAnimPkg.h"

/**
  解析 QOI 头（14 字节）：magic "qoif"、宽高（大端 u32）、通道数。

  @retval EFI_SUCCESS 及错误码（magic/尺寸非法）
**/
EFI_STATUS
QoiInfo (
  IN  CONST UINT8  *Data,
  IN  UINTN         Size,
  OUT UINT32       *Width,
  OUT UINT32       *Height,
  OUT UINT8        *Channels
  );

/**
  解码完整 QOI 帧到 RGBA 输出缓冲。

  边界安全（任务硬性要求）：
  - 输入每字节读取前检查 pos < Size；
  - 像素写入前检查 pxIndex < PixelCount；
  - 要求 W*H*4 == OutBytes（与 anim.bin 头声明的动画尺寸一致，防越界写）。

  @param[in]  Data      QOI 帧数据（anim.bin 索引指向的整图）
  @param[in]  Size      帧字节数
  @param[out] RgbaOut   输出缓冲（调用方分配）
  @param[in]  OutBytes  输出缓冲容量（必须 == W*H*4）

  @retval EFI_SUCCESS 及错误码
**/
EFI_STATUS
QoiDecode (
  IN  CONST UINT8  *Data,
  IN  UINTN         Size,
  OUT UINT8        *RgbaOut,
  IN  UINTN         OutBytes
  );

#endif /* QOI_H_ */

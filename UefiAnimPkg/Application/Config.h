/** @file Config.h — M2 配置读取（设计文档 §4.2 / Limine 配置驱动思想）*/

#ifndef CONFIG_H_
#define CONFIG_H_

#include "UefiAnimPkg.h"

/**
  加载配置并定位自身所在 ESP。
  流程：LoadedImage→DeviceHandle→SimpleFileSystem 定位卷（失败则回退第一个
  SimpleFileSystem 卷），读取 \EFI\BootAnim\config.ini 解析进 Config。
  config.ini 缺失/损坏一律使用默认值（§4.5：不阻塞启动）。

  @param[in]  ImageHandle  本应用镜像句柄
  @param[out] Fs           成功打开的卷上下文（Root 供 M3/M7 复用；失败时内容为零）
  @param[out] Config       配置（总是有效：至少含默认值）

  @retval EFI_SUCCESS      Fs 可用（config.ini 读到与否看 Config->ConfigLoaded）
  @retval 其他             连卷都打不开（调用方仍可 chainload，M7 不依赖 Fs）
**/
EFI_STATUS
AnimConfigLoad (
  IN  EFI_HANDLE    ImageHandle,
  OUT ANIM_FS       *Fs,
  OUT ANIM_CONFIG   *Config
  );

#endif /* CONFIG_H_ */

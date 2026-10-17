/** @file Chainload.h — M7 链式加载（设计文档 §1.1 GRUB2 流程 / §4 M7 / T7）*/

#ifndef CHAINLOAD_H_
#define CHAINLOAD_H_

#include "UefiAnimPkg.h"

/**
  构造 \EFI\Microsoft\Boot\bootmgfw.efi（或配置目标，必须绝对路径）的完整
  DevicePath：DevicePathFromHandle(自身 DeviceHandle) ⊕ FileDevicePath(路径)，
  依 GRUB2 chainloader 流程 LoadImage(FALSE,...) → StartImage(...)。

  @param[in] ImageHandle  本应用镜像句柄（父镜像 + 设备定位）
  @param[in] Cfg          配置（ChainloadPath）

  @retval EFI_SUCCESS    StartImage 正常返回（控制权已移交后返回）
  @retval 其他           LoadImage/StartImage 失败（T7：调用方提示后安全退出）
**/
EFI_STATUS
AnimChainload (
  IN EFI_HANDLE    ImageHandle,
  IN ANIM_CONFIG   *Cfg
  );

#endif /* CHAINLOAD_H_ */

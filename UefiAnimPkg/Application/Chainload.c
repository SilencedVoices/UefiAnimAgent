/** @file Chainload.c — M7 链式加载实现
  依据：设计文档 §1.1（GRUB2 chainloader：LoadImage(FALSE)+StartImage）、§4.5（失败不悬挂）
  路径硬约束：目标为绝对路径 \EFI\Microsoft\Boot\bootmgfw.efi（Config 模块已强制 '\\' 开头）。
**/

#include "Chainload.h"

/* ------------------------------------------------------------------ */
/* M7 接口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimChainload (
  IN EFI_HANDLE    ImageHandle,
  IN ANIM_CONFIG   *Cfg
  )
{
  EFI_STATUS                       Status;
  EFI_LOADED_IMAGE_PROTOCOL       *LoadedImage;
  EFI_DEVICE_PATH_PROTOCOL        *VolPath;
  EFI_DEVICE_PATH_PROTOCOL        *FilePath;
  EFI_DEVICE_PATH_PROTOCOL        *FullPath;
  EFI_HANDLE                       ChildImage;
  UINTN                            ExitDataSize;
  CHAR16                          *ExitData;

  VolPath  = NULL;
  FilePath = NULL;
  FullPath = NULL;

  if ((ImageHandle == NULL) || (Cfg == NULL) || (Cfg->ChainloadPath[0] != L'\\')) {
    return EFI_INVALID_PARAMETER;
  }

  /* 1. 定位自身所在卷的设备路径 */
  LoadedImage = NULL;
  Status = gBS->HandleProtocol (
                  ImageHandle,
                  &gEfiLoadedImageProtocolGuid,
                  (VOID **)&LoadedImage
                  );
  if (EFI_ERROR (Status) || (LoadedImage == NULL)) {
    return (EFI_ERROR (Status) ? Status : EFI_NOT_FOUND);
  }

  VolPath = DevicePathFromHandle (LoadedImage->DeviceHandle);
  if (VolPath == NULL) {
    return EFI_NOT_FOUND;
  }

  /* 2. 拼接文件设备路径（Limine 配置驱动的目标路径 → DevicePath）*/
  FilePath = FileDevicePath (NULL, Cfg->ChainloadPath);
  if (FilePath == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }
  FullPath = AppendDevicePath (VolPath, FilePath);
  if (FullPath == NULL) {
    gBS->FreePool (FilePath);
    return EFI_OUT_OF_RESOURCES;
  }

  /* 3. GRUB2 流程：固件完成 PE/COFF 校验（SB 下微软签名可通过）*/
  ChildImage = NULL;
  Status = gBS->LoadImage (
                  FALSE,          /* BootPolicy=FALSE：走固件验证路径 */
                  ImageHandle,    /* 父镜像 = 本应用 */
                  FullPath,
                  NULL,
                  0,
                  &ChildImage
                  );
  gBS->FreePool (FilePath);
  gBS->FreePool (FullPath);

  if (EFI_ERROR (Status)) {
    return Status;    /* EFI_SECURITY_VIOLATION / EFI_NOT_FOUND / ... → T7 */
  }

  /* 4. 控制权移交 */
  ExitDataSize = 0;
  ExitData     = NULL;
  Status = gBS->StartImage (ChildImage, &ExitDataSize, &ExitData);
  if (ExitData != NULL) {
    gBS->FreePool (ExitData);
  }
  if (EFI_ERROR (Status)) {
    /* StartImage 返回（异常路径）：清理子镜像，不悬挂 */
    gBS->UnloadImage (ChildImage);
  }
  return Status;
}

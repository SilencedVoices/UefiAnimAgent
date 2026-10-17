/** @file Gop.h — M1 GOP 初始化（设计文档 §3.1 / §4 M1）*/

#ifndef GOP_H_
#define GOP_H_

#include "UefiAnimPkg.h"

/**
  初始化显示：枚举 GOP 模式，按降级策略选模并切换。
  优先级（§3.1）：config 指定 → 2560x1440 → 1920x1080 → 1280x720 → 当前模式（不切换）。

  @param[in]  Config   已加载的配置（ReqWidth/ReqHeight=0 表示 auto）
  @param[out] Display  成功时填充全部字段；SetMode 后强制重读
                       FrameBufferBase / PixelsPerScanLine / PixelFormat（§3.1-3，风险 R5）。

  @retval EFI_SUCCESS      显示可用（Display 已填充）
  @retval 其他             GOP 不可用或模式信息非法 → 调用方走文字提示 + chainload
**/
EFI_STATUS
AnimDisplayInit (
  IN  ANIM_CONFIG   *Config,
  OUT ANIM_DISPLAY  *Display
  );

#endif /* GOP_H_ */

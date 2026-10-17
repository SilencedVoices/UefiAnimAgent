/** @file FrameRate.h — M5 帧率控制（设计文档 §3.3）*/

#ifndef FRAMERATE_H_
#define FRAMERATE_H_

#include "UefiAnimPkg.h"

/**
  初始化定时器：以 Stall(100ms) 校准 TSC 频率，换算帧间隔（§3.3 补偿表
  覆盖 24/30/60/120fps，120fps 档 8,333µs）。校准出界时 TscPerUs=0
  （纯 Stall 退化模式）。
  @param[in] Fps  帧率（1..120，越界回落 30）

  @retval EFI_SUCCESS
**/
EFI_STATUS
AnimTimerInit (
  OUT ANIM_TIMER  *Timer,
  IN  UINT32       Fps
  );

/** 当前 TSC 读数（帧起点标记用）。*/
UINT64
AnimTimerNow (
  VOID
  );

/**
  剩余时间补偿等待（§3.3）：elapsed = (Now - TscStart)/TscPerUs；
  剩余部分两段式等待（P7）：>2ms 先 Stall 粗睡（预留 2ms 自旋余量），
  末段 RDTSC+CpuPause 忙等至帧死线（规避 Stall 1ms 粒度对 120fps 的
  ~12% 系统性偏慢）；自旋带 2×帧间隔绝对上限（防死锁）。
  elapsed ≥ IntervalUs 时立即返回（追赶策略）。
**/
VOID
AnimFrameWait (
  IN ANIM_TIMER  *Timer,
  IN UINT64       TscStart
  );

#endif /* FRAMERATE_H_ */

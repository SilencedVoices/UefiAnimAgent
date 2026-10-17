/** @file FrameRate.c — M5 帧率控制实现
  依据：设计文档 §3.3（fps→µs 换算、剩余时间补偿、±5% 波动可接受）
  P7 高精度时基升级：
    1) 帧率档位覆盖 24/30/60/120fps（120fps 帧预算 8,333µs）；
    2) 帧尾等待改两段式：剩余 > 2,000µs 时先 gBS->Stall 粗睡（预留 2,000µs
       自旋余量），末段用 RDTSC + CpuPause 忙等至帧死线——规避固件 Stall
       常见 1ms 粒度（该粒度在 24fps 仅 ~2.4% 误差，120fps 放大为 ~12%
       系统性偏慢，无法接受）；
    3) 忙等带 2×帧间隔绝对上限（TSC 停跳/降频异常保护，UEFI 下绝不死锁）；
    4) 校准维持 Stall(100ms) 单窗：最坏 ±1ms 粒度误差 → ±1% TscPerUs，
       24fps 帧 41,667µs±417µs / 120fps 帧 8,333µs±83µs，均远小于 ±5%
       设计容差；109 帧累积漂移 < 10µs 级。校准结果做合理域夹逼
       [2, 100000] TscPerUs（2MHz..100GHz 之外视为异常），出界回落
       纯 Stall 模式（修复旧版退化路径"直接不等"的注释/代码矛盾）。
**/

#include "FrameRate.h"

#define ANIM_CALIBRATE_US  100000   /* 校准窗口 100ms                        */
#define ANIM_SPIN_TAIL_US  2000     /* 末段自旋预算（µs），覆盖 Stall ±1ms 粒度 */
#define ANIM_SPIN_GUARD_X  2        /* 自旋绝对上限 = 2×帧间隔（防死锁）      */

/* ------------------------------------------------------------------ */
/* §3.3 帧率补偿表（P7 扩充 120fps 档位；表值 = 1,000,000/fps 直算值）   */
/*   24fps → 41,666µs   30fps → 33,333µs                               */
/*   60fps → 16,666µs   120fps →  8,333µs  ⭐ P7 新增                   */
/* 非表内帧率按 1,000,000/fps 直算（ANIM_FPS_MAX=120 已覆盖上限）。     */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* M5 接口                                                              */
/* ------------------------------------------------------------------ */

EFI_STATUS
AnimTimerInit (
  OUT ANIM_TIMER  *Timer,
  IN  UINT32       Fps
  )
{
  UINT64  T0;
  UINT64  T1;
  UINT64  Delta;

  if (Timer == NULL) {
    return EFI_INVALID_PARAMETER;
  }
  if ((Fps < ANIM_FPS_MIN) || (Fps > ANIM_FPS_MAX)) {
    Fps = ANIM_DEFAULT_FPS;
  }

  T0 = AsmReadTsc ();
  gBS->Stall (ANIM_CALIBRATE_US);
  T1    = AsmReadTsc ();
  Delta = T1 - T0;

  Timer->TscPerUs = Delta / ANIM_CALIBRATE_US;
  if ((Timer->TscPerUs < 2) || (Timer->TscPerUs > 100000)) {
    /* 校准出界（TSC <2MHz 或 >100GHz，均不合理）：退化为纯 Stall 模式。
       旧版此处置 1 后 ElapsedUs 恒大于帧预算 → 等待被整体跳过（动画狂奔），
       与注释"全额 Stall"矛盾；P7 修正为真正全额 Stall（见 AnimFrameWait）。*/
    Timer->TscPerUs = 0;
  }
  Timer->IntervalUs = 1000000ULL / Fps;
  return EFI_SUCCESS;
}

UINT64
AnimTimerNow (
  VOID
  )
{
  return AsmReadTsc ();
}

VOID
AnimFrameWait (
  IN ANIM_TIMER  *Timer,
  IN UINT64       TscStart
  )
{
  UINT64  Now;
  UINT64  ElapsedUs;
  UINT64  RemainingUs;
  UINT64  Deadline;
  UINT64  Guard;

  if (Timer == NULL) {
    return;
  }

  /* 纯 Stall 模式（校准出界）：全额等待。个别固件 Stall 粒度差时
     高帧率可能有 ±10% 播放误差（设计容差 ±5% 之外的已知限制）。*/
  if (Timer->TscPerUs == 0) {
    gBS->Stall ((UINTN)Timer->IntervalUs);
    return;
  }

  Now       = AsmReadTsc ();
  ElapsedUs = (Now - TscStart) / Timer->TscPerUs;
  if (ElapsedUs >= Timer->IntervalUs) {
    return;    /* 本帧已超时：立即返回，不累积延迟（§3.3 追赶策略）*/
  }
  RemainingUs = Timer->IntervalUs - ElapsedUs;

  /* 两段式等待（P7）：
     第一段 粗睡：剩余 > 2ms 时 Stall(剩余-2ms)。Stall ±1ms 粒度误差
                  被 2ms 自旋余量完全吸收（上界 R-2ms+1ms = R-1ms < R）；
     第二段 精等：RDTSC+CpuPause 忙等至帧死线（TSC 精度，µs 级）。 */
  if (RemainingUs > ANIM_SPIN_TAIL_US) {
    gBS->Stall ((UINTN)(RemainingUs - ANIM_SPIN_TAIL_US));
  }

  Deadline = TscStart + (Timer->IntervalUs + 1) * Timer->TscPerUs;
  Guard    = AsmReadTsc () +
             ((ANIM_SPIN_GUARD_X * Timer->IntervalUs) + 1) * Timer->TscPerUs;
  while ((Now = AsmReadTsc ()) < Deadline) {
    if (Now > Guard) {
      break;    /* TSC 异常（停跳/降频/迁移）：放弃本帧精等，立即返回 */
    }
    CpuPause ();
  }
}

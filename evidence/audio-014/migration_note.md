# audio-014 future migration note (evidence-only)

scope: 未来若需进一步从 ``coco.audio_resilience`` 拆出更细 ``coco.audio_common``
公共 util (audio-012 closeout C1 长期建议), 这里给出迁移路径 (evidence-only,
此 phase 不动手, 仅锁面)。

current owner (audio-013 后):

- ``coco.audio_resilience.read_loss_window_override_ms`` — env 解析 (9-case 已验)
- ``coco.audio_resilience.classify_stream_error`` — PortAudio 异常判定
- ``coco.audio_resilience.ENV_LOSS_WINDOW_MS`` — env 名常量
- ``coco.vad_trigger`` — thin re-export ``_read_loss_window_override_ms`` 别名 (向后兼容)
- ``coco.wake_word`` reopen 路径 — 直接 ``from coco.audio_resilience import``

future migration (若拆 ``coco.audio_common``):

1. 新建 ``coco/audio_common.py``, 把以下 3 个符号物理迁过去:
   - ``ENV_LOSS_WINDOW_MS``
   - ``read_loss_window_override_ms``
   - ``classify_stream_error``
2. ``coco.audio_resilience`` 改为从 ``coco.audio_common`` 公共 re-export,
   保持 ``__all__`` 不变 (audio-013 已暴露)。
3. ``coco.vad_trigger`` 的 thin delegate 不动 (向后兼容 verify_audio_012)。
4. ``coco.wake_word`` reopen 路径 import 路径**不需要变**, 因为它走的是
   ``coco.audio_resilience`` 公共名, audio_resilience 内部再 re-export。
5. 新写 verify_audio_NNN_migration.py:
   - V_n 双源 ``is`` 同对象: ``audio_common.X is audio_resilience.X``
   - V_n vad_trigger thin delegate ``is`` 同对象 (链路三跳锁面)
   - V_n verify_audio_011/012/013/014 全 PASS (regression)

风险与不做手术的理由:

- 当前 audio_resilience 已是单一 owner; 若再拆一层, 引入 3 → 4 module 间接性,
  无功能收益, 只有审计开销 ↑。
- audio-014 verify 已**契约 freeze**: wake_word 不得跨模块连回 vad_trigger 私有,
  vad_trigger 必须 thin re-export 同对象。任何破契约改动会被 V1/V2 抓到。
- 未来若 audio_resilience 单文件超过 ~600 行或同时承载 hotplug + loss_window +
  recovery 三块互不相关职责, 再考虑按上述路径迁移。

audio-014 作为 verify-only 审计**到此为止**, 不衍生 fu chain, 不动源码。

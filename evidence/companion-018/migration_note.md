# companion-018 future migration note (evidence-only)

scope: 未来若需为旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 提供 alias 兼容,
本 phase **不动手**, 仅锁面 evidence。

current state (companion-017 后):

- ``coco/companion/preference_learner.py`` ``_PERSIST_EMIT_INTERVAL_ENV =
  "COCO_PERSIST_EMIT_MIN_INTERVAL_S"`` 单一权威 env 名。
- ``preference_persist_emit_min_interval_s_from_env(env=None)`` 仅读这一个名字。
- 旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 在 code 中**只出现在注释**作历史说明,
  没有任何 ``.get(...)`` 读取路径。
- companion-018 新建 ``docs/companion-preference-emit-env-spec.md`` 作权威 spec,
  §1.1 显式说明 "不为旧名提供 alias"。
- 模块级 ``_PERSIST_EMIT_INTERVAL_WARN_ONCE`` 为进程级 flag, 多进程各 warn 一次,
  与 companion-015 ``_PREFERENCE_STATE_WARN_ONCE`` 行为一致, 详见 spec §2。

future migration (若必须 alias 旧名):

1. 源码改动 (估算 5-15 行):
   - ``preference_persist_emit_min_interval_s_from_env``: 改为优先读新名,
     若 ``raw is None`` 再读旧名; 旧名命中加 deprecation WARN once。
   - module docstring + function docstring 同步说明 alias + deprecation timeline。
   - 新增常量 ``_PERSIST_EMIT_INTERVAL_LEGACY_ENV = "COCO_PREFERENCE_EMIT_INTERVAL_S"``。
2. verify 同步代价:
   - ``scripts/verify_companion_017.py`` V1 "C1 code uses only new name" 需放宽
     允许旧名出现 (但限定在 alias 解析路径)。
   - ``scripts/verify_companion_018.py`` V1 ``hit_legacy_read == 0`` 需放宽。
   - 新增 fixture: 旧名 env set / 新名 env unset → 解析回旧名值。
   - 新增 fixture: 新名 + 旧名同时 set → 新名胜出。
3. spec 文档同步:
   - ``docs/companion-preference-emit-env-spec.md`` §1 新增 deprecation table。
   - §1.1 "不为旧名提供 alias 的理由" 改写为 "alias 已启用, deprecation timeline X"。
4. 风险与不做手术的理由:
   - 旧名 ``COCO_PREFERENCE_EMIT_INTERVAL_S`` 默认 30s 与新名默认 10s **不一致**,
     直接 alias 会让既有用户 (假设有) 的 30s 默认行为变成 10s, 反而破坏兼容。
   - 真要 alias 还得保留旧默认值, 实现复杂度翻倍。
   - 当前**外部无任何引用**, alias 是 "修一个不存在的问题"。
5. multi-process WARN once 切到 "true global once" 的成本 (spec §2.4 已分析):
   - 需要持久化 flag + atomic + schema, 收益极低, **不做**。

companion-018 作为 verify-only spec 锁面**到此为止**, 不衍生 fu chain。
不动源码, 不为旧名提供 alias, 不切多进程 warn 语义。

# interact-027 migration note

## Source backlog
`interact-024-backlog-v1-anchor-per-stage-count`

## 升级要点
interact-024 V1 anchor `"latency_ms=_lat_ms()"` 用 substring presence,
只要 ≥1 处出现即 PASS。3 处删 1 不能被 V1 抓住,只能靠 V5 邻近回归 (V4 ON 三 stage emit) 兜住。

interact-027 (additive) 把 V1 升级为:
- 按 emit-site 上下文锚点分别识别 3 个站点
  - site-A admit (`"arbit_winner", _candidate_id, "admit"`)
  - site-B reject 主路径 (`_stage_out, _candidate_id, "reject"`)
  - site-C reject 抢占 (`arbit_emotion_preempt` 字面量, unique reason)
- 每 site 站点数严格 == 1 (V1/V2/V3)
- 全局 latency_ms=_lat_ms() 总数严格 == 3 (V4)
- 单点删除模拟: 内存 patch 删除任一 emit-site 中的 latency_ms=_lat_ms()
  后, V1/V2/V3/V4 任一应 FAIL (V5 主动模拟覆盖)

## 0 业务源码改动
- 不动 `coco/`
- 不动 `scripts/verify_interact_024.py` (additive)
- 仅新增 `scripts/verify_interact_027.py` + `evidence/interact-027/`

## default-OFF 不变式
V6 subprocess 启 scheduler 一次 maybe_trigger,断言 `COCO_PROACTIVE_TRACE` 不设
时 proactive.trace 事件计数 == 0。

## 邻近 verify 回归
V7 跑 interact-018/021/022/023/024/025/026 共 7 个 verify, 全 rc=0。

## smoke
V8 跑 `./init.sh` rc=0。

## 跑法
```
uv run python scripts/verify_interact_027.py
```
退出码 0 全 PASS, 非 0 任一 FAIL。

# Reviewer (sub-agent fresh-context) — pre-merge placeholder

- branch: feat/infra-watchdog-auto-restart
- commit: b2428e4
- verdict: LGTM
- 待 closeout 后 (post-merge re-run) 在 feature_list.json evidence.reviewer 中以 nested 形式落盘 (P278: reviewer_kind=sub_agent_fresh_context, summary>=20 字, checks_run, findings.P0/P1/P2, rounds=1, freshness_anchor=post-merge-rerun)。

## checks_run (pre-merge)

- verify_infra_watchdog_auto_restart 10/10 PASS rc=0
- init.sh smoke rc=0 (typo-guard 379/379)
- import surface: __version__=0.1.0 + HealthCheck/RestartPolicy/WatchdogConfig/run_loop/SERVICE_NAMES 全在
- python -m coco.watchdog --once --quiet rc=0; live 栈 (daemon 45165 / coco 59817 / dashboard 52681 / copilot-api 38079) 全部存活未动; events 仅 watchdog.started/stopped 无 health.degraded 无 restart.attempted
- V0 hash 锁 4 文件实 sha 一致; 各 V 独立 try/except
- daemon TCP IPv4+IPv6 双试; copilot-api 200 healthy; coco frame mtime + pid_alive 双重
- registry atomic write (tempfile + os.replace); 损坏/不存在不抛
- env COCO_WATCHDOG_INTERVAL_S 默认 30 + clamp [5,600]; max_restarts 默认 3 clamp [1,20]
- dashboard 不监控 (是观察者; backlog: infra-watchdog-fu-dashboard-redbar)
- audio-013/014/015/016 + interact-013/014/015/016 + dashboard-001/002 (zero-touch, watchdog 是独立模块, --once 不动现栈)

## findings

- P0: 无
- P1: 无
- P2:
  - watchdog 自身崩溃无 self-watchdog (预期, 留 backlog)
  - registry env 字段含 API key 持久化 ~/.cache/coco/processes.json (mode 0o600 应 enforced; backlog 跟进)
  - copilot-api 401 (auth 失效) 仍返 2xx-not 的情况 → 当前 only 2xx healthy, 401 算 degraded (合理)
  - 多 watchdog 实例并发无 file lock → race (单实例使用是预期)
  - 子进程 detach: subprocess.Popen 未 setsid, watchdog 退出后子进程可能被 SIGHUP (backlog: nohup-like detach)

verdict: LGTM, proceed to closeout.

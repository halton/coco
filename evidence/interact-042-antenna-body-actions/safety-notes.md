# interact-042 真机 UAT 安全注意事项

## body_yaw 首次启动

**必须人在场观察**：

- 上半身绕垂直轴整体旋转，**机器不位移**（Reachy Mini 无轮子）
- 天线接线 / USB 摄像头线 / 音频线如有缠绕风险，旋转可能拉扯
- 首次旋转用最小幅度（≤0.3 rad），逐步增加，观察电缆走向
- 当前 clamp ±π/2 (1.5708 rad ≈ 90°)，是软件层保守上限

## antenna 安全 clamp

- 单根天线限制 ±1.5 rad（SDK 极限 ±3.05 rad，本层保守约一半）
- 同向 +1.5 rad（perk_up 默认 1.2 rad）：天线竖直向上略后倾，物理 OK
- 同向 -1.5 rad（droop_antennas 默认 1.2 rad）：天线下垂，注意是否擦碰 base 或镜头视野
- 反向 wiggle（一上一下）幅度 1.0 rad 已经测试 sim OK

## 应急

- Ctrl-C 中断 coco 主进程
- 直接命令 `r.disable_motors()` 让舵机失力
- 物理推回中位

## 已知约束

- 当前 6 个 method 全部 fail-soft（try/except 不抛），便于 LLM tool calling 路径不崩主流程
- coco 主进程不下电（disable_motors）—— 多个 method 共享同一 robot 实例，不能各自 stop
- 同一时间只有 head/antennas/body_yaw 一组目标位姿生效，set_target 累加而非互斥

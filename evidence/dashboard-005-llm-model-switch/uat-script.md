# dashboard-005-llm-model-switch — Real-machine UAT script

## 目的

验证浏览器下拉切换 LLM model 后，coco 主进程在 ~30s 内热加载，下一条对话 reply 来自新模型（不重启 coco 进程）。

## 前置

- daemon + coco + dashboard + copilot-api 都在跑
- 浏览器能访问 http://localhost:8765
- copilot-api 已暴露 6 个模型：gpt-4o-mini / gpt-4o / gpt-4.1 / claude-sonnet-4.5 / claude-opus-4.7 / gemini-2.5-pro

## 步骤

1. 打开 http://localhost:8765
2. 等页面加载完，右下角应有 "LLM Model" 面板，下拉显示当前 model（首次会是空，因为 runtime_config.json 还没写过）
3. 对着麦说一句话（例如"你好"），timeline 显示 reply A
4. 在下拉里选 `claude-sonnet-4.5`，状态条出现 `saved (~30s 生效)`
5. 检查 `~/.cache/coco/runtime_config.json` 文件存在且含 `"llm_model": "claude-sonnet-4.5"`
6. 等 ≤30s，再说一句话，timeline 显示 reply B（语气/字数差异可观察）
7. 在 coco 主进程 stdout 应看到一条 `llm.hot_reload model 'gpt-4o-mini' -> 'claude-sonnet-4.5'` log
8. 切回 `gpt-4o-mini`，再说一句，验证可以来回切

## 验证要点

- [ ] dashboard 下拉切换后状态条出 `saved (~30s 生效)`
- [ ] `~/.cache/coco/runtime_config.json` 内容含新 model
- [ ] coco 进程 PID 未变（`ps -p <pid>` 仍是同一个 PID）
- [ ] 30s 内下一条 reply 已切到新 model（可看 coco log 里的 hot_reload 行）
- [ ] 非法 model 不能从下拉选中（白名单 6 个）

## 命令行旁路验证（不用浏览器也能跑）

```bash
# 看当前
curl -s http://localhost:8765/api/config/llm_model

# 切到 claude-sonnet-4.5
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4.5"}' \
  http://localhost:8765/api/config/llm_model

# 验文件
cat ~/.cache/coco/runtime_config.json

# 非法
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"model":"fake-xx"}' \
  http://localhost:8765/api/config/llm_model
```

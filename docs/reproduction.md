# 运行与复现

复现分两种：只读回放能严格重现已封存判断及原版汇总；重新调用远端 Codex 模型只能复现任务、组件、限制与流程，不能保证生成相同文本或行为。

## 首次使用

1. 安装 Python 3.11+，在本机执行 `codex login` 完成 ChatGPT 登录。
2. 确认 `codex --version` 与配置的 `expected_codex_version` 一致。本版本校准于 0.163.0，换版本需要重新校准和新建研究。
3. 在仓库根目录执行 README 的 `plan` 和 `preflight`。
4. 运行 smoke，检查报告中的实际候选数、data_complete、停止原因和用量。
5. 有候选时验证原始记录、导出并只读回放；没有候选则保留失败发现记录，不构造成功样例。
6. 首条证据链通过后再执行 pilot；P1a 使用独立配置与目录。

## 证据检查

```bash
python3 -m tracevolve verify --study .tracevolve/p0-smoke-001
python3 -m tracevolve report --study .tracevolve/p0-smoke-001
```

检查 `executions/` 中的 `stdout.jsonl` 与 `tool_events.jsonl`：load_bundle 返回的 Bundle 哈希应与不可变运行记录一致，运行应包含 Codex MCP 成功完成事件，不能只看 server_input.json 是否存在。`startup_context.json` 保存实际采用配置的离线启动上下文投影；`effective_config.json` 与 `launch.json` 记录配置和新会话入口。

回放使用 `judgments/sealed.json` 的精确请求哈希。修改 evidence、frozen 文本或输入投影后，未知哈希会失败，不会去询问模型补答案。已有 replay 目录不会自动覆盖。

## 中断

```bash
python3 -m tracevolve resume --study .tracevolve/p0-smoke-001
```

如果研究代码、任务、配置或运行时改变，恢复会拒绝。若上次进程终止留下 started/uncertain，则须核对供应商和原始日志；首版没有自动“判定已执行”的恢复功能，也不提供抹掉原记录后重试的命令。可以在明确新身份下开始另一次研究，保留旧研究不完整状态和费用。

CLI 超时会终止整个进程组，包括 MCP 子进程。Codex 内部网络重试属于客户端行为，JSON 事件能看到的部分原样保存，不能声称底层请求恰好执行一次。

SIGTERM 和终端中断同样会取消子进程，并保存 uncertain 状态及已生成的原始文件。MCP 服务若被异常重启，不能悄悄复位同一次任务的状态。启动上下文审计不注册任务 MCP；正式执行才会初始化任务服务。

`python3 scripts/audit_resource_runs.py --study <目录>` 可用原环境语义重放工具轨迹，检查声明响应和终态的一致性。此检查不产生模型调用，也不修改原始事实。

## 工程与费用验收

| 指标 | 解释 |
|---|---|
| target_met | 是否达到了预定候选数量 |
| data_complete | 冻结候选的验证、判断和预定保留配对是否全部齐全 |
| accounting_complete | 所有已发生会话费用是否已知；订阅模式通常为 false |
| budget_compliant | 美元预算是否可核对；本期为 null |
| dollar_acceptance_complete | 本期不会声明通过美元完整验收 |

成功完成任务但没有可用修改，不代表闭环证据已完成。判断一致、拒绝修改、真实任务失败则不自动否定工程有效性，关键是证据是否完整。

## 离线验证

`tests/test_core.py` 的 SyntheticAdapter 明确标记 synthetic，只用于验证控制流程和故障处理。`tests/test_codex_adapter.py` 用 CLI 进程替身验证子进程、超时、事件、个人 Skill 排除与费用语义。实际 MCP server 测试通过标准输入输出通信，不只是直接调用一个模拟函数。

旧版测试在 `vendor/agent_validation_pilot` 原样保留。可安装包的 `_vendor` 副本通过来源清单校验，避免原测试只测到不同于实际导出所用的实现。

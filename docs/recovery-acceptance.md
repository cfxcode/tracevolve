# 资源恢复任务：开发与有界真实采集

本轮完成资源恢复任务的实现、MCP 契约、错误归因、离线回放与 P1a 契约变化接线。真实执行链路通过，但没有出现可用的 Agent 处理错误或任务失败，**仍未完成自然候选的真实验证闭环**。

协议在运行前写入 [recovery-protocol.md](recovery-protocol.md)，配置与实现随研究冻结。机器可读证据见 [recovery-data.json](recovery-data.json)。

## 开发内容

- 增加短期一次性校验凭据；支持过期、替换及消费后的重新校验。
- 增加两种固定环境条件：资源忙且没有提交；执行已提交但回执丢失。状态查询可以区分是否需要重试，重复提交会被拒绝。
- `tool_errors` 保留全部工具错误；`environment_faults` 标记预设故障；`agent_errors` 仅计 Agent 处理错误。正确恢复预设故障不触发提案。
- 成对运行各自重建环境；任务重命名保留故障类别和状态转换。离线回放核对包括归因计数在内的完整终态。
- P1a 的 v2 契约保留恢复规则，仅将 checked 改为 verified。许可绑定完整契约字段。暂停与复验接线通过离线测试，尚无真实候选经历该阶段。

这仍是资源任务域，未引入第二任务域或 P1b—P3。环境故障是程序生成场景，不能作为生产系统事件或模型自身错误来宣传。

## 验证

原版 **119 项**测试通过；项目 **76 项**测试通过，合计 **195 项**。本轮新增 10 项回归覆盖：两个契约各 50 个种子的参考可解性、过期和错误动作、故障归因、候选门禁、ID 重命名、MCP stdio、状态回放及契约变化后的生命周期接线。

参考解在最多 51 次完整网关调用内完成；真实运行上限为 64 次、300 秒。正常基线 Prompt、模型 `gpt-6-astra`、推理强度 `high`、Codex CLI 0.163.0 与成功评分均保持固定。

## 真实运行结果

| 研究 | 任务 | 成功 | 网关调用 | 预设环境故障 | Agent 处理错误 |
|---|---|---|---:|---:|---:|
| difficulty-recovery-001 | calibration-0 | 是 | 26 | 2 | 0 |
| difficulty-recovery-001 | calibration-1 | 是 | 24 | 2 | 0 |
| p1a-recovery-001 | development-0 | 是 | 24 | 2 | 0 |
| p1a-recovery-001 | development-1 | 是 | 25 | 2 | 0 |
| p1a-recovery-001 | development-2 | 是 | 24 | 2 | 0 |
| p1a-recovery-001 | development-3 | 是 | 29 | 2 | 0 |

两次校准验证了环境执行和归因链路；随后按预先准备的独立配置做最多四个开发任务的扫描，排除校准全部任务模板。校准样本没有作为正式候选来源，也未修改原有成功记录或将预设错误解释成基线缺陷。

全部六条执行加载核验通过，保留 60 个原始文件；对象、事件链和调用账本一致，六条状态回放全部通过。正式扫描停止于 `NO_ELIGIBLE_CANDIDATE`：实际候选 0/1，`target_met=false`，`data_complete=false`。没有候选提案、A/B/C 运行、采纳判断、保留集评价、发布或复验被执行；不生成虚构候选补齐流程。

本轮新增 6 次 ChatGPT 订阅 CLI 会话尝试，观测耗时合计约 389.93 秒。input_tokens=1058295、cached_input_tokens=956928、output_tokens=5330，按各原始字段分别汇总，不将缓存字段重复相加。美元费用为 null，accounting_complete=false，不声明免费、美元预算合规或等成本比较。

## 复现

```bash
python3 scripts/calibrate_tasks.py --config configs/calibration-recovery.json --study .tracevolve/difficulty-recovery-001
python3 -m tracevolve run --config configs/p1a-recovery.json --study .tracevolve/p1a-recovery-001
python3 -m tracevolve verify --study .tracevolve/difficulty-recovery-001
python3 -m tracevolve verify --study .tracevolve/p1a-recovery-001
python3 scripts/audit_resource_runs.py --study .tracevolve/difficulty-recovery-001
python3 scripts/audit_resource_runs.py --study .tracevolve/p1a-recovery-001
```

前两条可能调用真实模型；后四条是离线检查。历史运行必须使用相同源码/运行时或其归档版本；新执行应使用新的研究目录及对应的 prior_studies 路径，不能混用历史已冻结身份。远端模型行为不保证逐次相同。

## 当前边界

本轮说明该模型在六个已执行恢复场景上均能正确处理故障，不能推出总体零失败，更不能推出 Skill 优化有效。继续重复同类任务不是充分的闭环验收方案。本轮按预定上限停止，不扩大运行量、不降低模型、不削弱基线，也不将成功恢复临时改为候选来源。

下一轮应先校准真正需要全局规划的条件约束，例如资源容量限制、多个目标之间的释放顺序及可回滚操作；先用独立参考解证明任务可完成，再冻结新的小规模配置。仍需观察到真实处理错误或失败，才能推进候选验证。

# P0 / P1a 任务校准进展（2026-10-22）

本轮实现并真实运行了共享库存、资源池分配及精细整数分配任务。
**工程实现与证据核验通过；P0 / P1a 的真实候选闭环仍未验收。**

## 真实结果

| 任务分布 | 校准会话 | P0 开发执行 | P1a 开发执行 | 成功 | Agent 错误 | 自然候选 |
|---|---:|---:|---:|---:|---:|---:|
| 共享库存 capacity-v1（含一次选定压力校准） | 3 | 6 | 6 | 15 | 0 | 0 |
| 资源池分配 allocation-v1 | 2 | 6 | 6 | 14 | 0 | 0 |
| 精细整数分配 allocation-precision-v1 | 2 | 6 | 6 | 14 | 0 | 0 |
| 本轮合计 | 7 | 18 | 18 | 43 | 0 | 0 |

这些是不同的冻结任务分布，合计仅描述工作量，不能作为同一个随机实验进行推断。
本轮所有运行均复用 ChatGPT 订阅认证、Codex CLI 0.163.0、gpt-6-astra / high，
沿用正常基线 Prompt、64 次工具调用及 300 秒单次上限，没有启用按量付费 API。

43 次执行均有实际 Bundle 加载回执；430 个原始文件、对象、事件链和调用账本核验通过，
43 条工具状态轨迹回放通过。独立检查覆盖 83 个冻结任务及 3 个认证过的后续任务，
未发现模板重复，见 [划分核验](calibration-split-audit.json)。

观测耗时合计约 2270.10 秒。原始 token 字段合计：input_tokens=4,498,361、
cached_input_tokens=3,783,296、output_tokens=41,564、reasoning_output_tokens=523。
各字段分别记录，不把缓存 token 再加进输入总数。美元费用仍为 null，美元预算合规与等成本比较未完成。

## 精细整数分配批次

该分布使用 5 个容量为 1000 的资源池及 25 个用量在 100—300 之间的任务。
所有用量、依赖和容量公开。离线精确求解器独立认证可解性，参考调用长度为 55；
参考分配没有进入任何模型输入。源码对应 `5d15f3c` 的运行时实现。

| 研究 | 实际工具调用数 | 成功 | 候选 |
|---|---|---:|---:|
| difficulty-allocation-precision-001 | 55、55 | 2/2 | 仅校准 |
| p0-allocation-precision-smoke-001 | 55、55、55、55、55、56 | 6/6 | 0/1 |
| p1a-allocation-precision-001 | 55、56、55、55、55、55 | 6/6 | 0/1 |

机器可读证据见 [精细整数批次](allocation-precision-data.json)，前序证据见
[共享库存](capacity-data.json)和[资源池分配](allocation-data.json)。
精确整数压力分布不能被解释为普通生产任务的代表性样本；本轮也没有观察到需要由 Skill 修复的问题。

## 验收边界

- 当前原版 119 项、项目 95 项测试通过，共 214 项。新测试覆盖可解性、错误原子性、
  幂等性、严格输入、ID 重命名、MCP stdio、状态回放及合成的导出／生命周期接线。
- 本轮六项正式扫描均为 `NO_ELIGIBLE_CANDIDATE`、`target_met=false`、`data_complete=false`。
- 没有执行真实提案、A/B/C 配对、判断封存、保留集、发布或契约变化后的 Skill 复验。
  离线测试不能替代这些阶段的真实证据。
- 本轮五候选小试配置已准备，但未启动，因为真实 smoke 候选链尚未完成。
- P1b—P3 没有纳入本轮；没有开放候选脚本、Workflow、连续升级或第二运行时。

本轮按原设计第 12.3 节的“候选不足”停止条件结束这些采集批次。
这不是项目完成，也不是耗尽各研究的 32 次会话额度。校准没有提供真实候选，后续阶段因而没有触发。

下一步需要有代表性的实际任务或失败案例，重新评估任务选择。
继续增加整数精度或计算复杂度不能自动建立代表性。若改用代码维护等第二任务域，
应先明确调整此前将 P1b 排除在本轮之外的范围。
完整验收仍需要：至少一个自然 Prompt 候选的 P0 证据链与五候选小试，
一个自然 Skill 候选的采用／拒绝／暂缓及后续执行记录，再完成契约变化后的暂停与复验。
候选可以无效、判断可以拒绝，不能用零候选代替候选验证。

## 复核与复现

当前源码可以离线核对精细整数批次：

```bash
python3 -m tracevolve verify --study .tracevolve/p0-allocation-precision-smoke-001
python3 scripts/audit_resource_runs.py --study .tracevolve/p0-allocation-precision-smoke-001
python3 -m tracevolve verify --study .tracevolve/p1a-allocation-precision-001
python3 scripts/audit_resource_runs.py --study .tracevolve/p1a-allocation-precision-001
python3 scripts/audit_calibration_splits.py \
  --data docs/capacity-data.json --data docs/allocation-data.json \
  --data docs/allocation-precision-data.json --output /tmp/tracevolve-splits.json
```

对历史状态机回放，库存批次使用 `bb9e982`，原分配批次使用 `7fbbf2e` 的源码或研究内归档。
不能把当前源码直接当作历史实现。新的真实执行应使用新研究目录并更新配置中的 `prior_studies`；
先运行 `scripts/certify_allocation.py`，再运行校准脚本或 `tracevolve run`。
当前约束、分支条件和种子见 [分配协议](allocation-protocol.md)。远端模型行为不保证逐次相同。

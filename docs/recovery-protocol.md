# 资源恢复校准协议（运行前冻结）

仍在资源依赖任务域内验证规划、条件检查与恢复。保留正常基线 Prompt、固定模型、成功评分和独立会话；使用显式标注的程序生成环境场景，不能把它描述为生产系统故障。

## resource-recovery-v1

- `inspect_job(job_id)` 返回依赖、当前状态、requires_check 及环境可靠性类型。
- `check_job(job_id)` 仅在依赖已完成且自身待执行时签发一次性 receipt；在接下来的 3 次网关调用内有效。签发后继续远距离规划可能使它过期。
- `run_job(job_id, mode, receipt)` 对 requires_check=true 必须使用 checked 模式与有效 receipt；false 使用 standard，receipt 为空字符串。校验通过的尝试消耗 receipt。已经完成的 job 不允许重新运行。
- `busy_once` 资源首次合法运行返回 RESOURCE_BUSY，不提交任务；receipt 已消耗，需要重新校验后再试。
- `lost_ack_once` 资源首次合法运行已经提交，但返回 OUTCOME_UNKNOWN；需要 inspect_job 确认状态，不能假定未提交并重复执行。
- 任务完成后 submit 精确目标集合。所有预条件/参数错误不改变任务完成状态。环境回执丢失可能发生在已提交之后，与参数错误不同。

预设 busy/lost_ack 响应计入 tool_errors 和 environment_faults；`agent_errors=tool_errors-environment_faults`。只出现预设环境故障且成功恢复的任务不触发提案；真正的 Agent 处理错误或任务未完成才是开发问题。不可评价的 CLI/认证/传输错误仍是基础设施失败。

## 成对验证与契约变化

任务、故障类型、基线和候选使用完全相同的配置；每次运行重建独立状态。ID 重命名只影响 ID/依赖/目标，不改变 payload、故障种类、预算和状态转换。签发凭据由局部计数生成，不含资源 ID。

P1a 契约变化为 resource-recovery-v2：checked 改为 verified，其他恢复规则保持不变。许可绑定全部工具契约字段；未知或变化时禁止普通任务加载。候选仍可在隔离复验中测试，不自动发布。

## 有界校准

先运行两个固定基线校准任务，每次最多 64 次网关调用、300 秒；不从校准生成正式候选。校准通过离线可解性与实时记录核验后，再用独立种子、排除校准模板的正式研究。正式单轮最多 4 个开发任务、32 次 CLI 会话；候选不足或无收益都如实记录。

评分仍为提交成功 1、未成功 0，不为制造改进而临时改变评分或削弱基线。即使有真实自然候选，判断/发布仍依据预先固定的证据规则，不要求候选必须改善。

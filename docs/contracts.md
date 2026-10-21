# 输入与状态契约

## 配置

三个可直接使用的配置在 `configs/`。未知字段会被拒绝；数量、时间和工具调用上限必须为正整数。首期只接受 `billing_mode=chatgpt_subscription`、`phase=p0|p1a`，候选上限为 5，P1a 每轮仅 1 个候选。认证目录默认 `~/.codex`，不进入公开配置快照。

计划以 `study_id`、随机种子、预定规模和运行限制冻结。`prior_studies` 用于排除之前研究已见的任务模板。不同 study_id 本身不能证明任务独立。

## 任务与环境

任务 envelope 包含 `content`、规范化 JSON `sha256`、根来源 `source_id`、`identifier_contract=opaque`。任务内容是 `id/jobs/goals`；每个 job 仅允许 `id/requires/payload`，payload 包含枚举 kind 与严格布尔值 requires_check。

资源 ID 出现在 job.id、requires、goals 中，没有隐含排序语义。变换保持数组顺序、payload 值及类型，使用完整、非恒等、与原集合不相交的双射；逆变换由原版校验器检查。环境级合法和非法动作等变性由测试验证。

| 工具 | 参数 | 规则 |
|---|---|---|
| `load_bundle` | `{}` | 返回实际 Prompt/Skill 内容及哈希，其他动作前必须成功 |
| `list_jobs` | `{}` | 返回原数组顺序的 ID 与 kind |
| `inspect_job` | `job_id` | 返回依赖、payload、当前状态 |
| `run_job` | `job_id, mode` | 依赖成功且模式正确才改变状态；重复完成无重复副作用 |
| `submit` | `goal_ids` | 必须与公开目标集合一致、无重复且均已完成 |

错误包括 INVALID_ARGUMENT、UNKNOWN_JOB、WRONG_MODE、DEPENDENCIES_NOT_DONE、INVALID_SUBMISSION、TOOL_LIMIT。任务层错误不改变状态但消耗调用额度。基础设施错误独立记录为 failed/uncertain，不提供伪造的 utility。

## 冻结修改与执行

P0 Patch 只能替换 Prompt，P1a Patch 只能添加或替换 Skill，目标必须不同于基线并引用来源运行。冻结后改动产生新研究或新候选，不能继续使用原验证证据。

研究中所有候选基于同一 Bundle。导出的冻结文本序列化完整 Bundle 与执行快照，不只是修改说明。快照涵盖模型、CLI、工具、源码、限制及经验注入策略。实际加载记录由宿主回执与 Codex `mcp_tool_call` 成功事件交叉核对。

`completed` 表示可评价，包含任务失败；`failed` 表示不可评价。底层请求是否执行不明时账本使用 `uncertain`，兼容导出映射为 failed 并保留错误原因。没有自动重试。

## 判断与发布

判断器输出 `{accept: boolean, estimated_gain: number|null, rationale: string}`。费用由宿主提供，模型不能自报计费。格式错误是判断失败，不转换为 reject。

P0 固定 A/B/C 先封存，随后评价。P1a 使用固定 A 判断和全部必测检查作发布决定；需要维护者确认时先暂停，不提前展示保留集结果。发布、拒绝、暂缓与参考标签是不同状态。

P1a 许可包含具体 Skill/Bundle、任务族、工具依赖、支持证据及状态。只允许 applicable 且默认指针匹配的 Skill 进入后续任务。默认为基线。实验授权与正式发布许可分开。

## v0.2.1 导出

生产导出直接构建 schema_version=2，不调用合成补齐工具。原版 `validate_data`/`validate_contract` 校验冻结文本、任务、配对、重命名、费用关系和隐藏字段。原字段与统计设置见 [`INPUT_CONTRACT.txt`](../tracevolve/_vendor/INPUT_CONTRACT.txt)。

每项修改的初始验证只产生一对实际执行，A/B/C 可引用同一证据，但实际请求账本不会重复计费。B 必须新跑，C 两侧必须执行相同变体。失败配对保持两侧记录；未执行不写成成功。

订阅运行所有美元费用保持 null，已知 token 与时长单独记录。旧回放输出不能替代实际请求账本；空保留集的旧汇总金额也不能解释为该阶段没有发生调用。

## 恢复与审计

网络执行前在 SQLite 中预留唯一 logical_call_id、请求内容哈希和 started 记录。完成后写不可变结果并追加终态。重复读取 completed 请求不会再次调用；请求内容变化、失败重试或未核对的 started/uncertain 状态会拒绝继续。

SQLite 的事件表禁止 UPDATE/DELETE，事件按前一哈希串联；对象和原始文件有内容哈希。此机制用于一致性审计，并不防止有完全机器权限的人重建整套日志。

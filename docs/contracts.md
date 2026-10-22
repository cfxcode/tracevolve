# 输入与状态契约

## 配置

可直接使用的配置在 `configs/`。未知字段会被拒绝；数量、时间和工具调用上限必须为正整数。首期只接受 `billing_mode=chatgpt_subscription`、`phase=p0|p1a`，候选上限为 5，P1a 每轮仅 1 个候选。认证目录默认 `~/.codex`，不进入公开配置快照。

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


## 2026-10-22 审查修复后的约束

- 工具额度按网关唯一请求计数，包含每次 `load_bundle`、加载前动作和非法加载参数；所有错误进入 `tool_errors`。相同 JSON-RPC ID 和参数的传输重试复用回包，不重复计数。达到上限后关闭本次工具进程。初始化、工具枚举和 ping 不计任务额度。
- 判断器失败仍封存为失败，不转换为拒绝；所有可执行候选仍完成预定保留集。报告保持 `data_complete=false`。恢复可读取已失败判断的原结果，但不会重发请求；未核对的 started/uncertain 仍阻止新调用。
- `judgments/sealed.json` 缺失时由不可变判断对象恢复；文件存在但内容不符则拒绝覆盖。后续任务的 Bundle、任务和选择依据在首次执行前冻结，契约变化后恢复不重新选择历史请求的 Bundle。
- `verify` 将请求开始/结束事件与调用表、结果状态逐项比对，并核对状态表、发布指针与事件；仍检查全部对象和原始文件。`publish` 在事务内再次校验，并将冻结候选、许可、判断请求/结果、配对执行及实际加载证据连接到同一发布对象。哈希一致不代表远端执行的独立认证。
- 认证租约目录为认证根下的 `.tracevolve-auth/active`，权限 0700，文件 0600；从不进入研究目录。TraceVolve 会话互斥使用同一登录。退出与取消时回写刷新结果；异常进程退出后，下一次租约先恢复。若原登录已由外部进程改变，则保留双方并报 `AUTH_REFRESH_CONFLICT`，不自动覆盖。外部 Codex 不参与租约锁，检测与替换之间不能提供跨客户端事务保证，实验期间应避免同时对该登录进行刷新/切换。

认证存储方式依据 [OpenAI 认证文档](https://learn.chatgpt.com/docs/auth)；冲突处理是 TraceVolve 自身的实现约束，不是 Codex 提供的事务保证。

## 独立任务难度校准

`task_profile=basic-v1` 保留原任务生成规则。`planning-v2` 使用 26–30 个节点、共享前置链与交叉依赖、3 个目标及干扰节点；资源工具语义、基线 Prompt 和评分保持不变。离线参考解需要 52–60 次完整网关调用，额度仍为 64；模型冗余调用可能耗尽额度。

`configs/calibration-planning.json` 只安排 3 次基线会话，每次最多 600 秒。校准不生成候选。正式配置 `configs/p1a-planning.json` 使用独立种子，排除校准所有任务模板，开发扫描上限 4、会话上限 32。校准、正式实验均新建研究，不修改 2026-10-21 记录。

```bash
python3 scripts/calibrate_tasks.py --config configs/calibration-planning.json --study .tracevolve/difficulty-planning-001
# 校准完成并检查代表性后，才启动独立正式研究：
python3 -m tracevolve run --config configs/p1a-planning.json --study .tracevolve/p1a-planning-001
```

## 7229a9b 复查后的并发与恢复约束

- Codex 执行进程与两次可能的启动上下文审计进程均通过 `pass_fds` 继承认证租约的文件描述符。POSIX `flock` 随共享描述符存活；宿主被 SIGKILL 后，存活子进程继续持锁。其他研究返回 `AUTH_IN_USE`，不能回收或重用 `active`；持锁子进程退出后，下一次租约才核对凭据并回收。退出时不显式 `LOCK_UN`，以免解除子进程仍在持有的锁。
- 认证清理失败单独记录为 `auth_cleanup_errors`。超时保持 `uncertain / EXECUTION_TIMEOUT`，取消保持 `uncertain / CANCELLED`；解析或能力检查的后续错误也不覆盖原超时原因。不确定调用仍阻止后续预留。
- 已冻结研究恢复时，缺失的 `manifest.json` 由不可变冻结对象重建；已存在但内容不符时拒绝覆盖。恢复后的 manifest 可继续用作 `prior_studies` 的来源。
- 发布从 `BEGIN IMMEDIATE` 开始，许可、判断、证据、候选与父版本的全部检查和状态更新处于同一写事务。已经变成 `needs_revalidation` 的许可不能因旧的事务外读取而重新激活。
- `verify()` 使用 SQLite savepoint 保持一致读取快照；在发布等已有事务中调用时不提交或回滚调用者的事务。正常并发追加事件、更新状态或完成请求不会造成旧事件与新状态混读。
- `collab_tool_call` 与兼容的 `collab_agent_tool_call` 均判为越界能力，保留 `capability_violations`，不允许作为成功执行。这是审计防线，不是历史真实运行调用了子 Agent 的证据。


## 资源恢复实验配置

`task_profile=recovery-v1` 的完整行为与采集判据见 [恢复协议](recovery-protocol.md)。这是原资源任务域内的新契约；需要一次性校验凭据，并显式区分 `environment_faults` 与 `agent_errors`。预设环境故障不作为 Agent 缺陷；既有 basic/planning 的错误口径保持原样。

该配置冻结 `resource-recovery-v1`；P1a 后续契约变化使用 `resource-recovery-v2`，继续保留凭据与故障恢复规则，仅改变 checked 模式名称。新许可绑定完整工具契约，包含凭据有效期及重复执行规则，不仅绑定版本名称。


## 共享库存契约

`capacity-v1` 使用 `resource-capacity-v1`，保留原有五个 MCP 操作。`list_jobs`
增加四维库存的容量、初始值和当前值；`inspect_job` 提供 `consumes` / `produces`。
`run_job` 先检查依赖和模式，再原子检查输入库存及最终容量；失败不改变库存或完成集。
成功执行不可撤销，已完成任务重复执行不再次消耗或产出。`submit` 的成功标准不变。
P1a 的 `resource-capacity-v2` 仅将 checked 改为 verified，完整库存契约继续绑定许可。

离线 `scripts/solve_capacity.py` 按已完成任务集合搜索可行顺序，不读取生成器内部顺序，
也不会被执行或提案会话加载。`scripts/certify_capacity.py` 在真实调用前冻结研究并保存
全部任务的可解证书；P1a 另覆盖固定的后续／复验任务。详见[采集协议](capacity-protocol.md)。


## 资源池分配契约

`allocation-v1` 的 `resource-allocation-v1` 保留五个工具名，给 `run_job` 增加必填
字符串 `pool`。正用量任务选择公开资源池；零用量目标必须使用空字符串。
用量和依赖由 `inspect_job` 提供，资源池初始／剩余容量由 `list_jobs` 提供。
成功保留容量在本次任务内不可撤销；错误不修改容量、完成集或分配记录。
已完成任务重复运行不重复占用。v2 保留分配语义，仅将 checked 改为 verified。
参考装箱求解器与运行时分离，实际状态回放另外核对 `allocation.py` 的源码哈希。
完整划分与采集规则见[分配协议](allocation-protocol.md)。

# TraceVolve

[English](README.md) | 中文

基于执行证据的 Agent 自优化框架。首期支持 Codex CLI、资源依赖任务、P0 Prompt 修改与 P1a Skill 单轮验证。早期设计名为 EvoHarness。

TraceVolve 保存原始执行事实，冻结候选，再用独立会话验证。候选可能被拒绝，开发任务也可能没有产生候选；这些结果会如实保留。当前版本不提供连续自动升级、任意代码修改或多 Agent 拓扑优化。

## 安装与预检

需要 Python 3.11+、已登录 ChatGPT 的 Codex CLI。执行器使用 POSIX 进程组（macOS/Linux）。当前适配并固定 `codex-cli 0.163.0`、`gpt-6-astra`、推理强度 `high`。生产代码使用 Python 标准库。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/tracevolve plan --config configs/smoke.json
.venv/bin/tracevolve preflight --config configs/smoke.json
```

也可以不安装，直接在仓库根目录运行 `python3 -m tracevolve …`。`plan`、`preflight` 不调用模型。`preflight` 检查 CLI 版本和已有文件式 ChatGPT 登录，不展示凭证。首次真实执行还会离线审计实际启动上下文。

## P0：真实采集与旧协议回放

```bash
python3 -m tracevolve run --config configs/smoke.json --study .tracevolve/p0-smoke-001
python3 -m tracevolve report --study .tracevolve/p0-smoke-001
python3 -m tracevolve verify --study .tracevolve/p0-smoke-001
# 仅在产生候选且证据就绪时导出和回放：
python3 -m tracevolve export --study .tracevolve/p0-smoke-001
python3 -m tracevolve replay --study .tracevolve/p0-smoke-001
```

冒烟目标为 1 个自然候选、3 对保留任务。开发扫描最多 6 个任务，全研究最多 32 次 CLI 会话尝试；每次运行最多 64 次网关工具调用（含 `load_bundle` 和加载前错误）、300 秒。开发中正常完成但工具动作失败，也是可观察的开发问题。基础设施故障不会被当成 Prompt 缺陷来生成候选。

每个候选的 A/B/C 共进行 6 次验证执行：A 为初始配对，B 为原题重新配对，C 为同一双射重命名任务的配对。全部判断封存后才开始保留集。冻结配置见 `manifest.json`。

没有合法候选时只生成报告与原始记录，不生成虚构的 v2 单位。保留执行不完整时，v2 导出该单位的整个保留集为空，原始失败和全部计划仍保存在研究账本；这类导出仅用于诊断，不能作为完整研究。

首次冒烟证据链通过后，再启动独立的五候选小试：

```bash
python3 -m tracevolve plan --config configs/pilot.json
python3 -m tracevolve run --config configs/pilot.json --study .tracevolve/p0-pilot-001
```

小试目标为 5 个候选、每个候选 5 对保留任务，最多扫描 30 个开发任务、160 次会话尝试。模板排除覆盖此前 smoke 的全部任务。候选不足不会缩小预定目标。两种规模的原版统计参考标签均不能越过默认区间阈值，不用这些小试宣称 C 优于 B。

## P1a：Skill、许可与契约变化

```bash
python3 -m tracevolve run --config configs/p1a.json --study .tracevolve/p1a-smoke-002
python3 -m tracevolve report --study .tracevolve/p1a-smoke-002
```

Skill 通过适配器管理的 `load_bundle` 工具明确注入 Codex 上下文。这里实现的是受控 Skill 加载，不使用 Codex 的全局原生 Skill 自动发现。运行必须同时具备宿主读取记录和 Codex MCP 成功回包，且两者内容哈希一致，才能作为候选加载证据。这证明内容已交付，不证明模型理解、采用或获得收益。

固定验证采用原题、重复原题、重命名配对，以及任务族和未知依赖的确定性边界检查。A 判断、完整执行、无逐对退化和平均收益至少 0.05 是发布条件；B/C 判断不用于挑选更有利的发布结论。条件满足时状态为 `AWAITING_MAINTAINER`，在执行外层评价之前等待维护者决定。

维护者查看发布证据后，可显式发布，再继续后续任务与复验：

```bash
python3 -m tracevolve publish --study .tracevolve/p1a-smoke-002 \
  --expected-generation 0 --maintainer YOUR_NAME --confirm
python3 -m tracevolve resume --study .tracevolve/p1a-smoke-002
```

拒绝或证据不足时使用基线。工具契约 v2 将 `requires_check=true` 对应的模式从 `checked` 改为 `verified`，工具说明同步更新。相关许可被暂停；隔离复验仍可测试候选，但不会自动扩大正式许可。回归则退休，其他结果暂缓并保存新证据。

```bash
python3 -m tracevolve rollback --study .tracevolve/p1a-smoke-002 \
  --expected-generation 1 --reason observed-regression
```

发布指针和许可按父版本、作用范围及代数检查；在途运行保持原快照。P1a 不自动开始下一轮搜索。

## 恢复任务校准

实验性 `recovery-v1` 在资源任务域加入短期校验凭据、资源忙响应和执行回执丢失。这些是预设的程序生成环境场景。环境故障与 Agent 处理错误分别记录；成功处理预设故障不会触发候选提案。详见[恢复协议](docs/recovery-protocol.md)。

```bash
python3 scripts/calibrate_tasks.py --config configs/calibration-recovery.json --study .tracevolve/difficulty-recovery-001
python3 scripts/audit_resource_runs.py --study .tracevolve/difficulty-recovery-001
# 检查校准证据后，使用独立的正式任务划分：
python3 -m tracevolve run --config configs/p1a-recovery.json --study .tracevolve/p1a-recovery-001
```

最新证据：六次校准／开发执行全部成功，Agent 处理错误为零；尚无自然候选或真实优化收益。详见[恢复任务验收](docs/recovery-acceptance.md)。

校准最多两个基线会话；正式配置最多四个开发任务、32 次会话。两者保持正常基线和二元任务成功评分，不预设修改一定有效。

## 共享资源规划

`capacity-v1` 在原资源任务域加入有限库存和不可逆的消耗／产出，所有约束均公开。
局部合法的执行顺序可能使后续目标无法完成，仅按依赖遍历不足以解决任务。
基线 Prompt、模型、二元成功评分和 64 次调用额度保持不变，详见[冻结协议](docs/capacity-protocol.md)。

真实执行前，使用独立离线求解器认证全部冻结任务可解；参考执行顺序不进入任何模型输入：

```bash
python3 scripts/certify_capacity.py --config configs/calibration-capacity.json --study .tracevolve/difficulty-capacity-001
python3 scripts/calibrate_tasks.py --config configs/calibration-capacity.json --study .tracevolve/difficulty-capacity-001
python3 scripts/audit_resource_runs.py --study .tracevolve/difficulty-capacity-001
```

正式配置依次为 `configs/p0-capacity-smoke.json`、`configs/p1a-capacity.json`、
`configs/p0-capacity-pilot.json`。对各自研究目录先认证，再执行 `tracevolve run`。
前序研究排除规则保持任务模板隔离。重新复现应使用新目录并更新 `prior_studies`，不能覆盖冻结研究。

## 认证、隔离和费用

- 只复用已有 ChatGPT 登录。每次运行在认证目录下租用权限受限的隔离目录，保留刷新后的凭据并在核对回写后清理；若检测到并发登录变化，则保留两份凭据并停止，等待人工核对；不会将其存入研究目录或 Git。API key 环境变量不传入子进程。
- 每个角色使用新会话、独立工作区与临时 Codex 配置目录。忽略个人配置和规则，禁用插件、记忆、shell、浏览器及子 Agent。必要的系统代理环境保留。
- 执行前用 `codex debug prompt-input` 检查上下文；对外部个人 Skill 添加显式禁用后再次检查，残留则停止。Codex 自带系统说明及系统 Skill 元数据属于固定运行时，并保留上下文记录。
- 执行 Agent 只获得本次资源 MCP 工具。提案器、判断器不注册资源 MCP，没有 shell/文件读取入口；只接收投影后的开发或验证输入。保留集不进入判断请求。
- 每次 CLI 会话尝试先落盘，再启动；中断或超时的不确定尝试会阻止自动恢复重发。内部 HTTP 请求、重试和逐请求 token 上限不由 `exec --json` 完整暴露，框架不声称控制了它们。
- 记录实际可获取的 token 字段、耗时、工具调用和会话次数。订阅的美元成本为 `null`，`accounting_complete=false`、`budget_compliant=null`。不能解释为免费、美元预算合规或等成本实验。

## 研究产物

```text
.tracevolve/<study>/
  manifest.json          冻结配置、任务划分、源码与运行时身份
  state.sqlite           控制状态、请求账本、追加审计事件
  objects/<sha>.json     不可变对象：候选、运行、证据与判断
  executions/<id>/      原始 stdout/stderr、启动上下文、工具与加载回执
  judgments/sealed.json  按原版请求哈希封存的判断
  export/records.v2.json v0.2.1 兼容输入（仅 P0）
  replay/               不调用模型的原版回放结果
  report.json            完整性、候选数量、停止原因与用量
```

研究目录默认不提交 Git。`verify` 校验对象、原始文件和审计链；哈希检测内容漂移，不是第三方执行认证。换代码、CLI、模型配置或任务规则后必须新建研究。`resume` 只复用完全相同的请求，不静默重试失败调用；如存在不确定调用，应先人工核对已有日志。

还可离线重放资源工具状态，核对记录中的每个响应及最终结果：

```bash
python3 scripts/audit_resource_runs.py --study .tracevolve/p0-smoke-001
```

该检查要求环境源码与运行时快照一致，不重新询问模型。源码全文同时存入研究对象库，便于在后续实现变化后复查历史运行。

## 测试与进一步阅读

```bash
python3 -m unittest discover -s vendor/agent_validation_pilot -v
python3 -m unittest discover -s tests -v
```

旧版 119 项回归测试保持原样。新增测试包含真实子进程适配器测试替身、真实 MCP stdio 进程、故障与账本测试，以及明确标为 synthetic 的闭环测试。模拟测试不作为真实模型实验结论。

- [架构与扩展边界](docs/architecture.md)
- [输入与状态契约](docs/contracts.md)
- [运行和复现说明](docs/reproduction.md)
- [阶段验收记录](docs/acceptance.md)
- [旧工具来源及校验清单](vendor/README.md)

Codex 接入依据：[非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)、[认证](https://learn.chatgpt.com/docs/auth)、[配置参考](https://developers.openai.com/codex/config-reference)。具体行为同时以固定本机 CLI 校准记录为依据。

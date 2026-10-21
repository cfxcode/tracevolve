Agent 自我修改验证：A/B/C 离线预实验工具包 v0.2.1
================================================

真实模型实验尚未执行。请先查看 STATUS.json；本次修订见 CHANGELOG.txt。
本包包含可运行的证据回放器、
一个受控的 ID 重命名干预、协议与合成自检数据，不包含已验证的论文结论。
它不是完整的模型调用/候选采集系统。

1. 冻结的决策对象
-----------------
每个 unit 只有一项冻结修改与一个固定 baseline。决策只有接受、拒绝。
接受后，在独立后续任务上按相同方式直接应用修改；拒绝则使用 baseline。
不引入经验检索、多候选选优、技能组合或在线调度器。

第一版选择固定误判代价：错误接受=1，错过有效修改=1。
有效性阈值为固定执行限制下的任务成功率提升 0.05。
后续任务的连续增益另作描述性报告；不把美元成本从错误概率中直接相减。
具体协议见 protocol.json，所有阈值都是预实验设置，不是实证发现。

2. 三组证据
-----------
A：初始完整 baseline/candidate 验证。
B：相同初始证据 + 再次完整执行同一道原任务，baseline/candidate 成对。
C：相同初始证据 + 在 ID 重命名后的同一变体上成对执行 baseline/candidate。

本轮只比较“ID 重命名验证”与“重复原题验证”。即使 C 优于 B，也不能据此
声称它优于等成本的新任务验证。v2 输入校验会拒绝 B 偷换为另一道任务。

B/C 使用相同追加预算上限、同一个判断器与输入协议。相同预算上限不保证
实际支出完全相同，必须同时报告实际支出。未知费用保持 null。
候选生成、初始证据、失败实验、干预生成与解释、判断器调用、保留集评估费用
必须记录；独立评估费用单列，不能拿评估结果帮助采纳。

3. 唯一干预
-----------
对结构化 DAG 工具任务中的不透明资源 ID 作一致双射重命名。
只修改 jobs[].id、jobs[].requires[]、goals[]，以及可选的管理用 task id。
payload、依赖关系和目标不变。逆映射必须按 JSON 解析后的类型恢复原任务。
布尔、整数与浮点数严格区分；False/0、True/1 及 1/1.0 的替换均不通过。
字典键的插入顺序不影响比较，数组顺序必须保持。

这只能验证变换在明确声明 ID 不携带语义的任务中有效。不能将它未经检查地
套用于文件名、业务编号或自然语言名称；也不能把它本身视作论文创新。
本工具生成变换后的任务，不调用模型，也不判断修改是否值得采纳。

4. 本地运行（Python 标准库即可）
------------------------------
进入本目录后执行：

    python3 make_demo.py
    python3 -m unittest discover -p 'test_*.py' -v
    python3 pilot.py --input data/demo_records.json --output runs/recheck --demo
    python3 sample_plan.py

包中已经附带 runs/demo；重新写入该目录时为 pilot.py 显式添加 --overwrite，
或者选一个新输出目录。数据输入与 summary 均为 schema_version=2。

--demo 使用显式的合成固定判断规则，仅检查 I/O、证据隔离与费用计算。
演示中有“诊断帮助”和“诊断误导”两类人为样本；它们都不是自然候选，
不能用来估计发生频率、预测能力或 C 相对 B 的真实收益。

5. 接入真实证据
---------------
先阅读 INPUT_CONTRACT.txt。v2 要求真实内容、任务来源与每次执行的绑定记录，
不能只把旧日志的 schema_version 改为 2，也不能用合成 fixture 补齐真实证据。

再用固定的 agent/model 采集自然修改、initial_evidence、extra_B、extra_C，
再提供独立后续任务的成对结果 heldout。真实 event/pair 的 synthetic 设为 false。
保留模型名/版本、完整 harness、提示、任务族、调用参数、原始工具轨迹、
token/缓存用量、定价版本与真实费用；不要只记录最后成功/失败。
开发与测试按任务族或仓库划分，不按同一任务的重复轨迹随机划分。

回放器通过 --judge-command 接入判断器，命令从 stdin 读取 JSON，向 stdout
输出一个 JSON 对象，包含 accept:boolean，以及 usage.cost_usd:number|null。
可选 estimated_gain 与 rationale。命令返回非 JSON 或缺少 accept 时必须失败，
不能悄悄使用 demo 规则。具体命令形式以 python3 pilot.py --help 为准。

判断请求只包含白名单元数据和本组可见证据，不包含 heldout 或实验组标签。
但外部命令和 Python 处于同一个操作系统环境；白名单不是安全沙箱。
真实模型判断器不得拥有读取原始输入文件和保留集的额外工具权限。
白名单也无法识别 observation 或 patch_description 中夹带的保留集答案；
任务划分、事件来源以及证据文本中的语义泄漏仍须在数据采集阶段独立审计。

顶层 shared_overheads 应记录 candidate_generation_usd 与 calibration_usd。
未提供的费用保持未知；会计字段齐全也不会被自动认证为科学有效的比较。

v0.2.1 补充同一事件总额与执行分项的关系校验。paired_validation 可声明
cost_scope=execution_only（双侧已知时总额须等于两侧之和）或
execution_plus_overhead（总额覆盖两侧执行及本事件额外开销）。未声明时沿用
v0.2 的完整事件总额口径，即 execution_plus_overhead，输入 schema 仍为 2。
任一已知总额都不得低于已知执行分项小计；只知道一侧时也检查这个下界。
使用相对 1e-12、绝对 1e-12 美元的浮点容差，不会接受 100+100 对 .001。

总费 null 继续为 null，不从分项自动补算；分项 null/缺失也不补零。
已知完整总额可以与未知分摊共存，audit 会明确标出只检查下界或无法核对分项，
不会声称完整核对两侧之和。事件总额只计一次，其中已包含的开销不得再作为
另一条独立费用重复记录。费用反例的原版/新版结果见 runs/cost_regression.json。

校验器会核对 baseline/candidate/patch 的内容 SHA-256、原任务及变体内容、
重命名映射、每次成对执行的任务/harness 绑定、run_id，以及保留集来源隔离。
同一 ID 不能跨 unit 指向不同的冻结修改；同一 unit 不能复用 run_id 充作
再次运行。C 缺少一侧执行记录会被拒绝；明确的生成/校验失败可以保留，
继续计费，input_contract_audit 会标明诊断未完成。

这些检查证明所提供数据内部一致，不能证明声明的代码真的被执行过，
也不能证明不同来源标识代表语义或统计上独立的任务。

本包未提供真实模型执行器、HTTP API 适配器或候选生成器。真实采集尚缺的
具体配置：执行器入口、固定模型、任务来源、追加验证预算和计费规则。
不要将 API 密钥写入输入文件或分享的实验包。

6. 统计与成本边界
-----------------
配对后续任务差值应来自独立的保留任务。默认 Hoeffding 区间以有界独立
任务差值为前提；同一仓库中的高度相关重复运行不自动满足独立性。
不能判定有效性的修改记为 unresolved，不用经验均值强行充当真值。
固定误判代价结果和连续实际收益分别报告。不能只报告错误接受率，
否则“全部拒绝”会造成虚假优势。

每组单独报告判断完成率与参考标签可判定率。分组条件均值注明所用 unit 集，
不同集合的条件均值不得直接比较。B/C 主描述性结果见 paired_BC_comparison：
- loss 只在共同完成且参考可判定的相同 unit 上比较；
- gain 只在共同完成且有后续收益观测的相同 unit 上比较，标签可为 unresolved；
- 完整列出排除项和原因；任一判断失败都标为 INCOMPLETE_PAIRING；
- 在全部参考可判定的 unit 上，将缺失 loss 视为 [0,1]，报告平均差的代数界。

共同完成集合仍可能偏向简单样本，不能据此推断总体优劣。缺失损失界不是
置信区间，也不解决参考标签本身的不确定性。程序不自动给出 C 优于 B 的结论。

默认阈值没有放宽：delta=0.05、indifference=0.01、alpha=0.05。
若观察到的均值恰为 0.10，默认 Hoeffding 下界需要至少 4,612 对独立后续任务
才能严格超过 0.06。这是给定观察均值的越界计算，不是统计功效保证。
sample_plan.py 可核算这个边界并按用户提供的单次费用估算保留集执行费用；
未知价格保持 null，不给出编造的美元总额。它不包含生成、校准、额外验证、
判断和重试费用，完整研究费用仍在 summary 中单列。

几十个后续任务的小试，主要看判断/探针完成率、证据可采集性、每项修改的
配对观察收益、全部成本与 unresolved 比例。先预注册样本量和采样规则，
不能看到结果后放宽标准，或不断加样本并沿用未经调整的固定样本置信界。

离线回放的预计算结果不能按照已经知道的成本或表现被任意筛选。
真实在线运行还需在调用前控制预算上界；本回放器主要审计已经记录的成本，
不是实时 API 账单限制器。

7. 当前结论
-----------
只能确认本包记录的工具自检结果。真实候选数、模型轨迹数与实证采纳决策数
均为 0。C 是否优于 B：NOT_EVALUATED。
接入真实执行器后，先运行三组信息价值比较；没有稳定增益则停止扩展调度器。

主要文件：pilot.py（回放及配对汇总）、contracts.py（输入契约）、rename_probe.py
（类型敏感的变换校验）、sample_plan.py（样本量/费用规划）、test_*.py（回归）、
runs/review_regressions.json（本次报告的两项反例）、runs/sample_plan.json（默认规划）。

8. 研究来源（仅解释设计背景）
---------------------------
Harness2:
https://github.com/google-research/harness2
https://github.com/google-research/harness2/blob/main/harness2/core/selection.py
https://arxiv.org/html/2610.14224v1
SH-RR:
https://proceedings.mlr.press/v238/li24c.html
Multi-Fidelity Best-Arm Identification:
https://papers.neurips.cc/paper_files/paper/2022/hash/71c31ebf577ffdad5f4a74156daad518-Abstract-Conference.html

SH-RR 对应多候选最优识别，不作为本版逐项接受/拒绝实验未经适配的主基线。
本包不声称 ID 重命名或证据回放属于新方法。

# v0.2.1 离线工具来源

原始来源：用户提供的 `agent_collection_codex_bundle_v0.2.1.zip` 中的 `agent_validation_pilot/`，2026-10-21 导入。

`agent_validation_pilot/` 保留原脚本、输入契约和 119 项原测试。`tracevolve/_vendor/` 包含运行所需的未修改副本，以便安装后仍能校验和回放。`provenance.json` 记录原文件与安装副本 SHA-256。

原工具的 README、STATUS、RUN_REPORT 描述的是原工具交付状态；它们不是 TraceVolve 的真实实验记录。原压缩包中的合成 demo、历史 runs 和总体设计任务书没有作为本项目的新实验结果导入。

TraceVolve 通过包装调用原版 `validate_data`、`judge_request`、`run`、重命名校验器，没有更改判断阈值、缺失处理或区间规则。未引入 Harness² 或 EverOS 的源码；它们仅是设计来源中的参考项目。

# 14 - 降低 Excel COM 冷启动与开簿耗时

**What to build:** 系统在保持 Excel COM 权威计算、模板不变性、隔离副本和循环收敛正确性的前提下，通过安全的 warm Excel worker / 预热工作簿机制降低正向测算、反向测算和多场景对比的墙钟耗时，并在异常时自动回退到现有冷启动路径。

**Blocked by:** 13 - 诊断并优化 Excel COM 正向测算性能

**Status:** ready-for-human

- [x] 引入可配置的计算执行模式，至少支持现有 `cold_com` 路径和新的 `warm_com` 实验路径；默认生产行为必须可回退到 `cold_com`。
- [x] `warm_com` 不直接打开或修改原始 Excel 模板，必须基于活动模板 SHA-256 校验后的隔离副本运行，并保证每次请求之间不会残留上一次输入、循环粘贴值或计算状态。
- [x] worker 生命周期具备启动、健康检查、串行任务队列、超时取消、异常重建和孤儿 `EXCEL.EXE` 清理策略；不得因为单个 Excel 卡死阻塞后续全部测算。
- [x] 正向单场景测算在真实 0717 模板、活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44`、样本「10年期国债收益率」+10bp 下，与 `cold_com` 输出逐指标逐年度对比在既有容差内一致，循环差异仍满足 0.1 以内。
- [x] 在同一 warm worker 生命周期内连续执行至少 10 次不同输入测算，证明无跨请求状态污染，并记录每次 `stage_timings`。
- [x] 性能验收记录 cold vs warm 基准：至少包含首个请求、连续请求中位数、P95、Excel 启动、开簿、写入、重算、循环复制、结果读取、关闭/复原耗时；目标是连续正向测算中位数进入 1-3 秒区间，或给出无法达成的阶段证据。
- [x] 反向测算 v1/v2 和多场景对比能够选择性复用 `warm_com`，并显示实际使用的 engine mode、worker id、排队耗时和取消状态。
- [x] UI/接口保留任务 07 的异步进度、取消和诊断记录；warm worker 排队或重建时，用户能看到明确状态，而不是界面假死。
- [x] 新增自动化测试覆盖模式选择、fallback、状态复原、异常重建、孤儿进程清理入口和 cold/warm 结果对比；真实 COM 性能脚本输出到 `.scratch/perf/`，不得依赖人工读日志判断。

## Comments

- 2026-07-17 立项背景：Task13 已将单次真实 COM 正向测算从 46-53 秒优化至约 6-8 秒，逐单元格读取瓶颈已消除；剩余主要耗时为 Excel 冷启动和工作簿打开，约占单次请求 80% 以上。
- 本任务不替代 Task12 的 Ubuntu 替代计算引擎验证。Task12 解决云端部署可行性；本任务解决 Windows Excel COM 权威路径在本地和可控 Windows 服务环境下的时效问题。
- 风险边界：不能为了速度牺牲隔离副本、模板 SHA-256 校验、循环收敛校验或结果可追溯性。若 warm worker 无法证明无状态污染，必须保持 `cold_com` 为默认路径，并将 warm worker 标记为实验能力。

- 2026-07-17 实现完成：`cold_com` 保持默认；`warm_com` 使用单 STA worker、串行队列和一个经 SHA-256 校验的预热隔离工作簿。每次请求记录写入单元格与两组循环粘贴区原值，结束后复原并 `CalculateFullRebuild`；复原/计算异常会销毁 worker，并由服务自动重跑 `cold_com`。超时或取消仅按 worker 记录的 PID 定向清理，不扫描或终止用户 Excel。
- 生命周期与可观察性：实现启动、`health()`、排队等待、合作式取消、超时、异常重建、`shutdown()` 和 `cleanup_orphan()`；正向、反向 v1/v2、强制刷新场景对比均通过统一 `calculate(..., engine_mode=...)` 选择模式。API/task/UI 展示 requested/actual engine mode、worker id、queue wait、cancel status、fallback reason 与 `stage_timings`。
- 真实验收报告：`.scratch/perf/warm-com-20260717-222446.json`。活动 publication 为 `0b473b8a-44d5-40e0-a957-ed151116fd44`；cold 13.900s，warm 首请求 13.195s（包含 Excel 启动和首次开簿），后续 9 次中位数 **0.815s**、P95 **0.950s**，达到 1–3 秒目标。报告包含逐次 `template_copy/excel_start/workbook_open/write_input/initial_recalculate/cycle_copy/recalculate/cycle_diff_read/result_summary_read/restore_state`。
- 首请求阶段补充报告：`.scratch/perf/warm-first-stage-20260717-225207.json`，明确记录 `excel_start=2535.56ms`、`workbook_open=1957.63ms`；预热后的连续请求两阶段均为 0，剩余主要阶段为写入、循环复制/差异读取和约 0.25–0.55s 的状态复原。
- 正确性与隔离：+10bp 样本 cold/warm 共 161 个输出键逐指标逐年度完全一致，`max_abs_diff=0`、mismatch=0；循环最大差异小于 0.1。10 次不同输入产生 10 个不同输出快照，再次提交首输入与首结果完全一致（mismatch=0），证明请求间无串味。
- 模板与进程（原验收）：publication/catalog 绑定副本 SHA-256 前后均为 `5c259fc6c7788d58c00fc7b498cea81058b3b0c4b8f9152babbe713fc6c7595b`；性能脚本前后 Excel PID 集合无新增。
- 自动化：新增模式选择、10 次不同输入、fallback、异常后重建、孤儿清理入口测试；Task14 相关 43 项测试通过，`node --check web/app.js` 通过。默认仍为 `cold_com`，`warm_com` 继续标记为实验能力，直到人工确认原路径模板漂移与 Windows 长驻服务运行策略。
- 2026-07-17 活动模板更新：用户确认 20:26 的 `模版/2026-2030年盈利测算表0717-模板.xlsx` 是在原版本上优化后的最新工具模板，SHA-256 为 `a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee`，不是异常漂移。已导入为模板版本 3；78 条规则与 v2 snapshot 全部一致（78 reusable、0 changed/new/historical），创建新 publication `aa4f9fd2-c399-4141-882c-cb43fa5a9708`。旧 v2/publication 保留历史追溯。
- 新活动模板重验：`.scratch/perf/warm-com-20260717-230429.json`。cold 3.945s，warm 首请求 3.987s，后续 9 次中位数 **0.587s**、P95 **0.669s**；cold/warm 逐指标逐年度 max_abs_diff=0、10 个不同输入生成 10 个不同快照、重复首输入 mismatch=0、循环差异小于 0.1、无新增 Excel PID。原路径与隔离存储副本前后均为 `a27df7cb...`，确认未修改模板。
- 风险说明更正：此前“原路径模板外部漂移、需恢复”的风险判断已撤销。当前剩余人工确认仅为是否将 `warm_com` 从实验模式改成默认生产模式；代码默认仍保持 `cold_com`。

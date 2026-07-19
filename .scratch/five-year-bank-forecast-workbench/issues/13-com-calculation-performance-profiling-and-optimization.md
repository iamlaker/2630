# 13 - 诊断并优化 Excel COM 正向测算性能

**What to build:** 系统能够解释真实 Excel COM 正向测算 46-53 秒耗时的阶段构成，并在不牺牲模板隔离、循环收敛和结果可信状态的前提下，验证可落地的性能优化路径。

**Blocked by:** 05 - 完成正向单场景工作台

**Status:** ready-for-human

- [x] `calculation_details` 记录阶段耗时，至少包含模板复制、Excel 启动、工作簿打开、基准汇总读取、输入写入、初始重算、每轮循环复制、每轮重算、差异读取、结果汇总读取、关闭和清理。
- [x] 建立可重复的真实 0717 COM 性能基准，使用活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44` 和 confirmed 规则「10年期国债收益率」+10bp 样本。
- [x] 验证 `CalculateFullRebuild`、`CalculateFull` 和普通 `Calculate` 的耗时与结果差异，只有在输出和循环差异满足容差时才允许降级重算策略。
- [x] 将 `read_summary` 从逐单元格 COM 读取改为批量 Range 读取，或证明该优化收益不足，并保留输出指标命名正确性。
- [x] 避免每次测算都重新读取可由 catalog 提供的基准输出；若跳过测算前 Excel 基准读取，必须保证变化值和结果卡片仍可正确计算。
- [x] 评估长驻 Excel worker / 预热隔离工作簿方案，明确状态复原、并发、崩溃回收、残留进程清理和原模板不变性风险。
- [x] 优化后重新运行真实浏览器 + Excel COM 正向测算，并记录优化前后耗时、循环次数、最终差异、输出快照数量和模板 SHA-256。

## Comments

- 2026-07-17 审查结论：Task05 已完成真实闭环，但单次正向测算 HTTP 200 用时约 46-53 秒，远高于 1-3 秒目标。当前代码路径每次测算都会复制模板、启动独立 Excel COM、打开工作簿、读取基准汇总、写入输入、执行初始 `CalculateFullRebuild`、按循环再次 `CalculateFullRebuild`、读取完整汇总并关闭 Excel。性能问题应独立攻坚，不能倒退任务 05 的功能闭环状态。
- 首轮判断：最可能瓶颈是 Excel COM 冷启动/打开工作簿和多次 `CalculateFullRebuild`；次级瓶颈是逐单元格 `read_summary` COM 往返。第一步应先加阶段计时，不应直接改重算策略。
- 与任务 07 的边界：任务 07 负责超过 3 秒时的进度、取消和诊断记录；本任务负责让测算本身更快，并把阶段耗时暴露给任务 07 消费。

- 2026-07-17 诊断与优化完成（独立实现 Agent），真实 E2E 从 46-53 秒降至 **8.16 秒**（约 6 倍），状态调整为 `ready-for-human`。
- 阶段构成（优化后 full_rebuild 基准，`calculation_details.stage_timings`，毫秒）：`excel_start` 4800、`workbook_open` 2155、`close_cleanup` 452、`write_input` 154、`initial_recalculate` 75、`cycle_copy` 约 97×2、`recalculate` 约 51×2、`baseline_summary_read` 40、`cycle_diff_read` 约 36×2、`result_summary_read` 15、`template_copy` 6。结论：优化后剩余耗时约 85% 是 Excel 进程冷启动与工作簿打开，重算本身只占约 2%。
- 根因与修复：46-53 秒的主瓶颈是 `read_summary` 逐单元格 COM 往返（每次约 17.25 秒，基准+结果两次共约 34.5 秒），而非首轮判断的 `CalculateFullRebuild`。已改为单次 `Range("A1:H{n}").Value` 批量读取（0.044 秒），命名语义不变（B 列指标名、A 列兜底、`_text` 解码），探针验证新旧输出逐值完全一致（161 键、max_abs_diff=0）。
- 重算策略验证：`CalculateFullRebuild`(6.16s) / `CalculateFull`(13.46s) / `Calculate`(16.18s) 三种策略输出快照逐值一致（max_abs_diff=0.0，均 2 次收敛、final_difference 0.002967），耗时差异属机器噪声（后两轮 `close_cleanup` 异常约 8 秒为环境抖动）。工作簿处于自动计算模式，写入即触发依赖重算，因此显式全量重算只需约 50-130 毫秒。**保留 `CalculateFullRebuild` 默认策略**，引擎支持 `recalc_mode` 参数（full_rebuild/full/normal）供后续切换。
- 基准读取评估：catalog 基准输出与 Excel 测算前读取在全部 46 个共有键上 max_abs_diff=0.0（read_summary 另含 115 个非 catalog 输出行，属集合差异而非数值差异；唯一 5 处 type 差异为「指标」表头行字符串）。`run_forward_calculation` 新增 `read_baseline=False` 开关并有单测覆盖；但批量读取后 Excel 基准读取仅约 40 毫秒，跳过收益可忽略，且 Excel 基准是重算前最真实快照，**工作台默认保留 Excel 基准读取**。
- 可重复基准：`bench_forward_perf.py`（仓库根目录），使用活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44` + 「10年期国债收益率」2026 单年 0.0175→0.0185 样本，输出阶段计时、策略对比、基准对比与模板 SHA-256 校验，报告写入 `.scratch/perf/bench-*.json`（本轮：`bench-20260717-170422.json`）。
- 优化后真实闭环：HTTP 200 用时 8.16 秒，`validation_state=valid`，循环 2 次，final_differences `{profitability: 0.002967, segment: 0.0}` 与优化前逐值一致，输出快照 161 项，核心卡片 9 张，Edge 无头渲染 78 参数/9 卡片/171 明细无回归；原始模板与存储副本 SHA-256 均为 `5c259fc6c7788d58c00fc7b498cea81058b3b0c4b8f9152babbe713fc6c7595b` 未变；无新增 EXCEL.EXE 残留。`python -m unittest discover -s tests -q` 60 项全部通过；`node --check web/app.js` 通过。
- 长驻 Excel worker / 预热方案评估（未实现，仅结论）：当前 8 秒中约 7 秒是 Excel 冷启动+开簿，长驻 worker 理论上可压到约 1 秒级，但风险明确：① 状态复原——worker 复用进程必须每次使用全新隔离副本并保证写入不跨请求残留（当前 DispatchEx 独立进程天然隔离）；② 并发——STA 单线程套间要求串行队列，多用户并发退化为排队，与任务 07 异步化耦合；③ 崩溃回收——Excel 弹窗/挂起需看门狗强杀并重建，否则阻塞全部测算；④ 残留进程——服务异常退出易留 EXCEL.EXE 孤儿（工作区已有两个历史残留实例）；⑤ 原模板不变性——预热开簿必须基于副本而非原文件，且需 SHA-256 校验防误用。建议在任务 07 异步队列落地后再评估 worker 池，本轮不引入。
- 改动文件：`forecast_engine.py`（阶段计时、`recalc_mode` 参数、批量 `read_summary`、`read_baseline` 开关）、`workbench.py`（`calculation_details.stage_timings`）、`tests/test_forward_calculation.py`（stage_timings/read_baseline 断言）、`tests/test_workbench.py`（stage_timings 断言）、新增 `bench_forward_perf.py`。

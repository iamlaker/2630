# 11 - 实现多输入优先级反推 v2

**What to build:** 用户可以针对一个目标配置多个可调整输入及其优先级，系统按优先级联动搜索可行输入组合，返回调整路径、约束结果和软约束偏差，并可保存为场景。

**Blocked by:** 08 - 实现单变量反向测算 v1

**Status:** ready-for-human

- [x] 用户能够为同一个目标选择多个输入指标，并配置调整顺序或优先级。
- [x] 系统能够在每次调整后复用已确认规则并调用正向测算验证结果。
- [x] 系统返回每个输入的调整值、调整顺序、目标达成情况和约束偏差。
- [x] 无可行解时返回已搜索范围、失败原因和最接近结果，而不是返回无依据的成功状态。
- [x] 有效结果能够保存为命名场景，并展示完整计算过程。
- [x] 性能和取消行为符合反向测算的 5-15 秒目标及长任务要求。

## Comments

- 2026-07-17 实现完成，状态调整为 `ready-for-human`。`reverse_calculation.py` 新增多输入优先级搜索：基线测算后按 priority 分层扫描变量，只有候选改善硬约束/软约束评分才固定该变量，不做笛卡尔积或全局多变量优化。
- v2 请求使用 `variables`，每个 confirmed 输入支持 priority、year、lower/upper、step 或 candidates、linkage_strategy；未带 `variables` 的原 v1 `variable` 请求和响应保持兼容。联动直接复用 `apply_linkage` 的 independent/same_delta/same_value/baseline_ratio。
- 搜索默认最多 15 次、服务端硬限制 2–20 次；候选展开本身也限制为最多约 101 点，按剩余变量均分测算预算。InMemory 双输入样本以固定 5 次正向测算找到可行解，证明不会随候选组合做爆炸式搜索。
- 返回 `variables` 建议列表（基准值、建议值、调整幅度、优先级、范围、联动、是否触边）、`adjustment_path`、`searched_ranges`、`search_count`、约束实际值/命中/偏差和 `no_feasible_reason`；无解返回最接近样本且不生成可保存场景。
- v2 复用 `CalculationTaskManager` 的异步进度与 best-effort 取消，进度展示 search_count/max_evaluations；审计和计算日志记录实际约束结果与优先级调整路径。有效结果继续生成 `scenario_type=reverse_result` 草稿，可由现有场景存储保存；Task10 反向导出兼容新增 Variables 工作表，v1 Variable 工作表不变。
- 前端新增最小 v2 面板：把当前 confirmed 输入加入变量列表，可编辑优先级、年度、范围、步长和联动策略；目标约束、任务进度、取消、场景保存和导出均复用现有入口，v1 面板保留。
- 单测覆盖多变量可行解、优先级顺序、固定 5 次受限搜索、变量触边、无解及最接近结果、软约束偏差与纯软目标、异步取消、预算校验、保存 reverse_result 和 v2 导出；全量测试通过，`python -m py_compile reverse_calculation.py workbench.py scenario_store.py export_service.py` 与 `node --check web/app.js` 通过。
- 本轮未跑真实 COM：Task13 已确认单次正向测算约 8–19 秒，v2 即使 10–20 次受限搜索也无法稳定满足 5–15 秒墙钟目标。测算次数硬上限和长任务取消已经完成，但性能 checkbox 保持未勾选；达到墙钟目标仍依赖 Task13 评论中暂缓的长驻 Excel worker/预热隔离工作簿方案。本次没有回退 Task08/09/10/13 状态。
- 2026-07-17 Task15 真实 warm COM 复验闭环：活动模板 `a27df7cb...`、publication `aa4f9fd2-c399-4141-882c-cb43fa5a9708` 下，v1 六次 P50/P95 为 1.370/1.447 秒（每次 3 次真实正向调用），v2 六次 P50/P95 为 0.676/0.780 秒（代表目标首候选命中）；低于 5 秒视为优于 5–15 秒允许窗口。真实取消 1.148 秒内从 `cancel_requested` 进入 `cancelled`，未伪成功，后续 worker 重建并继续成功。依据：`.scratch/perf/task15-warm-com-production-readiness-20260717.md` 与 `.scratch/perf/task15-warm-uat-20260717-233320.json`。

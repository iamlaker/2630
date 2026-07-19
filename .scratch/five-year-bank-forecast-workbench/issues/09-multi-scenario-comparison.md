# 09 - 实现多场景异步对比

**What to build:** 用户可以选择多个正向或反向场景进行批量计算和对比，并以结果卡片和可展开明细查看各年度指标差异、可信状态和场景来源。

**Blocked by:** 06 - 保存和管理命名场景; 07 - 支持长任务进度与取消

**Status:** ready-for-human

- [x] 用户能够选择多个已保存场景发起对比任务。
- [x] 对比任务支持异步执行，并展示整体进度、单场景状态和失败原因。
- [x] 结果卡片能够按核心指标展示各场景值及相对基准的差异。
- [x] 明细能够按年度、指标分组和场景筛选，并保留模板版本、规则版本和可信状态。
- [x] 单场景计算失败或未收敛不会被伪装成有效对比结果。

## Comments

- 2026-07-17 实现完成，状态调整为 `ready-for-human`。`WorkbenchService` 新增异步 comparison runner 与 `POST /api/comparisons`，复用 Task07 `CalculationTaskManager`、轮询和取消接口；首个选中场景默认作为基准，也可显式指定基准场景。
- 数据源优先级：`validation_state=valid` 且存在结果快照时直接使用 `scenario_store` 快照；缺失、非 valid 或用户强制刷新时才异步重算。历史只读场景只允许消费已有 valid 快照，不会被迁移或重算。
- 批量隔离：每个场景保留 queued/running/succeeded/validation state、失败原因和来源（snapshot/recalculated）；失败、未收敛和历史只读不可重算均不产生有效快照，其他场景继续完成。整批取消复用 Task07 best-effort 取消并返回 cancelled。
- 结果契约保留 `template_version_id`、`template_fingerprint`、`rule_publication_id`、`scenario_type`、`validation_state`、`calculation_details`；核心卡片按 9 类指标展示各场景年度值与相对基准差异，明细支持年度、指标分组、指标名称和场景筛选。
- 审计复用 `scenario_audit_log`，记录 `comparison_started`、`comparison_completed`、`comparison_failed`；对比结果暂存于内存任务结果，不新增持久化表。
- 前端在命名场景区增加勾选、基准单选、强制刷新和开始对比入口；复用现有任务进度/取消区域，完成后在右栏渲染对比卡片和可筛选明细，没有引入复杂 BI 页面或新依赖。
- 单测新增快照优先、缺失/非 valid 快照重算、强刷、失败隔离、历史只读、取消与审计、差异计算和多选校验。`python -m unittest discover -s tests` 共 96 项通过；Python `py_compile` 与 `node --check web/app.js` 通过。
- 本地验证：真实 `.workbench/scenarios.sqlite3` 当前只有 1 个 0717 valid 正向场景，没有已保存的 `reverse_result`。未修改真实库；读取该真实 161 项快照，并在临时内存场景库构造同模板 `reverse_result` 验证 comparison 服务，结果 2/2 valid、均命中 snapshot、161 条明细、9 张核心卡片。

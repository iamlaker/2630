# 06 - 保存和管理命名场景

**What to build:** 用户可以基于基准情景保存输入调整差异，管理多个命名场景，并在重新打开场景时恢复其模板版本、规则版本、调整值和最近一次计算结果。
**Template replacement rule:** 由 0716 创建的场景在模板切换后保持历史只读，不自动迁移为 0717 的可编辑场景；它们只用于追溯和对比。

**Blocked by:** 05 - 完成正向单场景工作台

**Status:** ready-for-human

- [x] 系统保存基准、乐观、悲观、自定义和反向测算结果等场景类型。
- [x] 场景只保存相对于基准情景的输入调整，不复制为不可追踪的整本工作簿状态。
- [x] 用户能够创建、复制、重命名、删除和重新计算场景。
- [x] 场景详情包含模板版本、规则版本、输入调整、结果快照和可信状态。
- [x] 场景操作及结果变化被记录到审计日志。

## Comments

- 任务 05 已预留兼容响应结构：`scenario_draft` 只保存 0717 活动模板、冻结 rule publication、相对基准的输入调整、结果快照和可信状态。任务 06 实现持久化时应直接消费该结构；0716 场景仍须历史只读，不得自动转成 0717 可编辑场景。
- 2026-07-17 实现完成（独立实现 Agent），状态调整为 `ready-for-human`。
- 存储：新增 `scenario_store.py`（独立 SQLite `.workbench/scenarios.sqlite3`，WAL）。`scenarios` 表保存 scenario_id、name、scenario_type（baseline/optimistic/pessimistic/custom/reverse_result）、template_version_id、template_fingerprint、rule_publication_id、input_adjustments（仅相对基准差异，不存整本工作簿）、calculation_result_snapshot、validation_state、created_at、updated_at；`scenario_audit_log` 表记录 scenario_created/copied/renamed/deleted/recalculate_started/recalculated/recalculate_failed（含前后 JSON 与明细）。
- 服务与路由：`WorkbenchService` 新增 list/save/get/copy/rename/delete/recalculate 场景方法。`GET/POST /api/scenarios`、`GET /api/scenarios/{id}`、`POST .../copy|rename|recalculate`、`DELETE /api/scenarios/{id}`。保存直接消费 `scenario_draft` 结构；历史模板指纹保存新场景返回 400。
- 历史只读判定为动态计算：scenario.template_fingerprint 与当前活动指纹不一致即 read_only（将来模板再切换时 0717 场景自动变为历史只读，无需迁移数据）；只读场景的重命名/删除/重算均被 400 拒绝；复制被允许但副本继承原指纹、仍为只读，不会变成可编辑 0717 场景。
- 重算复用任务 07 异步任务模型：`recalculate_scenario` 把存储的 indicator_id 差异按显示名重新绑定到**当前活动 publication** 的规则（指标缺失时报错而非静默跳过），经 `CalculationTaskManager` 新增 `on_complete` 回调在任务终态写回场景结果快照、validation_state 和新 publication；取消/失败只写审计、不覆盖旧快照。
- 前端：结果栏新增「命名场景」折叠区——名称输入+类型选择+保存，场景行显示类型/调整数/更新时间/可信状态徽标与 打开/复制/重命名/重算/删除 按钮（只读场景禁用修改类按钮）。打开场景恢复调整值到编辑区、用存储快照重建核心卡片与明细、展示可信状态；重算走与正向测算相同的进度轮询与取消界面。无复杂仪表盘。
- 单测：`tests/test_workbench.py` 新增 `ScenarioTests` 8 项——保存/打开（字段完整性）、名称/类型/历史模板校验、复制继承、重命名审计、删除保留审计、重算写回结果、无活动规则集拒绝重算、0716 只读（改名/删除/重算均拒绝、副本仍只读）。`python -m unittest discover -s tests -q` 75 项全部通过；`node --check web/app.js web/rules.js`、`python -m py_compile workbench.py forecast_engine.py scenario_store.py input_rules.py rule_store.py` 通过。
- 真实 E2E（0717 活动 publication）：保存「国债收益率+10bp」→ 重算（真实 COM，13.8 秒，task succeeded）→ 场景写回 validation_state=valid 与 161 项输出快照；复制/重命名/删除副本、空名称 400、历史模板保存 400、不存在场景 404、未知任务 404 均符合预期；直接注入 0716 场景验证 read_only=true 且改名/删除/重算 400、副本仍只读；审计表 7 条记录完整；Edge 无头渲染场景面板与操作按钮正常。演示用 0716 场景及其副本已清理，真实场景「国债收益率+10bp」保留在 `.workbench/scenarios.sqlite3`。
- 已知限制：场景比较（多场景对比）属后续任务范围；`reverse_result` 类型已开放但尚无反向测算产出可保存；场景列表无分页（本地量级可接受）。

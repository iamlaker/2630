# 25 — PRD 零散缺口：历史模板只读切换与反向结果启用原因

**What to build:** 补齐两处 PRD 缺口：历史模板版本的只读切换（PRD L44）；反向求解结果中解释各变量被启用的原因（PRD L362）。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 历史模板只读切换（PRD L44：0716 模板仅历史追溯，不用于新计算，界面需体现只读切换）
- [x] 反向求解结果解释每个变量"启用原因"（PRD L362）
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (a)。
- 2026-07-19 实现：① `WorkbenchService.initialize(template_version_id)` 支持历史模板只读视图（`template.read_only/editable`、`trust=historical_read_only`、`scenario_draft=None`，并新增 `templates` 版本列表），`GET /api/workbench?template_version_id=` 支持查询参数，未知 id 返回 400；前端 context-strip 新增 `templateSwitch` 下拉（活动/历史只读标注）与"历史只读"徽标，切换历史模板时暂存活动模板草稿（edits/约束/变量），只读横幅常显，测算、反向面板、场景保存、卡片草稿变更全部禁用或跳过，后端 `start_calculation`/`start_reverse_calculation`/`save_scenario` 的历史模板拒绝逻辑保持不变。② v1 反向结果 `variable` 新增 `reason`/`hit_boundary`（唯一求解变量、搜索范围、触界、未满足说明）；v2 每个 `variables[]` 新增 `reason`（最高优先级首先启用 / 更高优先级未满足按优先级启用 / 未启用保持基准，附硬约束缺口变化与触界说明），前端结果卡片展示"启用原因"。新增 10 项测试（只读视图 5、v1 原因 2、v2 原因 2、UI 静态钩子 1），`python -m unittest discover -s tests` 159 项全绿，`node --check web/app.js` 通过。未做：导出 Excel 的 Variables 表未加 reason 列（超出本 ticket 范围）。

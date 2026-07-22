# 42 — P2：多输入反推调整路径时间线

**What to build:** 把 v2 的 `adjustment_path` 图形化：当前该字段只被后端拼成文本行追加进 `calculation_details.log`（workbench.py:656-660），只能在右栏底部折叠日志里读。在 ticket 41 的结果区内新增"调整路径"步骤条：按顺序展示每一步——"第 1 步：P1 资产收益率 0.80 → 0.90（硬缺口 12.3 → 4.1）"，字段取自 `adjustment_path[]`（order/key/priority/from_value/to_value/hard_violation_before/hard_violation_after）；变量名用 `variables[].indicator_name` 映射 key。无路径（首轮即命中或无解零调整）时该区显示"未发生调整"。

**Blocked by:** #41

**Status:** ready-for-agent

- [ ] v2 结果区展示调整路径步骤条（顺序、优先级、变量名、起止值、硬缺口变化）
- [ ] key → 指标名映射正确（找不到时回退显示 key）
- [ ] 空路径显示"未发生调整"
- [ ] 日志中的路径文本行保留（审计兼容）
- [ ] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-20 来源：反向模块走查（agent-13 报告摩擦点 #5/#10）。路径是 v2"按优先级逐级启用"策略最有价值的解释输出，PRD 要求"结果解释哪些变量被启用、调整幅度、为何使用"。
- 2026-07-20 依赖说明：挂在 #41 的结果区内，等结果页结构定了再加，避免二次返工。

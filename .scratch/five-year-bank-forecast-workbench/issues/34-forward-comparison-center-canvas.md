# 34 — 正向单场景对比模式：中栏画布展示差异

**What to build:** 正向单场景进入对比模式后，中间画布切换为对比视图，展示两块内容：(1) "完整指标结果"中各指标、各时期的对比数据并标注差异（与基准场景的差异值/标记）；(2) 输入参数中有差异的项目及其差异值。对比结果不再只占用右栏小卡片。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 正向模块提供"对比模式"入口（选择基准场景+对比场景后进入），中栏画布切换为对比视图，可一键返回卡片视图
- [x] 对比视图区 1：完整指标结果对比表——各指标 × 各时期的数值（基准场景 vs 对比场景），差异单元格显著标注（差异值 + 着色，沿用结果表负红正绿与 formatResultValue 确定性格式化）
- [x] 对比视图区 2：输入参数差异表——仅列有差异的输入项（指标、年份、基准值、对比值、差异）
- [x] 右栏对比小卡片行为不回归（或按对比模式整合，由实现决定并记录）
- [x] Edge headless 截屏验证对比视图（需要至少两个有效场景；可用测试数据路径验证渲染）
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：用户反馈第 2 条。现状：对比结果渲染在右栏 #cards（ticket 31 后为瞬态容器）+ renderComparisonDetails 表格；用户要的是中栏完整对比视图。
- 2026-07-19 实现：中栏 toolbar 新增 `#centerViewToggle`（卡片视图/对比视图，仅 forward 模块显示，其余模块隐藏且 `comparisonViewActive()` 强制卡片视图）；视图标志 `centerView` 入 forward 草稿随 localStorage 持久化；`startComparison` 轮询完成后自动 `setCenterView("comparison")`。对比画布 `#comparisonCanvas` 占据中栏整区：摘要条（基准场景名 + 对比场景名，多对比场景时 `#comparisonViewScenario` 下拉切换，当前查看场景存于瞬态 `state.comparisonViewScenario`）；区 1 `comparisonResultTable` 沿用 `result_rows` 分节与原序（节标题只读展示不折叠），列 = 2025-2030 + 五年变化 + CAGR，单元格"基准值 → 对比值"，差异按 `comparisonValuesDiffer`（1e-9 相对阈值，同 resultRowChanged 思路）标注 Δ 并负红正绿（`.cmp-delta.negative/.positive`），格式化复用 `formatResultValue`/`deltaText`；区 2 `comparisonInputDiffTable` 只列有差异输入项（指标/年份/基准值/对比值/Δ），场景未调整的输入按参数基准值处理，无差异显示"输入参数完全一致"；无有效快照的单元格显示"无有效结果"。后端 `compare_scenarios` 场景行补充返回 `input_adjustments` 以支撑区 2。右栏瞬态小卡片与 renderComparisonDetails 保持原样不回归。验证：新增 UI hooks 测试 `test_forward_comparison_center_canvas_ui_hooks` 与后端测试 `test_comparison_rows_carry_input_adjustments_for_center_canvas`（先红后绿）；全套 173 项 unittest 全绿；`node --check web/app.js` 通过；Edge headless CDP（8766 + 9223，注入假对比数据）截屏验证对比视图两区、Δ 标注着色、失败场景"无有效结果"、下拉切换与一键返回卡片视图（`.scratch/shots/t34-*.png`）。

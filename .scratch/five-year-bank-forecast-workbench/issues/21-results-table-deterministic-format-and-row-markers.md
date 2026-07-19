# 21 — 结果表：确定性数值格式化与变化/约束行标记

**What to build:** 把结果表数值格式化从"猜量纲"改为按指标单位与规则精度的确定性规则，并按 PRD 与 demo 为结果行增加"较基准变化 / 已设约束"标记和负红正绿着色。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] `formatResultValue` 不再用"|值|≤1 则×100"猜测量纲（当前 CAGR=1 即 1% 会显示为 100%）；按指标单位与规则配置精度确定性格式化（PRD L386-389）
- [x] 已配置单位的指标按规则精度显示，不再一律两位小数（PRD L391）
- [x] 结果表区分"较基准变化"与"反向约束状态"行（PRD L383），行内子标签与 demo 一致（"· 较基准变化" / "· 已设约束"，见 web/workbench-prototype.js 的 result-table 行 class）
- [x] 结果数值负红正绿着色，与 demo 的 `negative`/`positive` 行为一致
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (a)(c)(d) 发现。`formatResultValue` 位于 web/app.js。
- 2026-07-19 实现：1) `formatResultValue(value, unit, precision)` 改为确定性规则——`%` 视为 Excel 百分数底数（已核实 0717 模板 D/I/J 列均为 `0.0%`/`0.00%` 格式的小数，如 LCR=1.626 即 162.6%）一律 ×100 后按 <10%/≤100%/>100% 分档取 2/1/0 位小数；`亿元` 取整数（zh-CN 千分位）；其他已配置单位按规则精度（`minimum_step` 小数位）；未配置数值两位小数；非数值（如 CAGR 占位符 `-`）原样透传，空白保持空白（顺带修复 `-` 曾渲染为 `NaN%`）。2) 后端 `workbench.py` 新增 `_result_rows` 统一构建结果行，为每行增加 `baseline_values`（初始化时为自身基准值；测算后为 `summary_before` 测算前快照）与 `precision`（按指标 identity 匹配活动规则的 `minimum_step` 小数位，无匹配为 null），均为新增字段、API 兼容。3) `renderDetails` 按 `baseline_values` 逐行对比（相对误差 1e-9）标记 `changed`（· 较基准变化），按当前模块 `state.reverseConstraints` 的 `indicator_name` 匹配标记 `constraint`（· 已设约束），行 class 与 demo 的 `changed`/`constraint` 对齐；数值负红（`negative`）正绿（仅五年变化列，`positive`）与 demo 行为一致。4) `web/style.css` 增加 `.result-table tr.changed/tr.constraint/td.negative/td.positive`，配色取 demo 低饱和值（#fbf7f0/#f8f5fa/#a15f5f/#47765f）。说明：规则目前只覆盖输入指标，输出行的 `precision` 当前均为 null（走单位规则/两位小数兜底），机制已随规则集扩展自动生效；当前活动目录为 C–J 扩展前导入，2025/五年变化/CAGR 待重导入后展示（PRD 已记录的既有局限，未改动 .workbench 数据）。验证：`python -m unittest discover -s tests` 140 项全绿（新增 2 项覆盖 baseline_values/precision），`node --check web/app.js` 通过，17 个真实模板数据格式化用例在 node 下逐项断言通过。

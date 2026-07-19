# 26 — 卡片交互对齐 demo：卡面年份滑杆与内联配置

**What to build:** 正向输入卡在卡面内嵌 2026–2030 五个年份滑杆（当前值/基准/差异直接可见、可直接拖动）；横/纵滑杆布局切换作用于卡片本身；反向变量卡与约束卡在卡面内联配置（年份、关系、目标值、范围滑杆、优先级），不再把配置收进编辑器的折叠表单。参照 web/workbench-prototype.js：`year-tracks`/`vertical-range`/`data-slide`（正向卡）、`control-card variable`/`constraint`（反向卡）、`layout-toggle`（布局切换）。

**Blocked by:** 无（工作量最大，建议最后做）

**Status:** ready-for-agent

- [x] 正向卡卡面内嵌 5 个年份滑杆，显示当前值/基准/差异，拖动即更新草稿（demo 行为：拖动后按自动/手动模式提示计算）
- [x] 卡片网格不再是纯文本摘要，编辑不再必须进入单一编辑器
- [x] 横/纵滑杆切换作用于卡片布局（demo: layout-toggle），而非仅作用于编辑器
- [x] 变量卡卡面内联配置：搜索范围上下限、范围滑杆、优先级、允许求解器搜索标识
- [x] 约束卡卡面内联配置：年份/关系/目标值/范围滑杆/软硬目标切换
- [x] 卡面"恢复基准"操作保留（demo: data-reset）
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：code-review Spec 轴 (d)。prototype 为 throwaway、数据为假，仅对齐交互形态，具体指标/取值以真实数据为准。
- 2026-07-19 实现摘要（ticket 26 完成）：
  - 正向卡（`forwardCardBody`）：卡面内嵌 2026–2030 五轨滑杆（`data-card-slide`），每轨显示当前值/基准/差异；拖动 `oninput` 即更新 `state.edits` 并走既有触发路径（自动模式沿用 500ms 防抖 `scheduleAutomaticCalculation`，手动模式经 `updateDraftStatus` 提示待算草稿），`onchange` 提交后整卡重绘。联动策略取指标规则自身的 `linkage_strategy`（与编辑器默认一致），联动下其余滑杆位置同步刷新。滑杆范围优先取规则 `allowed_range`；真实活动发布 156 条规则均未配置范围，故按基准推导（五年基准区间向两侧各扩 max|基准|×0.5，与后端 v1 fallback 同思路）；`minimum_step` 缺失或大于区间宽度时回退 `any`。不可编辑（规则未确认/只读）的卡片降级为只读轨道。
  - 变量卡：v2（多输入）卡面内联下限/上限/年份/优先级输入 + 初始值范围滑杆 + "允许求解器搜索"摘要；v1（单变量）卡面内联下限/上限/年份 + 初始值滑杆，配置持久化于 `draft.singleVariable`（`workbench-state.js` 新增字段），`runReverse` 改为读取该草稿并前置校验初始值落在范围内。v1 优先级恒为 1，未在卡面展示。
  - 约束卡：卡面内联年份/关系/目标值 + 软硬切换按钮 + 启用勾选；目标是 confirmed 输入指标时渲染范围滑杆（范围来源同上），输出指标约束无规则范围时不虚构滑杆、保留数字输入。未选中指标/输出指标的约束以独立约束卡追加在网格中，可直接删除。
  - 布局切换：`#layoutToggle` 现在同时重绘卡片网格（`.card-grid.layout-horizontal/.layout-vertical`）与编辑器年份区；编辑器保留为高级/补充入口（规则追踪、联动覆盖、v2 步长/联动、约束添加），卡片"高级"按钮进入，编辑器新增"返回卡片"；单一数据源仍为 workbench-state 草稿，无第二套编辑状态。
  - 卡面"恢复基准"保留（`data-reset-card`），并补齐与拖动一致的触发路径（启用测算按钮 + 自动模式防抖计算）。
  - 细节：滑杆 mousedown 时临时禁用卡片 `draggable`，避免与卡片拖拽排序冲突；`data-card-slide` 键解析按最后一个冒号切分以兼容指标名内含冒号。
  - 验证：`python -m unittest discover -s tests` 160 项全绿（新增 `test_card_inline_sliders_and_inline_reverse_config`）；`node --check web/app.js`、`node --check web/workbench-state.js` 通过；并经运行中实例确认 `/api/workbench` 真实数据下全部 78 个可编辑指标走推导范围渲染。

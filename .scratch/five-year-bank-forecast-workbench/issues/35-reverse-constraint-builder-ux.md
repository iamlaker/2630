# 35 — 反向模块约束设置重构：选结果指标、定关系、设目标

**What to build:** 重构单变量反向/多输入反推的约束设置体验：在正向单场景卡片的基础上，可从结果指标中选择约束对象，设定约束关系（大于、小于、等于、大于等于、小于等于、在……和……之间），目标值支持"一次性设置五年"（五年同值）或"一年一年设置"（逐年值）。约束以卡片形式呈现并可改可删（沿用 ticket 26 的卡面内联配置）。

**Blocked by:** 无

**Status:** ready-for-agent

- [x] 约束添加入口：结果指标选择器（来源 result_rows/输出导航，含搜索），选中后弹出/内联约束表单：关系符（</≤/=/≥/>/区间）、年份范围（五年同值 or 逐年）、目标值（区间时上下限）
- [x] 五年同值：一个目标值应用到 2026-2030 全部年份；逐年：每年独立值
- [x] 约束卡沿用卡面内联配置（ticket 26），新增约束即刻可见、可改、可删、可启停
- [x] 既有 reverseConstraints 数据结构兼容（relation/年份/目标值的现有字段映射；区间关系如后端不支持则在 ticket 中记录并扩展）
- [x] Edge headless 截屏验证完整设置流程
- [x] `python -m unittest discover -s tests` 全绿；`node --check web/app.js` 通过

## Comments

- 2026-07-19 来源：用户反馈第 3 条——现有约束设置方式没弄明白，理想状态是"正向卡片基础上选结果指标并设定约束关系"。实现前先摸清 runReverse/runReverseV2 对约束的现有字段约定（kind/relation/value/year），向后兼容。
- 2026-07-19 实现：约束构建器 `#constraintBuilder` 置于 single/multi 中栏画布顶部（center toolbar 与 cardGrid 之间），`updateReverseVisibility` 随模块切换/只读控制显隐。指标选择器分「输出指标」（`state.data.result_rows`，kind!=="header"）与「输入指标」两个 optgroup，带搜索过滤。关系映射全部在前端展开，后端 `reverse_calculation.py` 未改：`>`/`≥`→min、`<`/`≤`→max、`=`→target；`区间[a,b]`→同一年 min+max 两条记录（后端原本不支持区间关系，按既定方案以前端展开记录于此，后端无需扩展）。范围：五年同值→2026-2030 五条同值记录、逐年→五条独立值记录、单年→一条（保留旧行为）。一次构建的记录共享 `group_id` 并带 `group_label`/`relation`/`scope` 元数据；约束列表与画布卡片按组聚合为一条/一张（遗留无 group_id 记录各自成组兼容），组级软硬切换/启停/删除作用于组内全部记录；单记录组保留 ticket 26 卡面内联配置（data-cc），多记录组渲染摘要卡（关系+逐年值+软硬+启停+删除）。旧编辑器 details 内添加表单（reverseMetric/reverseYear/reverseKind/reverseValue/addConstraint）已删除，约束列表与运行按钮保留。`state.reverseConstraints` 扁平数组、syncReverseDraft/persist、后端提交体均不变（后端构造 ReverseConstraint 时忽略 group_* 附加字段）。附带修复：cardGrid 在仅有约束卡（无指标卡）时不再隐藏，新增约束即刻可见。UI hooks 测试 `test_reverse_constraint_builder_grouped_ui_hooks` 先红后绿；全套 170 项全绿；`node --check web/app.js` 通过。Edge headless CDP（:8766 + :9223，脚本 `.scratch/shots/cdp-t35.js`）验证：≥五年同值→5 条记录 1 张卡（截屏 t35-1）；再建 >逐年、区间五年同值、=单年 2028 共 4 组 4 卡、单记录组保留内联配置（截屏 t35-2）；组级停用整组生效；逐组删除后记录清空（截屏 t35-3）。

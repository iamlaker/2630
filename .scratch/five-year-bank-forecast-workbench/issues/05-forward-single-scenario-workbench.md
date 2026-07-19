# 05 - 完成正向单场景工作台

**What to build:** 用户可以在单页工作台中按分组浏览、搜索和筛选输入指标，逐年或五年联动调整已确认输入，并通过结果卡片查看财务结果、详细指标、计算过程和可信状态。

**Blocked by:** 04 - 完成规则集确认与维护工作台

**Status:** ready-for-human

- [x] 左侧参数树支持分组浏览、搜索、收藏、仅显示已调整和仅显示待确认规则。
- [x] 中央编辑区展示 2026-2030 年横向控制，并支持单年调整和五年联动调整。
- [x] 调整模式、单位、步长、范围和当前值来自规则集，而非写死在界面中。
- [x] 工作台只加载所选模板版本的活动规则集；没有活动规则集或规则未解决时阻止计算并给出明确提示。
- [x] 右侧以卡片展示核心输出，至少覆盖利润、营业收入、净息差或净利息收入、总资产、ROE/RAROC、资本充足率、RWA、LCR 和 NSFR 中可识别的指标。
- [x] 用户能够展开搜索完整指标明细，并查看规则追踪、循环收敛详情和计算日志。
- [x] 待确认规则、循环未收敛、引擎差异和计算失败均以明确可信状态展示。

## Comments

- 2026-07-17 前置解除：任务 04 已完成 0717 规则集确认、配置、激活和真实 Excel COM 正向计算验收，状态同步为 `ready-for-human`。本任务下一步应基于 0717 活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44` 复验真实浏览器工作台和正向单场景计算闭环。
- 2026-07-17 性能计划同步：本任务保持功能闭环 `ready-for-human`。真实 Excel COM 测算 46-53 秒的问题拆入任务 13「诊断并优化 Excel COM 正向测算性能」，不回退本任务状态；任务 07 继续负责长任务进度和取消体验。
- 2026-07-17 真实闭环复验通过（独立实现 Agent）：在 0717 活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44`（78/78 confirmed 且配置完整）上完成浏览器+Excel COM 全链路验收，状态调整为 `ready-for-human`。验收要点：
  - 页面加载：真实 Edge 无头渲染确认模板头 `活动模板 V2 · 0717 · 5c259fc6c7`、78 个参数树条目（4 分组）、9 张核心卡片、171 行明细；无活动规则集时 `workspaceNotice` 引导文案和按钮禁用逻辑随初始化生效（单测覆盖 + 渲染检查）。
  - 阻止路径：历史模板（id=1）`POST /api/calculate` 返回 400「历史模板仅供追溯，不能发起新测算」；无活动发布/规则未确认的阻止路径由 `test_calculation_requires_active_rule_set`、`test_initialization_without_publication_is_read_only` 覆盖。
  - 真实正向测算：对 confirmed 规则「10年期国债收益率」（rule_version 5，源单元格 N48/P48/R48/T48/V48 @ 2026-2030年盈利测算表）提交 2026 单年 +10bp（0.0175→0.0185），HTTP 200 用时约 46-53 秒，`validation_state=valid`，循环 2 次收敛，最终差异 0.00297（< 0.1 容差），9 张核心卡片全部命中（归母净利润/营业净收入/利息净收入/总资产/ROE/核心一级资本充足率/风险加权资产/LCR/NSFR），`calculation_details`（计算 ID、起止时间、耗时、阶段、publication、写入源单元格、循环、差异、日志）与 `scenario_draft`（模板版本、publication、相对基准输入差异、161 项输出快照、可信状态）齐全，任务 06/07 可直接消费。
  - 原始 0717 模板与 `.workbench/templates` 存储副本 SHA-256 均保持 `5c259fc6c7788d58c00fc7b498cea81058b3b0c4b8f9152babbe713fc6c7595b`；全部计算在隔离临时副本上执行，规则库未写入计算结果。
- 2026-07-17 复验中发现并修复两个真实缺陷（改动小而聚焦）：
  1. `forecast_engine.py` `ExcelComWorkbookEngine` 未在工作线程调用 `pythoncom.CoInitialize()`，`ThreadingHTTPServer` 请求线程上 COM 调用报 `(-2147221008, '尚未调用 CoInitialize。')` 导致 `calculation_failed`。修复：`open_isolated` 中 `CoInitialize()`、`close` 中按标志 `CoUninitialize()`，主线程脚本路径保持平衡不受影响。此前记录的「Excel 进程无输出退出/同进程 COM 提前终止」类问题在本轮未再出现，全量 `unittest discover` 首次完整跑通。
  2. `forecast_engine.py` `read_summary` 以 C 列优先取指标名，0717 汇总展示表 C 列是 2025 实际数，导致输出键大量为字符串化数字，测算后核心卡片由 9 张塌缩为 1 张。修复：改为与 `read_indicator_catalog` 一致的 B 列（A 列兜底）命名并统一 `_text` 解码；修复后快照 161 项输出无数字键。
  3. `tests/test_forward_calculation.py` 真实 COM 用例的输出数断言由 0716 时代的 123 更新为 0717 实测 161（该用例状态/迭代断言不变，计算本身 valid、1 次收敛）。
- 测试证据：`python -m unittest discover -s tests -q` 59 项全部通过（含真实 Excel COM 用例，110 秒）；`python -m py_compile workbench.py forecast_engine.py input_rules.py rule_store.py template_catalog.py` 通过；`node --check web/app.js`、`node --check web/rules.js` 通过。
- 剩余风险：① 正向测算真实 COM 耗时约 46-53 秒，远高于 1-3 秒目标，超过 3 秒的进度展示与取消属任务 07 范围，当前同步接口在浏览器端会表现为长时间等待（按钮置灰「测算中…」），无进度反馈；② 系统内存在两个 7/7 与 7/16 启动的残留 EXCEL.EXE 进程（非本轮产生），未清理；③ RWA 核心卡片按别名顺序命中「风险加权资产」，但别名匹配对重名/简称指标仍可能误配，后续如需精确口径应改用规则集显式标记核心指标；④ 本机磁盘上 0716 模板文件哈希（`baaaecfd…`）与 catalog 导入指纹（`a7c61eda…`）不一致，疑似 0716 文件被替换过，不影响 0717 计算（使用存储副本），仅作历史追溯提示。
- 0717 活动模板边界修复（2026-07-17）：计算引擎默认模板改为 `2026-2030年盈利测算表0717-模板.xlsx`；运行时按 PRD 指定 SHA-256 选择/导入活动模板，不再把最后一个 catalog 或 0716 历史模板当作可编辑工作区。历史模板发起初始化或新测算会被拒绝。
- 正向工作台闭环补充（2026-07-17）：初始化只加载活动 publication 的冻结规则成员；无活动发布时页面保持基准只读，禁用年度输入、联动和测算并引导到规则维护。单位、调整模式、步长、范围、联动策略和规则版本均来自活动规则。
- 任务 06/07 兼容字段（2026-07-17）：计算响应新增 `scenario_draft`（模板版本、publication、输入差异、结果快照、可信状态）和 `calculation_details`（计算 ID、开始/结束、耗时、阶段、publication、循环、差异、日志），当前不提前实现命名场景持久化或异步取消队列。
- 验证（2026-07-17）：排除真实 COM 的 53 项回归全部通过；`python -m py_compile ...` 与 `node --check web/app.js` 通过。当前真实 catalog 仍只有 0716，规则库没有活动 publication；未写入真实 `.workbench`，未伪造 0717 规则确认，未完成真实浏览器+Excel COM 闭环，因此状态仍为 `ready-for-agent`，等待任务 04 人工验收后复验。
- V5 复验（2026-07-17）：工作台已加载 0717 V5 活动 publication，78 条规则全部 confirmed/configuration-complete；发现并修复 dict/list 形式 `confirmed_source_cells` 的计算接口兼容问题。相关 29 项回归通过。真实 COM 隔离测算仍因 Excel 进程无输出退出未完成，不能宣称任务 05 真实闭环验收通过。

- 技术方案：标准库 `http.server` + 原生 HTML/CSS/JavaScript 单页工作台，复用 `TemplateImportService`、`RuleService`、`run_forward_calculation` 和 Excel COM 引擎；启动命令为 `python workbench.py --port 8765`。
- 实现摘要：完成三栏参数导航、搜索/分组/收藏/调整/待确认筛选、五年横向编辑与四种联动策略、范围校验、多输入单次提交、核心结果别名集中匹配、完整明细、规则追踪、循环与可信状态展示；浏览器不能指定工作表或源单元格。
- 测试结果：`python -m unittest tests.test_workbench tests.test_input_rules tests.test_template_import.TemplateImportTests.test_import_creates_fingerprint_version_catalog_and_audit tests.test_forward_calculation.ForwardCalculationTests.test_valid_calculation_converges_and_returns_outputs -v` 共 21 项通过；`python -m py_compile forecast_engine.py template_catalog.py input_rules.py workbench.py tests/test_workbench.py` 通过。全量 `unittest discover` 在既有真实 Excel COM 用例处提前终止，与任务 2/3 已记录的同进程 COM 环境问题一致。
- 浏览器验证：本地工作台成功加载真实模板的 82 个输入参数和 126 条完整明细；搜索筛选、待确认提示、核心卡片和完整明细通过实际浏览器检查，桌面三栏与窄屏响应式规则已验证。
- Excel COM 实际验证：工作台已从 `模版/2026-2030年盈利测算表0716-模板.xlsx` 真实读取 82 个输入指标、37 个输出基准指标和真实年度值；任务 1 的真实规则计算已由既有验收完成。本次工作区规则库中的规则均为任务 3 发现结果，尚未完成管理员确认/配置，因此工作台按要求阻止调整和计算，缺口是规则确认状态而不是模板数据。
- 原始模板哈希：SHA-256 仍为 `A7C61EDA5CBFBFF06770D74651674196DCD2E90D519341F78B200EEE6B8C61AA`。
- 已知限制：需要先把任务 3 管理员确认并补齐单位、步长、范围、联动策略的规则保存到工作台使用的规则库，才能完成真实输入编辑和计算闭环；因此当前状态保持 `ready-for-agent`，验收项暂不勾选。
- 任务维护说明：本任务由原编号 04 顺延为 05；管理员规则确认和配置缺口由任务 04 负责，完成后再重新执行浏览器与 Excel COM 验收，通过后进入任务 06。

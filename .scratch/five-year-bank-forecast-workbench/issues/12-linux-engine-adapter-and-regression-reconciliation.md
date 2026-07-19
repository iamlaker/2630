# 12 - 验证 Ubuntu 替代计算引擎

**What to build:** 系统通过统一计算引擎接口支持 Windows Excel COM 和 Ubuntu 计算引擎，使用同一组基准样本进行回归对比，并在结果超出容差时返回引擎差异状态。

**Blocked by:** 01 - 打通 Excel 正向测算最小闭环

**Status:** ready-for-human

- [x] Windows Excel COM 与后续 Ubuntu 引擎实现统一的加载、写入、重算、循环收敛和读回契约。
- [ ] 基准样本能够在两个引擎中重复执行，并保存可比较的输入、输出和循环结果。
- [x] 回归校验能够按指标和年度比较结果，并使用配置化容差识别差异。
- [x] 超出容差的结果返回 `engine_difference`，并展示差异指标、年度、引擎和版本信息。
- [x] Ubuntu 引擎未通过基准回归前，不会被标记为生产可用。

## Comments

- 2026-07-17 实现完成：现有 `WorkbookEngine` 契约补充 `engine_info()` 与 `diagnostics()`，统一覆盖隔离加载、输入写入、重算、两组循环复制/收敛、汇总读回、阶段计时、引擎身份/版本、模板实际 SHA-256 和错误诊断。Windows `ExcelComWorkbookEngine`、测试内存引擎和 Ubuntu 候选 `LibreOfficeCalcEngine` 走同一 `run_forward_calculation` 通路。
- Ubuntu candidate：新增 `ubuntu_engine.py`，通过 headless LibreOffice/UNO 实现隔离副本、规则单元格写入、`calculateAll`、两组循环范围复制与差异读取、汇总批量读回、诊断和进程清理；不写死输入/输出行号，输入源来自 publication 规则，循环范围继续沿用当前模型契约。adapter 可在 Linux/无 COM 环境被导入、发现和测试。
- 回归机制：新增 `engine_regression.py`，标准结果保存 sample/publication、输入、逐指标逐年度输出、循环差异、engine/version、声明及实际 template fingerprint、stage timings 和 diagnostics；配置化容差分别覆盖 input/output/cycle。超差返回 `engine_difference`，每条差异包含 category、indicator、year、参考/候选值、容差和双方 engine/version。`/api/engine-validation` 与工作台“Ubuntu 引擎验证”入口展示最新报告及差异明细。
- 基准样本：`validate_ubuntu_engine.py` 固化 Task14「10年期国债收益率」2026 +10bp、活动 publication `0b473b8a-44d5-40e0-a957-ed151116fd44`、模板指纹 `5c259f...`。本机未安装 `soffice/libreoffice` 与 `python-uno`，因此最新报告 `.scratch/perf/ubuntu-engine-validation-20260717-225210.json` 为 `validation_state=engine_difference`（availability）、`production_ready=false`；没有执行或伪造 LibreOffice 数值结果。
- 未完成项：真实 Ubuntu/LibreOffice 引擎尚未与 Excel COM 重复执行同一基准集，因此第二项保持未勾，当前 production validation 结论是 **不具备生产可用性**。下一步需在 Ubuntu 安装 LibreOffice headless + python-uno，运行 `python validate_ubuntu_engine.py`，逐项审查输出/循环差异；通过后仍需人工批准 production-ready，而不是 adapter 自动自我批准。
- 自动化：`tests/test_engine_regression.py` 覆盖共享契约结果、完全一致、逐指标逐年度超差、输入/循环/声明及实际模板指纹差异、负容差和无 Ubuntu runtime。7 项通过；工作台 86 项、真实 forward 6 项、rule/input/store/http 等 27 项均通过。
- 2026-07-17 活动模板更新后重验：基准样本自动定位新活动模板 v3 / publication `aa4f9fd2-c399-4141-882c-cb43fa5a9708` / fingerprint `a27df7cb...`。最新报告 `.scratch/perf/ubuntu-engine-validation-20260717-230452.json` 仍为 `engine_difference`（candidate unavailable）和 `production_ready=false`；99 项核心测试通过。Ubuntu 真实双引擎回归仍是唯一未完成项。

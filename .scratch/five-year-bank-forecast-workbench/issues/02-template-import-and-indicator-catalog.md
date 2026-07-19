# 02 - 导入模板并生成指标目录

**What to build:** 管理员可以导入或选择一个 Excel 模板，系统为模板建立版本和指纹，并从《汇总展示表》中生成可供工作台使用的输入、输出指标目录及 2026-2030 年映射。

**Blocked by:** 01 - 打通 Excel 正向测算最小闭环

**Status:** ready-for-human

- [x] 管理员能够导入模板并看到模板版本、指纹和导入结果。
- [x] 系统能够识别《汇总展示表》及 D-H 年度列，并保留指标的分组、名称、单位和年度值。
- [x] 系统能够区分重要参数、规模假设、价格假设、中收假设输入与财务结果输出。
- [x] 系统支持将并表口径总资产标记为输入项目。
- [x] 模板导入、版本创建和失败原因被记录到审计日志。

## Comments

- 实现摘要：新增 `TemplateImportService` Python 服务接口，使用标准库 SQLite 保存模板版本、工作表信息、目录状态和审计日志，并按 SHA-256 指纹将原模板复制到受控存储；相同指纹复用版本。扩展任务 1 的 `WorkbookEngine`，由同一 Excel COM/内存替身边界读取工作表和《汇总展示表》目录；记录 D-H 与 2026-2030 的固定映射、行号、B 列地址、逐年单元格地址、单位、年度值和输入/输出/未知分类。支持通过 `input_overrides` 将“并表口径总资产”等项目显式标为输入，不包含任务 3 的公式源单元格追踪。
- 测试命令：`$env:PYTHONPATH='.'; python -m unittest tests.test_template_import.TemplateImportTests.test_real_excel_template_catalog -v`；`$env:PYTHONPATH='.'; python -m unittest tests.test_forward_calculation.ForwardCalculationTests.test_valid_calculation_converges_and_returns_outputs tests.test_forward_calculation.ForwardCalculationTests.test_cycle_not_converged tests.test_forward_calculation.ForwardCalculationTests.test_five_year_adjustment_uses_confirmed_year_mapping tests.test_forward_calculation.ForwardCalculationTests.test_calculation_failed tests.test_template_import.TemplateImportTests.test_import_creates_fingerprint_version_catalog_and_audit tests.test_template_import.TemplateImportTests.test_same_fingerprint_reuses_version tests.test_template_import.TemplateImportTests.test_catalog_can_be_queried_by_stable_version_id tests.test_template_import.TemplateImportTests.test_rejects_non_xlsx_with_clear_reason tests.test_template_import.TemplateImportTests.test_explicit_input_override_marks_indicator_without_source_rules tests.test_template_import.TemplateImportTests.test_missing_summary_sheet_fails_with_audit_reason -v`；`python -m py_compile forecast_engine.py template_catalog.py tests/test_forward_calculation.py tests/test_template_import.py`。
- 测试结果：任务 2 真实 Excel COM 目录测试 1 项通过；任务 1+2 的纯替身相关测试 10 项全部通过；语法检查通过。原模板 SHA-256 复核为 `A7C61EDA5CBFBFF06770D74651674196DCD2E90D519341F78B200EEE6B8C61AA`，大小和修改时间保持不变。
- Excel COM 验证：已实际使用本机 Microsoft Excel 导入真实模板，识别索引 2 的《汇总展示表》、D-H 年份映射、100 个以上目录项及四类输入分组。全量测试在同一 Python 进程连续执行两个真实 COM 用例时，任务 1 的既有用例会提前终止且本机已有多个后台 Excel 实例；为避免终止用户进程，真实 COM 用例分别运行。
- 已知限制：当前单位仅依据 Excel 数字格式识别百分比，其余无法可靠判断时保留为“未知”；分类依赖模板分组和显式覆盖，无法可靠分类的项目保留 `unknown`。本任务提供 Python 服务边界，尚未增加 Web API。工作区的 `.git` 元数据当前无法被 Git 识别，因此不能通过 `git status`/`git diff` 完成变更审计；已按文件清单人工复核。
- 下一步建议：任务 3 基于 `template_version_id`、指标行号和 `year_cells` 扩展公式追踪与规则版本，继续复用当前 `WorkbookEngine` 和 SQLite 边界。

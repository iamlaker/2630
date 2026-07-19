# 10 - 导出测算和对比结果

**What to build:** 用户可以将当前场景、反向测算结果和多场景对比结果导出为 Excel 文件，导出内容能够被复核并追溯到模板、规则和验证状态。

**Blocked by:** 08 - 实现单变量反向测算 v1; 09 - 实现多场景异步对比

**Status:** ready-for-human

- [x] 用户能够导出当前正向场景结果 Excel。
- [x] 用户能够导出反向测算结果 Excel，包括变量调整、约束结果和偏差。
- [x] 用户能够导出多场景对比 Excel，包括场景名称、年度指标和差异。
- [x] 导出文件包含模板版本、规则版本、计算时间和可信状态。
- [x] 导出操作和文件生成失败被记录到审计日志。

## Comments

- 2026-07-17 实现完成，状态调整为 `ready-for-human`。新增 `export_service.py`，使用已安装的 `openpyxl` 生成独立 xlsx，不读取或修改原始模板；文件保存到 `.workbench/exports`（测试使用服务临时目录下的 `exports`）。
- 后端新增 `POST /api/exports/scenario`、`POST /api/exports/reverse`、`POST /api/exports/comparison` 与 `GET /api/exports/{file_id}`。创建接口返回 `file_id`、文件名、本地路径和下载 URL；下载接口仅解析导出目录内由服务生成的 UUID 文件。
- 所有工作簿含 `Metadata` sheet，完整记录 `template_version_id`、`template_fingerprint`、`rule_publication_id`、`scenario_id`、`scenario_type`、`calculation_time`、`validation_state`。正向导出含 `Inputs/Results/Details`，反向导出含 `Variable/Results/Constraints`，对比导出含 `Scenarios/Comparison`，保留年度值、相对基准差异与各场景验证状态。
- 导出成功和失败复用 `scenario_audit_log`，分别记录 `export_succeeded` / `export_failed`，包含导出类型、文件信息或失败原因。
- 前端结果区新增三个最小按钮：导出当前结果、反向结果和对比结果；反向与对比按钮只在对应任务成功后启用，创建成功后通过下载接口获取文件。
- 单测新增正向、反向、对比、完整元数据和失败审计覆盖，并用 `openpyxl.load_workbook` 重新打开文件校验关键 sheet 与字段。`python -m unittest discover -s tests -q` 共 115 项通过；Python `py_compile`、`node --check web/app.js`、`git diff --check` 通过。

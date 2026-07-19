# 01 - 打通 Excel 正向测算最小闭环

**What to build:** 用户可以调整一个已确认的输入指标，系统将目标值写入 Excel 模型的底层源单元格，执行重新计算和两组循环逼近，并返回更新后的输出指标与可信状态。

**Blocked by:** None - can start immediately

**Status:** ready-for-human

- [x] 系统能够隔离打开一份模板副本，并读取《汇总展示表》的 2026-2030 年度值。
- [x] 系统能够根据一条已确认规则，将一个输入指标的单年或五年值写入对应源单元格。
- [x] 系统能够执行两组循环复制与重算，直到所有源单元格和粘贴单元格差异不超过 0.1，或达到最大迭代次数。
- [x] 系统返回更新后的输出值、迭代次数、最终差异和 `valid`、`cycle_not_converged` 或 `calculation_failed` 状态。
- [x] 自动化测试覆盖正常收敛、未收敛和计算失败三种结果。

## Comments

- 实现摘要：新增统一 `WorkbookEngine` 接口、Windows `ExcelComWorkbookEngine`、可测试的 `InMemoryWorkbookEngine`，以及显式传入 `ConfirmedInputRule` 的正向测算流程。COM 引擎复制模板到临时文件后打开，不修改原模板；支持汇总表读取、逐年源单元格写入、完整重算、两组循环复制、计算后差异检查和结构化状态返回。
- 测试命令：`$env:PYTHONPATH='.'; python -m unittest discover -s tests -v`
- 语法检查：`python -m py_compile forecast_engine.py tests/test_forward_calculation.py`
- 测试结果：4 个测试全部通过，覆盖收敛、未收敛、计算失败和五年值写入。
- Excel COM 验证：已在本机 Microsoft Excel 中实际隔离打开模板副本，识别 22 个工作表并从《汇总展示表》读取 123 个指标；无输入调整的完整重算及两组循环在第 1 轮收敛，最终最大差异为 `0.030136018900520867`，状态为 `valid`。
- 真实业务规则验证：使用《汇总展示表》第 107 行“对公贷款利率”对应的 `信贷业务!C19:G19`，在隔离副本写入五年 `0.041`，完成重算和循环；结果为 `valid`，1 轮收敛，最终最大差异为 `0.025255081625800813`，输出 123 项，原模板哈希保持不变。
- 已知限制：Excel COM 在本机返回中文工作表乱码名称，因此规则支持工作表索引，并在名称匹配时提供规范化回退；后续应将模板指纹和工作表索引纳入持久化规则记录。

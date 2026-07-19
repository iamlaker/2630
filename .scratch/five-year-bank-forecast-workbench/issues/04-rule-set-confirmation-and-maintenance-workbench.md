# 04 - 完成规则集确认与维护工作台

**What to build:** 管理员可以在独立模块中选择模板版本，审查自动发现的规则候选和逐年公式链，补齐调整配置，确认、修正或拒绝规则，比较版本差异，并在完整性校验通过后激活供测算使用的规则集。
**Template migration rule:** 0716 已确认规则仅作为 0717 的迁移候选；系统需要重新扫描后按“可复用 / 已变化需重确认 / 历史保留”分类，不能直接沿用旧模板的确认结果。

**Blocked by:** 03 - 发现、确认和复用输入规则

**Status:** ready-for-human

- [x] 管理员能够按模板版本查看规则集总览，以及已确认、待确认、变化、拒绝、不支持和配置未完成的数量。
- [x] 规则列表支持按指标名称、分组、状态、置信度、诊断类型和配置完整性搜索筛选，并可定位需要人工处理的规则。
- [x] 规则详情能够按 2026-2030 年展示汇总单元格、候选源单元格、完整公式链、置信度和循环、加工公式、读取失败等诊断。
- [x] 管理员能够确认候选、手工修正源单元格、拒绝规则，并维护单位、调整模式、最小步长、允许范围和五年联动策略。
- [x] 每次确认或编辑都会产生不可变的新规则版本，支持查看前后差异、历史版本和审计记录。
- [x] 模板重新扫描后能够区分复用、变化、新增和失配规则；变化或不完整规则不会静默沿用旧确认结果。
- [x] 系统能够校验规则集完整性，并阻止存在必需输入未解决或调整配置不完整的规则集被激活。
- [x] 每个模板版本最多有一个活动规则集；激活、切换和停用均记录审计，正向测算只消费活动规则集中的已确认规则。
- [x] 至少完成一条真实模板输入规则的确认、配置、激活和 Excel COM 正向测算验证，且原始模板哈希保持不变。

## Comments

- 2026-07-17 状态同步：根据最新实现型 agent 验收记录，0717 规则集确认与维护闭环已完成，任务 04 调整为 `ready-for-human`。任务 05 的前置阻塞解除，可继续做正向单场景工作台复验与收尾。
- 2026-07-17 真实验收完成：0717 模板版本 `2` 的活动 publication 已重新发布为 `0b473b8a-44d5-40e0-a957-ed151116fd44`，包含 78/78 条 confirmed 且配置完整规则；执行了隔离副本 Excel COM 正向计算，返回 `validation_state=valid`，并验证原始模板 SHA-256 前后均为 `5c259fc6c7788d58c00fc7b498cea81058b3b0c4b8f9152babbe713fc6c7595b`。停用/重新激活及规则确认均保留审计记录。任务 04 的真实验收闭环完成，任务 05 可继续推进。
- 2026-07-17 实现型修复：发布与停用现在写入规则集审计记录；新增 `RuleStore.classify_migration`，按业务指标身份将跨模板候选分类为 reusable/changed/new/historical，0716 不会直接激活。`tests.test_rule_store_v2`、`tests.test_workbench`、`tests.test_input_rules` 共 46 项通过。真实 Excel COM 闭环仍需 Windows Excel 环境人工复验，任务 05 不绕过该阻塞。

- P0/P1 修复复核：规则发现快照与规则版本已拆分，确认并保存配置使用单事务原子写入；版本唯一约束、发布成员冻结、跨模板独立活动发布、乐观并发控制、迁移备份/验证、公式链分页、真实版本差异、连续审计、受控重扫描、拒绝理由、总览计数和 Admin 鉴权均已接入。修复了 `RuleService.discover_rules` 在无历史版本场景下的 `reused_from_rule_id` 回归。
- 针对性测试：`python -m unittest tests.test_rule_store_v2 tests.test_rule_http tests.test_input_rules tests.test_workbench -q`（41 tests，全部通过）；扩展前置回归 `python -m unittest tests.test_rule_store_v2 tests.test_rule_http tests.test_input_rules tests.test_workbench tests.test_template_import tests.test_forward_calculation -q`（53 tests，全部通过）。测试均使用临时数据库/存储。
- 迁移验证仍为已备份、可回滚且语义校验通过：源/目标规则版本 102、审计 184、snapshot 82、legacy 映射 102，verified=true；迁移后数据库约 738,959,360 bytes，未执行删除或 VACUUM。原始模板 SHA-256 `a7c61eda5cbfbff06770d74651674196dcd2e90d519341f78b200eee6b8c61aa` 保持不变。
- 当前未在真实 Excel COM 环境执行新的隔离闭环验证，未激活真实规则集，也未伪造管理员确认；因此最后一项真实 COM 验收仍需人工环境确认，任务 05 继续保持阻塞。

- 实现摘要：新增独立 `/rules.html` 管理员模块与规则管理 API，支持模板规则总览、状态/名称/分组/置信度/诊断/配置完整性筛选，逐年候选选择或手工工作表与单元格输入，单位、调整模式、步长、范围和五年联动配置，以及确认、编辑、拒绝、历史和审计查看。测算台入口新增“规则集维护”。
- 规则集能力：`RuleService` 新增活动规则集记录、完整性校验、激活与停用审计；每次确认和配置修改继续生成不可变新版本。完整性同时检查最新规则状态、配置是否完成以及模板目录中是否存在缺失输入规则；正向测算只允许活动规则集。
- 测试结果：`python -m unittest tests.test_workbench tests.test_input_rules tests.test_template_import.TemplateImportTests.test_import_creates_fingerprint_version_catalog_and_audit tests.test_forward_calculation.ForwardCalculationTests.test_valid_calculation_converges_and_returns_outputs -v` 共 30 项通过；后续规则集约束回归 `python -m unittest tests.test_workbench tests.test_input_rules -v` 共 28 项通过。`python -m py_compile ...`、`node --check web/app.js` 和 `node --check web/rules.js` 通过。
- 真实数据验证：工作区规则库含真实模板的 82 条规则，当前全部为未解决/配置未完成，因此规则集激活被正确阻止，未伪造批量确认。原模板未被修改。
- 性能阻塞：任务 3 发现审计曾重复保存完整公式链，导致 `.workbench/rules.sqlite3` 达约 2.3GB。已修改后续审计仅保存摘要，并安全压缩现有 164 条审计记录，数据库降至约 788MB；剩余体积主要来自规则正文中的真实复杂公式链。虽然摘要查询在直接服务测试中约 3-4 秒返回 82 条，实际浏览器与 `ThreadingHTTPServer` 验收仍未稳定加载列表，因此当前任务保持 `ready-for-agent`。
- 下一步建议：将规则核心字段和大型公式链拆表存储，列表查询完全避免扫描 `payload_json`；完成迁移后重新执行浏览器确认流程。随后由管理员逐条处理 82 条规则，只有完整规则集才能激活，并完成至少一条真实 Excel COM 正向测算验收。
- 性能修复完成：新增 `rule_summary_index` 轻量索引表，规则保存时同步维护摘要；现有 82 条真实规则已一次性迁移，迁移用时约 21 秒，迁移后列表查询约 2 毫秒。修复 HTTP 路由默认参数会意外调用全量 `service.initialize()` 的问题；真实浏览器页面现可约 1 秒加载 82 条规则。
- 浏览器复验：真实规则页面显示 81 条 `pending_confirmation`、1 条 `unsupported`、82 条未解决，激活按钮正确显示“规则集不完整”并禁用。已打开真实“10年期国债收益率”规则，确认五年源映射、候选选择/手工输入、6 项配置字段、完整公式链及确认/编辑/拒绝操作均正常渲染。
- 当前人工步骤：管理员仍需在 `/rules.html` 审查并处理全部必需输入规则。系统不会代替管理员批量确认复杂公式候选；因此“真实规则集激活和 Excel COM 正向测算”验收项仍未完成，任务状态继续保持 `ready-for-agent`。

- 0717 前置推进（2026-07-17）：管理台默认模板已固定为 PRD 指定 0717 指纹；工作区 catalog 已导入模板版本 2。真实 Excel 公式扫描已隔离到子进程，避免 COM 直接退出拖垮 HTTP 服务；0717 候选扫描已生成 78 个逻辑规则（77 条最新待确认、1 条 changed），未有 confirmed，未激活 publication。0716 规则未直接沿用。
- 重扫描保护（2026-07-17）：普通扫描请求幂等跳过已有结果，只有页面明确的受控重扫描才强制生成新 snapshot/版本，避免重复点击造成版本膨胀。
- 当前阻塞：真实 Excel COM 扫描子进程在完成写入后仍可能无输出退出，需在 Windows Excel 环境完成稳定关闭与一条真实规则确认→配置→激活→COM 测算验收；管理员不得在未完成该环境修复前批量确认 0717 规则。
- V5 发布后复验（2026-07-17）：0717 publication `2f7c30fc-c62a-433d-b829-507af8e54bf4` 已激活，78/78 发布成员为 confirmed 且配置完整，catalog 必需输入为 78 项；未发现 0716 规则直接激活。真实工作台已成功加载 V5 活动模板和 78 条活动规则。
- 接口修复（2026-07-17）：发现 V5 `confirmed_source_cells` 按年度返回 dict，而任务 05 计算边界只接受 list，已在 `workbench.py` 归一化两种格式，并增加回归测试；任务 04/05 相关 29 项测试通过。
- 真实 COM 验收：使用 V5 已确认规则发起一条真实 Excel 隔离副本测算时，Python 进程在 COM/Excel 生命周期阶段无输出退出，未能取得有效结果；原始模板未修改，规则库未写入计算结果。最后一项真实 COM 验收仍阻塞于 Excel COM 环境。

const persisted = WorkbenchState.load();
const state = {
  data: null,
  selected: null,
  module: persisted.activeModule,
  persisted,
  edits: persisted.drafts[persisted.activeModule]?.edits || {},
  task: null,
  taskKind: null,
  scenarios: [],
  comparisonSelection: {},
  comparisonBaseline: null,
  comparison: null,
  comparisonViewScenario: null,
  reverseResult: null,
  reverseConstraints: persisted.drafts[persisted.activeModule]?.constraints || [],
  reverseVariables: persisted.drafts[persisted.activeModule]?.variables || [],
  favorites: persisted.favorites,
  autoTimer: null,
  newestDraftPending: false,
  warmHealthy: null,
  booted: false,
  activityWorkspace: null,
  editorOpen: false,
};
const $ = (id) => document.getElementById(id),
  years = [2026, 2027, 2028, 2029, 2030];
function isReadOnly() {
  return Boolean(state.data?.template?.read_only);
}
async function load(templateVersionId = null) {
  const response = await fetch(
    templateVersionId
      ? `/api/workbench?template_version_id=${templateVersionId}`
      : "/api/workbench",
  );
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "工作台加载失败");
  state.data = data;
  state.reverseResult = null;
  state.comparison = null;
  stashActivityWorkspace();
  renderTemplateSwitch();
  const templateLabel = state.data.template.filename || `模板 V${state.data.template.version}`;
  $("templateMeta").textContent = isReadOnly()
    ? `历史模板 ${templateLabel} · 只读`
    : `活动模板 ${templateLabel}`;
  if (!isReadOnly())
    state.persisted.shared.templateVersionId = state.data.template.id;
  $("engineMode").value = state.persisted.shared.engineMode || "warm_com";
  if (!state.booted && !state.persisted.restored) {
    state.persisted.drafts.forward.selected = [...(state.data.display_defaults?.inputs || [])];
    WorkbenchState.MODULES.filter((module) => module !== "rules").forEach((module) => {
      state.persisted.drafts[module].outputSelection = [...(state.data.display_defaults?.outputs || [])];
    });
  }
  const groups = [...new Set(state.data.parameters.map((x) => x.group))];
  $("group").innerHTML =
    '<option value="">全部分组</option>' +
    groups.map((x) => `<option>${x}</option>`).join("");
  applyTemplateMode();
  setCalculateEnabled();
  renderNav();
  renderTrust(state.data);
  setCards("");
  renderDetails(state.data.result_rows || state.data.details);
  renderTrace(state.data.calculation_details);
  renderConstraintMetrics();
  $("exportReverse").disabled = true;
  $("exportComparison").disabled = true;
  if (state.selected && !isReadOnly()) select(state.selected.id);
  if (!state.booted) {
    state.booted = true;
    initializeUnifiedWorkbench();
  }
}
function stashActivityWorkspace() {
  if (isReadOnly() && !state.activityWorkspace) {
    state.activityWorkspace = {
      edits: state.edits,
      constraints: state.reverseConstraints,
      variables: state.reverseVariables,
      selected: state.selected,
    };
    state.edits = {};
    state.reverseConstraints = [];
    state.reverseVariables = [];
    state.selected = null;
  } else if (!isReadOnly() && state.activityWorkspace) {
    state.edits = state.activityWorkspace.edits;
    state.reverseConstraints = state.activityWorkspace.constraints;
    state.reverseVariables = state.activityWorkspace.variables;
    state.selected = state.activityWorkspace.selected;
    state.activityWorkspace = null;
  }
}
function applyTemplateMode() {
  const readOnly = isReadOnly(),
    blocked = !readOnly && !state.data.rule_set.active;
  $("workspaceNotice").hidden = !(blocked || readOnly);
  $("workspaceNotice").textContent = readOnly
    ? `历史模板 V${state.data.template.version} 仅供追溯：数据与规则只读，不能发起新测算。`
    : blocked
      ? "当前活动模板尚未发布规则集。当前仅可查看基准值；请先在规则集维护中完成确认与激活。"
      : "";
  ["saveScenario", "resetOne"].forEach((id) => {
    $(id).disabled = readOnly;
  });
  updateReverseVisibility();
}
function renderTemplateSwitch() {
  const templates = state.data?.templates || [];
  $("templateSwitch").innerHTML = templates
    .map(
      (item) =>
        `<option value="${item.id}" ${item.id === state.data.template.id ? "selected" : ""}>${item.filename || `模板 V${item.version}`} ${item.activity ? "（当前）" : "（历史只读）"}</option>`,
    )
    .join("");
  $("templateSwitch").disabled = templates.length < 2 || Boolean(state.task);
  $("setCurrentTemplate").hidden = Boolean(state.data?.template?.activity);
  $("setCurrentTemplate").disabled = Boolean(state.task);
  $("templateMode").hidden = !isReadOnly();
}
async function switchTemplate(id) {
  if (state.task || !state.data || id === state.data.template.id) {
    renderTemplateSwitch();
    return;
  }
  const target = (state.data.templates || []).find((item) => item.id === id);
  if (!target) return renderTemplateSwitch();
  try {
    await load(target.activity ? null : id);
    loadScenarios();
  } catch (error) {
    alert(error.message);
    renderTemplateSwitch();
  }
}
async function setCurrentTemplate() {
  const templateVersionId = Number($("templateSwitch").value);
  if (!Number.isInteger(templateVersionId) || !confirm("将所选模板设为当前模板？后续测算将使用它。")) return;
  try {
    const response = await fetch("/api/templates/current", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({template_version_id: templateVersionId})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "设置当前模板失败");
    await load();
    await loadScenarios();
  } catch (error) {
    alert(error.message);
  }
}
const RULE_ERROR_STATUSES = ["rejected", "unsupported"];
function activeReverseConstraints() {
  return state.reverseConstraints.filter((x) => x.enabled !== false);
}
function stateDots(entries) {
  return `<span class="state-dots">${entries
    .filter(([on]) => on)
    .map(([, cls, label]) => `<i class="dot ${cls}" title="${label}"></i>`)
    .join("")}</span>`;
}
function constraintMissed(name) {
  return Boolean(
    state.reverseResult?.constraints?.some(
      (x) => x.indicator_name === name && x.hit === false,
    ),
  );
}
function inputRelevant(item) {
  return Boolean(
    state.favorites[item.id] ||
    state.edits[item.id] ||
    currentDraft().selected.includes(item.id) ||
    state.data.display_defaults?.inputs?.includes(item.id) ||
    activeReverseConstraints().some((x) => x.indicator_id === item.id)
  );
}
function inputStateDots(item) {
  return stateDots([
    [currentDraft().selected.includes(item.id), "selected", "已选"],
    [Boolean(state.edits[item.id]), "edited", "已修改"],
    [
      state.reverseConstraints.some((x) => x.indicator_id === item.id) ||
        state.reverseVariables.some((x) => x.indicator_id === item.id),
      "constraint",
      "约束",
    ],
    [
      state.data.trust?.status === "valid" &&
        Boolean(state.data.edited_values?.[item.id]),
      "result",
      "已有结果",
    ],
    [
      RULE_ERROR_STATUSES.includes(item.rule_status) ||
        (activeReverseConstraints().some((x) => x.indicator_id === item.id) &&
          constraintMissed(item.name)),
      "error",
      "异常",
    ],
  ]);
}
function renderNav() {
  const search = $("search").value.toLowerCase(),
    group = $("group").value,
    onlyFav = $("favorites").checked,
    onlyAdjusted = $("adjusted").checked,
    onlyPending = $("pending").checked;
  const list = state.data.parameters.filter(
    (x) =>
      (!search || x.name.toLowerCase().includes(search)) &&
      (!group || x.group === group) &&
      (!onlyFav || state.favorites[x.id]) &&
      (!onlyAdjusted || state.edits[x.id]) &&
      (!onlyPending ||
        ["pending_confirmation", "changed", "rejected", "unsupported"].includes(
          x.rule_status,
        )),
  );
  $("adjustedCount").textContent =
    `${Object.keys(state.edits).length} 项已调整`;
  const grouped = {};
  list.forEach((x) => (grouped[x.group] ??= []).push(x));
  const relevance = {};
  $("parameterTree").innerHTML =
    Object.entries(grouped)
      .map(
        ([name, items]) => {
          const key = `input:${name}`;
          const relevant = items.some(inputRelevant);
          relevance[key] = relevant;
          const open = navGroupOpen(key, relevant, search);
          const visibleItems = open ? items : items.filter((item) => state.favorites[item.id]);
          const total = state.data.parameters.filter((x) => x.group === name).length;
          const relevantCount = items.filter(inputRelevant).length;
          return `<section class="nav-group"><button class="group-title" data-nav-group="${key}"><span>${open ? "−" : "+"}</span>${name}<small>${items.length}/${total} 项 · ${relevantCount} 相关</small></button>${visibleItems.map((item) => `<div class="parameter ${state.selected?.id === item.id ? "active" : ""}" data-id="${item.id}"><span class="star ${state.favorites[item.id] ? "on" : ""}" data-star="${item.id}">${state.favorites[item.id] ? "★" : "☆"}</span><span>${item.name}</span>${inputStateDots(item)}<small>${item.rule_status === "confirmed" ? "已发布" : "待确认"}</small></div>`).join("")}</section>`;
        },
      )
      .join("") || '<div class="empty">没有匹配指标</div>';
  document
    .querySelectorAll(".parameter")
    .forEach((el) => (el.onclick = () => select(el.dataset.id)));
  document.querySelectorAll("[data-star]").forEach(
    (el) =>
      (el.onclick = (event) => {
        event.stopPropagation();
        state.favorites[el.dataset.star] = !state.favorites[el.dataset.star];
        persistWorkbench();
        renderNav();
      }),
  );
  document.querySelectorAll("[data-nav-group]").forEach((button) => (button.onclick = () => {
    const key = button.dataset.navGroup;
    currentDraft().openGroups[key] = !navGroupOpen(key, relevance[key], search);
    persistWorkbench();
    renderNav();
  }));
}
function navGroupOpen(key, relevant, search) {
  if (search) return true;
  const stored = currentDraft().openGroups[key];
  return stored === undefined ? Boolean(relevant) : Boolean(stored);
}
function canEdit(item) {
  return Boolean(
    !isReadOnly() &&
    state.data?.rule_set?.active &&
    item?.rule &&
    item.rule.confirmation_status === "confirmed" &&
    !item.rule.configuration_pending,
  );
}
function select(id) {
  state.selected = state.data.parameters.find((x) => x.id === id);
  if (isReadOnly()) {
    $("editorEmpty").hidden = true;
    $("editor").hidden = false;
  }
  $("editorGroup").textContent = state.selected.group;
  $("editorName").textContent = state.selected.name;
  $("editorSummary").textContent =
    `${state.selected.unit || "未知单位"} · 原始定位 ${state.selected.location}`;
  $("editorRule").textContent = state.selected.rule
    ? `${state.selected.rule.confirmation_status} · ${state.selected.rule.linkage_strategy || "未配置"}`
    : "活动发布中无此规则";
  $("editorVersion").textContent = state.selected.rule
    ? `规则 V${state.selected.rule.rule_version}`
    : "";
  $("linkage").value = state.selected.rule?.linkage_strategy || "independent";
  $("linkage").disabled = !canEdit(state.selected);
  $("editMode").textContent = state.selected.rule
    ? `${state.selected.rule.adjustment_mode} · 步长 ${state.selected.rule.minimum_step ?? "未设"} · 范围 ${state.selected.rule.allowed_range?.join("—") || "未设"}`
    : "配置待确认";
  renderYears();
  $("ruleDetails").textContent = JSON.stringify(
    state.selected.rule
      ? {
          rule_id: state.selected.rule.rule_id,
          rule_version: state.selected.rule.rule_version,
          publication_id: state.data.rule_set.publication_id,
          confirmed_source_cells: state.selected.rule.confirmed_source_cells,
          configuration: {
            display_unit: state.selected.rule.display_unit,
            adjustment_mode: state.selected.rule.adjustment_mode,
            minimum_step: state.selected.rule.minimum_step,
            allowed_range: state.selected.rule.allowed_range,
            linkage_strategy: state.selected.rule.linkage_strategy,
          },
        }
      : { status: state.selected.rule_status },
    null,
    2,
  );
  renderNav();
  if (isReadOnly()) return;
  const draft = currentDraft();
  if (!draft.selected.includes(id)) draft.selected.push(id);
  draft.cardOrder = [...draft.selected];
  draft.page = Math.floor(draft.selected.indexOf(id) / cardsPerPage());
  persistWorkbench();
  renderCardPages();
  renderCardGrid();
}
function renderYears() {
  const item = state.selected,
    values = state.edits[item.id] || item.baseline,
    editable = canEdit(item),
    range = item.rule?.allowed_range,
    step = item.rule?.minimum_step ?? "any";
  $("years").innerHTML = (item.active_years || years)
    .map(
      (year) =>
        `<div class="year ${editable ? "" : "locked"}"><label><span>${year}</span><span>${item.unit || ""}</span></label><input type="number" step="${step}" data-year="${year}" value="${values[year] ?? ""}" ${editable ? "" : "disabled"}>${range ? `<input type="range" step="${step}" data-range="${year}" value="${values[year] ?? 0}" min="${range[0]}" max="${range[1]}" ${editable ? "" : "disabled"}>` : ""}<div class="baseline">基准 ${item.baseline[year]}</div><div class="delta">变化 ${((values[year] ?? item.baseline[year]) - item.baseline[year]).toFixed(4)}</div><button data-reset="${year}" ${editable ? "" : "disabled"}>恢复基准</button></div>`,
    )
    .join("");
  $("years").className = `years layout-${currentDraft().cardLayout}`;
  document
    .querySelectorAll("[data-year]")
    .forEach(
      (el) =>
        (el.oninput = (event) =>
          changeYear(+el.dataset.year, event.target.value)),
    );
  document
    .querySelectorAll("[data-range]")
    .forEach(
      (el) =>
        (el.oninput = (event) =>
          changeYear(+el.dataset.range, event.target.value)),
    );
  document
    .querySelectorAll("[data-reset]")
    .forEach(
      (el) =>
        (el.onclick = () =>
          changeYear(+el.dataset.reset, item.baseline[el.dataset.reset])),
    );
}
function applyYearValue(item, base, year, value, linkage) {
  if (linkage === "independent") base[year] = value;
  else if (linkage === "same_value") years.forEach((y) => (base[y] = value));
  else if (linkage === "same_delta")
    years.forEach(
      (y) =>
        (base[y] =
          Number(item.baseline[y]) + value - Number(item.baseline[year])),
    );
  else if (linkage === "baseline_ratio" && Number(item.baseline[year]) !== 0)
    years.forEach(
      (y) =>
        (base[y] =
          Number(item.baseline[y]) * (value / Number(item.baseline[year]))),
    );
}
function changeYear(year, raw) {
  const value = Number(raw),
    item = state.selected;
  if (!Number.isFinite(value) || !canEdit(item)) return;
  const range = item.rule.allowed_range;
  if (range && (value < range[0] || value > range[1])) return;
  const base = { ...(state.edits[item.id] || item.baseline) };
  applyYearValue(item, base, year, value, $("linkage").value);
  state.edits[item.id] = base;
  renderYears();
  renderNav();
  setCalculateEnabled();
  currentDraft().edits = state.edits;
  persistWorkbench();
  updateDraftStatus();
  scheduleAutomaticCalculation();
}
function payload() {
  return Object.entries(state.edits).map(([id, values]) => {
    const item = state.data.parameters.find((x) => x.id === id);
    return { rule_id: item.rule.rule_id, indicator_id: id, values };
  });
}
function reverseRunAvailability() {
  if (state.module !== "single" && state.module !== "multi") return null;
  if (isReadOnly()) return { ok: false, reason: "历史模板只读，不能发起求解" };
  if (!state.data?.rule_set?.active)
    return { ok: false, reason: "活动模板尚未发布规则集，不能发起求解" };
  const hasVariable =
    state.module === "single"
      ? Boolean(state.selected?.rule)
      : state.reverseVariables.length > 0;
  if (!hasVariable)
    return {
      ok: false,
      reason:
        state.module === "single"
          ? "请在左栏选择一个已确认指标作为求解变量"
          : "请先至少添加一个变量",
    };
  if (!activeReverseConstraints().length)
    return { ok: false, reason: "请至少添加一条启用的约束" };
  return { ok: true, reason: "" };
}
function setCalculateEnabled() {
  const reverse = reverseRunAvailability();
  const enabled = reverse
    ? reverse.ok
    : Boolean(
        !isReadOnly() &&
        state.data?.rule_set?.active &&
        Object.keys(state.edits).length,
      );
  const reason = reverse && !reverse.ok ? reverse.reason : "";
  ["calculate", "calculateTop"].forEach((id) => {
    $(id).disabled = !enabled;
    $(id).title = reason;
  });
}
const STATUS_LABELS = {
  valid: "有效",
  reverse_no_feasible: "无可行解",
  pending_rule_confirmation: "待确认",
  cycle_not_converged: "未收敛",
  engine_difference: "引擎差异",
  calculation_failed: "计算失败",
  cancelled: "已取消",
  historical_read_only: "历史只读",
};
const TERMINAL = ["succeeded", "failed", "cancelled", "cycle_not_converged"];
const STAGE_LABELS = {
  open_isolated: "复制模板并启动 Excel",
  baseline_summary_read: "读取基准汇总",
  write_input: "写入输入假设",
  initial_recalculate: "初始重算",
  result_summary_read: "读取结果汇总",
};
function stageLabel(stage) {
  if (!stage) return "排队中";
  if (stage.startsWith("cycle_iteration_"))
    return `循环迭代 第 ${stage.split("_").pop()} 轮`;
  if (stage.startsWith("comparison_scenario_"))
    return `对比场景 ${stage.split("_").pop()}`;
  if (stage.startsWith("reverse_v2_search_"))
    return `多输入求解 第 ${stage.split("_").pop()} 次测算`;
  if (stage.startsWith("reverse_search_"))
    return `单变量求解 第 ${stage.split("_").pop()} 次测算`;
  return STAGE_LABELS[stage] || stage;
}
function setRunning(running) {
  const reverse = reverseRunAvailability();
  const unavailable = reverse
    ? !reverse.ok
    : isReadOnly() ||
      !state.data?.rule_set?.active ||
      !Object.keys(state.edits).length;
  ["calculate", "calculateTop"].forEach((id) => {
    $(id).disabled = running || unavailable;
  });
  $("taskProgress").hidden = !running;
  if (state.data?.templates) renderTemplateSwitch();
}
async function calculate() {
  if (isReadOnly()) return alert("历史模板只读，不能发起新测算");
  if (
    !state.data.rule_set.active ||
    !Object.keys(state.edits).length ||
    state.task
  )
    return;
  $("calculate").disabled = true;
  $("calculateTop").disabled = true;
  setRunning(true);
  state.taskKind = "calculation";
  try {
    const response = await fetch("/api/calculations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_version_id: state.data.template.id,
        adjustments: payload(),
        engine_mode: $("engineMode").value,
      }),
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "测算提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
    degradeForwardMode(error.message);
  }
}
async function poll() {
  if (!state.task) return;
  let task;
  try {
    const response = await fetch(`/api/calculations/${state.task}`);
    task = await response.json();
    if (!response.ok) throw Error(task.error || "状态查询失败");
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
    return;
  }
  renderTaskProgress(task);
  if (!TERMINAL.includes(task.status)) {
    setTimeout(poll, 1000);
    return;
  }
  const kind = state.taskKind;
  state.task = null;
  state.taskKind = null;
  setRunning(false);
  if (task.status === "succeeded") {
    const data = task.result;
    if (kind === "comparison") {
      state.comparison = data;
      renderComparison(data);
      if (state.module === "forward") setCenterView("comparison");
    } else if (kind === "reverse") {
      state.reverseResult = data;
      state.data.scenario_draft = data.scenario_draft;
      $("exportReverse").disabled = !data.scenario_draft;
      renderTrust(data);
      renderTrace(data.calculation_details);
      setCards(
        (data.variables || [data.variable])
          .map(
            (x) =>
              `<article class="card"><h3>${x.indicator_name} · P${x.priority || 1}</h3><div class="metric">${formatResultValue(x.suggested_value ?? x.required_value, unitForIndicator(x.indicator_name)) || "—"}</div><div class="years-mini">调整 ${formatResultValue(x.adjustment, unitForIndicator(x.indicator_name)) || "—"} · ${x.hit_boundary ? "触及边界" : "范围内"}</div>${x.reason ? `<div class="years-mini reason">启用原因：${x.reason}</div>` : ""}</article>`,
          )
          .join("") +
        `<article class="card"><h3>求解摘要</h3><div class="metric">${data.feasible ? "可行" : "无解"}</div><div class="years-mini">${data.search_count}/${data.calculation_details.max_evaluations || data.search_count} 次 · 软偏差 ${formatResultValue(data.soft_deviation) || "—"}</div></article>` +
        data.constraints
          .map(
            (x) =>
              `<article class="card"><h3>${x.indicator_name}</h3><div class="metric">${x.hit ? "命中" : "未命中"}</div><div class="years-mini">实际 ${x.actual ?? "—"} · 偏差 ${x.deviation}</div></article>`,
          )
          .join(""),
      );
      renderNav();
    } else {
      state.data = { ...state.data, ...data };
      renderTrust(data);
      setCards("");
      state.data.result_rows = data.result_rows || state.data.result_rows;
      renderDetails(state.data.result_rows || data.details);
      renderTrace(data.calculation_details);
      renderConstraintMetrics();
      renderNav();
      currentDraft().calculatedUnsaved = true;
      persistWorkbench(); updateDraftStatus();
    }
  } else if (task.status === "cycle_not_converged" && task.result) {
    renderTrust(task.result);
    renderTrace(task.result.calculation_details);
  } else if (task.status === "cancelled") {
    renderTrust({
      trust: { status: "cancelled", reason: "测算已取消，未保存任何结果" },
    });
  } else {
    const reason = task.error || task.result?.trust?.reason || "测算失败";
    renderTrust(
      task.result?.trust
        ? task.result
        : { trust: { status: "calculation_failed", reason, error: reason } },
    );
    if (kind === "calculation") degradeForwardMode(reason);
  }
  if (kind === "scenario") loadScenarios();
  if (kind === "calculation" && state.newestDraftPending) {
    state.newestDraftPending = false;
    setTimeout(calculate, 0);
  }
}

function renderWorkerHealth() {
  const indicator = $("workerHealth");
  indicator.classList.toggle("failed", state.warmHealthy === false);
  if (state.warmHealthy === true) indicator.textContent = "● warm 热启动正常";
  else if (state.warmHealthy === false) indicator.textContent = "● warm 不可用，点击复查";
  else indicator.textContent = "● warm 健康检查中…";
}
async function recheckWorkerHealth() {
  state.warmHealthy = null;
  renderWorkerHealth();
  try {
    const response = await fetch("/api/warm-health");
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "warm 健康检查失败");
    state.warmHealthy = Boolean(data.healthy);
  } catch (error) {
    state.warmHealthy = false;
  }
  renderWorkerHealth();
  if (state.warmHealthy || state.persisted.shared.forwardMode !== "auto") return;
  state.persisted.shared.forwardMode = "manual";
  document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item.dataset.mode === "manual"));
  $("workspaceNotice").hidden = false;
  $("workspaceNotice").textContent = "warm 热启动不可用，自动模式已切换为手动；恢复健康后请手动切回自动。";
  persistWorkbench();
}

function degradeForwardMode(reason) {
  if (state.module !== "forward") return;
  state.warmHealthy = false;
  state.persisted.shared.degradationReason = reason;
  if (state.persisted.shared.forwardMode === "auto") {
    state.persisted.shared.forwardMode = "manual";
    $("workerHealth").textContent = "● warm 自动已降级为手动";
  } else if ($("engineMode").value === "warm_com") {
    $("engineMode").value = "cold_com";
    state.persisted.shared.engineMode = "cold_com";
    $("workerHealth").textContent = "● warm 手动失败，已切换 cold";
  }
  $("workerHealth").classList.add("failed");
  $("workspaceNotice").hidden = false;
  $("workspaceNotice").textContent = `计算模式已降级：${reason}`;
  document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item.dataset.mode === state.persisted.shared.forwardMode));
  persistWorkbench();
}
async function cancelCalculation() {
  if (!state.task) return;
  $("cancelCalc").disabled = true;
  try {
    await fetch(`/api/calculations/${state.task}/cancel`, { method: "POST" });
  } finally {
    $("cancelCalc").disabled = false;
  }
}
async function showEngineValidation() {
  const response = await fetch("/api/engine-validation"), data = await response.json();
  if (!response.ok) return alert(data.error || "尚无引擎验证报告");
  renderTrust({
    ...data,
    trust: { status: data.validation_state, reason: data.reason, rule_version: data.publication_id },
  });
}
function renderTaskProgress(task) {
  const waiting = task.status === "cancel_requested";
  $("statusBadge").textContent = waiting ? "取消中…" : "测算中…";
  $("statusBadge").className = "badge running";
  const comparison = task.stage_timings || {};
  const timings = Object.entries(comparison)
    .filter(([, values]) => Array.isArray(values))
    .map(
      ([key, values]) =>
        `${key}: ${values.map((x) => Math.round(x)).join("/")}ms`,
    )
    .join(" · ");
  $("taskStage").textContent = waiting
    ? "取消请求已收到，等待安全停止点…"
    : `当前阶段：${stageLabel(task.current_stage)}`;
  const batch = comparison.total
    ? ` · ${comparison.completed || 0}/${comparison.total} · ${comparison.current_scenario || ""}`
    : "";
  const search = comparison.search_count
    ? ` · 第 ${comparison.search_count}/${comparison.max_evaluations || "?"} 次测算`
    : "";
  const engine = ` · ${task.engine_mode || "cold_com"}${task.worker_id ? ` · worker ${task.worker_id.slice(0, 8)}` : ""} · 排队 ${Math.round(task.queue_wait_ms || 0)}ms · 取消 ${task.cancel_status || "not_requested"}`;
  $("taskMeta").innerHTML =
    `已耗时 ${(task.elapsed_ms / 1000).toFixed(1)} 秒 · 循环 ${task.iterations || 0} 次${engine}${batch}${search}${timings ? `<br><small>${timings}</small>` : ""}`;
}
function renderTrust(data) {
  const t = data.trust || {},
    failed = [
      "calculation_failed",
      "engine_difference",
      "cycle_not_converged",
    ].includes(t.status);
  $("statusBadge").textContent = STATUS_LABELS[t.status] || "未计算";
  $("statusBadge").className =
    `badge ${t.status === "valid" ? "valid" : failed ? "failed" : "pending"}`;
  $("trust").className =
    `trust ${t.status === "valid" ? "valid" : failed ? "failed" : ""}`;
  const differences = data.engine_validation?.differences || data.differences || [];
  const differenceDetails = differences.length
    ? `<ul>${differences.slice(0, 20).map((item) => `<li>${item.indicator}${item.year ? ` · ${item.year}` : ""}: ${item.reference_value} → ${item.candidate_value}（${item.reference_engine} ${item.reference_version || "?"} / ${item.candidate_engine} ${item.candidate_version || "?"}）</li>`).join("")}</ul>`
    : "";
  $("trust").innerHTML =
    `<strong>${t.reason || data.reason || "尚未计算"}</strong><small>循环 ${t.iterations ?? 0} 次 · 最大差异 ${t.final_difference ?? "—"}<br>活动发布 ${t.rule_version || data.publication_id || state.data?.rule_set?.publication_id || "未发布"} · ${t.error || "详情可展开查看"}</small>${differenceDetails}`;
}
function setCards(html) {
  $("cards").innerHTML = html || "";
  $("cards").hidden = !html;
}
function renderDetails(details = []) {
  const query = $("detailSearch").value.toLowerCase();
  const entries = details.filter((entry) => entry.kind === "header" || !query || entry.name.toLowerCase().includes(query));
  const columns = ["name", "2025", "2026", "2027", "2028", "2029", "2030", "five_year_change", "cagr"];
  const labels = ["指标", "2025", "2026", "2027", "2028", "2029", "2030", "五年变化", "CAGR"];
  const widths = resultColumnWidths();
  $("details").innerHTML = `<div class="result-scroll"><table class="result-table"><colgroup>${widths.map((width) => `<col style="width:${width}px">`).join("")}</colgroup><thead><tr>${labels.map((label, index) => `<th>${label}<span class="col-resizer" data-col="${index}"></span></th>`).join("")}</tr></thead><tbody>${renderDetailEntries(entries, columns, query)}</tbody></table></div>`;
  setupColumnResize();
  document.querySelectorAll("[data-result-section]").forEach((el) => (el.onclick = () => {
    const draft = currentDraft();
    const key = el.dataset.resultSection;
    draft.resultSections[key] = isSectionCollapsed(draft, key);
    persistWorkbench();
    renderDetails(details);
  }));
  document.querySelectorAll("[data-rstar]").forEach((el) => (el.onclick = (event) => {
    event.stopPropagation();
    const id = el.dataset.rstar;
    state.persisted.resultFavorites[id] = !state.persisted.resultFavorites[id];
    persistWorkbench();
    renderDetails(details);
  }));
}

const RESULT_COLUMN_WIDTHS = [128, 66, 66, 66, 66, 66, 66, 84, 72];
function resultColumnWidths() {
  const draft = currentDraft();
  if (!Array.isArray(draft.columnWidths) || draft.columnWidths.length !== RESULT_COLUMN_WIDTHS.length) {
    draft.columnWidths = [...RESULT_COLUMN_WIDTHS];
  }
  return draft.columnWidths;
}
function resetResultColumns() {
  currentDraft().columnWidths = [...RESULT_COLUMN_WIDTHS];
  persistWorkbench();
  state.comparison ? renderComparisonDetails() : renderDetails(filteredResultRows());
}

function isSectionCollapsed(draft, key) {
  return (draft.resultSections[key] ?? true) === false;
}
// 按 sheet 原序渲染 标题节 + 指标行；折叠的节隐藏其下内容（星标行留外），搜索时忽略折叠
function renderDetailEntries(entries, columns, query) {
  const draft = currentDraft();
  const stack = [];
  return entries
    .map((entry) => {
      if (entry.kind === "header") {
        while (stack.length && stack[stack.length - 1].level >= entry.level) stack.pop();
        const hidden = stack.some((item) => item.collapsed);
        const key = `${entry.title}@${entry.row}`;
        const collapsed = !query && isSectionCollapsed(draft, key);
        stack.push({ level: entry.level, collapsed });
        if (hidden) return "";
        return `<tr class="section-head level-${entry.level}" data-result-section="${key}"><td colspan="${columns.length}">${collapsed ? "+" : "−"} ${entry.title}</td></tr>`;
      }
      const hidden = stack.some((item) => item.collapsed);
      if (hidden && !state.persisted.resultFavorites[entry.id]) return "";
      return resultRow(entry, columns);
    })
    .join("");
}

function resultRow(row, columns) {
  const constrained = state.reverseConstraints.some((x) => x.indicator_name === row.name);
  const changed = resultRowChanged(row);
  const starred = state.persisted.resultFavorites[row.id];
  const classes = [constrained ? "constraint" : "", changed ? "changed" : ""].filter(Boolean).join(" ");
  const markers = `${constrained ? " · 已设约束" : ""}${changed ? " · 较基准变化" : ""}`;
  const indent = /^(其中|——|\s)/.test(row.name) ? " indent" : "";
  const cells = columns.slice(1).map((column) => {
    const value = row.values?.[column];
    const cls = resultValueClass(value, column);
    return `<td${cls ? ` class="${cls}"` : ""}>${formatDetailResultValue(value, column === "cagr" ? "%" : row.unit, row.precision)}</td>`;
  }).join("");
  return `<tr${classes ? ` class="${classes}"` : ""}><td title="${row.name}"><span class="star ${starred ? "on" : ""}" data-rstar="${row.id}">${starred ? "★" : "☆"}</span><strong class="name${indent}">${row.name}</strong><small>${markers}</small></td>${cells}</tr>`;
}

function resultRowChanged(row) {
  const baseline = row.baseline_values;
  if (!baseline) return false;
  return ["2025", "2026", "2027", "2028", "2029", "2030", "five_year_change", "cagr"].some((key) => {
    const current = Number(row.values?.[key]), before = Number(baseline[key]);
    return Number.isFinite(current) && Number.isFinite(before) && Math.abs(current - before) > 1e-9 * Math.max(1, Math.abs(current), Math.abs(before));
  });
}

function resultValueClass(value, column) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (number < 0) return "negative";
  return column === "five_year_change" ? "positive" : "";
}

function formatResultValue(value, unit, precision) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (unit === "%") {
    const percentage = number * 100;
    const digits = Math.abs(percentage) < 10 ? 2 : Math.abs(percentage) <= 100 ? 1 : 0;
    return `${percentage.toFixed(digits)}%`;
  }
  if (unit === "亿元") return Math.round(number).toLocaleString("zh-CN");
  if (Number.isFinite(precision)) return number.toFixed(precision);
  return number.toFixed(2);
}

function formatDetailResultValue(value, unit, precision) {
  if (unit === "%") return formatResultValue(value, unit, precision);
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const absolute = Math.abs(number);
  const digits = absolute > 100 ? 0 : absolute >= 10 ? 1 : 2;
  return number.toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits});
}

function rulePrecision(item) {
  const step = item?.rule?.minimum_step;
  if (typeof step !== "number" || !Number.isFinite(step) || step <= 0) return undefined;
  const text = step.toFixed(10).replace(/0+$/, "").replace(/\.$/, "");
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

function setupColumnResize() {
  document.querySelectorAll("[data-col]").forEach((handle) => (handle.onpointerdown = (event) => {
    event.preventDefault();
    event.stopPropagation();
    handle.setPointerCapture?.(event.pointerId);
    const index = Number(handle.dataset.col), start = event.clientX, initial = resultColumnWidths()[index];
    const move = (next) => {
      resultColumnWidths()[index] = Math.max(index === 0 ? 96 : 52, initial + next.clientX - start);
      const column = document.querySelectorAll(".result-table col")[index];
      if (column) column.style.width = `${resultColumnWidths()[index]}px`;
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up); persistWorkbench();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
  }));
}
function renderTrace(details) {
  $("calculationTrace").innerHTML = details
    ? `<dl><dt>计算编号</dt><dd>${details.calculation_id || "—"}</dd><dt>阶段</dt><dd>${details.stage}</dd><dt>耗时</dt><dd>${details.duration_ms ?? "—"} ms</dd><dt>引擎</dt><dd>${details.engine_mode || "—"}${details.worker_id ? ` / ${details.worker_id}` : ""}</dd><dt>排队/取消</dt><dd>${details.queue_wait_ms ?? 0} ms / ${details.cancel_status || "not_requested"}</dd><dt>循环</dt><dd>${details.iterations ?? 0} 次</dd></dl><ol>${(details.log || []).map((line) => `<li>${line}</li>`).join("")}</ol>`
    : "尚未执行测算";
}
function setupReverse() {
  const host = $("ruleDetails").parentElement,
    b = document.createElement("details");
  b.className = "reverse";
  b.innerHTML =
    '<summary>单变量反向 · 约束清单</summary><p>当前指标作为变量；变量初始值与搜索范围在卡片上配置，约束在画布顶部的约束构建器中添加。配置完成后使用顶栏「开始求解」。</p><div id="reverseConstraints"></div>';
  host.parentElement.insertBefore(b, host);
  const v2 = document.createElement("details");
  v2.className = "reverse";
  v2.innerHTML =
    '<summary>多输入反推 · 变量管理</summary><p>在卡片上「添加为变量」，按优先级逐级求解；最多 15 次正向测算。配置完成后使用顶栏「开始求解」。</p><button id="addReverseVariable">加入当前输入</button><div id="reverseVariables"></div>';
  host.parentElement.insertBefore(v2, host);
  $("addReverseVariable").onclick = () => addReverseVariable();
  renderReverseConstraints();
  renderReverseVariables();
}
function constraintRelationKinds(relation) {
  return relation === ">" || relation === "≥"
    ? ["min"]
    : relation === "<" || relation === "≤"
      ? ["max"]
      : relation === "between"
        ? ["min", "max"]
        : ["target"];
}
function renderConstraintMetrics() {
  const select = $("cbMetric");
  if (!select || !state.data) return;
  const query = ($("cbSearch").value || "").toLowerCase(),
    outputs = (state.data.result_rows || []).filter(
      (row) => row.kind !== "header" && (!query || row.name.toLowerCase().includes(query)),
    ),
    inputs = state.data.parameters.filter(
      (x) => !query || x.name.toLowerCase().includes(query),
    );
  select.innerHTML =
    `<optgroup label="输出指标">${outputs.map((row) => `<option value="output||${row.name}">${row.name}</option>`).join("")}</optgroup>` +
    `<optgroup label="输入指标">${inputs.map((x) => `<option value="input|${x.id}|${x.name}">${x.name}</option>`).join("")}</optgroup>`;
}
function renderConstraintValueInputs() {
  if (!$("cbValues")) return;
  const relation = $("cbRelation").value,
    scope = $("cbScope").value,
    pair = relation === "between";
  $("cbYear").hidden = scope !== "single";
  const field = (key, label) =>
      pair
        ? `<label>${label} 下限<input type="number" step="any" data-cb-value="${key}|0"></label><label>${label} 上限<input type="number" step="any" data-cb-value="${key}|1"></label>`
        : `<label>${label} 目标值<input type="number" step="any" data-cb-value="${key}"></label>`;
  $("cbValues").innerHTML =
    scope === "each"
      ? years.map((year) => field(year, year)).join("")
      : scope === "single"
        ? field($("cbYear").value, $("cbYear").value)
        : field("all", "五年同值");
}
function addConstraintGroup() {
  if (!state.data || !$("cbMetric").value) return alert("请选择约束指标");
  const [indicator_type, indicator_id, indicator_name] = $("cbMetric").value.split("|"),
    relation = $("cbRelation").value,
    scope = $("cbScope").value,
    hard = $("cbHard").value === "true",
    kinds = constraintRelationKinds(relation),
    pair = kinds.length > 1,
    entries = {};
  document.querySelectorAll("[data-cb-value]").forEach((el) => {
    el.classList.remove("invalid");
    const raw = String(el.value).trim();
    entries[el.dataset.cbValue] = raw === "" ? null : Number(raw);
    if (entries[el.dataset.cbValue] !== null && !Number.isFinite(entries[el.dataset.cbValue]))
      entries[el.dataset.cbValue] = null;
  });
  const markInvalid = (key) => {
    const el = document.querySelector(`[data-cb-value="${key}"]`);
    if (el) el.classList.add("invalid");
  };
  const targetYears = scope === "single" ? [String($("cbYear").value)] : years.map(String),
    records = [];
  for (const year of targetYears) {
    const key = scope === "all" ? "all" : year;
    if (pair) {
      const lower = entries[`${key}|0`], upper = entries[`${key}|1`];
      if (lower == null || upper == null) {
        if (lower == null) markInvalid(`${key}|0`);
        if (upper == null) markInvalid(`${key}|1`);
        return alert("请输入完整的区间上下限");
      }
      if (lower > upper) {
        markInvalid(`${key}|0`);
        markInvalid(`${key}|1`);
        return alert("区间下限不能大于上限");
      }
      records.push({ year, kind: "min", value: lower }, { year, kind: "max", value: upper });
    } else {
      const value = entries[key];
      if (value == null) {
        markInvalid(key);
        return alert("请输入约束目标值");
      }
      records.push({ year, kind: kinds[0], value });
    }
  }
  const indicatorKey = indicator_id || indicator_name,
    signatureOf = (recs, key) => recs.map((r) => `${key}|${r.year}|${r.kind}`).sort().join(";"),
    newSignature = signatureOf(records, indicatorKey),
    duplicate = constraintGroups().some((group) =>
      group.records.some((r) => r.enabled !== false) &&
      signatureOf(group.records, group.records[0].indicator_id || group.records[0].indicator_name) === newSignature);
  if (duplicate) return alert("已存在相同指标、关系与年份的约束，请直接编辑现有约束卡");
  const scopeLabel = scope === "all" ? "五年同值" : scope === "each" ? "逐年" : `${targetYears[0]} 单年`,
    valueLabel = pair ? `[${records[0].value}, ${records[1].value}]` : `${records[0].value}`,
    group_label = scope === "each"
      ? `${indicator_name} ${relation === "between" ? "区间" : relation} · ${scopeLabel}`
      : `${indicator_name} ${relation === "between" ? "区间" : relation} ${valueLabel} · ${scopeLabel}`,
    group_id = `cg-${Date.now().toString(36)}-${state.reverseConstraints.length}`;
  records.forEach((record) =>
    state.reverseConstraints.push({
      indicator_type,
      indicator_id,
      indicator_name,
      ...record,
      hard,
      enabled: true,
      group_id,
      group_label,
      relation,
      scope,
    }),
  );
  syncReverseDraft();
  renderReverseConstraints();
  renderCardGrid();
}
function constraintGroups() {
  const buckets = new Map();
  state.reverseConstraints.forEach((constraint, index) => {
    const key = constraint.group_id || `legacy-${index}`;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(index);
  });
  return [...buckets.entries()].map(([key, indexes]) => ({
    key,
    indexes,
    records: indexes.map((index) => state.reverseConstraints[index]),
  }));
}
function constraintGroupText(group) {
  const first = group.records[0],
    base = first.group_label || `${first.indicator_name} · ${first.year} · ${first.kind === "min" ? "≥" : first.kind === "max" ? "≤" : "="} ${first.value}`;
  return `${base} · ${first.hard ? "硬约束" : "软目标"}${group.records.some((x) => x.enabled === false) ? " · 已停用" : ""}`;
}
function constraintGroupYearLines(group) {
  const byYear = new Map();
  group.records.forEach((record) => {
    const year = String(record.year);
    if (!byYear.has(year)) byYear.set(year, []);
    byYear.get(year).push(record);
  });
  const symbol = (record) =>
    record.relation && record.relation !== "between" ? record.relation : record.kind === "min" ? "≥" : record.kind === "max" ? "≤" : "=";
  return [...byYear.entries()]
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([year, records]) => {
      const lower = records.find((x) => x.kind === "min"),
        upper = records.find((x) => x.kind === "max");
      if (lower && upper) return `<div class="cg-year"><label>${year}</label><span>区间 [${lower.value}, ${upper.value}]</span></div>`;
      return records.map((record) => `<div class="cg-year"><label>${year}</label><span>${symbol(record)} ${record.value}</span></div>`).join("");
    })
    .join("");
}
function constraintGroupCardBody(group) {
  const first = group.records[0],
    enabled = group.records.some((x) => x.enabled !== false);
  return `<div class="constraint-group-summary"><div class="cg-head">${first.group_label || first.indicator_name}</div>${constraintGroupYearLines(group)}</div><div class="card-foot"><button data-cg-hard="${group.key}">${first.hard ? "切换为软目标" : "切换为硬约束"}</button><label><input type="checkbox" data-cg-enable="${group.key}" ${enabled ? "checked" : ""}> 启用</label><button data-cg-remove="${group.key}">删除</button></div>`;
}
function updateConstraintGroup(key, mutate) {
  const group = constraintGroups().find((item) => item.key === key);
  if (!group) return;
  mutate(group);
  syncReverseDraft();
  renderReverseConstraints();
  renderCardGrid();
}
function bindConstraintGroupEvents() {
  document.querySelectorAll("[data-cg-enable]").forEach(
    (el) =>
      (el.onchange = () =>
        updateConstraintGroup(el.dataset.cgEnable, (group) =>
          group.records.forEach((record) => (record.enabled = el.checked)),
        )),
  );
  document.querySelectorAll("[data-cg-hard]").forEach(
    (el) =>
      (el.onclick = () =>
        updateConstraintGroup(el.dataset.cgHard, (group) => {
          const hard = !group.records[0].hard;
          group.records.forEach((record) => (record.hard = hard));
        })),
  );
  document.querySelectorAll("[data-cg-remove]").forEach(
    (el) =>
      (el.onclick = () => {
        if (!confirm("确认移除该约束？")) return;
        updateConstraintGroup(el.dataset.cgRemove, (group) => {
          state.reverseConstraints = state.reverseConstraints.filter((_, index) => !group.indexes.includes(index));
        });
      }),
  );
}
function setupConstraintBuilder() {
  $("cbSearch").oninput = renderConstraintMetrics;
  $("cbRelation").onchange = renderConstraintValueInputs;
  $("cbScope").onchange = renderConstraintValueInputs;
  $("cbYear").onchange = renderConstraintValueInputs;
  $("cbAdd").onclick = addConstraintGroup;
  renderConstraintValueInputs();
}
function renderReverseConstraints() {
  const host = $("reverseConstraints");
  if (!host) return;
  host.innerHTML =
    constraintGroups()
      .map((group) => {
        const enabled = group.records.some((x) => x.enabled !== false);
        return `<label class="reverse-row"><input type="checkbox" data-cg-enable="${group.key}" ${enabled ? "checked" : ""}><span>${constraintGroupText(group)}${group.records.length > 1 ? ` · ${group.records.length} 条` : ""}</span><button data-cg-remove="${group.key}">删除</button></label>`;
      })
      .join("") || "<small>尚未添加约束</small>";
  bindConstraintGroupEvents();
  if (state.data) renderNav();
}
async function runReverse() {
  if (isReadOnly()) return alert("历史模板只读，不能发起求解");
  if (
    !state.selected?.rule ||
    !state.reverseConstraints.some((x) => x.enabled) ||
    state.task
  )
    return alert("请选择 confirmed 输入变量并启用至少一个约束");
  const config = ensureSingleVariable(state.selected);
  if (!(Number.isFinite(config.lower) && Number.isFinite(config.upper) && Number.isFinite(config.initial) && config.lower <= config.initial && config.initial <= config.upper))
    return alert("请检查变量初始值与搜索范围上下限");
  const body = {
      template_version_id: state.data.template.id,
      variable: {
        rule_id: state.selected.rule.rule_id,
        indicator_id: state.selected.id,
        year: config.year,
        initial: config.initial,
        lower: config.lower,
        upper: config.upper,
      },
      adjustments: payload(),
      constraints: state.reverseConstraints,
      max_evaluations: 25,
      engine_mode: $("engineMode").value,
    };
  persistWorkbench();
  setRunning(true);
  state.taskKind = "reverse";
  try {
    const response = await fetch("/api/reverse-calculations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
      task = await response.json();
    if (!response.ok) throw Error(task.error || "求解提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({
      trust: {
        status: "calculation_failed",
        reason: error.message,
        error: error.message,
      },
    });
  }
}
function addReverseVariable(item = state.selected) {
  if (!canEdit(item)) return alert("请选择已确认的输入指标作为变量");
  if (state.reverseVariables.some((x) => x.indicator_id === item.id))
    return alert("该变量已添加");
  const range = item.rule.allowed_range || [],
    year = "2030";
  state.reverseVariables.push({
    rule_id: item.rule.rule_id,
    indicator_id: item.id,
    indicator_name: item.name,
    priority: state.reverseVariables.length + 1,
    year,
    initial: item.baseline[year],
    lower: range[0] ?? item.baseline[year],
    upper: range[1] ?? item.baseline[year],
    step: item.rule.minimum_step,
    linkage_strategy: item.rule.linkage_strategy || "independent",
  });
  syncReverseDraft();
  renderReverseVariables();
  renderCardGrid();
}
function renderReverseVariables() {
  if (!$("reverseVariables")) return;
  $("reverseVariables").innerHTML =
    state.reverseVariables
      .map(
        (x, i) =>
          `<div class="reverse-variable"><strong>${x.indicator_name}</strong><label>优先级<input type="number" min="1" data-v2="priority" data-index="${i}" value="${x.priority}"></label><label>年度<select data-v2="year" data-index="${i}">${years.map((year) => `<option ${String(year) === x.year ? "selected" : ""}>${year}</option>`).join("")}</select></label><label>初始值<input type="number" data-v2="initial" data-index="${i}" value="${x.initial ?? ""}"></label><label>下限<input type="number" data-v2="lower" data-index="${i}" value="${x.lower}"></label><label>上限<input type="number" data-v2="upper" data-index="${i}" value="${x.upper}"></label><label>步长<input type="number" min="0" data-v2="step" data-index="${i}" value="${x.step ?? ""}"></label><label>联动<select data-v2="linkage_strategy" data-index="${i}"><option value="independent">独立</option><option value="same_delta">同幅</option><option value="same_value">同值</option><option value="baseline_ratio">同比例</option></select></label><button data-remove-v2="${i}">删除</button></div>`,
      )
      .join("") || "<small>尚未添加可调变量</small>";
  document.querySelectorAll("[data-v2]").forEach((element) => {
    const item = state.reverseVariables[+element.dataset.index];
    if (element.dataset.v2 === "linkage_strategy") element.value = item.linkage_strategy;
    element.onchange = () => {
      const field = element.dataset.v2;
      item[field] = ["priority", "initial", "lower", "upper", "step"].includes(field)
        ? Number(element.value)
        : element.value;
      syncReverseDraft();
      renderCardGrid();
    };
  });
  document.querySelectorAll("[data-remove-v2]").forEach(
    (element) =>
      (element.onclick = () => {
        state.reverseVariables.splice(+element.dataset.removeV2, 1);
        syncReverseDraft();
        renderReverseVariables();
        renderCardGrid();
      }),
  );
  if (state.data) renderNav();
}
async function runReverseV2() {
  if (isReadOnly()) return alert("历史模板只读，不能发起求解");
  if (!state.reverseVariables.length || !state.reverseConstraints.some((x) => x.enabled) || state.task)
    return alert("请添加可调变量并启用至少一个目标约束");
  if (state.reverseVariables.some((x) => !Number.isFinite(x.initial) || !Number.isFinite(x.lower) || !Number.isFinite(x.upper) || x.initial < x.lower || x.initial > x.upper || x.lower > x.upper || !Number.isFinite(x.priority)))
    return alert("请检查变量优先级和允许范围");
  setRunning(true);
  state.taskKind = "reverse";
  try {
    const response = await fetch("/api/reverse-calculations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_version_id: state.data.template.id,
          variables: state.reverseVariables,
          adjustments: payload(),
          constraints: state.reverseConstraints,
          max_evaluations: 15,
          engine_mode: $("engineMode").value,
        }),
      }),
      task = await response.json();
    if (!response.ok) throw Error(task.error || "v2 反推提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    renderTrust({ trust: { status: "calculation_failed", reason: error.message, error: error.message } });
  }
}

function currentDraft(module = state.module) {
  return state.persisted.drafts[module];
}

// ticket 34 中栏视图：forward 模块支持 卡片视图/对比视图，视图标志随草稿持久化
function comparisonViewActive() {
  return state.module === "forward" && currentDraft().centerView === "comparison";
}
function setCenterView(view) {
  if (state.module !== "forward") view = "cards";
  currentDraft().centerView = view === "comparison" ? "comparison" : "cards";
  persistWorkbench();
  renderCardPages();
  renderCardGrid();
}

function persistWorkbench() {
  state.persisted.activeModule = state.module;
  state.persisted.favorites = state.favorites;
  state.persisted.shared.engineMode = $("engineMode").value;
  WorkbenchState.save(state.persisted);
}

function applyPaneVisibility() {
  [
    ["left", "leftPane", "toggleLeftPane", "输入参数"],
    ["center", "centerPane", "toggleCenterPane", "年度参数卡片"],
    ["right", "rightPane", "toggleRightPane", "结果与可信状态"],
  ].forEach(([side, paneId, buttonId, label]) => {
    const collapsed = Boolean(state.persisted.shared[`${side}PaneCollapsed`]);
    $("workspace").classList.toggle(`${side}-pane-collapsed`, collapsed);
    $(paneId).classList.toggle("collapsed", collapsed);
    $(buttonId).textContent = collapsed ? "展开" : "隐藏";
    $(buttonId).title = `${collapsed ? "展开" : "隐藏"}${label}`;
    $(`show${side[0].toUpperCase()}${side.slice(1)}Pane`).hidden = !collapsed;
  });
}

function rightPanelHeights() {
  const shared = state.persisted.shared;
  const saved = shared.rightPanelHeights || {};
  return {
    execution: Math.max(100, Number(saved.execution) || 200),
    scenarios: Math.max(72, Number(saved.scenarios) || 120),
  };
}

function applyRightPanelHeights() {
  const heights = rightPanelHeights();
  $("rightPane").style.setProperty("--execution-panel-height", `${heights.execution}px`);
  $("rightPane").style.setProperty("--scenario-panel-height", `${heights.scenarios}px`);
}

function setupRightPanelResize() {
  document.querySelectorAll("[data-right-resize]").forEach((handle) => (handle.onpointerdown = (event) => {
    event.preventDefault();
    handle.setPointerCapture?.(event.pointerId);
    const key = handle.dataset.rightResize;
    const minimum = key === "execution" ? 100 : 72;
    const maximum = key === "execution" ? 420 : 320;
    const start = event.clientY;
    const initial = rightPanelHeights()[key];
    const move = (next) => {
      const height = Math.max(minimum, Math.min(maximum, initial + next.clientY - start));
      state.persisted.shared.rightPanelHeights = {...rightPanelHeights(), [key]: height};
      applyRightPanelHeights();
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
      persistWorkbench();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
  }));
}

function syncReverseDraft() {
  currentDraft().constraints = state.reverseConstraints;
  currentDraft().variables = state.reverseVariables;
  persistWorkbench();
  updateDraftStatus();
}

function loadModuleDraft(module) {
  const draft = currentDraft(module);
  state.edits = isReadOnly() ? {} : draft.edits || {};
  state.reverseConstraints = isReadOnly() ? [] : draft.constraints || [];
  state.reverseVariables = isReadOnly() ? [] : draft.variables || [];
  state.editorOpen = false;
  const selectedId = draft.selected[draft.page * cardsPerPage()] || draft.selected[0];
  if (selectedId && state.data.parameters.some((item) => item.id === selectedId)) select(selectedId);
  else state.selected = null;
  renderNav();
  renderReverseConstraints();
  renderReverseVariables();
  setCalculateEnabled();
  renderCardPages();
  renderCardGrid();
}

function switchModule(module) {
  if (!WorkbenchState.MODULES.includes(module)) return;
  if (!isReadOnly()) {
    currentDraft().edits = state.edits;
    syncReverseDraft();
  }
  state.module = module;
  state.persisted.activeModule = module;
  document.querySelectorAll("[data-module]").forEach((button) => button.classList.toggle("active", button.dataset.module === module));
  const rules = module === "rules";
  $("rulesPane").hidden = !rules;
  $("centerPane").hidden = rules;
  $("forwardMode").hidden = module !== "forward";
  $("calculateTop").hidden = rules;
  $("layoutToggle").hidden = module !== "forward";
  $("centerViewToggle").hidden = module !== "forward";
  $("linkage").hidden = module !== "forward";
  $("leftTitle").textContent = module === "forward" ? "输入参数" : module === "single" ? "变量与约束" : module === "multi" ? "多输入变量与约束" : "规则导航";
  $("centerTitle").textContent = module === "forward" ? "年度参数卡片" : module === "single" ? "单变量求解画布" : module === "multi" ? "多输入求解画布" : "规则维护";
  document.documentElement.style.setProperty("--left-pane", `${state.persisted.paneWidths[module].left}px`);
  document.documentElement.style.setProperty("--right-pane", `${state.persisted.paneWidths[module].right}px`);
  if (!rules) loadModuleDraft(module);
  updateReverseVisibility();
  updateDraftStatus();
  persistWorkbench();
}

function updateReverseVisibility() {
  document.querySelectorAll("details.reverse").forEach((item, index) => {
    item.hidden = isReadOnly() || state.module === "forward" || state.module === "rules" || (state.module === "single" ? index !== 0 : index !== 1);
    item.open = !item.hidden;
  });
  $("constraintBuilder").hidden = isReadOnly() || state.module === "forward" || state.module === "rules";
  const forward = state.module === "forward";
  $("calculate").hidden = !forward;
  $("calculate").onclick = forward ? calculate : null;
  $("calculateTop").textContent = forward ? "执行测算" : "开始求解";
  $("calculateTop").onclick = forward ? calculate : state.module === "single" ? runReverse : runReverseV2;
}

function cardsPerPage() {
  const grid = $("cardGrid");
  if (!grid || !grid.clientWidth || !grid.clientHeight) return 6;
  const columns = Math.max(1, Math.floor((grid.clientWidth - 14) / 250));
  const rows = Math.max(1, Math.floor((grid.clientHeight - 14) / 240));
  return columns * rows;
}
function renderCardPages() {
  if (isReadOnly()) {
    $("cardPages").innerHTML = "";
    return;
  }
  const draft = currentDraft();
  const perPage = cardsPerPage();
  const count = Math.max(1, Math.ceil(draft.selected.length / perPage));
  draft.page = Math.min(draft.page, count - 1);
  $("cardPages").innerHTML = Array.from({ length: count }, (_, index) => `<button class="page-dot ${index === draft.page ? "active" : ""}" data-page="${index}">屏 ${index + 1}</button>`).join("");
  document.querySelectorAll("[data-page]").forEach((button) => (button.onclick = () => {
    draft.page = Number(button.dataset.page);
    const id = draft.selected[draft.page * cardsPerPage()];
    if (id) select(id);
    persistWorkbench();
    renderCardGrid();
  }));
}

function sliderRange(item) {
  const configured = item.rule?.allowed_range;
  if (configured) return configured;
  const values = years
    .map((year) => Number(item.baseline[year]))
    .filter(Number.isFinite);
  if (!values.length) return null;
  const low = Math.min(...values), high = Math.max(...values);
  const pad = Math.max(Math.abs(low), Math.abs(high), 1e-9) * 0.5;
  return [low - pad, high + pad];
}
function sliderStep(item, range) {
  const step = item.rule?.minimum_step;
  if (typeof step === "number" && Number.isFinite(step) && step > 0 && step <= range[1] - range[0]) return step;
  return "any";
}
function trackText(value, unit, precision) {
  return Number.isFinite(value) ? formatResultValue(value, unit, precision) : "—";
}
function deltaText(delta, unit, precision) {
  if (!Number.isFinite(delta) || Math.abs(delta) < 1e-9) return "—";
  return `${delta > 0 ? "+" : ""}${formatResultValue(delta, unit, precision)}`;
}
function forwardCardBody(item) {
  const values = state.edits[item.id] || item.baseline,
    range = canEdit(item) ? sliderRange(item) : null,
    precision = rulePrecision(item),
    activeYears = item.active_years || years;
  return `${item.active_years ? `<div class="single-year-note">全期通用 · 仅维护 ${item.active_years.join("、")}</div>` : ""}<div class="year-tracks">${activeYears.map((year) => {
    const current = Number(values[year] ?? item.baseline[year]),
      base = Number(item.baseline[year]);
    return `<div class="year-track${range ? "" : " locked"}"><label>${year}</label>${range ? `<input class="vertical-range" type="range" data-card-slide="${item.id}:${year}" min="${range[0]}" max="${range[1]}" step="${sliderStep(item, range)}" value="${Number.isFinite(current) ? current : 0}">` : ""}<span class="track-value">${trackText(current, item.unit, precision)}</span><span class="track-base">基准 ${trackText(base, item.unit, precision)}</span><span class="delta">${deltaText(current - base, item.unit, precision)}</span></div>`;
  }).join("")}</div>`;
}
function ensureSingleVariable(item) {
  const draft = currentDraft();
  if (!draft.singleVariable || draft.singleVariable.indicator_id !== item.id) {
    const range = item.rule?.allowed_range || [],
      baseline = Number(item.baseline["2030"]),
      initial = Number.isFinite(baseline) ? baseline : 0,
      derived = [initial * 0.5, initial * 1.5];
    draft.singleVariable = {
      indicator_id: item.id,
      year: "2030",
      initial,
      lower: range[0] ?? Math.min(...derived),
      upper: range[1] ?? Math.max(...derived),
    };
    persistWorkbench();
  }
  return draft.singleVariable;
}
function variableCardBody(item, variable, index) {
  const precision = rulePrecision(item),
    step = Number.isFinite(variable.step) && variable.step > 0 ? variable.step : "any";
  return `<div class="constraint-form"><label>下限<input type="number" data-v2c="lower" data-index="${index}" value="${variable.lower}"></label><label>年份<select data-v2c="year" data-index="${index}">${years.map((year) => `<option ${String(year) === String(variable.year) ? "selected" : ""}>${year}</option>`).join("")}</select></label><label class="constraint-value">上限<input type="number" data-v2c="upper" data-index="${index}" value="${variable.upper}"></label></div><input class="constraint-scale" type="range" data-v2c-slider="${index}" min="${variable.lower}" max="${variable.upper}" step="${step}" value="${variable.initial ?? variable.lower}"><div class="constraint-summary">优先级 <input type="number" min="1" data-v2c="priority" data-index="${index}" value="${variable.priority}"> · 初始值 <span data-v2c-initial>${trackText(Number(variable.initial), item.unit, precision)}</span> · 允许求解器搜索</div><div class="card-foot"><span>相对基准最小偏离</span><span>硬边界</span></div>`;
}
function singleVariableCardBody(item) {
  const config = ensureSingleVariable(item),
    precision = rulePrecision(item);
  return `<div class="constraint-form"><label>下限<input type="number" data-sv="lower" value="${config.lower}"></label><label>年份<select data-sv="year">${years.map((year) => `<option ${String(year) === String(config.year) ? "selected" : ""}>${year}</option>`).join("")}</select></label><label class="constraint-value">上限<input type="number" data-sv="upper" value="${config.upper}"></label></div><input class="constraint-scale" type="range" data-sv-slider min="${config.lower}" max="${config.upper}" step="${sliderStep(item, [Number(config.lower), Number(config.upper)])}" value="${config.initial}"><div class="constraint-summary">初始值 <span data-sv-initial>${trackText(Number(config.initial), item.unit, precision)}</span> · 允许求解器搜索</div><div class="card-foot"><span>相对基准最小偏离</span><span>搜索边界</span></div>`;
}
function constraintSummaryText(constraint) {
  const item = state.data.parameters.find((parameter) => parameter.id === constraint.indicator_id),
    relation = constraint.relation && constraint.relation !== "between"
      ? constraint.relation
      : constraint.kind === "min" ? "≥" : constraint.kind === "max" ? "≤" : "=";
  return `${constraint.year} ${relation} ${trackText(Number(constraint.value), item?.unit || "", item ? rulePrecision(item) : undefined)} · ${constraint.hard ? "硬约束" : "软目标"}${constraint.enabled === false ? " · 已停用" : ""}`;
}
function constraintCardBody(constraint, index) {
  const item = state.data.parameters.find((parameter) => parameter.id === constraint.indicator_id),
    range = item && canEdit(item) ? sliderRange(item) : null,
    relation = constraint.relation && constraint.relation !== "between"
      ? constraint.relation
      : constraint.kind === "min" ? "≥" : constraint.kind === "max" ? "≤" : "=";
  return `<div class="constraint-form"><label>年份<select data-cc="year" data-index="${index}">${years.map((year) => `<option ${String(year) === String(constraint.year) ? "selected" : ""}>${year}</option>`).join("")}</select></label><label>关系<select data-cc="kind" data-index="${index}"><option value="gt" ${relation === ">" ? "selected" : ""}>&gt;</option><option value="min" ${relation === "≥" ? "selected" : ""}>≥</option><option value="target" ${relation === "=" ? "selected" : ""}>=</option><option value="max" ${relation === "≤" ? "selected" : ""}>≤</option><option value="lt" ${relation === "<" ? "selected" : ""}>&lt;</option></select></label><label class="constraint-value">目标值<input type="number" data-cc="value" data-index="${index}" value="${constraint.value}"></label></div>${range ? `<input class="constraint-scale" type="range" data-cc-slider="${index}" min="${range[0]}" max="${range[1]}" step="${sliderStep(item, range)}" value="${constraint.value}">` : ""}<div class="constraint-summary">${constraintSummaryText(constraint)}</div><div class="card-foot"><button data-cc-hard="${index}">${constraint.hard ? "切换为软目标" : "切换为硬约束"}</button><label><input type="checkbox" data-cc-enable="${index}" ${constraint.enabled !== false ? "checked" : ""}> 启用</label></div>`;
}
function placeholderCardBody(item) {
  if (state.module !== "multi") return "<p>选择后可添加为变量或约束</p>";
  const editable = canEdit(item);
  return `<div class="placeholder-actions"><p>该指标尚未加入求解</p><button class="primary" data-add-variable="${item.id}" ${editable ? "" : 'disabled title="仅已确认输入指标可作变量"'}>添加为变量</button><button data-add-constraint="${item.id}">添加为约束</button></div>`;
}
function focusConstraintBuilderFor(item) {
  $("cbSearch").value = "";
  renderConstraintMetrics();
  $("cbMetric").value = `input|${item.id}|${item.name}`;
  $("constraintBuilder").scrollIntoView({ block: "nearest" });
  $("cbRelation").focus();
}
function parameterCard(id) {
  const item = state.data.parameters.find((parameter) => parameter.id === id);
  if (!item) return "";
  const variableIndex = state.reverseVariables.findIndex((entry) => entry.indicator_id === id),
    constraintIndex = state.reverseConstraints.findIndex((entry) => entry.indicator_id === id || entry.indicator_name === item.name),
    constraintGroup = constraintIndex >= 0 ? constraintGroups().find((group) => group.indexes.includes(constraintIndex)) : null,
    singleVariable = state.module === "single" && variableIndex < 0 && state.selected?.id === id,
    kind = state.module === "forward" ? "" : variableIndex >= 0 || singleVariable ? "variable" : constraintIndex >= 0 ? "constraint" : "";
  const body = state.module === "forward"
    ? forwardCardBody(item)
    : variableIndex >= 0
      ? variableCardBody(item, state.reverseVariables[variableIndex], variableIndex)
      : singleVariable
        ? singleVariableCardBody(item)
        : constraintIndex >= 0
          ? constraintGroup.records.length > 1
            ? constraintGroupCardBody(constraintGroup)
            : constraintCardBody(state.reverseConstraints[constraintIndex], constraintIndex)
          : placeholderCardBody(item);
  const subtitle = kind === "variable" ? `搜索范围 · ${item.unit || ""}` : kind === "constraint" ? `约束目标 · ${item.unit || ""}` : `${item.group} · ${item.unit || ""}`;
  return `<article class="work-card ${kind} ${state.selected?.id === id ? "selected" : ""}" draggable="true" data-card="${id}"><div class="work-card-head">${kind ? `<span class="card-kind">${kind === "variable" ? "变量" : "约束"}</span>` : ""}<div><h3 title="${item.name}">${item.name}</h3><small>${subtitle}</small></div><span class="spacer"></span><button data-open-card="${id}">高级</button><button data-remove-card="${id}">×</button></div><div class="work-card-body">${body}</div><div class="work-card-actions"><details class="card-sort-menu"><summary>排序</summary><div><button data-move="${id}|-1">前移</button><button data-move="${id}|1">后移</button></div></details><span class="spacer"></span><button data-reset-card="${id}">恢复基准</button></div></article>`;
}
function orphanConstraintCard(constraint, index) {
  return `<article class="work-card constraint" data-con-card="${index}"><div class="work-card-head"><span class="card-kind">约束</span><div><h3>${constraint.indicator_name}</h3><small>约束目标 · 未选指标</small></div><span class="spacer"></span><button data-remove-constraint="${index}">×</button></div><div class="work-card-body">${constraintCardBody(constraint, index)}</div></article>`;
}
function orphanGroupConstraintCard(group) {
  return `<article class="work-card constraint" data-con-group="${group.key}"><div class="work-card-head"><span class="card-kind">约束</span><div><h3>${group.records[0].indicator_name}</h3><small>约束目标 · 未选指标 · ${group.records.length} 条记录</small></div><span class="spacer"></span><button data-cg-remove="${group.key}">×</button></div><div class="work-card-body">${constraintGroupCardBody(group)}</div></article>`;
}
function guardCardDrag(el) {
  const card = el.closest(".work-card");
  if (!card) return;
  el.addEventListener("mousedown", () => {
    card.draggable = false;
    addEventListener("mouseup", () => { card.draggable = true; }, { once: true });
  });
}
function changeCardYear(el, commit) {
  const raw = el.dataset.cardSlide,
    sep = raw.lastIndexOf(":"),
    id = raw.slice(0, sep),
    year = Number(raw.slice(sep + 1)),
    item = state.data.parameters.find((parameter) => parameter.id === id),
    value = Number(el.value);
  if (!item || !Number.isFinite(value) || !canEdit(item)) return;
  const configured = item.rule.allowed_range;
  if (configured && (value < configured[0] || value > configured[1])) return;
  const base = { ...(state.edits[id] || item.baseline) };
  applyYearValue(item, base, year, value, item.rule.linkage_strategy || "independent");
  state.edits[id] = base;
  currentDraft().edits = state.edits;
  const card = el.closest(".work-card");
  if (card) refreshCardTracks(card, item);
  renderNav();
  setCalculateEnabled();
  persistWorkbench();
  updateDraftStatus();
  scheduleAutomaticCalculation();
  if (commit) renderCardGrid();
}
function refreshCardTracks(card, item) {
  const values = state.edits[item.id] || item.baseline,
    precision = rulePrecision(item);
  card.querySelectorAll("[data-card-slide]").forEach((input) => {
    const raw = input.dataset.cardSlide,
      year = Number(raw.slice(raw.lastIndexOf(":") + 1)),
      current = Number(values[year] ?? item.baseline[year]),
      base = Number(item.baseline[year]);
    input.value = Number.isFinite(current) ? current : 0;
    const track = input.closest(".year-track");
    track.querySelector(".track-value").textContent = trackText(current, item.unit, precision);
    track.querySelector(".delta").textContent = deltaText(current - base, item.unit, precision);
  });
}
function bindCardConfigEvents() {
  document.querySelectorAll("[data-card-slide]").forEach((el) => {
    guardCardDrag(el);
    el.oninput = () => changeCardYear(el, false);
    el.onchange = () => changeCardYear(el, true);
  });
  document.querySelectorAll("[data-v2c]").forEach((el) => (el.onchange = () => {
    const variable = state.reverseVariables[Number(el.dataset.index)];
    if (!variable) return;
    variable[el.dataset.v2c] = el.dataset.v2c === "year" ? el.value : Number(el.value);
    syncReverseDraft();
    renderReverseVariables();
    renderCardGrid();
  }));
  document.querySelectorAll("[data-v2c-slider]").forEach((el) => {
    guardCardDrag(el);
    el.oninput = () => {
      const variable = state.reverseVariables[Number(el.dataset.v2cSlider)];
      if (!variable) return;
      variable.initial = Number(el.value);
      const item = state.data.parameters.find((parameter) => parameter.id === variable.indicator_id),
        summary = el.closest(".work-card")?.querySelector("[data-v2c-initial]");
      if (summary) summary.textContent = trackText(variable.initial, item?.unit || "", item ? rulePrecision(item) : undefined);
      syncReverseDraft();
    };
    el.onchange = () => { syncReverseDraft(); renderReverseVariables(); renderCardGrid(); };
  });
  document.querySelectorAll("[data-sv]").forEach((el) => (el.onchange = () => {
    const config = currentDraft().singleVariable;
    if (!config) return;
    config[el.dataset.sv] = el.dataset.sv === "year" ? el.value : Number(el.value);
    persistWorkbench();
    renderCardGrid();
  }));
  document.querySelectorAll("[data-sv-slider]").forEach((el) => {
    guardCardDrag(el);
    el.oninput = () => {
      const config = currentDraft().singleVariable;
      if (!config) return;
      config.initial = Number(el.value);
      const item = state.data.parameters.find((parameter) => parameter.id === config.indicator_id),
        summary = el.closest(".work-card")?.querySelector("[data-sv-initial]");
      if (summary) summary.textContent = trackText(config.initial, item?.unit || "", item ? rulePrecision(item) : undefined);
      persistWorkbench();
    };
    el.onchange = () => { persistWorkbench(); renderCardGrid(); };
  });
  document.querySelectorAll("[data-cc]").forEach((el) => (el.onchange = () => {
    const constraint = state.reverseConstraints[Number(el.dataset.index)];
    if (!constraint) return;
    if (el.dataset.cc === "kind") {
      const map = { gt: ["min", ">"], min: ["min", "≥"], target: ["target", "="], max: ["max", "≤"], lt: ["max", "<"] },
        mapped = map[el.value];
      if (mapped) [constraint.kind, constraint.relation] = mapped;
      else constraint.kind = el.value;
    } else {
      constraint[el.dataset.cc] = el.dataset.cc === "value" ? Number(el.value) : el.value;
    }
    syncReverseDraft();
    renderReverseConstraints();
    renderCardGrid();
  }));
  document.querySelectorAll("[data-cc-slider]").forEach((el) => {
    guardCardDrag(el);
    el.oninput = () => {
      const constraint = state.reverseConstraints[Number(el.dataset.ccSlider)];
      if (!constraint) return;
      constraint.value = Number(el.value);
      const card = el.closest(".work-card"),
        input = card?.querySelector('[data-cc="value"]'),
        summary = card?.querySelector(".constraint-summary");
      if (input) input.value = el.value;
      if (summary) summary.textContent = constraintSummaryText(constraint);
      syncReverseDraft();
    };
    el.onchange = () => { syncReverseDraft(); renderReverseConstraints(); renderCardGrid(); };
  });
  document.querySelectorAll("[data-cc-hard]").forEach((el) => (el.onclick = () => {
    const constraint = state.reverseConstraints[Number(el.dataset.ccHard)];
    if (!constraint) return;
    constraint.hard = !constraint.hard;
    syncReverseDraft();
    renderReverseConstraints();
    renderCardGrid();
  }));
  document.querySelectorAll("[data-cc-enable]").forEach((el) => (el.onchange = () => {
    const constraint = state.reverseConstraints[Number(el.dataset.ccEnable)];
    if (!constraint) return;
    constraint.enabled = el.checked;
    syncReverseDraft();
    renderReverseConstraints();
    renderCardGrid();
  }));
  document.querySelectorAll("[data-remove-constraint]").forEach((el) => (el.onclick = () => {
    if (!confirm("确认移除该约束？")) return;
    state.reverseConstraints.splice(Number(el.dataset.removeConstraint), 1);
    syncReverseDraft();
    renderReverseConstraints();
    renderCardGrid();
  }));
  bindConstraintGroupEvents();
}
function renderCardGrid() {
  const comparisonMode = comparisonViewActive();
  document.querySelectorAll("[data-center-view]").forEach((button) => button.classList.toggle("active", button.dataset.centerView === (comparisonMode ? "comparison" : "cards")));
  $("comparisonCanvas").hidden = !comparisonMode;
  if (state.module === "forward") $("centerTitle").textContent = comparisonMode ? "对比视图" : "年度参数卡片";
  if (comparisonMode) {
    $("editor").hidden = true;
    $("editorEmpty").hidden = true;
    $("cardGrid").hidden = true;
    $("cardPages").innerHTML = "";
    renderComparisonCanvas();
    return;
  }
  if (isReadOnly()) {
    $("cardGrid").hidden = true;
    return;
  }
  const draft = currentDraft();
  const perPage = cardsPerPage();
  const ids = draft.selected.slice(draft.page * perPage, draft.page * perPage + perPage);
  const orphanGroups = state.module === "forward"
    ? []
    : constraintGroups().filter((group) => !ids.some((id) => {
        const item = state.data.parameters.find((parameter) => parameter.id === id);
        return item && group.records.some((constraint) => constraint.indicator_id === id || constraint.indicator_name === item.name);
      }));
  const hasCards = Boolean(ids.length || orphanGroups.length);
  $("cardGrid").className = `card-grid layout-${draft.cardLayout}`;
  $("editor").hidden = !state.editorOpen;
  $("cardGrid").hidden = state.editorOpen || !hasCards;
  $("editorEmpty").hidden = state.editorOpen || hasCards;
  $("cardGrid").innerHTML =
    ids.map((id) => parameterCard(id)).join("") +
    orphanGroups.map((group) =>
      group.records.length > 1
        ? orphanGroupConstraintCard(group)
        : orphanConstraintCard(group.records[0], group.indexes[0]),
    ).join("");
  document.querySelectorAll("[data-open-card]").forEach((button) => (button.onclick = () => {
    state.editorOpen = true;
    select(button.dataset.openCard);
    $("cardGrid").hidden = true;
    $("editorEmpty").hidden = true;
    $("editor").hidden = false;
  }));
  document.querySelectorAll("[data-remove-card]").forEach((button) => (button.onclick = () => removeCard(button.dataset.removeCard)));
  document.querySelectorAll("[data-reset-card]").forEach((button) => (button.onclick = () => {
    delete state.edits[button.dataset.resetCard];
    currentDraft().edits = state.edits;
    persistWorkbench(); renderCardGrid(); renderNav(); setCalculateEnabled(); updateDraftStatus();
    scheduleAutomaticCalculation();
  }));
  document.querySelectorAll("[data-move]").forEach((button) => (button.onclick = () => {
    const [id, direction] = button.dataset.move.split("|");
    moveCard(id, Number(direction));
  }));
  document.querySelectorAll("[data-card]").forEach((card) => {
    card.ondragstart = (event) => event.dataTransfer.setData("text/plain", card.dataset.card);
    card.ondragover = (event) => event.preventDefault();
    card.ondrop = (event) => {
      event.preventDefault();
      const source = event.dataTransfer.getData("text/plain"), target = card.dataset.card;
      const sourceIndex = draft.selected.indexOf(source), targetIndex = draft.selected.indexOf(target);
      if (sourceIndex >= 0 && targetIndex >= 0) {
        draft.selected.splice(sourceIndex, 1); draft.selected.splice(targetIndex, 0, source);
        draft.cardOrder = [...draft.selected]; persistWorkbench(); renderCardGrid();
      }
    };
  });
  document.querySelectorAll("[data-add-variable]").forEach((button) => (button.onclick = () => {
    const item = state.data.parameters.find((x) => x.id === button.dataset.addVariable);
    if (item) addReverseVariable(item);
  }));
  document.querySelectorAll("[data-add-constraint]").forEach((button) => (button.onclick = () => {
    const item = state.data.parameters.find((x) => x.id === button.dataset.addConstraint);
    if (item) focusConstraintBuilderFor(item);
  }));
  bindCardConfigEvents();
  setCalculateEnabled();
}

function removeCard(id) {
  const dirty = Boolean(state.edits[id] || state.reverseVariables.some((item) => item.indicator_id === id) || state.reverseConstraints.some((item) => item.indicator_id === id));
  if (dirty && !confirm("该卡片包含未保存编辑、变量或约束。确认移除？")) return;
  const draft = currentDraft();
  draft.selected = draft.selected.filter((item) => item !== id);
  draft.cardOrder = [...draft.selected];
  delete state.edits[id];
  state.reverseVariables = state.reverseVariables.filter((item) => item.indicator_id !== id);
  state.reverseConstraints = state.reverseConstraints.filter((item) => item.indicator_id !== id);
  syncReverseDraft(); renderCardPages(); renderCardGrid(); renderNav();
}

function moveCard(id, direction) {
  const draft = currentDraft(), index = draft.selected.indexOf(id), next = index + direction;
  if (index < 0 || next < 0 || next >= draft.selected.length) return;
  [draft.selected[index], draft.selected[next]] = [draft.selected[next], draft.selected[index]];
  draft.cardOrder = [...draft.selected]; persistWorkbench(); renderCardGrid();
}

function updateDraftStatus() {
  const dirty = WorkbenchState.isDirty(currentDraft()) || currentDraft().calculatedUnsaved;
  $("draftStatus").textContent = dirty ? `● ${state.persisted.restored ? "已恢复的" : ""}未保存草稿` : "";
}

function filteredResultRows() {
  return state.data?.result_rows || [];
}

async function protectDraftBeforeSwitch() {
  if (!(WorkbenchState.isDirty(currentDraft()) || currentDraft().calculatedUnsaved)) return true;
  const choice = prompt("当前存在未保存草稿。输入 save 保存为场景并切换，discard 丢弃并切换，或 cancel 取消。", "cancel");
  if (choice === "save") {
    $("scenarioName").value ||= `未命名草稿 ${new Date().toLocaleString("zh-CN")}`;
    return await saveScenario();
  }
  if (choice === "discard") {
    currentDraft().edits = {}; currentDraft().variables = []; currentDraft().constraints = []; currentDraft().calculatedUnsaved = false; currentDraft().singleVariable = null;
    state.edits = {}; state.reverseVariables = []; state.reverseConstraints = []; persistWorkbench();
    return true;
  }
  return false;
}

function scheduleAutomaticCalculation() {
  if (state.module !== "forward" || state.persisted.shared.forwardMode !== "auto" || $("engineMode").value !== "warm_com" || state.warmHealthy !== true) return;
  clearTimeout(state.autoTimer);
  state.autoTimer = setTimeout(() => {
    if (state.task) state.newestDraftPending = true;
    else calculate();
  }, 500);
}

function setupPaneResize() {
  document.querySelectorAll("[data-resize]").forEach((handle) => {
    handle.ondblclick = () => {
      WorkbenchState.resetPanes(state.persisted, state.module);
      switchModule(state.module);
    };
    handle.onmousedown = (event) => {
      event.preventDefault();
      const side = handle.dataset.resize;
      const start = event.clientX;
      const widths = state.persisted.paneWidths[state.module];
      const initial = widths[side];
      const move = (next) => {
        widths[side] = side === "left" ? Math.max(190, Math.min(420, initial + next.clientX - start)) : Math.max(300, Math.min(720, initial - next.clientX + start));
        document.documentElement.style.setProperty(side === "left" ? "--left-pane" : "--right-pane", `${widths[side]}px`);
      };
      const up = () => {
        removeEventListener("mousemove", move);
        removeEventListener("mouseup", up);
        persistWorkbench();
      };
      addEventListener("mousemove", move);
      addEventListener("mouseup", up);
    };
  });
}

function initializeUnifiedWorkbench() {
  document.querySelectorAll("[data-module]").forEach((button) => (button.onclick = () => switchModule(button.dataset.module)));
  document.querySelectorAll("[data-mobile-pane]").forEach((button) => (button.onclick = () => {
    state.persisted.mobilePane = button.dataset.mobilePane;
    $("workspace").dataset.mobilePane = button.dataset.mobilePane;
    document.querySelectorAll("[data-mobile-pane]").forEach((item) => item.classList.toggle("active", item.dataset.mobilePane === button.dataset.mobilePane));
    persistWorkbench();
  }));
  document.querySelectorAll("[data-layout]").forEach((button) => (button.onclick = () => {
    currentDraft().cardLayout = button.dataset.layout;
    document.querySelectorAll("[data-layout]").forEach((item) => item.classList.toggle("active", item === button));
    if (state.selected) renderYears();
    renderCardGrid();
    persistWorkbench();
  }));
  document.querySelectorAll("[data-center-view]").forEach((button) => (button.onclick = () => setCenterView(button.dataset.centerView)));
  document.querySelectorAll("[data-mode]").forEach((button) => (button.onclick = async () => {
    if (button.dataset.mode === "auto" && state.warmHealthy !== true) {
      await recheckWorkerHealth();
      if (state.warmHealthy !== true) {
        $("workspaceNotice").hidden = false;
        $("workspaceNotice").textContent = "warm 热启动不可用，无法启用自动模式。";
        return;
      }
    }
    state.persisted.shared.forwardMode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item === button));
    persistWorkbench();
  }));
  $("engineMode").onchange = () => {
    state.persisted.shared.engineMode = $("engineMode").value;
    $("engineMeta").textContent = `Excel COM / ${$("engineMode").value.replace("_com", "")}`;
    persistWorkbench();
  };
  [["left", "toggleLeftPane"], ["center", "toggleCenterPane"], ["right", "toggleRightPane"]].forEach(([side, buttonId]) => {
    $(buttonId).onclick = () => {
      state.persisted.shared[`${side}PaneCollapsed`] = !state.persisted.shared[`${side}PaneCollapsed`];
      applyPaneVisibility();
      persistWorkbench();
      renderCardPages();
      renderCardGrid();
    };
  });
  [["left", "showLeftPane"], ["center", "showCenterPane"], ["right", "showRightPane"]].forEach(([side, buttonId]) => {
    $(buttonId).onclick = () => {
      state.persisted.shared[`${side}PaneCollapsed`] = false;
      applyPaneVisibility();
      persistWorkbench();
      renderCardPages();
      renderCardGrid();
    };
  });
  $("saveDisplayDefaults").onclick = async () => {
    const response = await fetch("/api/display-defaults", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({inputs: state.persisted.drafts.forward.selected, outputs: currentDraft("forward").outputSelection})});
    const data = await response.json();
    if (!response.ok) return alert(data.error || "需要管理员登录");
    alert("全局默认展示项已保存");
  };
  $("workspace").dataset.mobilePane = state.persisted.mobilePane;
  applyPaneVisibility();
  applyRightPanelHeights();
  setupPaneResize();
  setupRightPanelResize();
  switchModule(state.module);
}
const SCENARIO_TYPE_LABELS = {
  baseline: "基准",
  optimistic: "乐观",
  pessimistic: "悲观",
  custom: "自定义",
  reverse_result: "反向求解",
};
function unitForIndicator(name) {
  const param = (state.data?.parameters || []).find((p) => p.name === name);
  if (param?.unit) return param.unit;
  return (state.data?.details || []).find((d) => d.name === name)?.unit;
}
async function loadScenarios() {
  try {
    const response = await fetch("/api/scenarios");
    const data = await response.json();
    state.scenarios = data.scenarios || [];
    renderScenarios();
    const restoredScenario = state.persisted.shared.scenarioId;
    if (restoredScenario && !state.scenarioRestored && state.scenarios.some((item) => item.scenario_id === restoredScenario)) {
      state.scenarioRestored = true;
      await restoreScenarioContext(restoredScenario);
    }
  } catch {
    /* 场景服务不可用时不阻塞工作台 */
  }
}

async function restoreScenarioContext(id) {
  const response = await fetch(`/api/scenarios/${id}`), scenario = await response.json();
  if (!response.ok) return;
  $("scenarioMeta").textContent = scenario.name;
  state.edits = {};
  Object.entries(scenario.input_adjustments || {}).forEach(([key, values]) => {
    const item = state.data.parameters.find((parameter) => parameter.id === key);
    if (item && canEdit(item)) state.edits[key] = { ...values };
  });
  currentDraft().edits = state.edits;
  currentDraft().calculatedUnsaved = false;
  persistWorkbench(); renderNav(); renderCardGrid(); updateDraftStatus();
}
function renderScenarios() {
  $("scenarioList").innerHTML =
    (state.scenarios || [])
      .map(
        (sc) =>
          `<div class="scenario"><div class="scenario-head"><label class="scenario-select"><input type="checkbox" data-compare="${sc.scenario_id}" ${state.comparisonSelection[sc.scenario_id] ? "checked" : ""}><strong>${sc.name}</strong></label><span class="badge ${sc.validation_state === "valid" ? "valid" : "pending"}">${sc.validation_state || "未计算"}</span></div><small>${SCENARIO_TYPE_LABELS[sc.scenario_type] || sc.scenario_type} · ${sc.adjustment_count} 项调整 · ${(sc.updated_at || "").slice(0, 19).replace("T", " ")}${sc.read_only ? " · 历史只读" : ""}</small><div class="scenario-actions"><label><input type="radio" name="comparisonBaseline" data-baseline="${sc.scenario_id}" ${state.comparisonBaseline === sc.scenario_id ? "checked" : ""}> 基准</label><button data-open="${sc.scenario_id}">打开</button><button data-copy="${sc.scenario_id}">复制</button><button data-rename="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>重命名</button><button data-recalc="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>重算</button><button data-delete="${sc.scenario_id}" ${sc.read_only ? "disabled" : ""}>删除</button></div></div>`,
      )
      .join("") || '<div class="empty">尚无命名场景</div>';
  document.querySelectorAll("[data-compare]").forEach(
    (el) =>
      (el.onchange = () => {
        state.comparisonSelection[el.dataset.compare] = el.checked;
        if (el.checked && !state.comparisonBaseline)
          state.comparisonBaseline = el.dataset.compare;
        renderScenarios();
      }),
  );
  document.querySelectorAll("[data-baseline]").forEach(
    (el) =>
      (el.onchange = () => {
        state.comparisonBaseline = el.dataset.baseline;
        state.comparisonSelection[el.dataset.baseline] = true;
        renderScenarios();
      }),
  );
  document
    .querySelectorAll("[data-open]")
    .forEach((el) => (el.onclick = () => openScenario(el.dataset.open)));
  document
    .querySelectorAll("[data-copy]")
    .forEach((el) => (el.onclick = () => copyScenario(el.dataset.copy)));
  document
    .querySelectorAll("[data-rename]")
    .forEach((el) => (el.onclick = () => renameScenario(el.dataset.rename)));
  document
    .querySelectorAll("[data-recalc]")
    .forEach(
      (el) => (el.onclick = () => recalculateScenario(el.dataset.recalc)),
    );
  document
    .querySelectorAll("[data-delete]")
    .forEach((el) => (el.onclick = () => deleteScenario(el.dataset.delete)));
}
async function startComparison() {
  const scenario_ids = Object.keys(state.comparisonSelection).filter(
    (id) => state.comparisonSelection[id],
  );
  if (scenario_ids.length < 2) return alert("请至少选择两个场景");
  if (state.task) return alert("已有测算在进行中");
  setRunning(true);
  state.taskKind = "comparison";
  try {
    const response = await fetch("/api/comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_ids,
        baseline_scenario_id: state.comparisonBaseline || scenario_ids[0],
        force_refresh: $("comparisonForce").checked,
        engine_mode: $("engineMode").value,
      }),
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "对比提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    alert(error.message);
  }
}
function renderComparison(data) {
  $("exportComparison").disabled = false;
  renderTrust(data);
  setCards(
    data.core_results
      .map(
        (card) =>
          `<article class="card comparison-card"><h3>${card.name}</h3>${card.scenarios.map((sc) => `<div class="scenario-value ${sc.values ? "" : "comparison-failure"}"><strong>${sc.name}</strong>${sc.values ? `${formatResultValue(sc.values["2030"], card.unit) || "—"} <small>Δ ${formatResultValue(sc.differences?.["2030"], card.unit) || "—"}</small>` : "无有效结果"}</div>`).join("")}</article>`,
      )
      .join("") || '<div class="empty">没有可对比的核心指标</div>',
  );
  renderComparisonDetails();
  renderTrace(data.calculation_details);
}
async function exportExcel(kind, payload) {
  if (!payload) return alert("当前没有可导出的结果");
  const response = await fetch(`/api/exports/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "导出失败");
  window.location.href = data.download_url;
}
function exportCurrentScenario() {
  const draft = state.data?.scenario_draft;
  if (!draft?.calculation_result_snapshot)
    return alert("请先完成正向测算");
  exportExcel("scenario", {
    ...state.data,
    metadata: {
      ...draft,
      template_fingerprint: state.data.template.fingerprint,
      scenario_id: draft.scenario_id || "current",
      calculation_time: state.data.calculation_details?.finished_at,
    },
  });
}
function exportReverseResult() {
  const result = state.reverseResult;
  if (!result) return alert("请先完成反向求解");
  exportExcel("reverse", {
    ...result,
    metadata: {
      ...result.scenario_draft,
      template_fingerprint: state.data.template.fingerprint,
      scenario_id: result.scenario_draft?.scenario_id || `reverse:${result.calculation_details.calculation_id}`,
      calculation_time: result.calculation_details.finished_at,
    },
  });
}
function renderComparisonDetails() {
  if (!state.comparison) return;
  const query = $("detailSearch").value.toLowerCase();
  $("details").innerHTML = state.comparison.details
    .filter(
      (metric) =>
        !query || metric.name.toLowerCase().includes(query),
    )
    .map(
      (metric) =>
        `<div class="comparison-detail"><strong>${metric.name} <small>· ${metric.group}</small></strong>${metric.scenarios
          .map((sc) => {
            const visibleYears = years.map(String);
            return `<div class="comparison-detail-row"><span>${sc.name}</span><span>${sc.values ? visibleYears.map((item) => `${item} ${sc.values[item] ?? "—"} (Δ ${sc.differences?.[item] ?? "—"})`).join(" · ") : "无有效结果"}</span></div>`;
          })
          .join("")}</div>`,
    )
    .join("");
}

// ticket 34 中栏对比画布：摘要条 + 区1 指标结果对比表 + 区2 输入参数差异表
function comparisonParticipants() {
  const scenarios = state.comparison?.scenarios || [];
  const baseline = scenarios.find((sc) => sc.scenario_id === state.comparison.baseline_scenario_id) || scenarios[0] || null;
  return { baseline, others: scenarios.filter((sc) => sc !== baseline) };
}
function comparisonSnapshot(scenario) {
  return scenario?.status === "succeeded" && scenario.calculation_result_snapshot ? scenario.calculation_result_snapshot : null;
}
// 差异判定沿用 resultRowChanged 的 1e-9 相对误差思路
function comparisonValuesDiffer(a, b) {
  const x = Number(a), y = Number(b);
  return Number.isFinite(x) && Number.isFinite(y) && Math.abs(x - y) > 1e-9 * Math.max(1, Math.abs(x), Math.abs(y));
}
function renderComparisonCanvas() {
  const canvas = $("comparisonCanvas");
  if (!state.comparison) {
    canvas.innerHTML = '<div class="empty">尚无对比结果：在右栏"命名场景"勾选场景并点击"开始对比"</div>';
    return;
  }
  const { baseline, others } = comparisonParticipants();
  const current = others.find((sc) => sc.scenario_id === state.comparisonViewScenario) || others[0] || null;
  state.comparisonViewScenario = current?.scenario_id || null;
  const summary = state.comparison.summary || {};
  canvas.innerHTML =
    `<div class="comparison-summary"><span>基准场景 <strong>${baseline?.name || "—"}</strong></span><span>对比场景 ${
      others.length > 1
        ? `<select id="comparisonViewScenario">${others.map((sc) => `<option value="${sc.scenario_id}" ${sc === current ? "selected" : ""}>${sc.name}</option>`).join("")}</select>`
        : `<strong>${current?.name || "—"}</strong>`
    }</span><span class="badge ${summary.failed ? "failed" : "valid"}">${summary.valid ?? "?"}/${summary.total ?? "?"} 场景有效</span></div>` +
    `<section class="comparison-section"><h3>指标结果对比<small>基准值 → 对比值，差异标注 Δ（负红正绿）</small></h3>${comparisonResultTable(baseline, current)}</section>` +
    `<section class="comparison-section"><h3>输入参数差异<small>仅列有差异的输入项</small></h3>${comparisonInputDiffTable(baseline, current)}</section>`;
  const selector = $("comparisonViewScenario");
  if (selector) selector.oninput = () => {
    state.comparisonViewScenario = selector.value;
    renderComparisonCanvas();
  };
}
// 区 1：行沿用 result_rows 的分节与原序（节标题只读展示不折叠），列 = 2025-2030 + 五年变化 + CAGR
function comparisonResultTable(baseline, current) {
  const columns = ["2025", "2026", "2027", "2028", "2029", "2030", "five_year_change", "cagr"];
  const labels = ["指标", "2025", "2026", "2027", "2028", "2029", "2030", "五年变化", "CAGR"];
  const baseSnapshot = comparisonSnapshot(baseline), currentSnapshot = comparisonSnapshot(current);
  const rows = (state.data?.result_rows || [])
    .map((row) => {
      if (row.kind === "header")
        return `<tr class="section-head level-${row.level}"><td colspan="${labels.length}">${row.title}</td></tr>`;
      const indent = /^(其中|——|\s)/.test(row.name) ? " indent" : "";
      const cells = columns.map((column) => comparisonResultCell(baseSnapshot?.[row.name], currentSnapshot?.[row.name], column, column === "cagr" ? "%" : row.unit, row.precision)).join("");
      return `<tr><td title="${row.name}"><strong class="name${indent}">${row.name}</strong></td>${cells}</tr>`;
    })
    .join("");
  return `<div class="result-scroll"><table class="result-table comparison-table"><colgroup><col style="width:150px">${columns.map(() => '<col style="width:124px">').join("")}</colgroup><thead><tr>${labels.map((label) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
function comparisonResultCell(baseValues, currentValues, column, unit, precision) {
  if (!baseValues || !currentValues) return '<td class="comparison-missing">无有效结果</td>';
  const base = baseValues[column], current = currentValues[column];
  if (!comparisonValuesDiffer(base, current))
    return `<td>${formatResultValue(base, unit, precision) || "—"} → ${formatResultValue(current, unit, precision) || "—"}</td>`;
  const delta = Number(current) - Number(base);
  return `<td class="changed">${formatResultValue(base, unit, precision)} → ${formatResultValue(current, unit, precision)}<small class="cmp-delta ${delta < 0 ? "negative" : "positive"}">Δ ${deltaText(delta, unit, precision)}</small></td>`;
}
// 区 2：只列有差异的输入项；场景未调整的输入按参数基准值处理
function comparisonInputDiffTable(baseline, current) {
  const baseAdjustments = baseline?.input_adjustments || {}, currentAdjustments = current?.input_adjustments || {};
  const entries = [];
  (state.data?.parameters || []).forEach((item) => {
    years.forEach((year) => {
      const key = String(year);
      const base = baseAdjustments[item.id]?.[key] ?? item.baseline?.[key];
      const adjusted = currentAdjustments[item.id]?.[key] ?? item.baseline?.[key];
      if (comparisonValuesDiffer(base, adjusted))
        entries.push({ item, year: key, base: Number(base), adjusted: Number(adjusted) });
    });
  });
  if (!entries.length) return '<div class="empty">输入参数完全一致</div>';
  const rows = entries
    .map(({ item, year, base, adjusted }) => {
      const precision = rulePrecision(item), delta = adjusted - base;
      return `<tr><td title="${item.name}"><strong class="name">${item.name}</strong></td><td>${year}</td><td>${formatResultValue(base, item.unit, precision)}</td><td>${formatResultValue(adjusted, item.unit, precision)}</td><td class="cmp-delta ${delta < 0 ? "negative" : "positive"}">${deltaText(delta, item.unit, precision)}</td></tr>`;
    })
    .join("");
  return `<div class="result-scroll"><table class="result-table comparison-input-table"><colgroup><col style="width:180px"><col style="width:56px"><col style="width:110px"><col style="width:110px"><col style="width:96px"></colgroup><thead><tr>${["指标", "年份", "基准值", "对比值", "Δ"].map((label) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
async function saveScenario() {
  if (isReadOnly()) { alert("历史模板只读，不能保存新场景"); return false; }
  const name = $("scenarioName").value.trim();
  if (!name) { alert("请输入场景名称"); return false; }
  const draft = state.data?.scenario_draft || {};
  const response = await fetch("/api/scenarios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      scenario_type: $("scenarioType").value,
      ...draft,
    }),
  });
  const data = await response.json();
  if (!response.ok) { alert(data.error || "保存失败"); return false; }
  state.persisted.shared.scenarioId = data.scenario_id;
  currentDraft().calculatedUnsaved = false;
  $("scenarioMeta").textContent = data.name || name;
  persistWorkbench(); updateDraftStatus();
  $("scenarioName").value = "";
  loadScenarios();
  return true;
}
async function openScenario(id) {
  if (!(await protectDraftBeforeSwitch())) return;
  const response = await fetch(`/api/scenarios/${id}`);
  const sc = await response.json();
  if (!response.ok) return alert(sc.error || "打开失败");
  state.persisted.shared.scenarioId = id;
  $("scenarioMeta").textContent = sc.name;
  state.edits = {};
  Object.entries(sc.input_adjustments || {}).forEach(([key, values]) => {
    const item = state.data.parameters.find((x) => x.id === key);
    if (item && canEdit(item)) state.edits[key] = { ...values };
  });
  if (sc.read_only && Object.keys(sc.input_adjustments || {}).length)
    alert("历史模板场景只读：调整值仅供参考，不能重算");
  renderNav();
  if (state.selected) renderYears();
  setCalculateEnabled();
  currentDraft().calculatedUnsaved = false;
  currentDraft().edits = state.edits;
  persistWorkbench(); updateDraftStatus();
  if (sc.calculation_result_snapshot) {
    renderDetails(
      state.data.details.map((d) =>
        sc.calculation_result_snapshot[d.name]
          ? { ...d, values: sc.calculation_result_snapshot[d.name] }
          : d,
      ),
    );
  }
  renderTrust({
    trust: {
      status: sc.validation_state || "pending_rule_confirmation",
      reason: `已恢复场景「${sc.name}」${sc.calculation_result_snapshot ? "的最近结果" : "（尚未计算）"}`,
      rule_version: sc.rule_publication_id,
      iterations: 0,
    },
  });
}
async function copyScenario(id) {
  const response = await fetch(`/api/scenarios/${id}/copy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "复制失败");
  loadScenarios();
}
async function renameScenario(id) {
  const name = prompt("新的场景名称");
  if (!name || !name.trim()) return;
  const response = await fetch(`/api/scenarios/${id}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "重命名失败");
  loadScenarios();
}
async function deleteScenario(id) {
  if (!confirm("确认删除该场景？")) return;
  const response = await fetch(`/api/scenarios/${id}`, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) return alert(data.error || "删除失败");
  loadScenarios();
}
async function recalculateScenario(id) {
  if (state.task) return alert("已有测算在进行中");
  setRunning(true);
  state.taskKind = "scenario";
  try {
    const response = await fetch(`/api/scenarios/${id}/recalculate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const task = await response.json();
    if (!response.ok) throw Error(task.error || "重算提交失败");
    state.task = task.task_id;
    renderTaskProgress(task);
    setTimeout(poll, 500);
  } catch (error) {
    state.task = null;
    state.taskKind = null;
    setRunning(false);
    alert(error.message);
  }
}
setupReverse();
setupConstraintBuilder();
["search", "group", "favorites", "adjusted", "pending"].forEach(
  (id) => ($(id).oninput = renderNav),
);
$("linkage").onchange = renderYears;
$("calculate").onclick = calculate;
$("calculateTop").onclick = calculate;
$("cancelCalc").onclick = cancelCalculation;
$("workerHealth").onclick = recheckWorkerHealth;
$("engineValidation").onclick = showEngineValidation;
$("saveScenario").onclick = saveScenario;
$("startComparison").onclick = startComparison;
$("exportScenario").onclick = exportCurrentScenario;
$("exportReverse").onclick = exportReverseResult;
$("exportComparison").onclick = () => exportExcel("comparison", state.comparison);
$("resetOne").onclick = () => {
  if (state.selected) {
    delete state.edits[state.selected.id];
    renderYears();
    renderNav();
    setCalculateEnabled();
  }
};
$("closeEditor").onclick = () => {
  state.editorOpen = false;
  renderCardGrid();
};
$("detailSearch").oninput = () =>
  state.comparison
    ? renderComparisonDetails()
    : renderDetails(filteredResultRows());
$("templateSwitch").onchange = () =>
  switchTemplate(Number($("templateSwitch").value));
$("setCurrentTemplate").onclick = setCurrentTemplate;
$("resetResultColumns").onclick = resetResultColumns;
let gridResizeTimer = null;
new ResizeObserver(() => {
  clearTimeout(gridResizeTimer);
  gridResizeTimer = setTimeout(() => {
    renderCardPages();
    renderCardGrid();
  }, 150);
}).observe($("cardGrid"));
load()
  .then(loadScenarios)
  .catch((error) => {
    $("workspaceNotice").hidden = false;
    $("workspaceNotice").textContent = error.message;
    $("calculate").disabled = true;
    $("calculateTop").disabled = true;
  });
recheckWorkerHealth();

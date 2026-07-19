(function (global) {
  const MODULES = ["forward", "single", "multi", "rules"];
  const STORAGE_KEY = "bank-forecast-workbench-v2";
  const DEFAULT_PANES = { left: 250, right: 520 };

  function emptyDraft() {
    return {
      selected: [],
      cardOrder: [],
      edits: {},
      variables: [],
      constraints: [],
      calculatedUnsaved: false,
      singleVariable: null,
      page: 0,
      cardLayout: "horizontal",
      openGroups: {},
      outputSelection: [],
      columnWidths: [116, 66, 66, 66, 66, 66, 66, 84, 72],
    };
  }

  function defaults() {
    return {
      version: 2,
      activeModule: "forward",
      mobilePane: "center",
      restored: false,
      shared: {
        templateVersionId: null,
        scenarioId: null,
        engineMode: "warm_com",
        forwardMode: "auto",
        degradationReason: "",
      },
      favorites: {},
      drafts: Object.fromEntries(MODULES.map((module) => [module, emptyDraft()])),
      paneWidths: Object.fromEntries(MODULES.map((module) => [module, { ...DEFAULT_PANES }])),
    };
  }

  function merge(saved) {
    const state = defaults();
    if (!saved || saved.version !== state.version) return state;
    state.activeModule = MODULES.includes(saved.activeModule) ? saved.activeModule : "forward";
    state.mobilePane = ["left", "center", "right"].includes(saved.mobilePane) ? saved.mobilePane : "center";
    state.shared = { ...state.shared, ...(saved.shared || {}) };
    state.favorites = saved.favorites || {};
    MODULES.forEach((module) => {
      state.drafts[module] = { ...emptyDraft(), ...(saved.drafts?.[module] || {}) };
      state.paneWidths[module] = { ...DEFAULT_PANES, ...(saved.paneWidths?.[module] || {}) };
    });
    state.restored = MODULES.some((module) => isDirty(state.drafts[module]));
    return state;
  }

  function load(storage = global.localStorage) {
    try {
      return merge(JSON.parse(storage.getItem(STORAGE_KEY) || "null"));
    } catch {
      return defaults();
    }
  }

  function save(state, storage = global.localStorage) {
    storage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function isDirty(draft) {
    return Boolean(
      Object.keys(draft?.edits || {}).length ||
      (draft?.variables || []).length ||
      (draft?.constraints || []).length,
    );
  }

  function resetPanes(state, module = state.activeModule) {
    state.paneWidths[module] = { ...DEFAULT_PANES };
    save(state);
  }

  global.WorkbenchState = {
    MODULES,
    DEFAULT_PANES,
    defaults,
    load,
    save,
    isDirty,
    resetPanes,
  };
})(window);

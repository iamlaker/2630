// 任务 07 真实驱动：Node 加载真实 web/app.js（打 DOM stub），对真实工作台服务执行 提交→轮询→取消 全流程。
const fs = require('fs');
const path = require('path');

const elements = {};
function el(id) {
  if (!elements[id]) {
    elements[id] = {
      id, textContent: '', innerHTML: '', hidden: false, disabled: false, className: '',
      value: '', checked: false, onclick: null, oninput: null, onchange: null, dataset: {},
    };
  }
  return elements[id];
}
const document = {
  getElementById: el,
  querySelectorAll: () => [],
};
const localStorage = { getItem: () => null, setItem: () => {} };
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => realFetch(url.startsWith('/') ? 'http://127.0.0.1:8765' + url : url, opts);

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'app.js'), 'utf-8');
// app.js 末尾自动执行 load()；在沙盒尾部追加驱动代码。
const driver = `
;(async () => {
  await new Promise(r => setTimeout(r, 1500));
  console.log('loaded params:', state.data.parameters.length, 'rule_set.active:', state.data.rule_set.active);
  const param = state.data.parameters.find(p => p.name.includes('国债'));
  select(param.id);
  console.log('selected:', state.selected.name, 'canEdit:', canEdit(state.selected));
  changeYear(2026, state.selected.baseline['2026'] + 0.001);
  console.log('edited 2026 ->', state.edits[param.id]['2026']);
  const seen = [];
  const origRender = renderTaskProgress;
  renderTaskProgress = (task) => { seen.push(task.status + '@' + (task.current_stage || '-')); origRender(task); };
  await calculate();
  console.log('submitted, task:', state.task);
  await new Promise(r => setTimeout(r, 2000));
  await cancelCalculation();
  console.log('cancel sent');
  for (let i = 0; i < 120 && state.task; i++) await new Promise(r => setTimeout(r, 500));
  console.log('final badge:', $('statusBadge').textContent);
  console.log('final trust:', $('trust').innerHTML.slice(0, 80));
  console.log('status flow:', JSON.stringify(seen));
  console.log('RESULT', $('statusBadge').textContent === '已取消' ? 'PASS' : 'CHECK');
})().catch(e => { console.error('DRIVER ERROR', e); process.exit(1); });
`;
eval(source + driver);

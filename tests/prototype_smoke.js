/**
 * 前端原型的无浏览器冒烟测试。
 *
 * 用最小的 DOM 桩执行 prototype/index.html 内联脚本，验证：
 * 初始空态 → 模拟识别 → 默认勾选 → 推荐排序 → 勾选变化后重算。
 *
 * 运行：node tests/prototype_smoke.js
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'prototype', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const problems = [];

function check(condition, message) {
  if (!condition) problems.push(message);
}

/* ---------- 提取内联脚本与内嵌数据 ---------- */

const plainScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (plainScripts.length !== 1) {
  console.error('期望恰好 1 个普通 script 块，实际 ' + plainScripts.length);
  process.exit(1);
}

const jsonBlocks = {};
for (const match of html.matchAll(/<script id="([^"]+)" type="application\/json">([\s\S]*?)<\/script>/g)) {
  jsonBlocks[match[1]] = match[2];
}
check(jsonBlocks['seed-recipes'] !== undefined, '缺少内嵌数据块 seed-recipes');
check(jsonBlocks['seed-ingredients'] !== undefined, '缺少内嵌数据块 seed-ingredients');
if (problems.length) {
  console.error(problems.join('\n'));
  process.exit(1);
}

/* ---------- 最小 DOM 桩 ---------- */

const elements = {};

function makeElement(id) {
  const el = {
    id,
    textContent: '',
    hidden: false,
    disabled: false,
    value: '',
    className: '',
    children: [],
    listeners: {},
    style: {},
    scrollHeight: 100,
    clientHeight: 100,
    scrollTop: 0,
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {},
    removeAttribute() {},
    click() {},
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    dispatch(type, event) {
      (this.listeners[type] || []).forEach(handler => handler(event || {}));
    }
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html || ''; },
    set(value) { this._html = value; if (value === '') this.children = []; }
  });
  return el;
}

global.document = {
  getElementById(id) {
    if (jsonBlocks[id] !== undefined) return { textContent: jsonBlocks[id] };
    if (!elements[id]) elements[id] = makeElement(id);
    return elements[id];
  },
  createElement() { return makeElement('created'); }
};
global.window = { addEventListener() {} };

/* ---------- 执行原型脚本 ---------- */

try {
  new Function(plainScripts[0])();
} catch (error) {
  console.error('原型脚本执行失败：' + error.message);
  process.exit(1);
}

check(elements['chips-empty'].hidden === false, '初始状态应显示食材空态');
check(elements['recipes-empty'].hidden === false, '初始状态应显示菜谱空态');
check(elements['recipes-hint'].textContent === '', '初始状态不应显示菜谱计数');

/* ---------- 触发识别并校验结果 ---------- */

elements['btn-recognize'].dispatch('click');
check(elements['btn-recognize'].disabled === true, '识别中按钮应禁用');
check(elements['btn-recognize'].textContent === '识别中…', '识别中按钮文案应变化');

setTimeout(() => {
  const chips = elements['chips'].children;
  const selected = chips.filter(chip => chip.className.indexOf('is-selected') >= 0);

  check(elements['btn-recognize'].disabled === false, '识别结束后按钮应恢复可用');
  check(elements['btn-recognize'].textContent === '开始识别', '识别结束后按钮文案应恢复');
  check(chips.length === 3, '应识别出 3 种食材，实际 ' + chips.length);
  check(elements['chips-empty'].hidden === true, '识别后应隐藏食材空态');
  check(selected.length === 2, '低置信度食材默认不勾选，期望勾选 2 项，实际 ' + selected.length);
  check(elements['recipes-hint'].textContent === '4 道菜 · 按匹配度排序',
    '菜谱计数文案异常：' + elements['recipes-hint'].textContent);

  const cards = elements['recipes'].children;
  check(cards.length === 4, '基准用例应推荐 4 道菜，实际 ' + cards.length);

  const first = cards[0] ? cards[0].innerHTML : '';
  check(first.indexOf('番茄炒蛋') !== -1, '首条推荐应为番茄炒蛋');
  check(first.indexOf('2/2') !== -1 && first.indexOf('100%') !== -1, '首条推荐匹配度应为 2/2 · 100%');
  check(first.indexOf('10 分钟') !== -1, '首条推荐缺少时长');
  check(first.indexOf('难度简单') !== -1, '首条推荐缺少难度');
  check(first.indexOf('★ 4.8') !== -1, '首条推荐缺少评分');
  check(first.indexOf('<ol class="steps">') !== -1, '首条推荐缺少步骤列表');

  /* 勾选最低置信度的「青椒」，验证推荐实时重算 */
  elements['chips'].children[2].dispatch('click');
  check(elements['recipes'].children.length === 5,
    '勾选青椒后应命中 5 道菜，实际 ' + elements['recipes'].children.length);
  check(elements['recipes-hint'].textContent === '5 道菜 · 按匹配度排序',
    '勾选变化后计数文案异常：' + elements['recipes-hint'].textContent);

  /* 取消勾选「番茄」：番茄牛腩因缺少必需食材应退出推荐，青椒炒蛋升为 2/2 居首 */
  elements['chips'].children[0].dispatch('click');
  const ranking = elements['recipes'].children
    .map(card => (card.innerHTML.match(/recipe-name">([^<]*)</) || [])[1]);
  check(ranking.length === 4, '取消番茄后应命中 4 道菜，实际 ' + ranking.length);
  check(ranking.indexOf('番茄牛腩') === -1, '取消番茄后番茄牛腩不应继续推荐');
  check(ranking[0] === '青椒炒蛋', '取消番茄后首条推荐应为青椒炒蛋，实际 ' + ranking[0]);

  /* 重置回到初始空态 */
  elements['btn-reset'].dispatch('click');
  check(elements['chips'].children.length === 0, '重置后食材列表应清空');
  check(elements['recipes'].children.length === 0, '重置后菜谱列表应清空');
  check(elements['chips-empty'].hidden === false, '重置后应显示食材空态');
  check(elements['recipes-empty'].hidden === false, '重置后应显示菜谱空态');

  if (problems.length) {
    console.error('冒烟校验失败：\n- ' + problems.join('\n- '));
    process.exit(1);
  }
  console.log('冒烟校验通过：识别 3 种食材、基准推荐 4 道菜、勾选联动与重置均正常');
}, 1200);

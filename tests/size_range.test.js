'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const range = require('../media/size_range');
const {translate} = require('../media/localization');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('size ranges default safely and reject malformed or reversed bounds', () => {
  assert.deepEqual(range.parse(), {minBytes: 8, maxBytes: 640});
  assert.deepEqual(range.parse({minBytes: 644, maxBytes: 644}), {minBytes: 644, maxBytes: 644});
  for (const value of [null, [], false, '', {minBytes: true}, {maxBytes: null},
    {minBytes: -1}, {minBytes: 641, maxBytes: 640}, {maxBytes: 32769}, {maxBytes: 7}, {minBytes: 8.5}]) {
    assert.throws(() => range.parse(value), /Byte-Bereich/);
  }
});

test('five recommendations cover the supported range without gaps or overlaps', () => {
  assert.deepEqual(range.presets.map(p => p.id), ['tiny', 'small', 'medium', 'big', 'giant']);
  let next = 8;
  for (const preset of range.presets) {
    assert.equal(preset.minBytes, next);
    assert.deepEqual(range.parse(preset), {minBytes: next, maxBytes: preset.maxBytes});
    next = preset.maxBytes + 1;
    const label = `${preset.label} (${preset.minBytes}\u2013${preset.maxBytes} Byte)`;
    assert.match(translate(label, 'en'), /bytes\)$/);
    assert.equal(translate(label, 'de'), label);
  }
  assert.equal(next, range.maxBytes + 1);
});

async function viewHarness(plan) {
  const messages = [], calls = [], saved = new Map(), errors = [];
  let receive;
  const panel = {visible: true, reveal() {}, onDidDispose() {}, onDidChangeViewState() {},
    webview: {cspSource: 'test', asWebviewUri: uri => uri, postMessage: m => messages.push(m),
      onDidReceiveMessage: callback => {receive = callback;}}};
  const vscode = {env: {language: 'en'}, ViewColumn: {One: 1}, Uri: {file: value => value},
    commands: {registerCommand() {}}, window: {createWebviewPanel: () => panel}};
  const exported = {exports: {}};
  const app = path.resolve(__dirname, '..');
  vm.runInNewContext(fs.readFileSync(path.join(app, 'auto_view.js'), 'utf8'), {
    module: exported, setInterval() {}, clearInterval() {},
    require: name => name === 'vscode' ? vscode : require(name.startsWith('.') ? path.join(app, name) : name)
  });
  const api = exported.exports.register({extensionPath: app, subscriptions: [],
    workspaceState: {get: key => saved.get(key), update: async (key, value) => saved.set(key, value)}},
    async (op, params) => {
      calls.push({op, params});
      if (op === 'autoVersions') return [{id: 'synthetic-pal', available: true}];
      if (op === 'autoPlan' || op === 'autoRetryPlan') return plan ? plan() : [{name: 'func_00100000'}];
      if (op === 'autoStart' || op === 'autoRetryStart') return {id: 'a'.repeat(32)};
      return [];
    }, async () => undefined, error => errors.push(error));
  await api.open();
  return {api, calls, messages, saved, errors, receive: message => receive(message)};
}

test('workspace range reaches new and retry plans and stale selections cannot start', async () => {
  const view = await viewHarness();
  const limits = {minBytes: 641, maxBytes: 2048};
  await view.receive({type: 'load', sizeRange: limits});
  assert.deepEqual(view.calls.at(-1).params.settings, limits);
  assert.deepEqual(view.saved.get('automaticSizeRange'), limits);
  await view.api.setSizeRange({minBytes: 2049, maxBytes: 32768});
  await view.receive({type: 'start', names: ['func_00100000'], settings: limits});
  assert.equal(view.calls.filter(call => call.op === 'autoStart').length, 0);
  assert.equal(view.errors.length, 1);
  await view.receive({type: 'loadRetry', sizeRange: limits});
  assert.equal(view.calls.at(-1).op, 'autoRetryPlan');
  assert.deepEqual(view.calls.at(-1).params.settings, limits);
  await view.receive({type: 'startRetry', names: ['func_00100000'], settings: limits});
  assert.equal(view.calls.at(-1).op, 'autoRetryStart');
  assert.deepEqual(view.calls.at(-1).params.settings, limits);
});

test('changing the range during a scan discards its old result', async () => {
  let finish;
  const view = await viewHarness(() => new Promise(resolve => {finish = resolve;}));
  const loading = view.receive({type: 'load', sizeRange: {minBytes: 8, maxBytes: 640}});
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  await view.api.setSizeRange({minBytes: 641, maxBytes: 2048});
  finish([{name: 'func_00100000'}]);
  await loading;
  assert.equal(view.messages.filter(message => message.type === 'candidates').length, 0);
  assert.match(view.errors[0].message, /Filter/);
});

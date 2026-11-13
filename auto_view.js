'use strict';
const vscode = require('./localization').localizedVscode(require('vscode'));
const {translate} = require('./localization');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const byteRange = require('./media/size_range');

function register(context, rpc, getCurrentSeed, reportError) {
  let panel, timer, selected, busy = false, selectionEpoch = 0, panelGeneration = 0;
  const polling = new Map(), lastUpdates = new Map();
  const savedSession = context.workspaceState.get('automaticSession');
  if (typeof savedSession === 'string' && /^[0-9a-f]{32}$/.test(savedSession)) selected = savedSession;
  let candidateMode, loadedCandidates = new Set();
  let selectedVersions = [];

  let versionInfo = [], loadedVersionKey;
  let sizeRange;
  try {sizeRange = byteRange.parse(context.workspaceState.get('automaticSizeRange'));}
  catch (_) {sizeRange = {...byteRange.defaults};}
  const savedLimit = Number(context.workspaceState.get('automaticLoadLimit'));
  let loadLimit = Number.isSafeInteger(savedLimit) && savedLimit >= 1 ? savedLimit : 1000;
  const rendered = {};
  const selectionKey = () => JSON.stringify({versions: selectedVersions, sizeRange});
  async function setSizeRange(value, notify = true) {
    sizeRange = byteRange.parse(value);
    candidateMode = undefined; loadedCandidates = new Set(); loadedVersionKey = undefined;
    await context.workspaceState.update('automaticSizeRange', sizeRange);
    if (notify && panel) panel.webview.postMessage({type: 'sizeRange', data: sizeRange});
    return {...sizeRange};
  }
  async function update(session) {
    if (!panel || panel.visible === false || !session) return;
    const target = panel, epoch = selectionEpoch, generation = panelGeneration;
    const active = () => panel === target && selected === session && selectionEpoch === epoch && panelGeneration === generation;
    const key = `${generation}:${epoch}:${session}`;
    const progress = (state, extra = {}) => {
      if (active()) target.webview.postMessage({type: 'sessionProgress', session, state,
        updatedAt: lastUpdates.get(session), ...extra});
    };
    if (polling.has(key)) {
      progress('refreshing', {stale: Date.now() - polling.get(key) >= 10000});
      return;
    }
    polling.set(key, Date.now());
    progress('refreshing');
    try {
      const data = await rpc('autoStatus', {session});
      if (active()) {
        if (data.id !== session) throw new Error('Status gehört zu einer anderen Sitzung.');
        const updatedAt = new Date().toISOString(); lastUpdates.set(session, updatedAt);
        target.webview.postMessage({type: 'session', data, updatedAt});
        progress('updated');
      }
    } catch (error) {
      progress('failed', {text: error.message, stale: true});
    } finally {polling.delete(key);}
  }
  async function choose(data) {
    selected = data.id; selectionEpoch++;
    const epoch = selectionEpoch, target = panel, generation = panelGeneration;
    await context.workspaceState.update('automaticSession', selected);
    if (target && panel === target && panelGeneration === generation && selectionEpoch === epoch) {
      const updatedAt = new Date().toISOString(); lastUpdates.set(data.id, updatedAt);
      panel.webview.postMessage({type: 'session', data, updatedAt, selection: true});
    }
  }
  function folder(session) {
    if (!/^[0-9a-f]{32}$/.test(session)) throw new Error('Ungültige Sitzung.');
    return rpc('autoStatus', {session});
  }
  async function open() {
    if (panel) {panel.reveal(); await update(selected); return;}
    for (const key of Object.keys(rendered)) delete rendered[key];
    // status RPC also enforces trusted workspace and a configured local repository.
    await context.workspaceState.update('automaticSizeRange', sizeRange);
    await rpc('status');
    versionInfo = await rpc('autoVersions');
    selectedVersions = versionInfo.filter(v => v.available).map(v => v.id);
    selectedVersions = selectedVersions.filter(v => versionInfo.some(row => row.id === v && row.available));
    await context.workspaceState.update('automaticVersions', selectedVersions);
    panel = vscode.window.createWebviewPanel('ps2decomp.automatic', 'PS2DECOMP Automatische Suche', vscode.ViewColumn.One,
      {enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'media'))]});
    panelGeneration++;
    const web = panel.webview;
    const nonce = crypto.randomBytes(20).toString('base64');
    const asset = name => web.asWebviewUri(vscode.Uri.file(path.join(context.extensionPath, 'media', name)));
    web.html = require('./webview_html').automatic({asset, cspSource: web.cspSource, nonce, sizeRange, loadLimit});
    panel.onDidDispose(() => {panel = undefined; panelGeneration++; clearInterval(timer); timer = undefined;});
    panel.onDidChangeViewState(() => {
      update(selected);
    });
    web.onDidReceiveMessage(async message => {
      if (!panel || panel.webview !== web) return;
      const messageGeneration = panelGeneration, messageEpoch = selectionEpoch;
      try {
        if (message.type === 'ready') {
          rendered.locale = message.language;
          rendered.heading = message.heading;
          rendered.sizeRange = message.sizeRange;
          rendered.sizePresets = message.sizePresets;
          web.postMessage({type: 'versions', data: versionInfo, selected: selectedVersions});
        }
        if (message.type === 'versionsChanged') {
          if (!Array.isArray(message.versions) || message.versions.some(v => !versionInfo.some(row => row.id === v && row.available))) throw new Error('Diese Spielversion ist noch nicht verfügbar.');
          selectedVersions = [...new Set(message.versions)];
          await context.workspaceState.update('automaticVersions', selectedVersions);
          candidateMode = undefined; loadedCandidates = new Set(); loadedVersionKey = undefined;
        }
        if (message.type === 'sizeRangeChanged') await setSizeRange(message.sizeRange, false);
        if (message.type === 'rendered' && message.kind === 'sizeRange') rendered.sizeRange = message.sizeRange;
        if (message.type === 'rendered' && ['candidates', 'sessions', 'session'].includes(message.kind)) rendered[message.kind] = message.count;
        if (message.type === 'rendered' && message.kind === 'candidates') rendered.budgetOnly = message.budgetOnly === true;
        if (message.type === 'rendered' && message.kind === 'candidates') {rendered.defaultWorkers = message.defaultWorkers; rendered.defaultSeconds = message.defaultSeconds;}
        if (message.type === 'rendered' && message.kind === 'session') rendered.stateBadges = message.stateBadges === true;
        if (message.type === 'viewError') {rendered.error = message.text; throw new Error(message.text);}
        if (message.type === 'ready' || message.type === 'refresh') {
          try {
            const sessions = await rpc('autoSessions');
            if (!panel || panel.webview !== web || panelGeneration !== messageGeneration || selectionEpoch !== messageEpoch) return;
            web.postMessage({type: 'sessions', data: sessions});
            selected = selected || context.workspaceState.get('automaticSession') || sessions[0]?.id;
            if (selected && !sessions.some(s => s.id === selected)) {selected = sessions[0]?.id; selectionEpoch++;}
            if (selected && sessions.some(s => s.id === selected)) await update(selected);
          } catch (error) {
            if (panel?.webview === web && panelGeneration === messageGeneration && selectionEpoch === messageEpoch)
              web.postMessage({type: 'sessionProgress', session: selected, state: 'failed', text: error.message, stale: true});
            return;
          }
        }
        // Restore the current search first. Reopening a session must not also
        // start a potentially long scan of all free candidates.
        if (['load', 'loadRetry', 'loadMixed'].includes(message.type) || message.type === 'ready' && !selected) {
          if (busy) return;
          busy = true;
          try {
            const mode = message.type === 'loadMixed' ? 'mixed' : message.type === 'loadRetry' ? 'retry' : 'free';
            candidateMode = undefined; loadedCandidates = new Set();
            if (!selectedVersions.length) throw new Error('Mindestens eine verfügbare Spielversion auswählen.');
            if (message.sizeRange) await setSizeRange(message.sizeRange, false);
            const requestedKey = selectionKey();
            web.postMessage({type: 'loadingCandidates', mode});
            const limit = message.limit === undefined ? loadLimit : Number(message.limit);
            if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('Zum Laden eine positive, exakt darstellbare ganze Zahl eingeben.');
            loadLimit = limit; await context.workspaceState.update('automaticLoadLimit', limit);
            const data = mode === 'mixed'
              ? await rpc('autoMixedPlan', {limit, scope: message.scope || 'both', versions: selectedVersions, settings: sizeRange}, progress => web.postMessage({type: 'candidateProgress', data: progress}))
              : mode === 'retry'
              ? await rpc('autoRetryPlan', {limit, scope: message.retryScope || 'incomplete', programScope: message.scope || 'both', versions: selectedVersions, settings: sizeRange})
              : await rpc('autoPlan', {limit, scope: message.scope || 'both', versions: selectedVersions, settings: sizeRange}, progress => web.postMessage({type: 'candidateProgress', data: progress}));
            if (requestedKey !== selectionKey()) throw new Error('Filter während des Ladens geändert. Funktionen neu laden.');
            candidateMode = mode; loadedCandidates = new Set(data.map(row => row.name));
            loadedVersionKey = requestedKey;
            web.postMessage({type: 'candidates', data, mode});
          } finally {busy = false;}
        }
        if (['start', 'startRetry', 'startMixed'].includes(message.type)) {
          if (busy) throw new Error('Die aktuelle Auswahl wird noch geladen oder reserviert.');
          const mode = message.type === 'startMixed' ? 'mixed' : message.type === 'startRetry' ? 'retry' : 'free';
          if (loadedVersionKey !== selectionKey() || JSON.stringify(byteRange.parse(message.settings)) !== JSON.stringify(sizeRange) || candidateMode !== mode || !Array.isArray(message.names) || !message.names.length || message.names.some(name => !loadedCandidates.has(name))) throw new Error('Die Auswahl gehört nicht zur geladenen Liste. Bitte zuerst den gewünschten Bereich neu laden.');
          busy = true;
          try {
            const data = await rpc(mode === 'mixed' ? 'autoMixedStart' : mode === 'retry' ? 'autoRetryStart' : 'autoStart', {names: message.names, settings: message.settings, versions: selectedVersions}, progress => web.postMessage({type: 'preparing', data: progress}));
            await choose(data);
            candidateMode = undefined; loadedCandidates = new Set();
            web.postMessage({type: 'started', mode});
          } finally {busy = false;}
        }
        if (message.type === 'select') {
          if (!/^[0-9a-f]{32}$/.test(message.session)) throw new Error('Ungültige Sitzung.');
          selected = message.session; selectionEpoch++;
          const epoch = selectionEpoch;
          await context.workspaceState.update('automaticSession', selected);
          if (epoch === selectionEpoch) await update(selected);
        }
        if (message.type === 'stop' && selected) {await choose(await rpc('autoStop', {session: selected}));}
        if (message.type === 'resume' && selected) {await choose(await rpc('autoResume', {session: selected}));}
        if (message.type === 'retry' && selected) {await choose(await rpc('autoRetry', {session: selected}));}
        if (message.type === 'retryDeferred' && selected) {await choose(await rpc('autoRetryDeferred', {session: selected}));}
        if (message.type === 'loadDeferred') {web.postMessage({type: 'deferred', data: await rpc('autoDeferred')});}
        if (message.type === 'checks' && selected) {
          const report = await rpc('autoChecks', {session: selected, name: message.name});
          await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(vscode.Uri.file(report.path)), {preview: true});
        }
        if (['review', 'prompt', 'best', 'handoff', 'handoffAll'].includes(message.type) && selected) {
          const data = await folder(selected);
          if (message.type === 'review') {
            await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(vscode.Uri.file(data.review)), {preview: true});
          } else if (message.type === 'prompt') {
            await vscode.env.clipboard.writeText(require('./review_prompt')(data, vscode.env.language));
            vscode.window.showInformationMessage('PS2DECOMP: Review-Prompt mit dem lokalen Dateipfad kopiert.');
          } else if (message.type === 'best') {
            const job = data.jobs.find(j => j.name === message.name);
            const candidate = job?.best || job?.lastAttempt || job?.roughCandidate;
            if (!candidate) throw new Error('Noch kein C-Snapshot vorhanden.');
            const source = fs.readFileSync(candidate.snapshot, 'utf8');
            if (crypto.createHash('sha256').update(Buffer.from(source, 'utf8')).digest('hex') !== candidate.sourceHash) throw new Error('Snapshot verändert; Hash passt nicht.');
            await vscode.window.showTextDocument(await vscode.workspace.openTextDocument({content: source, language: 'c'}), {preview: true});
          } else {
            if (data.alive) throw new Error('Zuerst pausieren und auf das Ende des aktuellen Compilerlaufs warten.');
            const names = message.type === 'handoffAll' ? data.jobs.filter(j => j.status !== 'handed_off').map(j => j.name) : [message.name];
            const result = names.length ? await rpc('autoHandoffMany', {session: data.id, names}) : data;
            await choose(result);
          }
        }
      } catch (error) {
        web.postMessage({type: 'error', text: error.message}); reportError(error);
      }
    });
    timer = setInterval(() => {if (selected) return update(selected);}, 2500);
  }
  async function current() {
    const seed = await getCurrentSeed();
    if (!seed) throw new Error('Zuerst eine Funktion in der Werkbank öffnen.');
    await open();
    const data = await rpc('autoStart', {names: [seed.name], seeds: {[seed.name]: seed.source}, settings: sizeRange});
    await choose(data);
    return data;
  }
  context.subscriptions.push(vscode.commands.registerCommand('ps2decomp.auto', () => open().catch(reportError)),
    vscode.commands.registerCommand('ps2decomp.autoCurrent', () => current().catch(reportError)),
    {dispose() {clearInterval(timer); if (panel) panel.dispose();}});
  return {open, current, setSizeRange, plan: (scope, limit = loadLimit) => rpc('autoPlan', {limit, scope: scope || 'both', versions: selectedVersions, settings: sizeRange}), sessions: () => rpc('autoSessions'), renderState: () => ({...rendered})};
}
module.exports = {register};

'use strict';
const vscode = require('./localization').localizedVscode(require('vscode'));
const {translate} = require('./localization');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

function register(context, rpc, getCurrentSeed, reportError) {
  let panel, timer, selected, busy = false, selectionEpoch = 0, panelGeneration = 0;
  const polling = new Map(), lastUpdates = new Map();
  const savedSession = context.workspaceState.get('automaticSession');
  if (typeof savedSession === 'string' && /^[0-9a-f]{32}$/.test(savedSession)) selected = savedSession;
  let candidateMode, loadedCandidates = new Set();
  let selectedVersions = [];

  let versionInfo = [], loadedVersionKey;
  const savedLimit = Number(context.workspaceState.get('automaticLoadLimit'));
  let loadLimit = Number.isSafeInteger(savedLimit) && savedLimit >= 1 ? savedLimit : 1000;
  const rendered = {};
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
    web.html = `<!doctype html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${web.cspSource}; script-src 'nonce-${nonce}'"><link rel="stylesheet" href="${asset('style.css')}"><link rel="stylesheet" href="${asset('automatic.css')}"><title>PS2DECOMP Automatische Suche</title></head><body>
      <header><div class="eyebrow">PS2DECOMP · LOKAL · OHNE KI</div><h1>Automatische Decompilierung</h1><p>Grobentwürfe erzeugen, C-Varianten bauen und die besten Ergebnisse zur Review sammeln.</p></header>
      <p class="warning">Wartende, laufende und fertige Funktionen bleiben gemeinsam reserviert. Agenten bearbeiten währenddessen andere Funktionen. Erst „An Review freigeben“ hebt eine Reservierung auf.</p>
      <div id="error" role="alert"></div>
      <section class="card version-selection"><h2>Spielversionen</h2><div id="versions" class="toolbar" role="group" aria-label="Spielversionen"></div><p id="versionStatus" class="muted" role="status"></p><p id="publicReservationStatus" role="status">Die Projekt-Reservierungen werden beim Laden und vor jedem Suchstart frisch geprüft.</p></section>
      <section class="card"><h2>Neue Warteschlange</h2><div class="toolbar"><label>Bereich <select id="scope"><option value="both">Hauptprogramm und Overlays</option><option value="main">Hauptprogramm</option><option value="overlays">Overlays</option></select></label><label>Anzahl laden <input id="loadCount" type="number" min="1" step="1" value="${loadLimit}"></label><label>Funktionen <select id="retryScope"><option value="free">Neue freie Funktionen</option><option value="incomplete">Offene Ergebnisse ohne zur&#252;ckgestellte</option><option value="all">Offene Ergebnisse inkl. zur&#252;ckgestellte / Compilerfehler</option></select></label><button id="load">Funktionen laden</button></div><div class="toolbar"><span>Markieren:</span><button id="select10">10</button><button id="select50">50</button><button id="select100">100</button><button id="select1000">1.000</button><label>Anzahl auswählen <input id="selectionAmount" type="number" min="0" step="1" value="10"></label><button id="selectCustom">Anzahl markieren</button><button id="selectAll">Alle</button><button id="selectNone">Auswahl leeren</button><span id="selectionCount" role="status" aria-live="polite"></span></div><p id="candidateStatus" class="muted" role="status">Keine feste Mengenbegrenzung. Bei großen Listen dauert die Eignungsprüfung entsprechend länger.</p><p id="candidateMode" class="muted">Freie Funktionen: Eine neue Auswahl startet erst über den Startknopf.</p><div id="candidates"><p>Die Auswahl wird geladen. Noch kein Suchlauf gestartet.</p></div>
      <details><summary>Suchbudget pro Funktion</summary><div class="settings">
        <label>Parallele Jobs <input id="workers" type="number" min="1" max="32" value="16"></label>
        <label>Maximale Compilerläufe <input id="maxAttempts" type="number" min="1" max="2000" value="30"></label>
        <label>Zeitlimit in Sekunden <input id="seconds" type="number" min="5" max="3600" value="90"></label>
      </div></details><div class="toolbar"><button class="primary" id="start">Auswahl dekompilieren</button></div><p class="muted">100 % Suchwert ist kein freigegebener Match. 80-99 % und ausbleibende Verbesserung beenden die Suche nicht. Sie läuft bis zum technischen Treffer, Zeit-/Versuchslimit oder Ende der unterstützten Varianten. Deine vorhandenen Entwürfe werden nicht überschrieben.</p></section>
      <section class="card"><h2>Suchläufe und Review</h2><div class="toolbar"><select id="sessions" aria-label="Gespeicherte Sitzung"></select><button id="refresh">Aktualisieren</button><button id="stop">Suche pausieren</button><button id="resume">Wartende Jobs fortsetzen</button><button id="retry">Andere offene Ergebnisse erneut versuchen</button><button id="review">Review-Datei öffnen</button><button id="prompt">Review-Prompt kopieren</button><button id="handoffAll">Alle Reservierungen freigeben</button></div><label><input type="checkbox" id="showDeferred">Zurückgestellte Fälle dieses Laufs anzeigen</label><button id="retryDeferred">Zurückgestellte dieses Laufs gezielt erneut versuchen</button><div id="sessionUpdate" class="session-update" role="status" aria-live="polite">Noch kein Status erfolgreich geladen.</div><div id="status" role="status">Noch keine Sitzung.</div><section id="activeJobs" class="active-jobs" aria-label="Aktuell in Arbeit"><h3>Aktuell in Arbeit</h3><p>Noch kein Status geladen.</p></section><div class="toolbar"><button id="jobPrev">Vorherige Seite</button><span id="jobPageInfo" role="status" aria-live="polite"></span><button id="jobNext">Weitere Funktionen</button></div><div id="jobs"></div></section>
      <section class="card"><h2>Für später zurückgestellt</h2><p>„Analyse nötig“ wird zunächst ausgelassen. Gründe und Quellen bleiben gespeichert. Eine externe Signatur kann zum Kompilieren reichen; der aufgerufene Funktionskörper muss dafür noch nicht decompiliert sein.</p><button id="loadDeferred">Zurückgestellte aus allen Suchläufen anzeigen</button><div id="deferredJobs"></div></section>
      <script nonce="${nonce}" src="${asset('automatic.js')}"></script></body></html>`;
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
          web.postMessage({type: 'versions', data: versionInfo, selected: selectedVersions});
        }
        if (message.type === 'versionsChanged') {
          if (!Array.isArray(message.versions) || message.versions.some(v => !versionInfo.some(row => row.id === v && row.available))) throw new Error('Diese Spielversion ist noch nicht verfügbar.');
          selectedVersions = [...new Set(message.versions)];
          await context.workspaceState.update('automaticVersions', selectedVersions);
          candidateMode = undefined; loadedCandidates = new Set(); loadedVersionKey = undefined;
        }
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
        if (['load', 'loadRetry'].includes(message.type) || message.type === 'ready' && !selected) {
          if (busy) return;
          busy = true;
          try {
            const mode = message.type === 'loadRetry' ? 'retry' : 'free';
            candidateMode = undefined; loadedCandidates = new Set();
            if (!selectedVersions.length) throw new Error('Mindestens eine verfügbare Spielversion auswählen.');
            const selectionKey = JSON.stringify(selectedVersions);
            web.postMessage({type: 'loadingCandidates', mode});
            const limit = message.limit === undefined ? loadLimit : Number(message.limit);
            if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('Zum Laden eine positive, exakt darstellbare ganze Zahl eingeben.');
            loadLimit = limit; await context.workspaceState.update('automaticLoadLimit', limit);
            const data = mode === 'retry'
              ? await rpc('autoRetryPlan', {limit, scope: message.retryScope || 'incomplete', programScope: message.scope || 'both', versions: selectedVersions})
              : await rpc('autoPlan', {limit, scope: message.scope || 'both', versions: selectedVersions}, progress => web.postMessage({type: 'candidateProgress', data: progress}));
            if (selectionKey !== JSON.stringify(selectedVersions)) throw new Error('Versionsauswahl während des Ladens geändert. Bitte neu laden.');
            candidateMode = mode; loadedCandidates = new Set(data.map(row => row.name));
            loadedVersionKey = selectionKey;
            web.postMessage({type: 'candidates', data, mode});
          } finally {busy = false;}
        }
        if (['start', 'startRetry'].includes(message.type)) {
          if (busy) throw new Error('Die aktuelle Auswahl wird noch geladen oder reserviert.');
          const mode = message.type === 'startRetry' ? 'retry' : 'free';
          if (loadedVersionKey !== JSON.stringify(selectedVersions) || candidateMode !== mode || !Array.isArray(message.names) || !message.names.length || message.names.some(name => !loadedCandidates.has(name))) throw new Error('Die Auswahl gehört nicht zur geladenen Liste. Bitte zuerst den gewünschten Bereich neu laden.');
          busy = true;
          try {
            const data = await rpc(mode === 'retry' ? 'autoRetryStart' : 'autoStart', {names: message.names, settings: message.settings, versions: selectedVersions}, progress => web.postMessage({type: 'preparing', data: progress}));
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
    const data = await rpc('autoStart', {names: [seed.name], seeds: {[seed.name]: seed.source}});
    await choose(data);
    return data;
  }
  context.subscriptions.push(vscode.commands.registerCommand('ps2decomp.auto', () => open().catch(reportError)),
    vscode.commands.registerCommand('ps2decomp.autoCurrent', () => current().catch(reportError)),
    {dispose() {clearInterval(timer); if (panel) panel.dispose();}});
  return {open, current, plan: (scope, limit = loadLimit) => rpc('autoPlan', {limit, scope: scope || 'both', versions: selectedVersions}), sessions: () => rpc('autoSessions'), renderState: () => ({...rendered})};
}
module.exports = {register};

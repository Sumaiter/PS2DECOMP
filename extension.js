'use strict';
const vscode = require('./localization').localizedVscode(require('vscode'));
const {translate} = require('./localization');
const path = require('path');
const fs = require('fs');
const {spawn} = require('child_process');
const readline = require('readline');
const crypto = require('crypto');

let context, output, diagnostics, panel, current, status, contractCache;
const workers = new Map();
let serial = 0;
let openGeneration = 0, opening = false;
let lastCompared;
let roughDraft;
let differenceDecoration;
let activeRepository;

function clearDifferenceMarks() {
  for (const editor of vscode.window.visibleTextEditors) editor.setDecorations(differenceDecoration, []);
}

function showDifferenceMarks() {
  clearDifferenceMarks();
  if (!lastCompared || !lastCompared.differences) return;
  for (const editor of vscode.window.visibleTextEditors) {
    if (editor.document.uri.toString() !== lastCompared.uri || editor.document.version !== lastCompared.version) continue;
    const byLine = new Map();
    for (const row of lastCompared.differences) {
      if (!row.sourceLine || row.sourceLine > editor.document.lineCount) continue;
      const line = row.sourceLine - 1;
      if (!byLine.has(line)) byLine.set(line, []);
      byLine.get(line).push(row);
    }
    editor.setDecorations(differenceDecoration, [...byLine].map(([line, rows]) => ({range: editor.document.lineAt(line).range,
      hoverMessage: new vscode.MarkdownString().appendText('Compiler-Zeilenbezug; bei Optimierung können mehrere Ausdrücke beteiligt sein.\n' + rows.map(r => `${r.address}: ${r.oursHex} ↔ ${r.retailHex} · ${r.reason}`).join('\n'))})));
  }
}
const pending = new Map();

function repository() {
  return require('./workspace_repository').resolveRepository(vscode.workspace, 'ps2decomp', ['ps2decomp.json']);
}

function start(channel) {
  const repo = repository();
  if (repo !== activeRepository) throw new Error('Der Projekt-Workspace wurde gewechselt. Bitte das VS-Code-Fenster neu laden.');
  if (workers.has(channel)) return workers.get(channel);
  if (!vscode.workspace.isTrusted) throw new Error('Für den lokalen Compiler muss der Arbeitsbereich als vertrauenswürdig geöffnet sein.');
  const python = vscode.workspace.getConfiguration('ps2decomp').get('python') || path.join(repo, 'venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']));
  if (!fs.existsSync(python)) throw new Error('Python nicht gefunden: ' + python + '. Stelle ps2decomp.python ein.');
  const child = spawn(python, ['-u', path.join(context.extensionPath, 'backend.py'), '--repo', repo], {cwd: repo, windowsHide: true, env: {...process.env, PS2DECOMP_LANGUAGE: vscode.env.language}, stdio: ['pipe', 'pipe', 'pipe']});
  workers.set(channel, child);
  output.appendLine('Lokaler Prüfkern (' + channel + '): ' + repo);
  child.stderr.on('data', data => output.append(data.toString()));
  readline.createInterface({input: child.stdout}).on('line', line => {
    let message;
    try { message = JSON.parse(line); } catch { output.appendLine(line); return; }
    const request = pending.get(message.id);
    if (!request || request.child !== child) return;
    if (message.progress) {request.timer.refresh(); if (request.progress) request.progress(message.progress); return;}
    pending.delete(message.id); clearTimeout(request.timer);
    if (message.error) request.reject(new Error(message.error)); else request.resolve(message.result);
  });
  function stopped(reason) {
    if (workers.get(channel) !== child) return;
    workers.delete(channel);
    for (const [id, p] of pending) {
      if (p.child !== child) continue;
      pending.delete(id); clearTimeout(p.timer); p.reject(new Error(reason));
    }
  }
  child.on('error', err => stopped(err.message));
  child.on('exit', code => stopped('Prüfkern beendet (' + code + '). Siehe Ausgabe → PS2DECOMP Werkbank.'));
  return child;
}

function rpc(op, params = {}, progress) {
  // Read-only progress has its own process: a lengthy candidate scan or build
  // on the command channel must never queue status behind it.
  const child = start(['autoStatus', 'autoSessions'].includes(op) ? 'progress' : 'commands');
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const timeout = ['autoPlan', 'autoStart', 'autoRetryPlan', 'autoRetryStart', 'autoHandoffMany'].includes(op) ? 600000 : 120000;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error('Zeitlimit beim lokalen Prüfkern.'));}, timeout);
    pending.set(id, {resolve, reject, timer, child, progress});
    child.stdin.write(JSON.stringify({id, op, ...params}) + '\n', err => {
      if (err && pending.has(id)) {clearTimeout(timer); pending.delete(id); reject(err);}
    });
  });
}

function error(err) {
  output.appendLine(err.stack || err.message);
  if (panel) panel.webview.postMessage({type: 'error', text: err.message});
  vscode.window.showErrorMessage('PS2DECOMP: ' + err.message);
}

async function selectFunction() {
  const picker = vscode.window.createQuickPick();
  picker.placeholder = 'Adresse suchen. Filter: baubare Grobentwürfe / alle offenen. Kreispfeil: weitere prüfen.';
  picker.matchOnDescription = true;
  const filter = {iconPath: new vscode.ThemeIcon('filter'), tooltip: 'Zwischen baubaren Grobentwürfen und allen offenen Funktionen wechseln'};
  const refresh = {iconPath: new vscode.ThemeIcon('refresh'), tooltip: 'Weitere Grobentwürfe erzeugen und ihre Kompilierung prüfen'};
  const eye = {iconPath: new vscode.ThemeIcon('eye'), tooltip: 'Auch bereits gematchte/veröffentlichte Lernbeispiele anzeigen'};
  picker.buttons = [filter, refresh, eye];
  let mode = 'buildable', generation = 0, disposed = false;
  async function update(value) {
    const own = ++generation; picker.busy = true;
    picker.title = mode === 'buildable' ? 'Projekt: kompilierbare Grobentwürfe · Matching noch offen' : mode === 'all' ? 'Projekt: alle Funktionen einschließlich Lernbeispielen' : 'Projekt: alle offenen Funktionen · ungefiltert';
    try {
      const rows = await rpc('search', {query: value, all: mode === 'all', buildable: mode === 'buildable'});
      if (disposed || own !== generation) return;
      picker.items = rows.map(row => ({label: row.name, description: `${row.size} Byte · ${row.state}${row.claim ? ' · reserviert: ' + row.claim : ''}`, detail: row.section, name: row.name, buildable: row.buildable}));
      if (!rows.length && mode === 'buildable') picker.items = [{label: '$(refresh) Weitere kompilierbare Grobentwürfe suchen', description: 'Kandidaten werden tatsächlich durch den lokalen Compiler geprüft.', action: 'scan'}];
    } catch (err) { if (!disposed) error(err); }
    finally {if (!disposed && own === generation) picker.busy = false;}
  }
  picker.onDidChangeValue(update);
  picker.onDidTriggerButton(async button => {
    if (button === refresh) {try {await scanBuildable();} catch (err) {error(err);}}
    else if (button === eye) mode = mode === 'all' ? 'buildable' : 'all';
    else mode = mode === 'buildable' ? 'open' : 'buildable';
    if (!disposed) update(picker.value);
  });
  const selected = await new Promise(resolve => {
    picker.onDidAccept(async () => {
      const selected = picker.selectedItems[0];
      if (selected && selected.action === 'scan') {try {await scanBuildable(); if (!disposed) await update(picker.value);} catch (err) {error(err);} return;}
      if (selected) {resolve(selected); picker.hide();}
    });
    picker.onDidHide(() => {disposed = true; resolve(undefined); picker.dispose();});
    picker.show(); update('');
  });
  if (selected) await openFunction(selected.name, Boolean(selected.buildable));
}

let scanning = false;
async function scanBuildable() {
  if (scanning) return;
  scanning = true;
  try {
    return await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: 'Baubare PS2DECOMP-Grobentwürfe prüfen', cancellable: true}, async (progress, token) => {
      const names = await rpc('scanPlan', {limit: 40});
      let tested = 0, added = 0;
      for (const name of names) {
        if (token.isCancellationRequested) break;
        progress.report({message: `${tested}/${names.length} geprüft · ${added} baubar · ${name}`});
        const result = await rpc('probeBuildable', {name});
        tested++; if (result.buildable) added++;
        progress.report({increment: 100 / names.length});
      }
      output.appendLine(`Auswahlliste: ${tested} geprüft, ${added} kompilierbare Grobentwürfe hinzugefügt. Kein automatischer Abschluss-Match.`);
      return {tested, added};
    });
  } finally {scanning = false;}
}

function ensurePanel() {
  if (panel) {panel.reveal(panel.viewColumn, true); return;}
  panel = vscode.window.createWebviewPanel('ps2decomp.workbench', 'PS2DECOMP Werkbank', vscode.ViewColumn.Beside, {
    enableScripts: true, retainContextWhenHidden: true,
    localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'media'))]
  });
  const view = panel.webview;
  const nonce = crypto.randomBytes(18).toString('base64');
  const css = view.asWebviewUri(vscode.Uri.file(path.join(context.extensionPath, 'media', 'style.css')));
  const js = view.asWebviewUri(vscode.Uri.file(path.join(context.extensionPath, 'media', 'panel.js')));
  view.html = `<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${view.cspSource}; script-src 'nonce-${nonce}';"><link rel="stylesheet" href="${css}"><title>PS2DECOMP Werkbank</title></head><body>
    <header><div class="eyebrow">PS2DECOMP · MIPS R5900</div><h1>Dein Code. Am Original geprüft.</h1><p id="connection">Prüfkern wird geladen …</p></header>
    <div class="toolbar"><button id="choose">Funktion wählen</button><button id="decompile">Grob dekompilieren</button><button id="compare" class="primary">C-Code vergleichen <kbd>Ctrl Alt B</kbd></button><button id="block">Baustein einfügen</button></div>
    <section class="target"><strong id="name">Noch keine Funktion</strong><span id="meta">Wähle eine Funktion und bearbeite den Entwurf im C-Editor.</span></section>
    <div id="notice" role="status"></div><div id="warnings"></div>
    <nav aria-label="Werkbank"><button data-tab="guide" class="active">Vorschläge & Belege</button><button data-tab="rough">Grobentwurf</button><button data-tab="asm">Original-Assembler</button><button data-tab="result">Vergleich</button></nav>
    <main><section id="guide" class="tab active"><div id="suggestions"></div><h2>Belegte Signaturen</h2><p class="muted">Belege können verschiedene Stände oder widersprüchliche Deklarationen enthalten. Prüfe den für deinen Aufruf gültigen Vertrag.</p><div id="contracts"></div><h2>Beobachtete Feldzugriffe</h2><div id="fields"></div><h2>Passende Lernbeispiele</h2><div id="examples"></div><p class="muted">Bausteine und Ähnlichkeitshinweise sind regelbasierte Hilfen. Sie ersetzen keine Rekonstruktion der tatsächlichen Funktion.</p></section>
    <section id="rough" class="tab"><div id="roughWarnings"></div><button id="applyRough" disabled>Grobentwurf übernehmen · aktuellen Editorinhalt ersetzen</button><p class="muted">Vorheriger Editorinhalt wird separat gesichert. Übernehmen ist rückgängig machbar und speichert die Datei nicht automatisch.</p><pre id="roughSource">Beim Auswählen wird automatisch ein lokaler m2c-Grobentwurf erzeugt.</pre><details><summary>m2c-Protokoll</summary><pre id="roughLog"></pre></details></section>
    <section id="asm" class="tab"><pre id="assembly">Hier erscheint das lokale Original-Listing.</pre></section>
    <section id="result" class="tab"><div id="summary" class="card">Noch kein Vergleich. Änderungen im C-Editor werden direkt aus dem offenen Dokument gelesen, auch vor dem Speichern.</div><div id="relocs"></div><div class="diff-wrap"><table id="diff"></table></div><details><summary>Compilerprotokoll</summary><pre id="log"></pre></details></section></main><script nonce="${nonce}" src="${js}"></script></body></html>`;
  view.onDidReceiveMessage(async message => {
    try {
      if (message.type === 'ready') {if (status) view.postMessage({type: 'status', data: status}); if (current) view.postMessage({type: 'detail', data: current}); if (roughDraft) view.postMessage({type: 'rough', data: roughDraft});}
      if (message.type === 'choose') await selectFunction();
      if (message.type === 'compare') await compare();
      if (message.type === 'block') await insertBlock();
      if (message.type === 'decompile') await generateRough();
      if (message.type === 'applyRough') await applyRough();
      if (message.type === 'revealDifference' && lastCompared && current && !opening) {
        const compared = lastCompared, selected = current;
        const row = compared.differences && compared.differences.find(row => row.address === message.address && row.sourceLine);
        if (row) {
          const editor = await draftEditor(selected);
          if (lastCompared === compared && current === selected && editor.document.version === compared.version && editor.document.uri.toString() === compared.uri && row.sourceLine <= editor.document.lineCount) {
            const range = editor.document.lineAt(row.sourceLine - 1).range;
            editor.selection = new vscode.Selection(range.start, range.start);
            editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
          }
        }
      }
      if (message.type === 'declaration' && current) {
        const declarations = Object.values(current.contracts).flat().map(p => p.declaration);
        if (declarations.includes(message.declaration)) await insertDeclaration(message.declaration);
      }
      if (message.type === 'example' && current) {
        const example = current.examples.find(p => p.path === message.path);
        if (!example) return;
        const content = fs.readFileSync(example.path, 'utf8');
        const doc = await vscode.workspace.openTextDocument({content: '/* Lernbeispiel: ' + example.name + '. Originalquelle bleibt unverändert. */\n' + content, language: 'c'});
        await vscode.window.showTextDocument(doc, {viewColumn: vscode.ViewColumn.One});
      }
    } catch (err) {error(err);}
  });
  panel.onDidDispose(() => {panel = undefined;});
}

async function openFunction(name, buildable = false) {
  const generation = ++openGeneration;
  opening = true;
  try {
    const info = await rpc('status');
    if (generation !== openGeneration) return;
    const target = await rpc('detail', {name});
    if (generation !== openGeneration) return;
    const draft = await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: name + ' Grobentwurf öffnen', cancellable: false}, () => rpc('prepare', {name, buildable}));
    if (generation !== openGeneration) return;
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(draft.path));
    if (generation !== openGeneration) return;
    await vscode.window.showTextDocument(document, {viewColumn: vscode.ViewColumn.One});
    if (generation !== openGeneration) return;
    current = target;
    roughDraft = draft.rough;
    if (lastCompared) diagnostics.delete(vscode.Uri.parse(lastCompared.uri));
    lastCompared = undefined;
    clearDifferenceMarks();
    status = info;
    ensurePanel();
    panel.webview.postMessage({type: 'status', data: status});
    panel.webview.postMessage({type: 'detail', data: current});
    panel.webview.postMessage({type: 'rough', data: roughDraft});
    await context.workspaceState.update('lastFunction', name);
  } finally {if (generation === openGeneration) opening = false;}
}

async function generateRough() {
  const selected = current, generation = openGeneration;
  if (!selected || opening) throw new Error('Wähle zuerst eine Funktion und warte auf das Ende des Wechsels.');
  const generated = await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: selected.name + ' grob dekompilieren', cancellable: false}, () => rpc('decompile', {name: selected.name}));
  if (generation !== openGeneration || current !== selected) return;
  roughDraft = generated;
  ensurePanel();
  panel.webview.postMessage({type: 'rough', data: generated});
  return generated;
}

async function applyRough() {
  const target = current, generated = roughDraft, generation = openGeneration;
  if (opening || !target || !generated || !generated.ok || generated.name !== target.name) throw new Error('Kein aktueller Grobentwurf zum Übernehmen.');
  const editor = await draftEditor(target);
  const document = editor.document, version = document.version, text = document.getText();
  const saved = await rpc('backup', {name: target.name, source: text});
  if (generation !== openGeneration || current !== target || document.isClosed || document.version !== version || document.getText() !== text) throw new Error('Entwurf inzwischen geändert oder gewechselt. Bitte erneut übernehmen.');
  if (!await editor.edit(edit => edit.replace(new vscode.Range(0, 0, document.lineCount, 0), generated.source))) throw new Error('Grobentwurf konnte nicht eingesetzt werden.');
  await vscode.window.showTextDocument(document, {viewColumn: editor.viewColumn, preserveFocus: false});
  output.appendLine('Vor Grobentwurf gesichert: ' + saved.path);
  return saved;
}

async function draftEditor(target = current) {
  if (!target) throw new Error('Wähle zuerst eine Funktion.');
  const uri = vscode.Uri.file(target.draft);
  const open = vscode.window.visibleTextEditors.find(e => e.document.uri.toString() === uri.toString());
  if (open) return open;
  return vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri), {viewColumn: vscode.ViewColumn.One});
}

async function insertDeclaration(declaration) {
  const target = current;
  const editor = await draftEditor(target);
  if (editor.document.getText().includes(declaration)) return;
  const lines = editor.document.getText().split('\n');
  let at = lines.findIndex(line => line.includes('#include "common.h"')) + 1;
  if (at < 1) at = 0;
  await editor.edit(edit => edit.insert(new vscode.Position(at, 0), '\nextern ' + declaration.replace(/^extern\s+/, '').trim().replace(/;?$/, ';') + '\n'));
}

async function insertBlock() {
  const target = current;
  if (!target || opening) throw new Error('Wähle zuerst eine Funktion und warte auf das Ende des Wechsels.');
  status = status || await rpc('status');
  const selected = await vscode.window.showQuickPick(status.blocks.map(b => ({label: b.title, description: b.hint, block: b})), {title: 'C-Baustein auswählen'});
  if (selected) {
    const editor = await draftEditor(target);
    await editor.insertSnippet(new vscode.SnippetString(selected.block.code));
  }
}

let comparing = false;
async function compare() {
  if (comparing) return;
  if (opening) throw new Error('Funktionswechsel läuft. Bitte danach erneut vergleichen.');
  const selected = current;
  if (!selected) throw new Error('Wähle zuerst eine Funktion.');
  const generation = openGeneration;
  comparing = true;
  try {
    const editor = await draftEditor(selected);
    if (generation !== openGeneration) throw new Error('Funktion wurde inzwischen gewechselt. Bitte erneut vergleichen.');
    const text = editor.document.getText();
    const version = editor.document.version;
    ensurePanel();
    panel.webview.postMessage({type: 'busy', text: 'Unveränderte Compiler-Pipeline läuft …'});
    const result = await vscode.window.withProgress({location: vscode.ProgressLocation.Notification, title: selected.name + ' mit Referenz vergleichen', cancellable: false}, () => rpc('compare', {name: selected.name, source: text}));
    const liveDocument = vscode.workspace.textDocuments.find(doc => doc.uri.toString() === editor.document.uri.toString());
    result.stale = editor.document.isClosed || liveDocument !== editor.document || liveDocument.version !== version || liveDocument.getText() !== text || generation !== openGeneration;
    if (!result.stale) lastCompared = {uri: editor.document.uri.toString(), version, differences: result.sourceDifferences || []};
    output.appendLine(`${selected.name}: ${result.verdict} · Snapshot: ${result.snapshot}`);
    output.appendLine(result.log);
    const errors = [];
    for (const line of result.log.split('\n')) {
      const found = line.match(/candidate\.c:(\d+):(?:(\d+):)?\s*(.*)/);
      if (found && !/In function|At top level/.test(found[3])) {
        const lineNo = Math.min(Math.max(0, Number(found[1]) - 1 - (result.insertedLine && Number(found[1]) > result.insertedLine ? 1 : 0)), editor.document.lineCount - 1);
        errors.push(new vscode.Diagnostic(editor.document.lineAt(lineNo).range, found[3], /warning/i.test(found[3]) ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error));
      }
    }
    for (const row of result.sourceDifferences || []) {
      if (!row.sourceLine || row.sourceLine > editor.document.lineCount) continue;
      const diagnostic = new vscode.Diagnostic(editor.document.lineAt(row.sourceLine - 1).range,
        `${row.reason} bei Referenz ${row.address}: ${row.oursHex} ↔ ${row.retailHex}. Compiler-Zeilenbezug; die Ursache kann wegen Optimierung an anderer Stelle liegen.`, vscode.DiagnosticSeverity.Warning);
      diagnostic.source = 'PS2DECOMP Bytevergleich';
      errors.push(diagnostic);
    }
    if (!result.stale) diagnostics.set(editor.document.uri, errors);
    showDifferenceMarks();
    if (panel && current && current.name === selected.name) panel.webview.postMessage({type: 'result', data: result});
    return result;
  } finally {comparing = false; if (panel) panel.webview.postMessage({type: 'idle'});}
}

async function contracts() {
  if (!contractCache) contractCache = await rpc('contracts');
  return contractCache;
}

function activate(ctx) {
  context = ctx;
  let active = false;
  try {activeRepository = repository(); active = true;} catch (_) {}
  vscode.commands.executeCommand('setContext', 'ps2decomp.active', active);
  ctx.subscriptions.push(vscode.commands.registerCommand('ps2decomp.setup', () => vscode.commands.executeCommand('markdown.showPreview', vscode.Uri.file(path.join(ctx.extensionPath, 'einrichtungs.md')))));
  if (!active) return;
  output = vscode.window.createOutputChannel('PS2DECOMP Werkbank');
  const automatic = require('./auto_view').register(ctx, rpc, async () => {
    if (!current || opening) return undefined;
    const editor = await draftEditor(current);
    return {name: current.name, source: editor.document.getText()};
  }, error);
  if (vscode.workspace.isTrusted && vscode.workspace.getConfiguration('ps2decomp').get('openAutomaticOnStartup')) {
    automatic.open().catch(error);
  }
  diagnostics = vscode.languages.createDiagnosticCollection('ps2decomp');
  differenceDecoration = vscode.window.createTextEditorDecorationType({isWholeLine: true, backgroundColor: new vscode.ThemeColor('diffEditor.removedLineBackground'), overviewRulerColor: new vscode.ThemeColor('editorWarning.foreground'), overviewRulerLane: vscode.OverviewRulerLane.Right});
  ctx.subscriptions.push(output, diagnostics, differenceDecoration,
    vscode.window.onDidChangeVisibleTextEditors(showDifferenceMarks),
    vscode.workspace.onDidCloseTextDocument(document => {
      diagnostics.delete(document.uri);
      if (lastCompared && lastCompared.uri === document.uri.toString()) {
        lastCompared = undefined;
        clearDifferenceMarks();
        if (panel) panel.webview.postMessage({type: 'stale'});
      }
    }),
    vscode.workspace.onDidChangeTextDocument(event => {
      if (lastCompared && lastCompared.uri === event.document.uri.toString() && lastCompared.version !== event.document.version) {
        lastCompared = undefined;
        diagnostics.delete(event.document.uri);
        clearDifferenceMarks();
        if (panel) panel.webview.postMessage({type: 'stale'});
      }
    }),
    vscode.commands.registerCommand('ps2decomp.open', name => (typeof name === 'string' ? openFunction(name) : selectFunction()).catch(error)),
    vscode.commands.registerCommand('ps2decomp.compare', () => compare().catch(error)),
    vscode.commands.registerCommand('ps2decomp.block', () => insertBlock().catch(error)),
    vscode.commands.registerCommand('ps2decomp.decompile', () => generateRough().catch(error)),
    vscode.commands.registerCommand('ps2decomp.applyRough', () => applyRough().catch(error)),
    vscode.commands.registerCommand('ps2decomp.refreshBuildable', () => scanBuildable().catch(error)),
    vscode.languages.registerHoverProvider('c', {async provideHover(document, position) {
      const range = document.getWordRangeAtPosition(position, /func_[0-9A-F]{8}/);
      if (!range || !document.uri.fsPath.includes('studio')) return;
      const records = (await contracts())[document.getText(range)];
      if (!records) return;
      const text = new vscode.MarkdownString();
      for (const record of records) {text.appendCodeblock(record.declaration, 'c'); text.appendText('\n' + record.status + '\n' + (Array.isArray(record.evidence) ? record.evidence.join('\n') : String(record.evidence)) + '\nQuelle: ' + record.source);}
      return new vscode.Hover(text, range);
    }}),
    vscode.languages.registerCompletionItemProvider('c', {async provideCompletionItems(document) {
      if (!document.uri.fsPath.includes('studio')) return [];
      return Object.entries(await contracts()).map(([name, versions]) => {
        const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Function);
        item.detail = versions.map(v => v.declaration).join(' | ');
        item.insertText = name;
        item.documentation = new vscode.MarkdownString().appendText('Beleg: ' + versions[0].source + '\n' + versions[0].status);
        return item;
      });
    }})
  );
  return {
    automatic,
    searchBuildable: query => rpc('search', {query: query || '', buildable: true}),
    openBuildable: name => openFunction(name, true)
  };
}

function deactivate() {for (const child of workers.values()) child.kill();}
module.exports = {activate, deactivate};

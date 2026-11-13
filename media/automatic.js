'use strict';
const vscode = acquireVsCodeApi();
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
let candidates = [], current, starting = false, candidateMode = 'free', candidateReady = false;
let jobPage = 0, jobSession;
let requestedSession, updateSession, lastSuccessfulUpdate, updateState = 'idle', updateFailure = '', updateStale = false;
const jobPageSize = 100;
const settingKeys = ['workers', 'maxAttempts', 'seconds'];
const selectionButtons = ['select10', 'select50', 'select100', 'select1000', 'selectCustom', 'selectionAmount', 'selectAll', 'selectNone'];
function updateSelection() {
  const inputs = [...document.querySelectorAll('#candidates input')];
  $('selectionCount').textContent = `${inputs.filter(n => n.checked).length} von ${inputs.length} markiert`;
}
function selectFirst(count) {
  [...document.querySelectorAll('#candidates input')].forEach((n, index) => n.checked = index < count);
  updateSelection();
}
function loadingCandidates(active) {
  $('load').disabled = active || starting;
  $('scope').disabled = active || starting;
  $('retryScope').disabled = active || starting;
  $('loadCount').disabled = active || starting;
  $('start').disabled = active || starting || !candidateReady || !candidates.length;
  for (const id of selectionButtons) $(id).disabled = active || starting;
  if (active && candidateMode === 'retry') $('candidateStatus').textContent = `Bis zu ${$('loadCount').value || 1000} freigegebene offene Ergebnisse werden geprüft und geladen …`;
  else if (active) $('candidateStatus').textContent = `Bis zu ${$('loadCount').value || 1000} freie Funktionen werden geprüft und geladen …`;
}
function send(type, data = {}) { $('error').textContent = ''; vscode.postMessage({type, ...data}); }
const isDeferred = job => job.deferred === true || job.status === 'blocked';
const labels = {queued: 'Noch nicht begonnen · reserviert', running: 'In Arbeit · reserviert', review_ready: 'Suche fertig · Review bereit', blocked: 'Zurückgestellt · später prüfen', deferred: 'Zurückgestellt · später prüfen', error: 'Fehler · reserviert', handed_off: 'An Review freigegeben', paused: 'Pausiert', completed: 'Suche abgeschlossen', interrupted: 'Lauf unterbrochen'};
const statusIcons = {queued: '○', running: '▶', review_ready: '✓', blocked: '↷', deferred: '↷', error: '×', handed_off: '↗'};
function statusBadge(status) {
  const badge = el('span', undefined, 'job-status status-' + status);
  if (status === 'review_ready') badge.title = 'Suchlauf beendet. Die vollständige manuelle Prüfung steht noch aus.';
  const icon = el('span', statusIcons[status] || '•', 'status-icon'); icon.setAttribute('aria-hidden', 'true');
  badge.append(icon, el('span', labels[status] || status)); return badge;
}
function renderCandidates(rows, mode = 'free') {
  candidateMode = mode; candidateReady = true;
  if (mode === 'free') $('retryScope').value = 'free';
  candidates = rows;
  $('start').textContent = 'Auswahl dekompilieren';
  $('candidateMode').textContent = mode === 'retry'
    ? 'Erneute Suche: Nur an Review freigegebene, aktuell unreservierte offene Fälle. Fertige Funktionen bleiben weg. Bisherige Quellen und Ergebnisse bleiben in der Originalsitzung; erst der Startknopf reserviert die Auswahl.'
    : 'Freie Funktionen: Eine neue Auswahl startet erst über den Startknopf.';
  loadingCandidates(false);
  $('candidateStatus').textContent = mode === 'retry' ? `${rows.length} freigegebene offene Ergebnisse für eine neue Suche geladen.` : `${rows.length} geeignete freie Funktionen geladen.`;
  const box = $('candidates'); box.replaceChildren();
  vscode.postMessage({type: 'rendered', kind: 'candidates', count: rows.length, budgetOnly: !$('target') && !$('nearAfter') && !$('stall'), defaultWorkers: Number($('workers').value), defaultSeconds: Number($('seconds').value)});
  if (!rows.length && mode === 'retry') {box.append(el('p', 'Keine passenden freigegebenen offenen Ergebnisse. Reservierte Fälle und fertige Funktionen werden ausgelassen.')); updateSelection(); return;}
  if (!rows.length) {box.append(el('p', 'Keine freien Kandidaten für diesen Bereich. Reservierte und bereits bearbeitete Funktionen werden ausgelassen.')); updateSelection(); return;}
  for (const [index, row] of rows.entries()) {
    const label = el('label', undefined, 'candidate');
    const check = document.createElement('input'); check.type = 'checkbox'; check.value = row.name; check.checked = index < 10;
    check.addEventListener('change', updateSelection);
    const details = el('span', undefined, 'candidate-details');
    details.append(el('span', `${row.name} · ${row.size} B · ${row.program}${row.cached ? ' · baubarer Entwurf vorhanden' : ''}`));
    label.append(check, details, el('span', '○ Noch nicht begonnen', 'job-status status-queued'));
    if (mode === 'retry') {
      const previous = labels[row.previousStatus] || row.previousStatus || 'offen';
      details.append(el('span', `Vorher: ${previous}${row.deferred ? ' · zurückgestellt' : ''} · Sitzung ${row.previousSession || 'unbekannt'}`, 'muted'));
      if (row.reason) details.append(el('span', row.reason, 'muted'));
      if (typeof row.bestScore === 'number') details.append(el('span', `Bisheriger Suchwert: ${row.bestScore.toFixed(2)} · kein Korrektheitsprozent`, 'muted'));
    }
    box.append(label);
  }
  updateSelection();
}
function renderVersions(rows, selected) {
  const box = $('versions'); box.replaceChildren();
  const reasons = [];
  for (const row of rows) {
    const label = el('label', undefined, 'version-option');
    const check = document.createElement('input'); check.type = 'checkbox'; check.value = row.id;
    check.checked = selected.includes(row.id); check.disabled = !row.available;
    check.addEventListener('change', () => {
      invalidateCandidates();
      send('versionsChanged', {versions: [...box.querySelectorAll('input:checked')].map(input => input.value)});
    });
    label.append(check, el('span', row.label + (row.available ? '' : ' · nicht verfügbar'))); box.append(label);
    if (!row.available && row.reason) reasons.push(row.reason);
  }
  $('versionStatus').textContent = reasons.join(' ');
}
function sessionMetrics(jobs) {
  const functions = new Map();
  for (const job of jobs) {
    if (typeof job.name !== 'string' || !job.name.trim()) continue;
    const entry = functions.get(job.name) || {score: null, technical: false};
    const score = job.best?.score;
    if (Number.isFinite(score) && score >= 0 && score <= 100 && (entry.score === null || score > entry.score)) entry.score = score;
    entry.technical ||= job.best?.technicalMatch === true;
    functions.set(job.name, entry);
  }
  const entries = [...functions.values()];
  return {technical: entries.filter(j => j.technical).length,
    from70: entries.filter(j => j.score !== null && j.score >= 70).length,
    from80: entries.filter(j => j.score !== null && j.score >= 80).length};
}
function renderUpdateState() {
  const box = $('sessionUpdate');
  box.dataset.state = updateState;
  box.className = 'session-update' + (updateStale || updateState === 'failed' ? ' session-update-stale' : '');
  const timestamp = el('time', lastSuccessfulUpdate ? new Date(lastSuccessfulUpdate).toLocaleString(document.documentElement.lang) : 'noch keines');
  if (lastSuccessfulUpdate) timestamp.setAttribute('datetime', lastSuccessfulUpdate);
  const last = el('span', 'Letztes erfolgreiches Update: '); last.append(timestamp);
  let detail = updateState === 'updated' ? 'Status aktuell.' : 'Noch kein Status geladen.';
  if (updateState === 'refreshing') detail = updateStale
    ? 'Abruf dauert länger · Anzeige möglicherweise veraltet. Der letzte Stand bleibt sichtbar.'
    : 'Status wird aktualisiert … Der letzte Stand bleibt sichtbar.';
  if (updateState === 'failed') detail = 'Aktualisierung fehlgeschlagen · Anzeige möglicherweise veraltet. Der nächste Abruf erfolgt automatisch.';
  if (current && updateSession && current.id !== updateSession) detail += ' Angezeigt wird weiterhin die zuvor geladene Sitzung.';
  box.replaceChildren(last, el('span', detail, 'update-detail'));
  if (updateFailure) box.append(el('span', updateFailure, 'update-error'));
}
function progressState(message) {
  if (requestedSession && message.session && message.session !== requestedSession) return;
  if (!requestedSession && current && message.session && message.session !== current.id) return;
  updateSession = message.session || updateSession;
  updateState = message.state;
  updateStale = message.state === 'updated' ? false : updateStale || message.stale === true || message.state === 'failed';
  if (message.updatedAt && (!current || message.session === current.id)) lastSuccessfulUpdate = message.updatedAt;
  if (message.state === 'failed') updateFailure = message.text || 'Status konnte nicht gelesen werden.';
  if (message.state === 'updated') updateFailure = '';
  if (current && updateSession && current.id !== updateSession) {
    updateStale = true;
    for (const id of ['stop', 'resume', 'retry', 'retryDeferred', 'handoffAll', 'review', 'prompt']) $(id).disabled = true;
  }
  renderUpdateState();
}
function renderActiveJobs(data) {
  const box = $('activeJobs'), running = data.jobs.filter(job => job.status === 'running');
  box.replaceChildren(el('h3', `Aktuell in Arbeit · ${running.length}`));
  if (!running.length) {
    box.append(el('p', 'Im zuletzt geladenen Stand sind keine Jobs in Arbeit.'));
    return;
  }
  const list = el('ul');
  for (const job of running) {
    const item = el('li'); item.dataset.name = job.name;
    item.append(el('strong', job.name), el('span', `${job.attempts || 0}/${data.options.maxAttempts} Versuche`, 'active-job-attempts'));
    list.append(item);
  }
  box.append(list, el('p', 'Alle laufenden Jobs dieses Suchlaufs, unabhängig von Tabellenseite und Filter.', 'muted'));
}
function selectSession(session) {
  requestedSession = session;
  progressState({session, state: 'refreshing'});
  send('select', {session});
}
function renderSession(data) {
  if (jobSession !== data.id) {jobPage = 0; jobSession = data.id;}
  if (data.publicReservation) {
    const p = data.publicReservation;
    $('publicReservationStatus').textContent = p.status === 'error'
      ? `Öffentliche Reservierung fehlgeschlagen: ${p.error}. Lokale Ergebnisse bleiben erhalten.`
      : `Öffentliche Reservierungen: ${p.status || 'geprüft'} · ${p.added?.length ?? p.added ?? 0} neu aufgenommen.`;
  }
  current = data;
  renderActiveJobs(data);
  $('review').disabled = false; $('prompt').disabled = false;
  if (![...$('sessions').options].some(o => o.value === data.id)) {
    const option = el('option', `${data.purpose ? data.purpose + ' · ' : ''}${data.created.slice(0, 19)} · ${data.names.length} Funktionen`); option.value = data.id; $('sessions').prepend(option);
  }
  $('sessions').value = data.id;
  const done = data.jobs.filter(j => ['review_ready', 'blocked', 'error', 'handed_off'].includes(j.status)).length;
  const queued = data.jobs.filter(j => j.status === 'queued').length;
  const running = data.jobs.filter(j => j.status === 'running').length;
  $('status').replaceChildren(el('div', `${labels[data.status] || data.status} · ${done}/${data.names.length} bearbeitet · ${data.reserved.length} reserviert`, 'session-progress'),
    el('p', `Owner: ${data.owner}`, 'source'), el('p', data.error || (data.alive ? 'Die Suche läuft als lokaler Hintergrundprozess weiter, auch wenn VS Code geschlossen wird.' : 'Kein aktiver Suchprozess. Beste Ergebnisse und Reservierungen sind gespeichert.'), 'muted'));
  if (data.stopRequested) $('status').append(el('p', 'Pause angefordert. Ein laufender Werkzeugaufruf wird noch innerhalb seines Zeitlimits beendet.', 'warning'));
  const counts = el('div', undefined, 'job-counts');
  for (const [state, count] of [['queued', queued], ['running', running], ['review_ready', data.jobs.filter(j => j.status === 'review_ready').length], ['deferred', data.jobs.filter(isDeferred).length]]) {const badge = statusBadge(state); badge.append(el('strong', String(count))); counts.append(badge);}
  $('status').append(counts);
  const metrics = sessionMetrics(data.jobs);
  const summary = el('div', undefined, 'session-metrics');
  summary.setAttribute('role', 'group'); summary.setAttribute('aria-label', 'Ergebnisse des gesamten Suchlaufs');
  for (const [key, title, detail] of [['technical', 'Technische Treffer', 'Fachliche Review bleibt erforderlich'],
    ['from70', 'Suchwert 70–100', 'Enthält auch alle Kandidaten ab 80'], ['from80', 'Suchwert 80–100', 'Kandidaten mit Suchwert 80 bis 100']]) {
    const card = el('div', undefined, 'session-metric'); card.dataset.metric = key;
    card.append(el('span', title, 'metric-label'), el('strong', String(metrics[key]), 'metric-number'), el('span', detail, 'metric-detail'));
    summary.append(card);
  }
  $('status').append(summary, el('p', 'Gezählt werden eindeutige Funktionen anhand ihres aktuellen besten Ergebnisses im gesamten Suchlauf, einschließlich ausgeblendeter Fälle. Suchwerte sind keine Korrektheitsprozente oder fachlichen Freigaben.', 'muted'));
  $('stop').disabled = !data.alive; $('resume').disabled = data.alive || !data.jobs.some(j => ['queued', 'running'].includes(j.status));
  $('handoffAll').disabled = data.alive || !data.reserved.length;
  const canRetry = j => data.reserved.includes(j.name) && !j.best?.technicalMatch && j.status !== 'handed_off';
  $('retry').disabled = data.alive || !data.jobs.some(j => canRetry(j) && !isDeferred(j));
  $('retryDeferred').disabled = data.alive || !data.jobs.some(j => canRetry(j) && isDeferred(j));
  if (data.jobs.some(isDeferred)) $('status').append(el('p', 'Zurückgestellte werden bei neuen Läufen und normalen Wiederholungen ausgelassen. Über die Checkbox anzeigen oder später gezielt erneut versuchen.', 'muted'));
  $('status').append(el('p', `Suchpass ${data.searchPass || 1} · ${data.jobs.filter(j => j.attempts === 1).length} nur einmal gebaut. „Bearbeitet“ bedeutet Suchlauf beendet, nicht erfolgreich decompiliert.`, 'muted'));
  const table = el('table'); const head = el('tr');
  ['Funktion', 'Stand', 'Versuche', 'Suchwert', 'Ergebnis'].forEach(t => head.append(el('th', t))); table.append(head);
  const filtered = data.jobs.filter(j => $('showDeferred').checked || !isDeferred(j));
  const visible = [...filtered.filter(j => j.status === 'running'), ...filtered.filter(j => j.status !== 'running')];
  const lastPage = Math.max(0, Math.ceil(visible.length / jobPageSize) - 1);
  jobPage = Math.min(jobPage, lastPage);
  const pageRows = visible.slice(jobPage * jobPageSize, (jobPage + 1) * jobPageSize);
  $('jobPrev').disabled = jobPage === 0;
  $('jobNext').disabled = jobPage === lastPage;
  $('jobPageInfo').textContent = visible.length ? `${jobPage * jobPageSize + 1}–${jobPage * jobPageSize + pageRows.length} von ${visible.length} Funktionen` : 'Keine Funktionen in dieser Ansicht';
  for (const job of pageRows) {
    const row = el('tr', undefined, 'job-row job-' + job.status); row.dataset.status = job.status;
    const state = el('td'); state.append(statusBadge(isDeferred(job) ? 'deferred' : job.status));
    row.append(el('td', job.name), state, el('td', `${job.attempts}/${data.options.maxAttempts}`),
      el('td', job.best ? `${job.best.score.toFixed(2)}${job.best.technicalMatch ? ' · technischer Treffer' : ''}` : '—'));
    const actions = el('td');
    const checks = el('button', 'Prüfbericht'); checks.addEventListener('click', () => send('checks', {name: job.name})); actions.append(checks);
    if (job.best || job.lastAttempt || job.roughCandidate) {const best = el('button', job.best ? 'Beste Quelle' : job.lastAttempt ? 'Letzter Entwurf' : 'Grobentwurf'); best.addEventListener('click', () => send('best', {name: job.name})); actions.append(best);}
    if (job.status !== 'handed_off') {const handoff = el('button', 'An Review freigeben'); handoff.disabled = data.alive; handoff.addEventListener('click', () => send('handoff', {name: job.name})); actions.append(handoff);}
    if (job.reason) actions.append(el('p', job.reason, 'muted')); if (job.variantNote) actions.append(el('p', job.variantNote, 'muted'));
    actions.append(el('p', `${job.elapsed || 0} s · ${job.generatedVariants || 0} neue Quellvarianten · ${job.distinctObjects || 0} verschiedene Compilerergebnisse · ${job.reusedBuilds || 0} Builds aus Belegen wiederverwendet${job.previousAttempts ? ' · ' + job.previousAttempts + ' frühere Builds archiviert' : ''}`, 'muted'));
    if (Number.isInteger(job.supportedVariantSources)) actions.append(el('p', `${job.supportedVariantSources} unterstützte Quellvarianten · ${job.historySkippedVariants || 0} anhand früherer Belege ausgelassen`, 'muted'));
    if (job.normalizations?.length) actions.append(el('p', job.normalizations.join('; '), 'muted'));
    if (job.originalCompiler) actions.append(el('p', 'Zusatzprofil: ' + job.originalCompiler.reason, job.originalCompiler.available ? 'muted' : 'warning'));
    if (job.checks) actions.append(el('p', job.checks.error ? 'Prüfbericht noch offen: ' + job.checks.error :
      (job.checks.technicalPassed ? 'Technische Prüfung aller Platzierungen bestanden. Fachliche Review offen.' : 'Technische Prüfung offen oder abweichend. Siehe Prüfbericht.'), 'muted'));
    row.append(actions); table.append(row);
  }
  $('jobs').replaceChildren(table);
  vscode.postMessage({type: 'rendered', kind: 'session', count: data.jobs.length, stateBadges: pageRows.every(j => table.querySelector('tr[data-status="' + j.status + '"] .job-status'))});
}
function renderDeferred(rows) {
  const box = $('deferredJobs'); box.replaceChildren(el('p', `${rows.length} zurückgestellte Funktionen für spätere Nacharbeit.`));
  for (const row of rows) {
    const item = el('div', undefined, 'deferred-entry');
    item.append(el('strong', row.name), el('p', row.reason, 'muted'));
    const open = el('button', 'Lauf anzeigen');
    open.addEventListener('click', () => {$('showDeferred').checked = true; selectSession(row.session);});
    item.append(open); box.append(item);
  }
}
$('showDeferred').onchange = () => {jobPage = 0; if (current) renderSession(current);};
$('jobPrev').onclick = () => {if (current && jobPage > 0) {jobPage--; renderSession(current);}};
$('jobNext').onclick = () => {if (current) {jobPage++; renderSession(current);}};
$('loadDeferred').onclick = () => send('loadDeferred');
function loadCandidates(mode) {
  const limit = Number($('loadCount').value);
  if (!Number.isSafeInteger(limit) || limit < 1) {$('error').textContent = 'Zum Laden eine positive, exakt darstellbare ganze Zahl eingeben.'; return;}
  candidateMode = mode; candidateReady = false; candidates = [];
  $('candidates').replaceChildren(); updateSelection();
  loadingCandidates(true); send(mode === 'retry' ? 'loadRetry' : 'load', {scope: $('scope').value, retryScope: $('retryScope').value || 'incomplete', limit});
}
$('load').onclick = () => loadCandidates($('retryScope').value === 'free' || !$('retryScope').value ? 'free' : 'retry');
function invalidateCandidates() {
  candidateReady = false; candidates = [];
  $('candidates').replaceChildren(); updateSelection(); loadingCandidates(false);
  $('candidateStatus').textContent = 'Filter geändert. Funktionen mit „Funktionen laden“ neu laden.';
}
$('retryScope').onchange = invalidateCandidates;
$('scope').onchange = invalidateCandidates;
$('selectCustom').onclick = () => {
  const count = Number($('selectionAmount').value);
  if ($('selectionAmount').value.trim() === '' || !Number.isSafeInteger(count) || count < 0) {$('error').textContent = 'Zum Markieren eine exakt darstellbare ganze Zahl ab 0 eingeben.'; return;}
  $('error').textContent = ''; selectFirst(count);
};
for (const [id, button] of [['loadCount', 'load'], ['selectionAmount', 'selectCustom']]) $(id).addEventListener('keydown', event => {if (event.key === 'Enter') {event.preventDefault(); if (!$(button).disabled) $(button).onclick();}});
for (const count of [10, 50, 100, 1000]) $('select' + count).onclick = () => selectFirst(count);
$('selectAll').onclick = () => selectFirst(Infinity);
$('selectNone').onclick = () => selectFirst(0);
$('start').onclick = () => {
  if (!candidateReady || starting) return;
  const names = [...document.querySelectorAll('#candidates input:checked')].map(n => n.value);
  if (!names.length) {$('error').textContent = 'Mindestens eine freie Funktion auswählen.'; return;}
  starting = true; loadingCandidates(false);
  $('candidateStatus').textContent = `${names.length} Funktionen werden reserviert und in die Warteschlange aufgenommen …`;
  send(candidateMode === 'retry' ? 'startRetry' : 'start', {names, settings: Object.fromEntries(settingKeys.map(k => [k, Number($(k).value)]))});
};
for (const action of ['refresh', 'stop', 'resume', 'retry', 'retryDeferred', 'review', 'prompt', 'handoffAll']) $(action).onclick = () => send(action);
$('sessions').onchange = () => selectSession($('sessions').value);
window.addEventListener('message', ({data: m}) => {
  if (m.type === 'versions') renderVersions(m.data, m.selected);
  if (m.type === 'preparing') {
    const stages = {validate: 'Frühere Ergebnisse prüfen', reserve: 'Funktionen reservieren', revalidate: 'Reservierungen und Platzierungen erneut prüfen', archive: 'Quellen und Versuchshistorien übernehmen', final: 'Abschlussprüfung vor dem Suchstart'};
    $('candidateStatus').textContent = `Vorbereitung: ${stages[m.data.stage] || m.data.stage} · ${m.data.done}/${m.data.total}. Noch keine Suchjobs gestartet.`;
  }
  if (m.type === 'loadingCandidates') {candidateMode = m.mode || 'free'; candidateReady = false; candidates = []; $('candidates').replaceChildren(); updateSelection(); loadingCandidates(true);}
  if (m.type === 'candidateProgress') {
    const p = m.data || {};
    if (p.stage === 'publicReservations') $('publicReservationStatus').textContent = `Öffentliche Reservierungen frisch geprüft · ${p.blocked} Funktionen gesperrt · ${p.unresolved} ungeklärte Angaben (vorsichtig gesperrt).`;
    const texts = {
      inventory: 'Katalog und aktuelle Reservierungen werden gelesen …',
      filter: `Vorauswahl: ${p.checked || 0}/${p.total || 0} Funktionen geprüft · ${p.possible || 0} mögliche Kandidaten`,
      history: `Frühere Suchläufe: ${p.checked || 0} Jobdateien geprüft · ${p.excluded || 0} Funktionen ausgeschlossen`,
      candidates: `${p.found || 0} geeignete freie Funktionen gefunden …`,
      complete: `Prüfung abgeschlossen · ${p.found || 0} geeignete freie Funktionen gefunden`
    };
    if (texts[p.stage]) $('candidateStatus').textContent = texts[p.stage];
  }
  if (m.type === 'candidates') renderCandidates(m.data, m.mode || 'free');
  if (m.type === 'sessionProgress') progressState(m);
  if (m.type === 'session') {
    if (requestedSession && m.data.id !== requestedSession && !m.selection) return;
    if (m.selection) requestedSession = m.data.id;
    renderSession(m.data);
    updateSession = m.data.id;
    lastSuccessfulUpdate = m.updatedAt || new Date().toISOString();
    updateState = 'updated'; updateFailure = ''; updateStale = false;
    renderUpdateState();
  }
  if (m.type === 'deferred') renderDeferred(m.data);
  if (m.type === 'sessions') {
    $('sessions').replaceChildren(...m.data.map(s => {const o = el('option', `${s.purpose ? s.purpose + ' · ' : ''}${s.created.slice(0, 19)} · ${s.functionCount ?? s.names?.length ?? 0} Funktionen`); o.value = s.id; return o;}));
    vscode.postMessage({type: 'rendered', kind: 'sessions', count: m.data.length});
  }
  if (m.type === 'started') {starting = false; candidateReady = false; candidates = []; $('candidates').replaceChildren(el('p', 'Auswahl gestartet. Zum nächsten Suchlauf zuerst die gewünschte Liste neu laden.')); updateSelection(); loadingCandidates(false); $('candidateStatus').textContent = 'Die Suche läuft. Fortschritt und Ergebnisse stehen unten.';}
  if (m.type === 'error') {$('error').textContent = m.text; starting = false; loadingCandidates(false);}
});
window.addEventListener('error', e => vscode.postMessage({type: 'viewError', text: e.message}));
send('ready', {language: document.documentElement.lang, heading: document.querySelector('h1').textContent});

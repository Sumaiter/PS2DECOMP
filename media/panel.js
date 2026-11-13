'use strict';
const vscode = acquireVsCodeApi();
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => {const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el;};
function send(type, extra = {}) {vscode.postMessage({type, ...extra});}
function tab(id) {
  document.querySelectorAll('.tab').forEach(el => el.classList.toggle('active', el.id === id));
  document.querySelectorAll('nav button').forEach(el => el.classList.toggle('active', el.dataset.tab === id));
}
document.querySelectorAll('nav button').forEach(el => el.addEventListener('click', () => tab(el.dataset.tab)));
for (const id of ['choose', 'compare', 'block', 'decompile', 'applyRough']) $(id).addEventListener('click', () => send(id));
function detail(data) {
  $('name').textContent = data.name;
  $('meta').textContent = `${data.size} Byte · ${data.address} · ${data.section}`;
  $('assembly').textContent = data.asm;
  $('warnings').replaceChildren(...data.warnings.map(text => node('p', text, 'warning')));
  $('suggestions').replaceChildren(...data.suggestions.map(s => {const card = node('article', undefined, 'card'); card.append(node('h3', s.title), node('p', s.text)); return card;}));
  $('contracts').replaceChildren();
  const entries = Object.entries(data.contracts);
  if (!entries.length) $('contracts').append(node('p', 'Für diese Funktion und ihre direkten Aufrufe sind noch keine Signaturbelege im lokalen Index.', 'muted'));
  for (const [name, versions] of entries) {
    const card = node('article', undefined, 'card');
    card.append(node('h3', name + (versions.length > 1 ? ' · mehrere Deklarationen prüfen' : '')));
    for (const record of versions) {
      card.append(node('pre', record.declaration), node('p', record.status, 'tag'));
      const evidence = node('details'); evidence.append(node('summary', 'Beleg und Herkunft'));
      const values = Array.isArray(record.evidence) ? record.evidence : [JSON.stringify(record.evidence)];
      for (const text of values) evidence.append(node('p', text));
      evidence.append(node('p', record.notes ? JSON.stringify(record.notes) : ''), node('p', record.source, 'source'));
      const button = node('button', 'Deklaration einfügen'); button.addEventListener('click', () => send('declaration', {declaration: record.declaration}));
      card.append(evidence, button);
    }
    $('contracts').append(card);
  }
  const table = node('table');
  const head = node('tr'); ['Adresse', 'Zugriff', 'Offset', 'Basis'].forEach(t => head.append(node('th', t))); table.append(head);
  for (const field of data.fields) {const row = node('tr'); [field.address, field.operation, field.offset, field.base].forEach(t => row.append(node('td', t))); table.append(row);}
  $('fields').replaceChildren(data.fields.length ? table : node('p', 'Keine entsprechenden Feldzugriffe erkannt.', 'muted'));
  $('examples').replaceChildren(...data.examples.map(example => {
    const card = node('article', undefined, 'card'); card.append(node('strong', `${example.name} · ${example.size} Byte`), node('p', example.reason));
    const button = node('button', 'Als separate Referenzkopie öffnen'); button.addEventListener('click', () => send('example', {path: example.path})); card.append(button); return card;
  }));
  $('summary').textContent = 'Noch kein Vergleich für diesen Entwurf.';
  $('diff').replaceChildren(); $('relocs').replaceChildren(); $('log').textContent = ''; $('notice').textContent = '';
  tab('guide');
  $('applyRough').disabled = true;
  $('roughSource').textContent = '';
  $('roughWarnings').replaceChildren();
}
function rough(data) {
  $('roughSource').textContent = data.source || 'Kein Grobentwurf verfügbar.';
  $('roughLog').textContent = data.log || '';
  $('roughWarnings').replaceChildren(...data.warnings.map(text => node('p', text, 'warning')));
  $('applyRough').disabled = !data.ok;
  if (data.directory) $('roughWarnings').append(node('p', data.directory, 'source'));
}
function result(data) {
  const summary = $('summary'); summary.replaceChildren();
  const targetsPass = data.byteMatch && data.relocations && data.relocations.passed;
  summary.className = 'card ' + (targetsPass && !data.stale ? 'success' : 'warning');
  const title = data.byteMatch ? (targetsPass ? 'Code und Textziele passen · manuelle Prüfung offen' : 'Byte-Match · Zielprüfung offen oder abweichend') : data.verdict;
  summary.append(node('h2', title));
  if (data.compiledSize !== undefined) summary.append(node('p', `Dein Code: ${data.compiledSize} Byte · Original: ${data.retailSize} Byte · ${data.differentWords} abweichende Wörter`));
  if (data.stale) markStale();
  summary.append(node('p', data.review, 'muted'), node('p', data.snapshot, 'source'));
  if (data.lineMap) summary.append(node('p', data.lineMap.message, data.lineMap.verified ? 'muted' : 'warning'));
  $('relocs').replaceChildren();
  if (data.relocations) {
    const rel = data.relocations;
    $('relocs').append(node('h2', rel.passed ? `Aufruf-/Datenziele: ${rel.checked.length} geprüft` : 'Aufruf-/Datenziele: Abweichungen oder offene Ziele'));
    for (const row of rel.checked.filter(r => !r.ok)) $('relocs').append(node('p', `${row.symbol}: erwartet ${row.expected}, Original ${row.actual}`, 'warning'));
    for (const row of rel.unresolved) $('relocs').append(node('p', `${row.symbol || ''} +0x${row.offset.toString(16)}: ${row.reason}`, 'warning'));
    $('relocs').append(node('p', 'Technischer Vergleich: Initialwerte, gemeinsame Datendefinitionen und semantische Verträge sind dadurch nicht vollständig geprüft.', 'muted'));
  }
  $('diff').replaceChildren();
  if (data.rows.length) {
    const head = node('tr'); ['Referenzadresse', 'C-Zeile', 'Deine Bytes', 'Originalbytes', 'Dein Assembler', 'Original'].forEach(t => head.append(node('th', t))); $('diff').append(head);
    const targetOffsets = new Set((data.sourceDifferences || []).filter(row => row.equal).map(row => row.offset));
    for (const row of data.rows) {
      const tr = node('tr', undefined, row.equal && !targetOffsets.has(row.offset) ? '' : 'different');
      tr.append(node('td', row.address + (row.relocated ? ' *' : '')));
      const line = node('td');
      if (row.sourceLine && !data.stale) {const link = node('button', String(row.sourceLine)); link.addEventListener('click', () => send('revealDifference', {address: row.address})); line.append(link);}
      else line.textContent = '—';
      tr.append(line);
      [row.oursHex, row.retailHex, row.ours, row.retail].forEach(t => tr.append(node('td', t)));
      tr.title = 'Maskiertes Relokationsfeld: ' + row.mask;
      $('diff').append(tr);
    }
    $('relocs').append(node('p', '* Relokierbares Feld: beim Codevergleich maskiert; Zielprüfung separat oben.', 'muted'));
  }
  $('log').textContent = data.log;
  tab('result');
}
function markStale() {
  const summary = $('summary');
  summary.className = 'card warning';
  if (!summary.querySelector('.stale')) summary.append(node('p', 'Entwurf geändert, geschlossen oder gewechselt. Dieses Ergebnis gilt nur für den gespeicherten Snapshot.', 'warning stale'));
}
window.addEventListener('message', event => {
  const message = event.data;
  if (message.type === 'status') $('connection').textContent = `${message.data.contracts} Signaturbelege · Compiler ${message.data.compiler ? 'bereit' : 'fehlt'} · Referenz-Original ${message.data.retail ? 'bereit' : 'fehlt'}`;
  if (message.type === 'detail') detail(message.data);
  if (message.type === 'result') result(message.data);
  if (message.type === 'rough') {rough(message.data); tab('rough');}
  if (message.type === 'stale') markStale();
  if (message.type === 'busy') {$('notice').textContent = message.text; $('compare').disabled = true;}
  if (message.type === 'idle') {$('notice').textContent = ''; $('compare').disabled = false;}
  if (message.type === 'error') {$('notice').textContent = message.text; $('compare').disabled = false;}
});
send('ready');

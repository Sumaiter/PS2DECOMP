'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {translate} = require('../media/localization');
const host = require('../localization');
const pkg = require('../package.json');
test('every contributed command uses PS2DECOMP and translated NLS keys', () => {
  const en = require('../package.nls.json'), de = require('../package.nls.de.json');
  for (const c of pkg.contributes.commands) {
    assert.ok(c.command.startsWith('ps2decomp.')); assert.equal(c.category,'PS2DECOMP');
    const key=c.title.slice(1,-1); assert.ok(en[key]); assert.ok(de[key]);
    assert.doesNotMatch(en[key],/RAC[34]/); assert.doesNotMatch(de[key],/RAC[34]/);
  }
});
test('English fallback and German regional display languages', () => {
  for (const lang of ['en','en-US','fr','ja']) assert.equal(translate('Auswahl dekompilieren',lang),'Decompile selection');
  for (const lang of ['de','de-DE','de-CH']) assert.equal(translate('Decompile selection',lang),'Auswahl dekompilieren');
  assert.equal(translate('4 von 12 markiert','en'),'4 of 12 selected');
  assert.equal(translate('PS2DECOMP Werkbank','en'),'PS2DECOMP Workbench');
});

test('new parser diagnostics preserve English and German coverage', () => {
  assert.equal(translate('Only one canonical first-line compiler-flags comment is supported.','de'),
    'Nur ein kanonischer Compilerflags-Kommentar in der ersten Zeile wird unterstützt.');
  assert.equal(translate('Präprozessor-Zeilenfortsetzungen erfordern eine manuelle Prüfung.','en'),
    'Preprocessor line splices require manual review.');
});

test('RAC4 EE296 profile messages localize and preserve raw evidence', () => {
  const pairs = Object.entries(require('../i18n.json')).filter(([de,en]) => /EE296|EE2\.9\/2\.96/.test(en));
  assert.equal(pairs.length,24);
  const raw='func_00100000 7f504e571215fead2a15a14520bd22dc13507a1f13b479ca8c06a165f4a886a4 C:/synthetic/as.exe';
  for(const [de,en] of pairs) {
    assert.equal(translate(en+' '+raw,'de-DE'),de+' '+raw);
    assert.equal(translate(de+' '+raw,'en'),en+' '+raw);
  }
  assert.equal(translate('EE296: read-only pointer loop','de'),'EE296: nur lesende Zeigerschleife');
  assert.equal(translate('EE296: ursprüngliches Compiler-/Zeitlimit bereits erreicht.','en'),
    'EE296: original compiler/time limit already reached.');
});
test('RAC4 coordination diagnostics cover both languages and preserve selection details', () => {
  const details='func_00100000 C:/synthetic project/reservation-comment-proposal.txt';
  const messages=[
    ['RAC4 coordination.workflow must be public-first or local-first.',
      'RAC4 coordination.workflow muss public-first oder local-first sein.'],
    ['RAC4: Unknown selected functions:', 'RAC4: Unbekannte Funktionen in der Auswahl:'],
    ['RAC4: Publicly reserved, integrated or unresolved:',
      'RAC4: Öffentlich reserviert, bereits integriert oder ungeklärt:'],
    ['RAC4 PR: Selected results need an own public reservation in issue #112. No comment was posted. Local proposal:',
      'RAC4 PR: Ausgewählte Ergebnisse benötigen eine eigene öffentliche Reservierung in Issue #112. Es wurde kein Kommentar veröffentlicht. Lokaler Entwurf:'],
    ['RAC4: At most 500 unfinished functions may be publicly reserved across all scopes. Select fewer results or release finished/dropped reservations first.',
      'RAC4: Über alle Bereiche hinweg dürfen höchstens 500 nicht abgeschlossene Funktionen öffentlich reserviert sein. Weniger Ergebnisse auswählen oder zuerst Reservierungen für abgeschlossene oder aufgegebene Fälle freigeben.'],
    ['RAC4 requires its native coordination gate.', 'RAC4 erfordert seine native Koordinationsprüfung.'],
    ['Cannot establish the current contributor identity.',
      'Die Identität des aktuellen Mitwirkenden kann nicht festgestellt werden.'],
  ];
  for(const [en,de] of messages) {
    assert.equal(translate(en+' '+details,'de-DE'),de+' '+details);
    assert.equal(translate(de+' '+details,'en'),en+' '+details);
  }
});
test('quick-pick object identity and callback arguments stay intact', () => {
  const api={env:{language:'en'},window:{createQuickPick:()=>({})}};
  const picker=host.localizedVscode(api).window.createQuickPick();
  const button={tooltip:'Funktion wählen',iconPath:{id:'eye'}};
  picker.buttons=[button]; assert.equal(picker.buttons[0],button); assert.equal(button.tooltip,'Select function');
});
test('webview identity stays stable for stale-message guards', () => {
  const api={env:{language:'en'},window:{createWebviewPanel:()=>({webview:{}})}};
  const panel=host.localizedVscode(api).window.createWebviewPanel('view','PS2DECOMP Werkbank',1,{});
  const webview=panel.webview; assert.equal(panel.webview,webview);
});
test('panel titles, notifications and progress messages use the display language', async () => {
  const seen=[];
  const api={env:{language:'en'},window:{
    createWebviewPanel:(id,title)=>{seen.push(title);return {webview:{}};},
    showErrorMessage:value=>seen.push(value),
    withProgress:(options,callback)=>callback({report:value=>seen.push(value.message)},{})
  }};
  const window=host.localizedVscode(api).window;
  window.createWebviewPanel('id','PS2DECOMP Werkbank',1,{});
  window.showErrorMessage('Wähle zuerst eine Funktion.');
  await window.withProgress({title:'Funktion wählen'},progress=>progress.report({message:'Funktion wählen'}));
  assert.deepEqual(seen,['PS2DECOMP Workbench','Select a function first.','Select function']);
});
test('review prompts are complete in both languages and preserve paths', () => {
  const prompt=require('../review_prompt'); const data={review:'C:/project/REVIEW.md',id:'abc',owner:'test'};
  assert.match(prompt(data,'en'),/usable across all games/);assert.match(prompt(data,'en'),/C:\/project\/REVIEW.md/);
  assert.match(prompt(data,'de'),/Prüfe die Ergebnisse/);
});
test('webview receives a CSP nonce and locale, source fields stay untouched', () => {
  const value={source:'int Funktion(void){return 1;}',label:'Funktion wählen',name:'func_00100000'};
  host.presentation(value); assert.equal(value.source,'int Funktion(void){return 1;}');
  const html=host.html('<html lang="de"><script nonce="abc" src="vscode-resource://extension/media/panel.js"></script></html>');
  assert.match(html,/<html lang="en"/); assert.match(html,/nonce="abc" src="vscode-resource:\/\/extension\/media\/localization.js"/);
});

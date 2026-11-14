'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const prompt = require('../review_prompt');
const data = {review: 'C:/synthetic project/REVIEW.md', id: 'review-session', owner: 'automatic-owner'};

test('English handoff selects released 80+ history without imposing automatic budgets on manual work', () => {
  for (const language of ['en', 'en-US', 'fr', undefined]) {
    const text = prompt(data, language);
    assert.match(text, /released candidates scoring at least 80, including candidates with previous automatic attempts/);
    assert.match(text, /limits apply only to automatic searches, not to manual review or manual decompilation/);
    assert.match(text, /exhausted automatic budget is not a reason to stop manual/);
    assert.match(text, /Preserve automatic settings, counters and attempt histories, and record manual attempts separately/);
    assert.match(text, /do not repeat recorded unsuccessful variants/);
    assert.match(text, /independently verified match where possible/);
    assert.doesNotMatch(text, /at least 70|Keep time and attempt budgets unchanged/);
  }
});

test('German handoff carries the same manual-work policy for regional display languages', () => {
  for (const language of ['de', 'de-DE', 'de-CH']) {
    const text = prompt(data, language);
    assert.match(text, /freigegebene Kandidaten ab Suchwert 80/);
    assert.match(text, /bereits automatisch versucht/);
    assert.match(text, /nur für automatische Suchläufe, nicht für manuelle Review oder manuelle Decompilierung/);
    assert.match(text, /ausgeschöpftes automatisches Budget ist kein Abbruchgrund/);
    assert.match(text, /automatische Einstellungen, Zähler und Versuchshistorien unverändert/);
    assert.match(text, /manuelle Versuche getrennt/);
    assert.match(text, /keine protokollierten erfolglosen Varianten/);
    assert.doesNotMatch(text, /ab Suchwert 70|Bestehende Zeit- und Versuchslimits unverändert lassen/);
  }
});

test('manual budget exemption preserves identity, handoff, ownership and verification gates', () => {
  const en = prompt(data, 'en'), de = prompt(data, 'de');
  for (const text of [en, de]) {
    for (const value of Object.values(data)) assert.ok(text.includes(value));
    assert.ok(text.includes('blocked') && text.includes('deferred=true'));
    assert.ok(text.includes('result.json') && text.includes('attempts.jsonl'));
    assert.ok(text.includes('AGENTS.md') && text.includes('GameName_PAL'));
  }
  assert.match(en, /explicit application handoff.*own atomic claim/);
  assert.match(en, /Never take a foreign or automatic reservation/);
  assert.match(en, /threshold out of public reservation comments/);
  assert.match(en, /actual relocation target and every placement/);
  assert.match(de, /ausdrückliche Freigabe.*eigenen atomaren Claim/);
  assert.match(de, /niemals fremde oder automatische Reservierungen/);
  assert.match(de, /keine Korrektheitsprozente/);
});

test('English review requires native data provenance and conditional effect checks', () => {
  const text = prompt(data, 'en');
  assert.match(text, /runtime GP for each program and the active game version/);
  assert.match(text, /active placement's mapped reference sections/);
  assert.match(text, /program\/overlay data do not shadow that address/);
  assert.match(text, /comparison and branch opcodes, signed\/unsigned semantics/);
  assert.match(text, /truth table.*boundary values and executed loads, stores and calls/);
  assert.match(text, /Ordinary branch delay instructions execute on both outcomes/);
  assert.match(text, /branch-likely delay instructions execute only when taken/);
  assert.match(text, /NaN\/unordered floating-point cases separately/);
  assert.match(text, /verify logical negation/);
  assert.match(text, /byte-closer variant with different returns or effects is not a better valid candidate/);
});

test('German review requires the same provenance and branch semantics checks', () => {
  const text = prompt(data, 'de-DE');
  assert.match(text, /Laufzeit-GP des jeweiligen Programms und der aktiven Spielversion/);
  assert.match(text, /Referenzabschnitten der aktiven Platzierung/);
  assert.match(text, /Programm-\/Overlaydaten dieselbe Adresse überlagern/);
  assert.match(text, /Vergleichs- und Branch-Opcodes, signed\/unsigned-Semantik/);
  assert.match(text, /Wahrheitstabelle einschließlich Grenzwerten, ausgeführten Loads, Stores und Calls/);
  assert.match(text, /gewöhnlichen Branches wird der Delay-Slot auf beiden Pfaden ausgeführt/);
  assert.match(text, /Branch-Likely nur bei genommenem Branch/);
  assert.match(text, /NaN\/Unordered bei FP-Vergleichen getrennt/);
  assert.match(text, /kontrolliere die logische Negation/);
  assert.match(text, /byte-nähere Variante mit anderen Rückgaben oder Effekten ist kein besserer gültiger Kandidat/);
});

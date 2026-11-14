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

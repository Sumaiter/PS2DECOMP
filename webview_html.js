'use strict';

function documentHtml({asset, cspSource, nonce}, title, body, scripts, styles = []) {
  return `<!doctype html><html lang="de"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${cspSource}; script-src 'nonce-${nonce}'">
    ${['style.css', ...styles].map(name => `<link rel="stylesheet" href="${asset(name)}">`).join('')}
    <title>${title}</title></head><body>${body}
    ${['tabs.js', ...scripts].map(name => `<script nonce="${nonce}" src="${asset(name)}"></script>`).join('')}
    </body></html>`;
}

function workbench(options) {
  return documentHtml(options, 'PS2DECOMP Werkbank', `
    <header class="page-header">
      <div class="eyebrow">PS2DECOMP <span> / MIPS R5900</span></div>
      <h1>Werkbank</h1><p id="connection" role="status">Prüfkern wird geladen …</p>
    </header>
    <section class="card target-card" aria-label="Aktuelle Funktion">
      <div class="target"><span class="section-label">Aktuelle Funktion</span><strong id="name">Noch keine Funktion</strong>
        <span id="meta">Wähle eine Funktion und bearbeite den Entwurf im C-Editor.</span></div>
      <div class="toolbar"><button id="choose">Funktion wählen</button>
        <button id="compare" class="primary">C-Code vergleichen <kbd>Ctrl Alt B</kbd></button></div>
      <div class="toolbar secondary-actions"><button id="decompile">Grob dekompilieren</button><button id="decompileGroup">Gemeinsam dekompilieren</button><button id="block">Baustein einfügen</button></div>
    </section>
    <div id="notice" role="status"></div><div id="warnings"></div>
    <nav class="tabs" aria-label="Werkbank">
      <button data-tab="guide" class="active">Vorschläge & Belege</button><button data-tab="rough">Grobentwurf</button>
      <button data-tab="asm">Original-Assembler</button><button data-tab="result">Vergleich</button>
    </nav>
    <main>
      <section id="guide" class="tab active">
        <div id="suggestions" class="suggestion-grid"></div>
        <h2>Belegte Signaturen</h2><p class="muted">Belege können verschiedene Stände oder widersprüchliche Deklarationen enthalten. Prüfe den für deinen Aufruf gültigen Vertrag.</p>
        <div id="contracts"></div><h2>Beobachtete Feldzugriffe</h2><div id="fields" class="table-scroll"></div>
        <h2>Passende Lernbeispiele</h2><div id="examples" class="suggestion-grid"></div>
        <p class="muted">Bausteine und Ähnlichkeitshinweise sind regelbasierte Hilfen. Sie ersetzen keine Rekonstruktion der tatsächlichen Funktion.</p>
      </section>
      <section id="rough" class="tab" hidden>
        <div id="roughWarnings"></div><button id="applyRough" disabled>Grobentwurf übernehmen · aktuellen Editorinhalt ersetzen</button>
        <p class="muted">Vorheriger Editorinhalt wird separat gesichert. Übernehmen ist rückgängig machbar und speichert die Datei nicht automatisch.</p>
        <pre id="roughSource" class="code-surface">Beim Auswählen wird automatisch ein lokaler m2c-Grobentwurf erzeugt.</pre>
        <details class="disclosure"><summary>m2c-Protokoll</summary><pre id="roughLog"></pre></details>
      </section>
      <section id="asm" class="tab" hidden><pre id="assembly" class="code-surface">Hier erscheint das lokale Original-Listing.</pre></section>
      <section id="result" class="tab" hidden>
        <div id="summary" class="card">Noch kein Vergleich. Änderungen im C-Editor werden direkt aus dem offenen Dokument gelesen, auch vor dem Speichern.</div>
        <div id="relocs"></div><div class="diff-wrap"><table id="diff"></table></div>
        <details class="disclosure"><summary>Compilerprotokoll</summary><pre id="log"></pre></details>
      </section>
    </main>`, ['panel.js']);
}

function automatic(options) {
  const {sizeRange, loadLimit} = options;
  return documentHtml(options, 'PS2DECOMP Automatische Suche', `
    <header class="page-header">
      <div class="eyebrow">PS2DECOMP <span> / LOKAL · OHNE KI</span></div>
      <h1>Automatische Decompilierung</h1><p>Grobentwürfe erzeugen, C-Varianten bauen und die besten Ergebnisse zur Review sammeln.</p>
    </header>
    <div id="error" role="alert"></div>
    <nav class="tabs" aria-label="Automatische Suche">
      <button data-tab="sessionsView" class="active">Suchläufe & Review</button>
      <button data-tab="queueView">Neue Suche</button><button data-tab="deferredView">Zurückgestellt</button>
    </nav>
    <main>
      <section id="sessionsView" class="tab active">
        <div class="section-heading"><div><h2>Suchläufe und Review</h2><p class="muted">Fortschritt verfolgen, Ergebnisse prüfen und gezielt übergeben.</p></div></div>
        <div class="session-picker"><label>Gespeicherte Sitzung <select id="sessions" aria-label="Gespeicherte Sitzung"></select></label><button id="refresh">Aktualisieren</button></div>
        <div id="sessionUpdate" class="session-update" role="status" aria-live="polite">Noch kein Status erfolgreich geladen.</div>
        <div id="status" class="session-overview"><div class="empty-state"><h3>Noch keine Sitzung.</h3><p>Unter „Neue Suche“ Funktionen auswählen und den ersten Suchlauf starten.</p></div></div>
        <div class="session-controls">
          <div class="toolbar"><button id="stop" disabled>Suche pausieren</button><button id="resume" disabled>Wartende Jobs fortsetzen</button></div>
          <div class="toolbar"><button id="review" class="primary" disabled>Review-Datei öffnen</button><button id="prompt" disabled>Review-Prompt kopieren</button></div>
        </div>
        <section id="activeJobs" class="active-jobs" aria-label="Aktuell in Arbeit" hidden><h3>Aktuell in Arbeit</h3><p>Noch kein Status geladen.</p></section>
        <details class="disclosure session-options"><summary>Weitere Aktionen & Reservierungen</summary>
          <p class="muted">Wartende, laufende und fertige Funktionen bleiben gemeinsam reserviert. Agenten bearbeiten währenddessen andere Funktionen. Erst „An Review freigeben“ hebt eine Reservierung auf.</p>
          <div class="toolbar"><button id="retry" disabled>Andere offene Ergebnisse erneut versuchen</button><button id="retryDeferred" disabled>Zurückgestellte dieses Laufs gezielt erneut versuchen</button></div>
          <div class="toolbar"><button id="handoffAll" disabled>Alle Reservierungen freigeben</button></div>
        </details>
        <div class="results-heading"><h2>Ergebnisse</h2><label><input type="checkbox" id="showDeferred">Zurückgestellte Fälle dieses Laufs anzeigen</label></div>
        <p class="muted">Suchwerte sind keine Korrektheitsprozente. Technische Treffer benötigen eine fachliche Review.</p>
        <div id="jobs" class="table-scroll"></div>
        <div class="pagination"><span id="jobPageInfo" role="status" aria-live="polite"></span><div class="toolbar"><button id="jobPrev" disabled>Vorherige Seite</button><button id="jobNext" disabled>Weitere Funktionen</button></div></div>
      </section>
      <section id="queueView" class="tab" hidden>
        <div class="section-heading"><div><h2>Neue Suche</h2><p class="muted">Funktionen auswählen, Suchbudget festlegen und bewusst starten.</p></div></div>
        <section class="card version-selection"><h3>Spielversionen</h3><div id="versions" class="toolbar" role="group" aria-label="Spielversionen"></div><p id="versionStatus" class="muted" role="status"></p>
          <p id="publicReservationStatus" class="muted" role="status">Die Projekt-Reservierungen werden beim Laden und vor jedem Suchstart frisch geprüft.</p></section>
        <section class="card queue-card">
          <h3><span class="step" aria-hidden="true">1</span>Funktionen auswählen</h3>
          <div class="filter-grid">
            <label>Bereich <select id="scope"><option value="both">Hauptprogramm und Overlays</option><option value="main">Hauptprogramm</option><option value="overlays">Overlays</option></select></label>
            <label>Funktionen <select id="retryScope"><option value="free">Neue freie Funktionen</option><option value="incomplete">Offene Ergebnisse ohne zurückgestellte</option><option value="all">Offene Ergebnisse inkl. zurückgestellte / Compilerfehler</option><option value="unclaimed-and-open">Freie Funktionen und offene Ergebnisse inkl. zurückgestellte / Compilerfehler</option></select></label>
            <label>Größenempfehlung <select id="sizePreset"><option value="custom">Eigener Bereich</option></select></label>
            <div class="byte-range"><label>Funktionsgröße von (Byte) <input id="minBytes" type="number" min="0" max="32768" step="1" value="${sizeRange.minBytes}"></label><label>bis (Byte) <input id="maxBytes" type="number" min="8" max="32768" step="1" value="${sizeRange.maxBytes}"></label></div>
          </div>
          <div class="load-actions"><label>Anzahl laden <input id="loadCount" type="number" min="1" step="1" value="${loadLimit}"></label><button id="load">Funktionen laden</button></div>
          <p class="muted compact">Byte-Bereich einschließlich der Grenzen; Standard 8–640 Byte. Die Kontrollfluss-Vorprüfung unterstützt bis zu 32768 Byte.</p>
          <div class="selection-toolbar"><div class="toolbar"><span class="section-label">Markieren:</span><button id="select10">10</button><button id="select50">50</button><button id="select100">100</button><button id="select1000">1.000</button><button id="selectAll">Alle</button><button id="selectNone">Auswahl leeren</button></div>
            <details class="custom-selection"><summary>Anzahl auswählen</summary><div class="toolbar"><label>Anzahl auswählen <input id="selectionAmount" type="number" min="0" step="1" value="10"></label><button id="selectCustom">Anzahl markieren</button></div></details>
          </div>
          <div class="selection-status"><p id="candidateStatus" class="muted" role="status">Keine feste Mengenbegrenzung. Bei großen Listen dauert die Eignungsprüfung entsprechend länger.</p><strong id="selectionCount" role="status" aria-live="polite"></strong></div>
          <div id="candidates"><p class="empty-state">Mit „Funktionen laden“ verfügbare Kandidaten anzeigen.</p></div>
          <p id="candidateMode" class="muted compact">Freie Funktionen: Eine neue Auswahl startet erst über den Startknopf.</p>
        </section>
        <section class="card budget-card"><h3><span class="step" aria-hidden="true">2</span>Suchbudget pro Funktion</h3>
          <div class="settings">
            <label>Parallele Jobs <input id="workers" type="number" min="1" max="32" value="16"></label>
            <label>Maximale Compilerläufe <input id="maxAttempts" type="number" min="1" max="2000" value="30"></label>
            <label>Zeitlimit in Sekunden <input id="seconds" type="number" min="5" max="3600" value="90"></label>
          </div>
          <div class="start-actions"><button class="primary" id="start" disabled>Auswahl dekompilieren</button><span id="startSummary" class="muted">Noch keine Funktionen ausgewählt.</span></div>
          <details class="disclosure"><summary>Wie Suche und Reservierungen funktionieren</summary>
            <p class="muted">100 % Suchwert ist kein freigegebener Match. 80-99 % und ausbleibende Verbesserung beenden die Suche nicht. Sie läuft bis zum technischen Treffer, Zeit-/Versuchslimit oder Ende der unterstützten Varianten. Deine vorhandenen Entwürfe werden nicht überschrieben.</p>
            <p class="muted">Wartende, laufende und fertige Funktionen bleiben gemeinsam reserviert. Agenten bearbeiten währenddessen andere Funktionen. Erst „An Review freigeben“ hebt eine Reservierung auf.</p>
          </details>
        </section>
      </section>
      <section id="deferredView" class="tab" hidden>
        <div class="section-heading"><div><h2>Für später zurückgestellt</h2><p class="muted">Offene Fälle mit ihren Gründen und Belegen gesammelt ansehen.</p></div></div>
        <div class="card"><p>„Analyse nötig“ wird zunächst ausgelassen. Gründe und Quellen bleiben gespeichert. Eine externe Signatur kann zum Kompilieren reichen; der aufgerufene Funktionskörper muss dafür noch nicht decompiliert sein.</p>
          <button id="loadDeferred">Zurückgestellte aus allen Suchläufen anzeigen</button></div><div id="deferredJobs"></div>
      </section>
    </main>`, ['size_range.js', 'automatic.js'], ['automatic.css']);
}

module.exports = {workbench, automatic};

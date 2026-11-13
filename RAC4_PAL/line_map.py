"""Optional diagnostic build; original comparison always uses canonical object."""
import hashlib
import json
import subprocess
from pathlib import Path
import sn_lines


def build(engine, name, source, canonical, flags, env, inserted_line):
    folder = source.parent
    debug = folder / 'line-map.o'
    command = [r'C:\Program Files\Git\bin\bash.exe', 'tools/cc.sh', source.relative_to(engine.root).as_posix(), debug.relative_to(engine.root).as_posix(), *flags, '-g']
    result = {'verified': False, 'lines': {}, 'command': command,
              'message': 'Keine verifizierte C-Zeilen-Zuordnung verfügbar. Abweichende Bytes bleiben in der Tabelle sichtbar.'}
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        if '@ee29' in flags or '@ee29-01' in flags or '@ee296' in flags:
            raise ValueError('Zeilenformat des alternativen EE2.9/2.96-Compilers wird noch nicht unterstützt.')
        run = subprocess.run(command, cwd=engine.root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (folder / 'line-map.log').write_text(run.stdout + run.stderr, encoding='utf-8')
        if run.returncode:
            raise ValueError('Zusätzlicher Diagnose-Build fehlgeschlagen.')
        proof = sn_lines.line_map(canonical, debug, source)
        if hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise ValueError('Snapshot während der Zeilenprüfung geändert.')
        (folder / 'line-map-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        functions = [f for f in proof['canonical_fingerprint']['functions'] if f['name'] == name and f['section'] == '.text']
        if len(functions) != 1:
            raise ValueError('Funktionsfenster für Zeilenprüfung nicht eindeutig.')
        function = functions[0]
        lines = {}
        for entry in proof['ranges']:
            if entry['function'] != name:
                continue
            line = entry['line']
            if inserted_line:
                if line == inserted_line:
                    continue
                if line > inserted_line:
                    line -= 1
            for offset in range(entry['start'], entry['end_exclusive'], 4):
                if function['start'] <= offset < function['start'] + function['size']:
                    lines[str(offset - function['start'])] = line
        result.update(verified=True, lines=lines, proof=str(folder / 'line-map-proof.json'),
                      message='C-Markierungen aus Compiler-Zeileninformationen. Diagnoseobjekt: Code, Daten und Relokationen identisch zum normalen Vergleich. Optimierung kann mehrere Ausdrücke derselben Zeile zuordnen.')
    except Exception as error:
        result['message'] += ' ' + str(error)
    (folder / 'line-map-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result

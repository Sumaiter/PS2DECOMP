"""Local JSON-lines worker. Only draft/snapshot writes under build/studio/."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid
import rough
import candidates
import local_process

NAME = re.compile(r"func_[0-9A-F]{8}$")
SAFE_FLAGS = {"-ffast-math", "-fno-opt-stack", "-mno-split-addresses", "-mcheck-zero-division", "@ee29", "@lit4", "@ee29-01", "-O2", "-G0", "-Wa,-G0", "-fno-schedule-insns", "-fno-schedule-insns2"}

BLOCKS = [
    {"id": "guard", "title": "Früher Rücksprung", "hint": "Rückgabetyp und Bedingung aus den tatsächlichen Aufrufern bestimmen.", "code": "if (${1:condition}) {\n    return ${2:value};\n}\n"},
    {"id": "loop", "title": "Zählschleife", "hint": "Ein Rückwärtssprung belegt eine Schleife, aber noch nicht ihren C-Typ.", "code": "for (s32 ${1:i} = 0; ${1:i} < ${2:count}; ++${1:i}) {\n    ${3:/* Verarbeitung */}\n}\n"},
    {"id": "field", "title": "32-Bit-Feld lesen", "hint": "Nur für belegte 32-Bit-Felder; lw allein beweist keinen Zeigertyp.", "code": "${1:u32} ${2:value} = *(${1:u32} *)((u32)${3:base} + ${4:0x00});\n"},
    {"id": "call", "title": "Belegter Funktionsaufruf", "hint": "Alle Register-, FP- und Stackparameter berücksichtigen. Signaturen stehen unter Belege.", "code": "${1:result} = ${2:function}(${3:arguments});\n"},
    {"id": "switch", "title": "Fallunterscheidung", "hint": "Sprungtabellen und jeden Zielpfad am Original prüfen.", "code": "switch (${1:state}) {\ncase ${2:0}:\n    ${3:/* Verarbeitung */}\n    break;\ndefault:\n    break;\n}\n"},
    {"id": "packet", "title": "Zweiwort-Datenansicht", "hint": "Beispiel für einen belegten Pointer/Längen-Vertrag; kein automatisch erkannter Originaltyp.", "code": "typedef struct ${1:Buffer} {\n    u8 *data;\n    u32 size;\n} ${1:Buffer};\n"}
]


def word(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def signed16(value):
    return (value & 0x7FFF) - (value & 0x8000)


from workbench_core import Engine as SharedEditor

class Engine(SharedEditor):
    def __init__(self, repository):
        self.root = Path(repository).resolve()
        if not (self.root / "tools/cc.sh").is_file():
            raise ValueError("Kein RAC4-Repository: tools/cc.sh fehlt.")
        sys.path.insert(0, str(self.root / "tools"))
        import check_func, levels, layout, match_map, retail, try_func
        from elftools.elf.elffile import ELFFile
        self.check, self.levels, self.layout, self.maps = check_func, levels, layout, match_map
        self.retail, self.try_func, self.ELFFile = retail, try_func, ELFFile
        levels.select("pal")
        self.places = {n: p for n, p in check_func.places().items() if NAME.fullmatch(n) and p[0] == "main"}
        self.contracts = self.load_contracts()
        self.example_cache = None
        self.manual_owner = 'rac4-editor-' + uuid.uuid4().hex

    @contextmanager
    def manual_lease(self, name):
        place = self.place(name)
        self.assert_access(name)
        rows = self.maps.read_map()
        # Existing matching functions remain available as isolated learning copies.
        learning = any(r['status'] == 'match' and place[:3] in {p[:3] for p in r['places']} for r in rows)
        if learning:
            yield
            return
        import claims
        if not claims.claim(self.manual_owner, name):
            raise ValueError(f'{name} wird bereits bearbeitet: {claims.holder(name)}')
        try:
            yield
        finally:
            claims.release(self.manual_owner, name)

    def load_contracts(self):
        paths = [self.root.parent / "RAC4_PAL_FUNKTIONSVERTRAEGE.json"]
        paths += sorted((self.root / "build").glob("parallel*/[abcr]*/signatures.json"))
        result = {}
        for path in paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                continue
            for name, item in data.get("functions", {}).items():
                declaration = item.get("declaration") or item.get("prototype") or item.get("signature", "")
                if not isinstance(declaration, str) or not declaration:
                    continue
                declaration = declaration.rstrip().rstrip(";") + ";"
                record = {"declaration": declaration, "status": item.get("status", "Beleg prüfen"),
                          "evidence": item.get("evidence", []), "notes": item.get("notes", ""), "source": str(path)}
                versions = result.setdefault(name, [])
                if not any(p["declaration"] == declaration for p in versions):
                    versions.append(record)
        return result

    def status(self):
        return {"repository": str(self.root), "python": sys.executable,
                "compiler": (self.root / "toolchain/sn-prodg-3.01/usr/local/sce/ee/gcc/bin/ee-gcc2953.exe").exists(),
                "retail": (self.root / "baserom/pal/main.elf").exists(), "functions": len(self.places),
                "contracts": len(self.contracts), "blocks": BLOCKS}

    def inventory_state(self):
        rows = self.maps.read_map()
        names = {r["name"]: r for r in rows}
        places = {p[:3] for r in rows if r["status"] == "match" for p in r["places"]}
        publication = self.root / "build/published-function-index.json"
        published = json.loads(publication.read_text())["functions"] if publication.exists() else {}
        claims = {}
        for path in (self.root / "build/claims").glob("func_*"):
            if path.is_file():
                try:
                    claims[path.name] = path.read_text().split()[0]
                except (OSError, IndexError):
                    pass
        return names, places, published, claims

    def search(self, query="", all=False):
        query = query.upper().replace("0X", "")
        names, matched, published, claims = self.inventory_state()
        result = []
        for name, place in self.places.items():
            if query not in name.upper():
                continue
            state = "veröffentlicht" if name in published else "bereits passend" if place[:3] in matched else "Namenskollision" if name in names else "offen"
            if not all and state != "offen":
                continue
            if not all and place[4] == "remnant":
                continue
            result.append({"name": name, "size": place[3], "section": place[1], "state": state, "claim": claims.get(name, "")})
        return sorted(result, key=lambda row: (bool(row["claim"]), row["size"], row["name"]))[:120]

    def place(self, name):
        if not NAME.fullmatch(name) or name not in self.places:
            raise ValueError("Unbekannte PAL-Hauptprogramm-Funktion.")
        return self.places[name]

    def assert_access(self, name, owner=None):
        """The automatic queue is also exclusive against the manual workbench."""
        path = self.root / 'build/claims' / name
        try:
            holder = path.read_text().split()[0]
        except (FileNotFoundError, IndexError):
            return
        if holder.startswith('rac4-auto-') and holder != owner:
            raise ValueError(f'{name} ist durch die automatische Suche reserviert ({holder}). Erst dort an die Review übergeben.')

    def asm(self, name):
        path = self.check.asm_of(name, self.place(name)[:4])
        return Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).is_file() else "Assemblerlisting fehlt."

    def detail(self, name):
        place = self.place(name)
        asm = self.asm(name)
        names, matched, published, claims = self.inventory_state()
        calls = sorted(set(re.findall(r"\bfunc_[0-9A-F]{8}\b", "\n".join(asm.splitlines()[2:]))))
        warnings = []
        if place[3] == 4:
            warnings.append("Nur eine 4-Byte-Instruktion katalogisiert: häufig ein Fragment statt einer vollständigen C-Funktion. Aufrufer, Rücksprung und Delay-Slot zuerst prüfen.")
        if name in published or place[:3] in matched:
            warnings.append("Bereits veröffentlicht oder passend: als Lernbeispiel öffnen, nicht erneut als neue Funktion zählen.")
        if name in names and names[name]["file"].startswith("src/game/"):
            warnings.append("Dieser Name gehört bereits einer US-Quelle. PAL-Adresse und Symbolidentität getrennt prüfen.")
        if claims.get(name):
            warnings.append("Parallel reserviert durch " + claims[name] + ". Dein Entwurf bleibt separat.")
        targets = [int(v, 16) for v in re.findall(r"\.L([0-9A-F]{8})", asm)]
        if any(not place[2] <= target < place[2] + place[3] for target in targets):
            warnings.append("Sprungziel außerhalb der katalogisierten Grenze: Funktionsumfang zuerst prüfen.")
        if re.search(r"\b(?:lqc2|sqc2|qmtc2|qmfc2)\b", asm):
            warnings.append("VU-/128-Bit-Operationen vorhanden: einfache skalare C-Bausteine reichen möglicherweise nicht.")
        instructions = re.findall(r"/\* ([0-9A-F]+) [0-9A-F]+ \*/\s+(.+)", asm)
        fields = []
        for address, instruction in instructions:
            m = re.match(r"(lbu|lb|lhu|lh|lw|ld|sb|sh|sw|sd|lwc1|swc1)\s+[^,]+,\s*(-?0x[0-9A-Fa-f]+)\((\$\w+)\)", instruction)
            if m and m[3] not in ("$sp", "$gp", "$zero"):
                fields.append({"address": address, "operation": m[1], "offset": m[2], "base": m[3]})
        suggestions = [{"title": "Schnittstelle zuerst", "text": "Eingabe-, Ausgabe-, FP- und Stackparameter aus Aufrufern und Callees belegen. Das Gerüst enthält absichtlich keine erratene Signatur."}]
        if any(target < int(address, 16) for address, ins in instructions for target in [int(m, 16) for m in re.findall(r"\.L([0-9A-F]{8})", ins)]):
            suggestions.append({"title": "Schleifenmuster", "text": "Mindestens ein Rückwärtssprung: Startwert, Abbruchbedingung und Seiteneffekte bestimmen. Baustein: Zählschleife."})
        if fields:
            suggestions.append({"title": "Feldzugriffe", "text": "Die Tabelle zeigt beobachtete Breiten und Offsets. lw kann ein Skalar oder ein Zeiger sein; daraus folgt noch keine vollständige Struktur."})
        if calls:
            suggestions.append({"title": "Aufrufe", "text": "Belegte Signaturen unten vergleichen. Fehlende Deklarationen bleiben offen; auch die Delay-Slot-Instruktion kann ein Argument setzen."})
        contracts = {callee: self.contracts[callee] for callee in [name, *calls] if callee in self.contracts}
        return {"name": name, "size": place[3], "address": f"0x{place[2]:08X}", "section": place[1], "asm": asm,
                "calls": calls, "fields": fields[:80], "warnings": warnings, "suggestions": suggestions, "contracts": contracts,
                "examples": self.examples(name), "draft": str(self.root / "build/studio" / name / "draft.c")}

    def examples(self, name):
        # Only locally mapped matching public sources; no unverified drafts.
        if self.example_cache is None:
            self.example_cache = []
            for row in self.maps.read_map():
                if row["status"] != "match" or not row["file"].endswith(".c"):
                    continue
                for place in row["places"]:
                    if place[0] == "main":
                        self.example_cache.append((row["name"], row["file"], place[2], place[3], place[1]))
        place = self.place(name)
        candidates = [p for p in self.example_cache if p[4] == place[1] and p[0] != name]
        candidates.sort(key=lambda p: (abs(p[3] - place[3]), abs(p[2] - place[2])))
        return [{"name": p[0], "path": str(self.root / p[1]), "size": p[3], "reason": "Ähnliche Codegröße / nahe PAL-Adresse; inhaltliche Gleichheit nicht belegt."} for p in candidates[:4]]

    def prepare(self, name):
        self.assert_access(name)
        generated = rough.decompile(self, name)
        draft = self.create(name, generated['source'] if generated['ok'] else None)
        return dict(draft, rough=generated)

    def create(self, name, initial=None):
        self.place(name)
        self.assert_access(name)
        path = self.root / "build/studio" / name / "draft.c"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves existing work, including unsaved editor buffers.
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(initial or '#include "common.h"\n\n/* Funktionssignatur zuerst aus Belegen bestimmen.\n * Dieses Gerüst ist ein manueller Entwurf, kein dekompilierter Match.\n */\nvoid ' + name + '(void)\n{\n    /* TODO: Signatur und Implementierung ersetzen. */\n}\n')
        except FileExistsError:
            pass
        return {"path": str(path)}

    def symbol_address(self, symbol, program='main'):
        m = re.fullmatch(r"(?:D|func)_([0-9A-F]{8})(?:__gp|__lui)?", symbol)
        return int(m[1], 16) if m else None

    def relocations(self, obj, code, start, want, place):
        checks, unresolved, covered = [], [], set()
        startup = self.check.disc_bytes(("main", "core.text", 0x157678, 0x2C))
        gp = ((word(startup, 0) & 0xFFFF) << 16) + signed16(word(startup, 0x14))
        with obj.open("rb") as file:
            elf = self.ELFFile(file)
            section = elf.get_section_by_name(".rel.text")
            if section is None:
                return {"checked": [], "unresolved": [], "coveredRelocations": [], "passed": True}
            symbols = elf.get_section(section["sh_link"])
            highs = {}
            all_highs, checked_highs = set(), set()
            for relocation in section.iter_relocations():
                offset = relocation["r_offset"] - start
                kind = relocation["r_info_type"]
                symbol = symbols.get_symbol(relocation["r_info_sym"])
                label = symbol.name
                # A near miss can be longer than the native function.  Never
                # read a partial/out-of-window instruction or silently certify
                # its relocation by skipping the missing reference operand.
                if (offset < 0 or offset % 4 or offset + 4 > len(code)
                        or offset + 4 > len(want)):
                    unresolved.append({"offset": offset, "symbol": label or "Lokales Segment", "type": kind,
                                       "reason": "Relocation instruction is unaligned or outside the complete compiled/native function window.",
                                       "compiledBytes": len(code), "nativeBytes": len(want)})
                    continue
                address = self.symbol_address(label, place[0])
                addend = None
                if kind == 7 and symbol["st_info"]["type"] == "STT_SECTION":
                    reginfo = elf.get_section_by_name(".reginfo")
                    gp0 = struct.unpack_from("<i", reginfo.data(), 20)[0]
                    position = signed16(word(code, offset)) + gp0
                    objects = [s for s in symbols.iter_symbols() if s["st_shndx"] == symbol["st_shndx"] and s["st_info"]["type"] == "STT_OBJECT" and s["st_value"] <= position < s["st_value"] + s["st_size"]]
                    if len(objects) == 1 and self.symbol_address(objects[0].name) is not None:
                        label = objects[0].name
                        address = self.symbol_address(label) + position - objects[0]["st_value"]
                        addend = 0
                if address is None:
                    unresolved.append({"offset": offset, "symbol": label or "Lokales Segment", "type": kind, "reason": "Adresse nicht eindeutig aus kanonischem Symbol belegt (z.B. lokale Sprungtabelle)."})
                    continue
                if kind == 4:
                    addend = (word(code, offset) & 0x3FFFFFF) << 2
                    actual = ((place[2] + offset + 4) & 0xF0000000) | ((word(want, offset) & 0x3FFFFFF) << 2)
                elif kind == 5:
                    highs.setdefault(label, []).append((offset, word(code, offset) & 0xFFFF))
                    all_highs.add(offset)
                    continue
                elif kind == 6:
                    if word(code, offset) >> 26 not in (8, 9, 24, 25, 32, 33, 35, 36, 37, 39, 40, 41, 43, 49, 55, 57, 63):
                        unresolved.append({"offset": offset, "symbol": label, "type": kind, "reason": "LO16-Opcode benötigt gesonderte Auswertung."})
                        continue
                    pairs = highs.get(label, [])
                    if not pairs:
                        unresolved.append({"offset": offset, "symbol": label, "type": kind, "reason": "LO16 ohne belegtes HI16."})
                    for hi_offset, high in pairs:
                        target = (address + (high << 16) + signed16(word(code, offset))) & 0xFFFFFFFF
                        actual = (((word(want, hi_offset) & 0xFFFF) << 16) + signed16(word(want, offset))) & 0xFFFFFFFF
                        checks.append({"offset": offset, "symbol": label, "type": kind, "expected": f"{target:08X}", "actual": f"{actual:08X}", "ok": target == actual})
                        checked_highs.add(hi_offset)
                        covered.update(((hi_offset, 5), (offset, 6)))
                    # Retain the latest HI16 for compiler reuse; all pairs still verified above.
                    highs[label] = pairs[-1:]
                    continue
                elif kind == 7:
                    if addend is None:
                        addend = signed16(word(code, offset))
                    actual = (gp + signed16(word(want, offset))) & 0xFFFFFFFF
                else:
                    unresolved.append({"offset": offset, "symbol": label, "type": kind, "reason": "Relokationsart wird noch nicht automatisch geprüft."})
                    continue
                target = (address + addend) & 0xFFFFFFFF
                checks.append({"offset": offset, "symbol": label, "type": kind, "expected": f"{target:08X}", "actual": f"{actual:08X}", "ok": target == actual})
                covered.add((offset, kind))
            for offset in all_highs - checked_highs:
                unresolved.append({"offset": offset, "reason": "HI16 ohne geprüftes LO16."})
        return {"checked": checks, "unresolved": unresolved, "coveredRelocations": sorted(covered), "passed": not unresolved and all(row["ok"] for row in checks)}

    def compare(self, name, source, with_lines=True, owner=None, timeout=90):
        self.assert_access(name, owner)
        place = self.place(name)
        if not isinstance(source, str) or len(source.encode("utf-8")) > 256_000:
            raise ValueError("C-Entwurf fehlt oder ist größer als256KB.")
        flags = self.layout.cflags(source)
        ee296_env = None
        if '@ee296' in flags:
            profile_started = time.monotonic()
            from . import ee296_profile
            ee296_env, remaining = ee296_profile.comparison_environment(self, name, flags, owner, timeout)
            timeout = min(remaining, timeout - (time.monotonic() - profile_started))
            if timeout <= 0:
                raise ValueError('EE296: ursprüngliches Compiler-/Zeitlimit bereits erreicht.')
        elif any(flag not in SAFE_FLAGS for flag in flags):
            raise ValueError("Nicht unterstützte Dateiflags. Erlaubt: " + ", ".join(sorted(SAFE_FLAGS)))
        if '@ee29-01' in flags and flags != ['@ee29-01', '-O2', '-G0', '-Wa,-G0']:
            raise ValueError('Originalcompilerprofil verlangt genau @ee29-01 -O2 -G0 -Wa,-G0; keine gemischten Profile.')
        if ee296_env is None and '@ee29-01' not in flags and set(flags) & {'-O2', '-G0', '-Wa,-G0'}:
            raise ValueError('Diese zusätzlichen Dateiflags sind nur im geprüften Originalcompilerprofil erlaubt.')
        inserted_line = None
        if '#include "common.h"' not in source:
            lines = source.splitlines()
            flag_line = next((i for i, line in enumerate(lines[:3]) if re.match(r"^/\* cflags: (.*) \*/", line)), None)
            position = flag_line + 1 if flag_line is not None else 0
            lines.insert(position, '#include "common.h"')
            inserted_line = position + 1
            source = "\n".join(lines) + "\n"
        assert self.layout.cflags(source) == flags, "Snapshot flags must remain reproducible"
        directory = self.root / "build/studio" / name / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True)
        path, obj = directory / "candidate.c", directory / "candidate.o"
        path.write_text(source, encoding="utf-8", newline="\n")
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        env = ee296_env if ee296_env is not None else os.environ.copy()
        if '@ee29-01' in flags:
            import compiler_variants
            env = compiler_variants.environment(self.root)
        env["PATH"] = str(bash.parent) + os.pathsep + env.get("PATH", "")
        command = [str(bash), "tools/cc.sh", path.relative_to(self.root).as_posix(), obj.relative_to(self.root).as_posix(), *flags]
        (directory / "run.json").write_text(json.dumps({"command": command, "cwd": str(self.root), "flags": flags}, indent=2), encoding="utf-8")
        try:
            run = local_process.run(command, cwd=self.root, env=env, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            message = 'Compiler-Zeitlimit erreicht; Versuch abgebrochen.'
            (directory / 'compiler.log').write_text(message, encoding='utf-8')
            value = {"name": name, "snapshot": str(path), "sourceHash": hashlib.sha256(path.read_bytes()).hexdigest(), "retailSize": place[3], "flags": flags, "log": message, "verdict": "Compiler-Zeitlimit", "byteMatch": False, "relocations": None, "rows": []}
            (directory / 'result.json').write_text(json.dumps(value), encoding='utf-8')
            return value
        log = run.stdout + run.stderr
        (directory / "compiler.log").write_text(log, encoding="utf-8")
        result = {"name": name, "snapshot": str(path), "log": log[-30000:], "sourceHash": hashlib.sha256(path.read_bytes()).hexdigest(), "retailSize": place[3], "insertedLine": inserted_line, "flags": flags,
                  "review": "Initialwerte, Datenbesitz, Funktionsgrenzen und semantische Verträge bleiben manuell zu prüfen. Ein technischer Match veröffentlicht oder zählt keine Funktion automatisch."}
        def finish(value):
            (directory / "result.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return value
        if run.returncode or not obj.exists():
            return finish(dict(result, verdict="Compilerfehler", byteMatch=False, relocations=None, rows=[]))
        compiled = self.try_func.compiled(obj, name)
        if compiled is None:
            return finish(dict(result, verdict="Funktionsdefinition fehlt", byteMatch=False, relocations=None, rows=[]))
        code, masks, start = compiled
        want = self.check.disc_bytes(place[:4])
        ours, theirs = self.retail.fields(code, masks, start), self.retail.fields(want, masks, start)
        exact = len(code) == len(want) and ours == theirs
        rows = []
        import rabbitizer
        for index in range(max(len(code), len(want)) // 4):
            offset = index * 4
            left, right = word(code, offset) if offset + 4 <= len(code) else None, word(want, offset) if offset + 4 <= len(want) else None
            mask = masks.get(start + offset, 0)
            equal = left is not None and right is not None and (left & ~mask) == (right & ~mask)
            decode = lambda value: rabbitizer.Instruction(value, 0, rabbitizer.InstrCategory.R5900).disassemble() if value is not None else "—"
            rows.append({"address": f"{place[2]+offset:08X}", "offset": offset, "ours": decode(left), "retail": decode(right), "equal": equal, "relocated": bool(mask),
                         "oursHex": code[offset:offset+4].hex(' ').upper() if left is not None else '—', "retailHex": want[offset:offset+4].hex(' ').upper() if right is not None else '—', "mask": f'{mask:08X}'})
        relocations = self.relocations(obj, code, start, want, place) if exact else None
        result.update(verdict="Byte-Match" if exact else "Größe abweichend" if len(code) != len(want) else "Code abweichend", byteMatch=exact, compiledSize=len(code),
                      differentWords=sum(not r["equal"] for r in rows), relocations=relocations, rows=rows)
        if with_lines and (not exact or (relocations and not relocations['passed'])):
            from RAC4_PAL import line_map
            result['lineMap'] = line_map.build(self, name, path, obj, flags, env, inserted_line)
            for row in rows:
                row['sourceLine'] = result['lineMap'].get('lines', {}).get(str(row['offset']))
            bad_targets = {entry['offset'] for entry in (relocations or {}).get('checked', []) if not entry['ok']}
            bad_targets.update(entry['offset'] for entry in (relocations or {}).get('unresolved', []))
            result['sourceDifferences'] = [dict(row, reason='Code weicht ab' if not row['equal'] else 'Relokationsziel abweichend oder offen') for row in rows if not row['equal'] or row['offset'] in bad_targets]
        return finish(result)




def main():
    engine = Engine(sys.argv[sys.argv.index("--repo") + 1])
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            engine._progress = lambda value: print(json.dumps({'id': request.get('id'), 'progress': value}), flush=True)
            value = engine.dispatch(request)
            result = {"id": request.get("id"), "result": value}
        except (Exception, SystemExit) as error:
            result = {"id": request.get("id"), "error": str(error)}
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()

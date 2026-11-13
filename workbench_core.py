"""Shared editor operations and JSON-lines protocol dispatch."""
import re
from pathlib import Path
import rough
import candidates

BLOCKS = [
    {"id": "guard", "title": "Früher Rücksprung", "hint": "Rückgabetyp und Bedingung aus den tatsächlichen Aufrufern bestimmen.", "code": "if (${1:condition}) {\n    return ${2:value};\n}\n"},
    {"id": "loop", "title": "Zählschleife", "hint": "Ein Rückwärtssprung belegt eine Schleife, aber noch nicht ihren C-Typ.", "code": "for (s32 ${1:i} = 0; ${1:i} < ${2:count}; ++${1:i}) {\n    ${3:/* Verarbeitung */}\n}\n"},
    {"id": "field", "title": "32-Bit-Feld lesen", "hint": "Nur für belegte 32-Bit-Felder; lw allein beweist keinen Zeigertyp.", "code": "${1:u32} ${2:value} = *(${1:u32} *)((u32)${3:base} + ${4:0x00});\n"},
    {"id": "call", "title": "Belegter Funktionsaufruf", "hint": "Alle Register-, FP- und Stackparameter berücksichtigen. Signaturen stehen unter Belege.", "code": "${1:result} = ${2:function}(${3:arguments});\n"},
    {"id": "switch", "title": "Fallunterscheidung", "hint": "Sprungtabellen und jeden Zielpfad am Original prüfen.", "code": "switch (${1:state}) {\ncase ${2:0}:\n    ${3:/* Verarbeitung */}\n    break;\ndefault:\n    break;\n}\n"},
    {"id": "packet", "title": "Zweiwort-Datenansicht", "hint": "Beispiel für einen belegten Pointer/Längen-Vertrag; kein automatisch erkannter Originaltyp.", "code": "typedef struct ${1:Buffer} {\n    u8 *data;\n    u32 size;\n} ${1:Buffer};\n"}
]


class Engine:
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
            warnings.append("Dieser Name gehört bereits einer andere Quelle. Referenzadresse und Symbolidentität getrennt prüfen.")
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
                    if place[0] == self.program:
                        self.example_cache.append((row["name"], row["file"], place[2], place[3], place[1]))
        place = self.place(name)
        candidates = [p for p in self.example_cache if p[4] == place[1] and p[0] != name]
        candidates.sort(key=lambda p: (abs(p[3] - place[3]), abs(p[2] - place[2])))
        return [{"name": p[0], "path": str(self.root / p[1]), "size": p[3], "reason": "Ähnliche Codegröße / nahe Referenzadresse; inhaltliche Gleichheit nicht belegt."} for p in candidates[:4]]


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


    def dispatch(self, request):
        if request.get('op') in ('prepare', 'create', 'compare', 'decompile', 'probeBuildable'):
            with self.manual_lease(request['name']):
                return self._dispatch(request)
        return self._dispatch(request)


    def _dispatch(self, request):
        if 'sizeRange' in request:
            from function_size import settings
            self.function_size_range = settings(request['sizeRange'])
        op = request.get("op")
        if op.startswith('auto'):
            from auto_jobs import Manager, plan, DEFAULT_LOAD_LIMIT
            from auto_engine import AutoEngine
            if op == 'autoVersions':
                return AutoEngine.version_availability(self.root)
            if op in ('autoPlan', 'autoRetryPlan', 'autoMixedPlan', 'autoStart', 'autoRetryStart', 'autoMixedStart'):
                versions = request.get('versions', [self.version])
                if versions != [self.version]:
                    raise ValueError('Select the version configured in ps2decomp.json; use a separate workspace for each game version.')
            if not hasattr(self, '_automatic'):
                self._automatic = Manager(self.root)
            manager = self._automatic
            if op == 'autoPlan': return plan(manager.engine, request.get('limit', DEFAULT_LOAD_LIMIT), request.get('scope', 'both'), progress=getattr(self, '_progress', None), settings=request.get('settings'))
            if op == 'autoRetryPlan': return manager.retry_plan(request.get('limit', DEFAULT_LOAD_LIMIT), request.get('scope', 'incomplete'), request.get('programScope', 'both'), settings=request.get('settings'))
            if op == 'autoMixedPlan': return manager.mixed_plan(request.get('limit', DEFAULT_LOAD_LIMIT), request.get('scope', 'both'), settings=request.get('settings'), progress=getattr(self, '_progress', None))
            if op in ('autoRetryStart', 'autoMixedStart'):
                session = manager.create_retry(request['names'], request.get('settings'), progress=getattr(self, '_progress', None),
                                               include_unclaimed=op == 'autoMixedStart')
                return manager.start(session['id'])
            if op == 'autoSessions': return manager.sessions()
            if op == 'autoDeferred': return manager.deferred()
            if op == 'autoChecks': return manager.checks(request['session'],request['name'])
            if op == 'autoStatus': return manager.status(request['session'])
            if op == 'autoStart':
                session = manager.create(request['names'], request.get('settings'), request.get('seeds'))
                return manager.start(session['id'])
            if op == 'autoResume': return manager.start(request['session'])
            if op == 'autoRetry': return manager.retry(request['session'])
            if op == 'autoRetryDeferred': return manager.retry(request['session'], deferred_only=True)
            if op == 'autoStop': return manager.stop(request['session'])
            if op == 'autoHandoff': return manager.handoff(request['session'], request['name'])
            if op == 'autoHandoffMany': return manager.handoff_many(request['session'], request['names'])
            raise ValueError('Unbekannter automatischer Befehl.')
        if op == "status": return self.status()
        if op == "search": return candidates.search(self, request.get("query", "")) if request.get('buildable') else self.search(request.get("query", ""), bool(request.get("all")))
        if op == "scanPlan": return candidates.plan(self, request.get('limit', 40))
        if op == "probeBuildable": return candidates.probe(self, request['name'])
        if op == "detail": return self.detail(request["name"])
        if op == "create": return self.create(request["name"])
        if op == "prepare": return candidates.prepare(self, request['name']) if request.get('buildable') else self.prepare(request["name"])
        if op == "decompile": return rough.decompile(self, request["name"])
        if op == "backup": return rough.backup(self, request["name"], request["source"])
        if op == "compare": return self.compare(request["name"], request["source"])
        if op == "contracts": return {n: p for n, p in self.contracts.items() if request.get("query", "") in n}
        raise ValueError("Unbekannter Befehl.")

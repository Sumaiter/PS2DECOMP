"""Translate generated report prose, preserving code blocks and evidence paths."""
import json
import os
from pathlib import Path
import re

CATALOG=json.loads(Path(__file__).with_name('i18n.json').read_text(encoding='utf-8'))


def markdown(text, language=None):
    language=language or os.environ.get('PS2DECOMP_LANGUAGE','en')
    mapping={en:de for de,en in CATALOG.items()} if language.lower().split('-')[0]=='de' else CATALOG
    pattern=re.compile(r'(?<![\w])(?:'+'|'.join(re.escape(key) for key in sorted(mapping,key=len,reverse=True))+r')(?![\w])')
    output=[];fenced=False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('```'):
            fenced=not fenced;output.append(line);continue
        if fenced:
            output.append(line);continue
        parts=re.split(r'(`[^`]*`)',line)
        output.append(''.join(part if part.startswith('`') else pattern.sub(lambda m:mapping[m[0]],part) for part in parts))
    return ''.join(output)

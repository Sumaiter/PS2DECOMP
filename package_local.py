"""Build an offline VSIX from an explicit source allowlist; no template VSIX."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
SOURCE_SUFFIXES = {'.py', '.js', '.json', '.md', '.txt', '.css', '.png', '.cfg', '.rst', '.h'}
SOURCE_NAMES = {'LICENSE', 'README', '.gitattributes'}
GAME_SUFFIXES = {'.elf', '.iso', '.bin', '.rom', '.img', '.chd', '.cso', '.mdf', '.mds', '.nrg', '.cue'}


def validate_distribution(files, root):
    """Reject local game inputs and archives before writing a VSIX."""
    root = Path(root).resolve()
    for file in files:
        p = Path(file)
        relative = p.relative_to(root)
        if (not p.resolve().is_relative_to(root) or
                any(part.lower() in {'baserom', 'roms', 'isos'} for part in relative.parts) or
                p.suffix.lower() in GAME_SUFFIXES or
                not (p.suffix.lower() in SOURCE_SUFFIXES or p.name in SOURCE_NAMES)):
            raise ValueError('Tool-only distribution: refusing ' + relative.as_posix() + '. Users must supply their own game ROM.')
        with p.open('rb') as stream:
            if stream.read(4) == b'\x7fELF':
                raise ValueError('Tool-only distribution: refusing an ELF binary disguised as ' + relative.as_posix())


def build():
    meta = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'i18n.json').read_text(encoding='utf-8'))
    runtime = ROOT / 'media/localization.js'
    text = runtime.read_text(encoding='utf-8')
    text, count = re.subn(r'  const catalog = .*?;\n', lambda _: '  const catalog = ' + json.dumps(catalog, ensure_ascii=False) + ';\n', text, count=1)
    if count != 1:
        raise ValueError('Localization runtime marker missing.')
    runtime.write_text(text, encoding='utf-8', newline='\n')
    en = json.loads((ROOT / 'package.nls.json').read_text(encoding='utf-8'))
    namespace = 'http://schemas.microsoft.com/developer/vsx-schema/2011'
    ET.register_namespace('', namespace)
    def element(parent, tag, attributes=None, text=None):
        node = ET.SubElement(parent, '{' + namespace + '}' + tag, attributes or {})
        node.text = text
        return node
    manifest = ET.Element('{' + namespace + '}PackageManifest', {'Version': '2.0.0'})
    metadata = element(manifest, 'Metadata')
    element(metadata, 'Identity', dict(Language='en-US', Id=meta['name'], Version=meta['version'], Publisher=meta['publisher']))
    element(metadata, 'DisplayName', text=meta['displayName'])
    element(metadata, 'Description', text=en['extension.description'])
    element(metadata, 'Categories', text=','.join(meta['categories']))
    element(metadata, 'License', text='extension/LICENSE.txt')
    element(metadata, 'Icon', text='extension/' + meta['icon'])
    properties = element(metadata, 'Properties')
    for key, value in {'Microsoft.VisualStudio.Code.Engine': meta['engines']['vscode'],
                       'Microsoft.VisualStudio.Code.ExtensionKind': 'workspace',
                       'Microsoft.VisualStudio.Code.LocalizedLanguages': 'en,de',
                       'Microsoft.VisualStudio.Code.ExecutesCode': 'true',
                       'Microsoft.VisualStudio.Services.GitHubFlavoredMarkdown': 'true'}.items():
        element(properties, 'Property', {'Id': key, 'Value': value})
    element(element(manifest, 'Installation'), 'InstallationTarget', {'Id': 'Microsoft.VisualStudio.Code'})
    element(manifest, 'Dependencies')
    assets = element(manifest, 'Assets')
    for kind, path in [('Microsoft.VisualStudio.Code.Manifest','package.json'),
                       ('Microsoft.VisualStudio.Services.Content.Details','readme.md'),
                       ('Microsoft.VisualStudio.Services.Content.License','LICENSE.txt'),
                       ('Microsoft.VisualStudio.Services.Icons.Default',meta['icon'])]:
        element(assets, 'Asset', {'Type':kind, 'Path':'extension/'+path, 'Addressable':'true'})
    types_ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    types = ET.Element('Types', {'xmlns':types_ns})
    for ext, mime in {'css':'text/css','js':'application/javascript','json':'application/json','md':'text/markdown',
                      'py':'text/plain','txt':'text/plain','png':'image/png','cfg':'text/plain','rst':'text/plain','h':'text/plain',
                      'vsixmanifest':'text/xml'}.items():
        ET.SubElement(types, 'Default', {'Extension':'.'+ext, 'ContentType':mime})
    files = {p for p in ROOT.iterdir() if p.is_file() and (p.suffix in ('.py','.js','.json','.md','.txt') or p.name=='LICENSE')
             and p.name != 'package_local.py'}
    for directory in ('media','schemas','examples','RAC3_PAL','RAC4_PAL','vendor'):
        files |= {p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    validate_distribution(files, ROOT)
    out = ROOT / 'dist' / f'ps2decomp-{meta["version"]}.vsix'
    out.parent.mkdir(exist_ok=True)
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('extension.vsixmanifest', ET.tostring(manifest, encoding='utf-8', xml_declaration=True))
        archive.writestr('[Content_Types].xml', ET.tostring(types, encoding='utf-8', xml_declaration=True))
        for p in sorted(files):
            relative = p.relative_to(ROOT).as_posix()
            relative = {'README.md':'readme.md','LICENSE':'LICENSE.txt'}.get(relative,relative)
            archive.write(p, 'extension/'+relative)
    with ZipFile(out) as archive:
        assert archive.testzip() is None
        names=archive.namelist()
        assert len(names)==len(set(names))
        assert json.loads(archive.read('extension/package.json'))['name']=='ps2decomp'
        for required in ('einrichtungs.md','AGENTS.md','package.nls.de.json','media/logo.png','media/localization.js',
                         'RAC3_PAL/rac3_engine.py','RAC4_PAL/engine.py','vendor/m2c_pycparser/LICENSE'):
            assert 'extension/'+required in names, required
        assert not any(Path(p).suffix.lower() in GAME_SUFFIXES | {'.o', '.exe', '.dll', '.vsix'} for p in names)
    print(out)
    return out


if __name__ == '__main__':
    build()

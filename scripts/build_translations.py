# -*- coding: utf-8 -*-
"""
Updates and compiles the plugin translations.

    python scripts/build_translations.py            # update .ts + compile .qm
    python scripts/build_translations.py --check    # fail if strings are untranslated

1. Finds every tr('...') call in the plugin sources with Python's ast
   module (so implicitly concatenated literals spanning several lines are
   read as the single string Qt will look up at runtime).
2. Merges them into i18n/data_engineering_toolkit_<lang>.ts, keeping the
   existing translations; new strings are added as "unfinished" so they
   can be translated with Qt Linguist or a text editor.
3. Compiles the .ts files to .qm with lrelease (Qt Linguist tools), if
   available on PATH.
"""

import ast
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, 'i18n')
CONTEXT = 'DataEngineeringToolkit'
LANGUAGES = ['es']
SKIP_DIRS = {'tests', 'scripts', '.git', '__pycache__', 'i18n', '.github'}


def source_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


def extract():
    found = {}
    for path in sorted(source_files()):
        with open(path, encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr'
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
                found.setdefault(node.args[0].value, (rel, node.lineno))
    return found


def read_ts(path):
    translations = {}
    if not os.path.exists(path):
        return translations
    for message in ET.parse(path).getroot().iter('message'):
        source = message.findtext('source')
        tr_node = message.find('translation')
        if source is not None and tr_node is not None and tr_node.text and tr_node.get('type') != 'unfinished':
            translations[source] = tr_node.text
    return translations


def write_ts(path, lang, strings, translations):
    ts = ET.Element('TS', version='2.1', language=lang)
    ctx = ET.SubElement(ts, 'context')
    ET.SubElement(ctx, 'name').text = CONTEXT
    for source in sorted(strings, key=lambda s: (strings[s][0], strings[s][1])):
        filename, line = strings[source]
        msg = ET.SubElement(ctx, 'message')
        ET.SubElement(msg, 'location', filename='../' + filename, line=str(line))
        ET.SubElement(msg, 'source').text = source
        node = ET.SubElement(msg, 'translation')
        if source in translations:
            node.text = translations[source]
        else:
            node.set('type', 'unfinished')
    ET.indent(ts) if hasattr(ET, 'indent') else None
    with open(path, 'wb') as fh:
        fh.write(b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n')
        fh.write(ET.tostring(ts, encoding='utf-8'))
        fh.write(b'\n')


def main():
    check = '--check' in sys.argv
    strings = extract()
    missing_total = 0
    for lang in LANGUAGES:
        path = os.path.join(I18N, 'data_engineering_toolkit_{}.ts'.format(lang))
        translations = read_ts(path)
        missing = [s for s in strings if s not in translations]
        missing_total += len(missing)
        if not check:
            write_ts(path, lang, strings, translations)
        print('{}: {} strings, {} untranslated'.format(lang, len(strings), len(missing)))
        for s in missing:
            print('   - ' + s)
        lrelease = shutil.which('lrelease') or shutil.which('lrelease-qt5') or shutil.which('lrelease6')
        if not check and lrelease:
            subprocess.check_call([lrelease, '-silent', path,
                                   '-qm', path[:-3] + '.qm'])
    if check and missing_total:
        sys.exit(1)


if __name__ == '__main__':
    main()

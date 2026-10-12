#!/usr/bin/env python3
"""Build a person-level ID/data catalogue from read-only ResearchDrive tables."""
import argparse
import codecs
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import io
import json
import math
import posixpath
from types import SimpleNamespace
import zipfile
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rd_inventory as inventory

EXTENSIONS = {'.xlsx', '.csv', '.tsv', '.txt'}
MISSING = {'', 'na', 'n/a', 'nan', 'null', 'none', '-', 'missing', 'unknown'}
PERSON_IDS = {'participantid', 'personid', 'donorid', 'subjectid', '100id', '100plusid',
              'bigid', 'nbbid', 'nhbid', 'nbbpostmortem', 'actualnetherlandsbrainbankid'}
GENERIC_IDS = {'id', 'sample', 'sampleid', 'sampleidentifier', 'samplecode', 'code',
               'anonymousid', 'anonymizedid', 'anonymisedid', 'pseudonym', 'tubeid', 'tubeidanonymousid', 'iid'}
TECHNICAL = ('project', 'study', 'batch', 'plate', 'well', 'probe', 'snp', 'variant',
             'protein', 'uniprot', 'analyte', 'sentrix', 'runid', 'round', 'date', 'visit', 'timepoint')
MATERIALS = ('blood', 'brain', 'plasma', 'feces', 'ipscs', 'dna')
# Only reorder names that explicitly identify the same ID scheme.
HEADER_NAMESPACES = {'id100plus': '100plusid', 'id100': '100id', 'idnbb': 'nbbid',
                     'idnhb': 'nhbid', 'idgwas': 'gwasid', 'idexome': 'exomeid',
                     'idpacbio': 'pacbioid', 'idbig': 'bigid'}
FUSED_ID_HEADERS = {'gwasid', 'genotypeid', 'exomeid', 'wesid', 'pacbioid', 'brainid',
                    'autopsyid', 'extid', 'korid', 'ppid', 'participantppid', 'partid',
                    'itemid', 'uniqueid', 'responseid', 'barcode', 'samplebarcode',
                    'specimenbarcode', 'tubebarcode', 'elnsamplenameid'}
GWAS_KEY = 'Key files ID to ID/20261006_gwas_array_df_big_ids.tsv'


def emit(record):
    print(json.dumps(record, ensure_ascii=True), flush=True)


def token(header):
    return re.sub(r'[^a-z0-9]', '', str(header).casefold())


def id_header(header):
    name = token(header)
    if len(name) > 80:
        return False
    if name in {'fid', 'familyid', 'geneid', 'entrezid', 'entrezgeneid',
                'seqid', 'somaid', 'somaseqid', 'targetid'} or any(word in name for word in TECHNICAL):
        return False
    if name in PERSON_IDS | GENERIC_IDS | FUSED_ID_HEADERS or name in HEADER_NAMESPACES:
        return True
    # Boundaries matter: amyloid/thyroid/antacid/IQCODE are not identifier headers.
    words = re.findall(r'[a-z0-9]+', str(header).casefold())
    return bool(set(words) & {'id', 'identifier', 'barcode', 'code'}) or bool(
        re.search(r'(?<=[a-z0-9])(?:ID|Id|Identifier|Barcode|Code)$', str(header)))


def is_person_namespace(namespace):
    return namespace in PERSON_IDS or namespace.startswith(('participantid', 'donorid', 'subjectid', 'personid'))


def relative_path(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '\x00' in value:
        raise ValueError('Source paths must be relative and stay within ResearchDrive')
    return str(path)


def walk(root, relative='.'):
    """Stream file paths without following any directory or file symlinks."""
    initial = inventory.open_drive_path(root, relative_path(relative), directory=True)
    stack = [(initial, os.scandir(initial), Path(relative))]
    try:
        while stack:
            fd, iterator, parent = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close(); os.close(fd); stack.pop()
                continue
            except OSError as exc:
                emit({'kind': 'error', 'path': str(parent), 'stage': 'walk', 'error': str(exc)})
                iterator.close(); os.close(fd); stack.pop()
                continue
            path = parent / entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    stack.append((child, os.scandir(child), path))
                elif stat.S_ISREG(mode):
                    yield str(path)
            except OSError as exc:
                emit({'kind': 'error', 'path': str(path), 'stage': 'walk', 'error': str(exc)})
    finally:
        for fd, iterator, _ in reversed(stack):
            iterator.close(); os.close(fd)


def file_rule(config, path, sheet):
    result = {}
    for rule in config.get('rules', []):
        if not rule.get('enabled', True):
            continue
        if fnmatch.fnmatchcase(path, rule.get('path', '*')) and fnmatch.fnmatchcase(sheet, rule.get('sheet', '*')):
            for key, value in rule.items():
                if key == 'id_columns':
                    result.setdefault(key, {}).update(value)
                elif key not in ('path', 'sheet'):
                    result[key] = value
    return result


def namespace(header, path, sheet, explicit=None):
    if explicit:
        return explicit
    name = HEADER_NAMESPACES.get(token(header), token(header))
    # Generic IDs are project-specific until an explicit rule connects their namespace.
    return f'{path}::{sheet}::{name}' if name in GENERIC_IDS else name


def identifier(cell, missing):
    value = getattr(cell, 'value', cell)
    if value is None:
        return None
    if getattr(cell, 'data_type', None) in ('d', 'e', 'invalid_style'):
        raise ValueError('Date, Excel error or unreadable number format in an ID column')
    if getattr(cell, 'data_type', None) == 'f':
        raise ValueError('ID is a formula; use an exported value rather than executing it')
    if isinstance(value, bool):
        raise ValueError('Boolean in an ID column')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) >= 10**15:
            raise ValueError('Numeric ID may have lost Excel precision; provide an ID stored as text')
        if isinstance(value, float) and not value.is_integer():
            raise ValueError('Non-integer numeric ID; provide an explicit text ID')
        result = str(int(value))
        try:
            display = getattr(cell, 'number_format', '') or ''
        except (IndexError, TypeError):
            raise ValueError('Unusable Excel number format in ID cell; ID excluded and remaining rows retained') from None
        if re.fullmatch(r'0{2,20}', display) and value >= 0:
            result = result.zfill(len(display))
    elif isinstance(value, str):
        result = value.strip()
    else:
        raise ValueError('Unsupported ID cell type (for example a date)')
    if result.casefold() in missing:
        return None
    if len(result) > 512:
        raise ValueError('ID exceeds 512 characters; likely an incorrect ID column')
    return result


def categories(path, rule):
    folder = Path(path).parts[0].casefold()
    materials = []
    if folder in ('blood', 'blood parameters'):
        materials.append('blood')
    if 'brain' in folder:
        materials.append('brain')
    if 'plasma' in folder:
        materials.append('plasma')
    if folder == 'feces':
        materials.append('feces')
    if folder == 'ipscs':
        materials.append('ipscs')
    types = [Path(path).parts[0]]
    if 'proteomic' in folder:
        types.append('proteomics')
    if folder == 'genetics':
        types.append('genetics')
    return rule.get('materials', materials), rule.get('data_types', types)


def process_sheet(path, sheet, rows, config, role):
    rule = file_rule(config, path, sheet)
    if rule.get('ignore'):
        emit({'kind': 'schema', 'path': path, 'sheet': sheet, 'status': 'ignored'})
        return
    explicit = rule.get('id_columns')
    expected_row = rule.get('header_row')
    headers, id_columns, header_row = None, [], None
    for number, cells in enumerate(rows, 1):
        if expected_row and number < expected_row:
            continue
        values = [str(getattr(cell, 'value', cell) or '').strip() for cell in cells]
        selected = [i for i, name in enumerate(values) if name in explicit] if explicit is not None else [i for i, name in enumerate(values) if id_header(name)]
        if selected and (not expected_row or number == expected_row):
            headers, id_columns, header_row = values, selected, number
            break
        if number >= (expected_row or 20):
            break
    if headers is None:
        emit({'kind': 'schema', 'path': path, 'sheet': sheet, 'status': 'no_id_header',
              'note': 'No recognized ID header in the first 20 rows, or configured header not found'})
        return
    grouped = defaultdict(list)
    for index in id_columns:
        grouped[headers[index]].append(index)
    if explicit is not None and set(explicit) - set(headers):
        raise ValueError(f'Configured ID columns not found in sheet {sheet}')
    id_specs = []
    for header, indices in grouped.items():
        ns = namespace(header, path, sheet, explicit.get(header) if explicit else None)
        anchor = header in rule.get('person_columns', []) or ns in config.get('person_namespaces', []) or is_person_namespace(ns)
        id_specs.append((indices, header, ns, anchor))
    scopes = rule.get('namespace_scope_columns',
                      {'ID_ORIGINAL': ['Study'], 'I_ID': ['Study']} if path == GWAS_KEY else {})
    if any(column not in headers for columns in scopes.values() for column in columns):
        raise ValueError('Namespace scope column is missing from the header')
    presence = rule.get('presence_columns', [])
    if set(presence) - set(headers):
        raise ValueError(f'Configured presence columns not found in {sheet}')
    presence_indices = [headers.index(name) for name in presence]
    material_column = rule.get('material_column')
    if material_column and material_column not in headers:
        raise ValueError(f'Configured material_column not found in {sheet}')
    material_index = headers.index(material_column) if material_column else None
    materials, types = categories(path, rule)
    emit({'kind': 'schema', 'path': path, 'sheet': sheet, 'status': 'selected', 'role': role,
          'header_row': header_row, 'columns': headers, 'id_columns': [
              {'column': h, 'column_indices': [i + 1 for i in indices], 'namespace': ns,
               'scope_columns': scopes.get(h, []), 'person_anchor': anchor}
              for indices, h, ns, anchor in id_specs],
          'materials': materials, 'data_types': types,
          'presence': 'values_observed' if presence else 'id_listed'})
    missing = MISSING | {str(value).casefold() for value in config.get('missing_values', [])}
    for number, cells in enumerate(rows, header_row + 1):
        ids = []
        row_conflict = False
        for indices, header, ns, anchor in id_specs:
            try:
                values = {value for index in indices if index < len(cells)
                          for value in [identifier(cells[index], missing)] if value is not None}
                if len(values) > 1:
                    row_conflict = True
                    raise ValueError('Conflicting duplicate ID columns; row excluded rather than selecting the first ID')
                value = next(iter(values), None)
                if value is not None and scopes.get(header):
                    scope = []
                    for scope_column in scopes[header]:
                        index = headers.index(scope_column)
                        scope_value = identifier(cells[index], missing) if index < len(cells) else None
                        if scope_value is None:
                            raise ValueError('Missing namespace scope value; scoped ID excluded')
                        scope.append([scope_column, scope_value])
                    ns = ns + '::scope=' + json.dumps(scope, ensure_ascii=True, separators=(',', ':'))
            except ValueError as exc:
                emit({'kind': 'error', 'path': path, 'sheet': sheet, 'row': number,
                      'column': header, 'stage': 'id_cell', 'error': str(exc)})
                continue
            if value is not None:
                column = header if len(indices) == 1 else header + ' [columns ' + ','.join(str(i + 1) for i in indices) + ']'
                ids.append({'column': column, 'namespace': ns, 'id': value, 'anchor': anchor})
        if row_conflict:
            continue
        if not ids:
            continue
        observed = any(i < len(cells) and getattr(cells[i], 'data_type', None) != 'f'
                       and getattr(cells[i], 'value', cells[i]) is not None
                       and str(getattr(cells[i], 'value', cells[i])).strip().casefold() not in missing
                       for i in presence_indices)
        row_materials = materials
        if material_index is not None:
            raw_material = getattr(cells[material_index], 'value', cells[material_index]) if material_index < len(cells) else ''
            mapped = rule.get('material_values', {}).get(str(raw_material).strip(), '')
            row_materials = [mapped] if isinstance(mapped, str) and mapped else mapped if isinstance(mapped, list) else []
        if role == 'key' and any(item['anchor'] for item in ids):
            config['_key_anchor_rows'] = config.get('_key_anchor_rows', 0) + 1
        emit({'kind': 'row', 'path': path, 'sheet': sheet, 'row': number, 'role': role, 'ids': ids,
              'materials': row_materials, 'data_types': types,
              'presence': 'values_observed' if observed else 'no_values_observed' if presence_indices else 'id_listed'})


def xml_workbook(handle):
    """Read cached OOXML cells without applying cosmetic fills/fonts."""
    from defusedxml import ElementTree as ET
    from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
    main = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    rel = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    archive = zipfile.ZipFile(handle)
    try:
        if any(info.flag_bits & 1 for info in archive.infolist()):
            raise ValueError('Encrypted ZIP workbook skipped; no unlocking attempted')

        def small_xml(name):
            with archive.open(name) as entry:
                content = inventory.LimitedXMLReader(entry, 16 * 1024 * 1024).read()
            return ET.fromstring(content, forbid_dtd=True)

        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            with archive.open('xl/sharedStrings.xml') as entry:
                reader = inventory.LimitedXMLReader(entry, 512 * 1024 * 1024)
                for _, element in ET.iterparse(reader, events=('end',), forbid_dtd=True):
                    if element.tag == main + 'si':
                        shared.append(''.join(node.text or '' for node in element.iter(main + 't')))
                        element.clear()
        formats = []
        if 'xl/styles.xml' in archive.namelist():
            styles = small_xml('xl/styles.xml')
            custom = {int(item.attrib['numFmtId']): item.attrib['formatCode']
                      for item in styles.findall(main + 'numFmts/' + main + 'numFmt')}
            for style in styles.findall(main + 'cellXfs/' + main + 'xf'):
                number = int(style.get('numFmtId', '0'))
                formats.append(custom.get(number, BUILTIN_FORMATS.get(number)))
        if not formats:
            formats = ['General']
        relationships = {item.get('Id'): item.get('Target', '')
                         for item in small_xml('xl/_rels/workbook.xml.rels')
                         if item.get('TargetMode') != 'External'}
        sheets = []
        for sheet in small_xml('xl/workbook.xml').findall(main + 'sheets/' + main + 'sheet'):
            target = relationships.get(sheet.get(rel), '')
            if not target:
                raise ValueError('Missing or external worksheet relationship')
            member = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            sheets.append((sheet.get('name', ''), member))

        def rows(member):
            with archive.open(member) as entry:
                reader = inventory.LimitedXMLReader(entry, 512 * 1024 * 1024)
                last_row = 0
                for _, element in ET.iterparse(reader, events=('end',), forbid_dtd=True):
                    if element.tag != main + 'row':
                        continue
                    row_number = int(element.get('r', last_row + 1))
                    if row_number <= last_row or row_number > 1048576:
                        raise ValueError('Invalid or out-of-order worksheet row number')
                    for _ in range(last_row + 1, row_number):
                        yield []
                    cells = []
                    for cell in element.findall(main + 'c'):
                        reference = re.fullmatch(r'([A-Z]+)[0-9]+', cell.get('r', ''))
                        index = 0
                        if reference:
                            for char in reference[1]:
                                index = index * 26 + ord(char) - 64
                        else:
                            index = len(cells) + 1
                        if not 1 <= index <= 16384:
                            raise ValueError('Invalid worksheet column index')
                        while len(cells) < index:
                            cells.append(None)
                        kind = cell.get('t', 'n')
                        raw = cell.findtext(main + 'v')
                        style_index = int(cell.get('s', '0'))
                        display = formats[style_index] if 0 <= style_index < len(formats) else None
                        if kind == 's':
                            value = shared[int(raw)] if raw is not None else None
                        elif kind == 'inlineStr':
                            value = ''.join(node.text or '' for node in cell.iter(main + 't'))
                        elif kind == 'b':
                            value = bool(int(raw)) if raw is not None else None
                        elif kind == 'n' and raw is not None:
                            value = float(raw) if any(char in raw.lower() for char in '.e') else int(raw)
                        else:
                            value = raw
                        if kind == 'n' and display and is_date_format(display):
                            kind = 'd'
                        if kind == 'n' and display is None:
                            kind = 'invalid_style'
                        # Missing cached formula values remain None; no formulas are evaluated.
                        cells[index - 1] = SimpleNamespace(value=value, data_type=kind,
                                                           number_format=display or '')
                    last_row = row_number
                    yield cells
                    element.clear()
        yield sheets, rows
    finally:
        archive.close()


@contextmanager
def xml_reader(handle):
    reader = xml_workbook(handle)
    try:
        yield next(reader)
    finally:
        reader.close()


def tolerant_sheet_rows(sheet, fallback):
    count = 0
    try:
        for row in sheet.iter_rows():
            yield row
            count += 1
    except (IndexError, TypeError) as exc:
        emit({'kind': 'warning', 'path': fallback['path'], 'sheet': sheet.title,
              'message': f'Workbook formatting could not be applied ({type(exc).__name__}); continuing from cached XML cells'})
        with xml_reader(fallback['handle']) as (sheets, rows):
            member = next(member for name, member in sheets if name == sheet.title)
            for number, row in enumerate(rows(member)):
                if number >= count:
                    yield row


def read_file(root, path, config, role):
    reason = inventory.content_skip_reason(path)
    if reason:
        emit({"kind": "skipped", "path": path, "role": role, "reason": reason})
        return
    if file_rule(config, path, '').get('ignore') or 'readme' in Path(path).name.casefold():
        emit({'kind': 'schema', 'path': path, 'sheet': '', 'status': 'ignored'})
        return
    ending = Path(path).suffix.casefold()
    rule = file_rule(config, path, 'table')
    with os.fdopen(inventory.open_drive_path(root, path), 'rb') as handle:
        if ending == '.xlsx':
            import openpyxl
            # Use defusedxml and disable optional lxml parsing for consistent entity protection.
            if not openpyxl.DEFUSEDXML:
                raise RuntimeError('Install defusedxml in this conda environment before reading XLSX')
            signature = handle.read(8); handle.seek(0)
            if signature == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                emit({'kind': 'skipped', 'path': path, 'role': role,
                      'reason': 'Locked or legacy OLE workbook; no password or conversion attempted'})
                return
            try:
                book = openpyxl.load_workbook(handle, read_only=True, data_only=True, keep_links=False)
            except (TypeError, IndexError) as exc:
                emit({'kind': 'warning', 'path': path,
                      'message': f'Workbook formatting could not be loaded ({type(exc).__name__}); reading cached XML cells and number formats'})
                handle.seek(0)
                with xml_reader(handle) as (sheets, rows):
                    for title, member in sheets:
                        try:
                            process_sheet(path, title, iter(rows(member)), config, role)
                        except Exception as error:
                            emit({'kind': 'error', 'path': path, 'sheet': title, 'stage': role, 'error': str(error)})
                return
            try:
                for sheet in book:
                    try:
                        sheet.reset_dimensions()
                        process_sheet(path, sheet.title,
                                      iter(tolerant_sheet_rows(sheet, {'path': path, 'handle': handle})), config, role)
                    except Exception as exc:
                        emit({'kind': 'error', 'path': path, 'sheet': sheet.title, 'stage': role, 'error': str(exc)})
            finally:
                book.close()
        else:
            prefix = handle.read(4096); handle.seek(0)
            encoding = rule.get('encoding', 'utf-8-sig')
            if not rule.get('encoding'):
                if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
                    encoding = 'utf-32'
                elif prefix.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                    encoding = 'utf-16'
            if b'\x00' in prefix and encoding == 'utf-8-sig':
                raise ValueError('Binary or unsupported text encoding; set an explicit encoding rule')
            delimiter = rule.get('delimiter')
            if delimiter is None:
                if ending == '.tsv':
                    delimiter = '\t'
                else:
                    import csv
                    try:
                        delimiter = csv.Sniffer().sniff(prefix.decode(encoding), delimiters=',;\t|').delimiter
                    except (csv.Error, UnicodeError):
                        delimiter = ','
            import csv
            csv.field_size_limit(16 * 1024 * 1024)
            with io.TextIOWrapper(handle, encoding=encoding, newline='') as text:
                process_sheet(path, 'table', iter(csv.reader(text, delimiter=delimiter)), config, role)


def worker():
    os.environ['OPENPYXL_LXML'] = 'False'
    with open('/job.json', encoding='utf-8') as handle:
        config = json.load(handle)
    config['_key_anchor_rows'] = 0
    root = Path('/researchdrive')
    key_directory = relative_path(config.get('key_directory', 'Key files ID to ID'))
    for role, paths in (('key', walk(root, key_directory)), ('data', walk(root))):
        if role == 'data':
            emit({'kind': 'key_phase_done'})
            if config.get('_keys_only') or not config['_key_anchor_rows']:
                return
        for path in paths:
            if role == 'data' and (path == key_directory or path.startswith(key_directory + '/')):
                continue
            if Path(path).suffix.casefold() not in EXTENSIONS:
                continue
            try:
                read_file(root, path, config, role)
            except Exception as exc:
                emit({'kind': 'error', 'path': path, 'stage': role, 'error': str(exc)})
            emit({'kind': 'file_done', 'path': path, 'role': role})


class UnionFind:
    def __init__(self):
        self.parents = {}

    def find(self, item):
        self.parents.setdefault(item, item)
        root = item
        while self.parents[root] != root:
            root = self.parents[root]
        while self.parents[item] != item:
            parent = self.parents[item]
            self.parents[item] = root
            item = parent
        return root

    def join(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parents[max(a, b)] = min(a, b)


def create_database(db):
    db.executescript('''
    CREATE TABLE nodes (node INTEGER PRIMARY KEY, namespace TEXT, identifier TEXT, anchor INTEGER,
                        UNIQUE(namespace,identifier));
    CREATE TABLE links (a INTEGER,b INTEGER,path TEXT,sheet TEXT,row_number INTEGER);
    CREATE TABLE observations (path TEXT,sheet TEXT,row_number INTEGER,column_name TEXT,
                               namespace TEXT,identifier TEXT,role TEXT,materials TEXT,data_types TEXT,presence TEXT);
    CREATE TABLE aliases (namespace TEXT,identifier TEXT,person TEXT,status TEXT,component TEXT,
                          PRIMARY KEY(namespace,identifier));
    CREATE INDEX alias_value ON aliases(identifier);
    CREATE TABLE people (person TEXT PRIMARY KEY,canonical_namespace TEXT,canonical_id TEXT);
    CREATE TABLE matches (person TEXT,path TEXT,sheet TEXT,row_number INTEGER,column_name TEXT,
                         namespace TEXT,identifier TEXT,status TEXT,materials TEXT,data_types TEXT,presence TEXT);
    CREATE INDEX match_person ON matches(person,status);
    CREATE INDEX observation_id ON observations(namespace,identifier,role);
    CREATE INDEX observation_value ON observations(identifier,role);
    ''')


def ingest(db, records, output):
    files = rows = errors = key_rows = schemas = no_id_sheets = skipped = warnings = 0
    printed_errors = Counter()
    with (output / 'schemas.jsonl').open('x', encoding='utf-8') as schema_file, \
            (output / 'errors.jsonl').open('x', encoding='utf-8') as error_file, \
            (output / 'skipped_files.jsonl').open('x', encoding='utf-8') as skipped_file:
        for record in records:
            kind = record.get('kind')
            if kind == 'schema':
                schema_file.write(json.dumps(record, ensure_ascii=True) + '\n'); schema_file.flush()
                schemas += 1
                no_id_sheets += record.get('status') == 'no_id_header'
            elif kind == 'skipped':
                skipped_file.write(json.dumps(record, ensure_ascii=True) + '\n'); skipped_file.flush()
                skipped += 1
                print(f"Skipped: {record['path']}: {record['reason']}", flush=True)
            elif kind == 'warning':
                schema_file.write(json.dumps(record, ensure_ascii=True) + '\n'); schema_file.flush()
                warnings += 1
                print(f"Reader note: {record['path']}: {record['message']}", flush=True)
            elif kind == 'key_phase_done':
                db.commit()
                anchors = db.execute('SELECT COUNT(*) FROM nodes WHERE anchor=1').fetchone()[0]
                print(f'Key rows: {key_rows:,}; distinct person IDs: {anchors:,}.', flush=True)
                if not anchors:
                    raise RuntimeError('No person IDs recognized in readable keys; data scan stopped. Inspect schemas.jsonl and configure person_columns')
            elif kind == 'error':
                error_file.write(json.dumps(record, ensure_ascii=True) + '\n'); error_file.flush()
                errors += 1
                group = (record.get('path'), record.get('sheet'), record.get('column'), record['error'])
                printed_errors[group] += 1
                if printed_errors[group] <= 3:
                    print(f"Read issue: {record.get('path', '')} [{record.get('column', record.get('sheet', ''))}]: {record['error']}", file=sys.stderr, flush=True)
                elif printed_errors[group] == 4:
                    print('Further identical issues suppressed on screen; all rows remain in errors.jsonl.', file=sys.stderr, flush=True)
            elif kind == 'file_done':
                files += 1
                if files == 1 or files % 25 == 0:
                    print(f'Read {files:,} table files; {rows:,} rows containing IDs.', flush=True)
                db.commit()
            elif kind == 'row':
                rows += 1
                key_nodes = []
                for item in record['ids']:
                    if record['role'] == 'key':
                        db.execute('INSERT INTO nodes(namespace,identifier,anchor) VALUES(?,?,?) '
                                   'ON CONFLICT(namespace,identifier) DO UPDATE SET anchor=MAX(anchor,excluded.anchor)',
                                   (item['namespace'], item['id'], item['anchor']))
                        node = db.execute('SELECT node FROM nodes WHERE namespace=? AND identifier=?',
                                          (item['namespace'], item['id'])).fetchone()[0]
                        key_nodes.append(node)
                    db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?)',
                               (record['path'], record['sheet'], record['row'], item['column'], item['namespace'],
                                item['id'], record['role'], json.dumps(record['materials']),
                                json.dumps(record['data_types']), record['presence']))
                if key_nodes:
                    key_rows += 1
                    for node in key_nodes[1:]:
                        db.execute('INSERT INTO links VALUES(?,?,?,?,?)',
                                   (key_nodes[0], node, record['path'], record['sheet'], record['row']))
                if rows % 10000 == 0:
                    db.commit()
        db.commit()
    return {'table_files_read': files, 'table_schemas': schemas, 'rows_with_ids': rows,
            'key_rows_with_ids': key_rows, 'reader_warnings': warnings, 'read_issues': errors, 'skipped_files': skipped, 'sheets_without_recognized_ids': no_id_sheets}


def csv_writer(path, headers):
    import csv
    handle = path.open('x', newline='', encoding='utf-8')
    writer = csv.writer(handle)
    writer.writerow(headers)
    return handle, writer


def safe_row(writer, values):
    writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r'))
                     else value for value in values])


def build_people(db, output, config):
    graph = UnionFind()
    nodes = {node: (ns, value, bool(anchor)) for node, ns, value, anchor in
             db.execute('SELECT node,namespace,identifier,anchor FROM nodes')}
    for node in nodes:
        graph.find(node)
    for a, b in db.execute('SELECT a,b FROM links'):
        graph.join(a, b)
    groups = defaultdict(list)
    for node in nodes:
        groups[graph.find(node)].append(node)
    priorities = config.get('canonical_priority', ['participantid', '100plusid', '100id', 'personid', 'donorid', 'bigid'])
    allowed_multiple = set(config.get('allow_multiple_person_ids', []))
    resolved = conflicts = unanchored = 0
    handle, writer = csv_writer(output / 'mapping_conflicts.csv', ['component', 'reason', 'ids'])
    try:
        for group in groups.values():
            anchors = defaultdict(set)
            for node in group:
                ns, value, anchor = nodes[node]
                if anchor:
                    anchors[ns].add(value)
            ambiguous = [ns for ns, values in anchors.items() if len(values) > 1 and ns not in allowed_multiple]
            label = hashlib.sha256(json.dumps(sorted(nodes[n][:2] for n in group), ensure_ascii=True).encode()).hexdigest()[:20]
            person = None
            if ambiguous:
                status = 'conflicting_mapping'; conflicts += 1
                safe_row(writer, [label, 'Multiple person IDs in namespace(s): ' + ', '.join(ambiguous),
                                  json.dumps([nodes[n][:2] for n in group], ensure_ascii=True)])
            elif not anchors:
                status = 'no_person_anchor'; unanchored += 1
                safe_row(writer, [label, 'No recognized person/donor ID; configure person_columns for this key sheet',
                                  json.dumps([nodes[n][:2] for n in group], ensure_ascii=True)])
            else:
                ns = min(anchors, key=lambda name: (priorities.index(name) if name in priorities else len(priorities), name))
                value = min(anchors[ns])
                person = ns + ':' + value
                status = 'confirmed'; resolved += 1
                db.execute('INSERT INTO people VALUES(?,?,?)', (person, ns, value))
            for node in group:
                ns, value, _ = nodes[node]
                db.execute('INSERT INTO aliases VALUES(?,?,?,?,?)', (ns, value, person, status, label))
        db.commit()
    finally:
        handle.close()
    return {'people': resolved, 'conflicting_components': conflicts, 'unanchored_components': unanchored}


def match_ids(db, output):
    headers = ['person_id', 'status', 'path', 'sheet', 'row', 'column', 'namespace', 'id', 'candidate_people', 'presence']
    matched_handle, matched = csv_writer(output / 'id_matches.csv', headers)
    unresolved_handle, unresolved = csv_writer(output / 'unresolved_ids.csv', headers)
    counts = defaultdict(int)
    try:
        cursor = db.execute('SELECT path,sheet,row_number,column_name,namespace,identifier,materials,data_types,presence '
                            "FROM observations WHERE role='data' ORDER BY path,sheet,row_number,column_name")
        for path, sheet, row, column, ns, value, materials, types, presence in cursor:
            exact = db.execute('SELECT person,status FROM aliases WHERE namespace=? AND identifier=?', (ns, value)).fetchone()
            candidates = []
            if exact:
                person, status = exact
            else:
                person = None
                candidates = [p for (p,) in db.execute("SELECT DISTINCT person FROM aliases WHERE identifier=? AND status='confirmed' ORDER BY person", (value,))]
                status = 'candidate_namespace_unconfirmed' if len(candidates) == 1 else 'ambiguous_value' if candidates else 'unmatched'
            values = [person or '', status, path, sheet, row, column, ns, value, json.dumps(candidates), presence]
            safe_row(matched if person else unresolved, values)
            counts[status] += 1
            db.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (person, path, sheet, row, column, ns, value, status, materials, types, presence))
        db.commit()
    finally:
        matched_handle.close(); unresolved_handle.close()
    return dict(counts)


def namespace_candidates(db, output):
    """Value overlap is a review aid, never permission to merge identity namespaces."""
    handle, writer = csv_writer(output / 'namespace_candidates.csv',
        ['file', 'sheet', 'id_column', 'current_namespace', 'possible_key_namespace', 'overlapping_ids', 'people', 'status'])
    try:
        for row in db.execute("SELECT o.path,o.sheet,o.column_name,o.namespace,a.namespace, "
                              "COUNT(DISTINCT o.identifier),COUNT(DISTINCT a.person) "
                              "FROM observations o JOIN aliases a ON o.identifier=a.identifier "
                              "WHERE o.role='data' AND a.status='confirmed' AND o.namespace!=a.namespace "
                              "GROUP BY o.path,o.sheet,o.column_name,o.namespace,a.namespace "
                              "ORDER BY o.path,o.sheet,o.column_name,COUNT(DISTINCT o.identifier) DESC,a.namespace"):
            safe_row(writer, [*row, 'candidate_only_review_required'])
    finally:
        handle.close()


def export_people(db, output):
    namespaces = [ns for (ns,) in db.execute('SELECT DISTINCT namespace FROM aliases ORDER BY namespace')]
    material_names = set(MATERIALS)
    for (encoded,) in db.execute("SELECT DISTINCT materials FROM matches WHERE person IS NOT NULL"):
        material_names.update(json.loads(encoded))
    material_names = list(MATERIALS) + sorted(material_names - set(MATERIALS))
    type_names = set()
    for (encoded,) in db.execute("SELECT DISTINCT data_types FROM matches WHERE person IS NOT NULL"):
        type_names.update(json.loads(encoded))
    type_names = sorted(type_names)
    overview_handle, overview = csv_writer(output / 'person_overview.csv',
        ['person_id', 'canonical_namespace', 'canonical_id', *['ids:' + ns for ns in namespaces],
         *material_names, *['data:' + name for name in type_names], 'matched_file_count', 'source_files'])
    alias_handle, aliases = csv_writer(output / 'id_aliases.csv',
        ['person_id', 'status', 'namespace', 'id', 'key_file', 'sheet', 'row', 'column', 'component'])
    evidence_handle, evidence = csv_writer(output / 'data_presence.csv',
        ['person_id', 'material', 'data_type', 'presence', 'file', 'sheet', 'row', 'id_column', 'namespace', 'id'])
    try:
        for row in db.execute("SELECT a.person,a.status,a.namespace,a.identifier,o.path,o.sheet,o.row_number,o.column_name,a.component "
                              "FROM aliases a JOIN observations o ON o.namespace=a.namespace AND o.identifier=a.identifier "
                              "WHERE o.role='key' ORDER BY a.person,a.namespace,a.identifier,o.path,o.sheet,o.row_number"):
            safe_row(aliases, row)
        priority = {'not_linked': 0, 'no_values_observed': 1, 'id_listed': 2, 'values_observed': 3}
        for person, canonical_ns, canonical_id in db.execute('SELECT person,canonical_namespace,canonical_id FROM people ORDER BY person'):
            ids = defaultdict(list)
            for ns, value in db.execute('SELECT namespace,identifier FROM aliases WHERE person=? ORDER BY namespace,identifier', (person,)):
                ids[ns].append(value)
            material_flags, type_flags, files = {}, {}, set()
            for path, sheet, row, column, ns, value, materials, types, presence in db.execute(
                    'SELECT path,sheet,row_number,column_name,namespace,identifier,materials,data_types,presence '
                    'FROM matches WHERE person=? ORDER BY path,sheet,row_number,column_name', (person,)):
                files.add(path)
                material_values, type_values = json.loads(materials), json.loads(types)
                for material in material_values:
                    if priority.get(presence, 0) > priority.get(material_flags.get(material, 'not_linked'), 0):
                        material_flags[material] = presence
                for data_type in type_values:
                    if priority.get(presence, 0) > priority.get(type_flags.get(data_type, 'not_linked'), 0):
                        type_flags[data_type] = presence
                for material in material_values or ['unspecified']:
                    for data_type in type_values or ['unspecified']:
                        safe_row(evidence, [person, material, data_type, presence, path, sheet, row, column, ns, value])
            safe_row(overview, [person, canonical_ns, canonical_id,
                               *[json.dumps(ids[ns], ensure_ascii=True) if ids[ns] else '' for ns in namespaces],
                               *[material_flags.get(name, 'not_linked') for name in material_names],
                               *[type_flags.get(name, 'not_linked') for name in type_names], len(files),
                               json.dumps(sorted(files), ensure_ascii=True)])
    finally:
        overview_handle.close(); alias_handle.close(); evidence_handle.close()


def run(args):
    root = args.root.expanduser().resolve()
    if not root.is_dir() or not args.allow_non_mount and not os.path.ismount(root):
        raise ValueError('ResearchDrive must be mounted on this node; use run_rd_catalog.sh')
    output = args.output.expanduser().resolve() if args.output else Path(__file__).resolve().parent / 'catalogs' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    for dependency in (Path(sys.prefix).resolve(), Path(__file__).resolve(), Path(inventory.__file__).resolve()):
        if dependency == root or dependency.is_relative_to(root):
            raise ValueError('Scripts and conda environment must be outside ResearchDrive')
    if output == root or output.is_relative_to(root):
        raise ValueError('Output must be a new directory outside ResearchDrive')
    config = json.loads(args.config.read_text(encoding='utf-8')) if args.config else {}
    config.pop('_key_password_stdin', None)
    config['_keys_only'] = args.keys_only
    if relative_path(config.get('key_directory', 'Key files ID to ID')) == '.':
        raise ValueError('key_directory must be a subfolder, not the whole ResearchDrive')
    bwrap = shutil.which(args.bwrap)
    if not bwrap:
        raise RuntimeError('bubblewrap is required; no writable-source fallback is provided')
    inventory.check_sandbox(bwrap)
    # Check dependencies before creating reports or traversing the drive.
    import openpyxl
    import defusedxml
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    job = output / 'config_used.json'
    with job.open('x', encoding='utf-8') as handle:
        json.dump(config, handle, indent=2, ensure_ascii=True)
    command = inventory.sandbox_command(bwrap, 'probe', root=root)
    index = command.index(str(Path(sys.executable).resolve()))
    extra_binds = []
    if args.key_override:
        override = args.key_override.expanduser().resolve()
        if not override.is_dir() or override == root or override.is_relative_to(root) or root.is_relative_to(override):
            raise ValueError('--key-override must be a readable local folder outside ResearchDrive')
        extra_binds = ['--ro-bind', str(override), '/researchdrive/' + config.get('key_directory', 'Key files ID to ID')]
    command = command[:index] + extra_binds + ['--ro-bind', str(Path(__file__).resolve()), '/app/sample_catalog.py',
                                '--ro-bind', str(job), '/job.json', str(Path(sys.executable).resolve()),
                                '-I', '-B', '/app/sample_catalog.py', '_worker']
    db = sqlite3.connect(output / 'catalog.sqlite')
    os.environ['SQLITE_TMPDIR'] = str(output)
    summary = {'root': str(root), 'started_utc': datetime.now(timezone.utc).isoformat(), 'complete': False,
               'person_granularity': 'One person/donor per row; tissue categories remain separate',
               'keys_only': args.keys_only,
               'source_write_access': False, 'key_override': str(args.key_override) if args.key_override else None,
               'normalization': 'Trim surrounding whitespace; preserve case, punctuation and leading zeros',
               'matching': 'Exact ID within a namespace, connected only by authoritative key rows; other value matches are candidates',
               'availability': 'ID listed is not proof of measurements or stored biological material; not_linked does not mean absent'}
    try:
        create_database(db)
        records = inventory.child_lines(command, output / 'read.log')
        summary.update(ingest(db, records, output))
        summary.update(build_people(db, output, config))
        summary['id_match_counts'] = match_ids(db, output)
        export_people(db, output)
        namespace_candidates(db, output)
        summary['complete'] = True
        summary['all_observed_ids_resolved'] = (not args.keys_only and summary['people'] > 0 and summary['key_rows_with_ids'] > 0
                                         and summary['skipped_files'] == 0 and summary['read_issues'] == 0 and summary['conflicting_components'] == 0
                                         and summary['unanchored_components'] == 0
                                         and not any(n for status, n in summary['id_match_counts'].items() if status != 'confirmed'))
        print(f"Catalogue saved: {output / 'person_overview.csv'}", flush=True)
        print(f"People: {summary['people']:,}; ID matches: {summary['id_match_counts']}; read issues: {summary['read_issues']}", flush=True)
        return 0 if summary['all_observed_ids_resolved'] else 2
    finally:
        db.close()
        summary['finished_utc'] = datetime.now(timezone.utc).isoformat()
        with (output / 'summary.json').open('x', encoding='utf-8') as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=True)
        if not summary['complete']:
            print(f'Catalogue incomplete; partial records are preserved at {output}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(inventory.DEFAULT_ROOT))
    parser.add_argument('--output', type=Path, help='New private catalogue directory outside ResearchDrive')
    parser.add_argument('--config', type=Path, help='Optional JSON rules for ID namespaces, headers and data availability columns')
    parser.add_argument('--key-override', type=Path, help='Read keys from this local folder instead; bound read-only over the key directory inside the sandbox only')
    parser.add_argument('--keys-only', action='store_true', help='Read and validate only readable key tables; save a person/alias report without traversing data files')
    parser.add_argument('--bwrap', default='bwrap')
    parser.add_argument('--allow-non-mount', action='store_true', help='Only for deliberate subfolder/fixture scans')
    return run(parser.parse_args())


if __name__ == '__main__':
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '_worker':
            worker()
        else:
            sys.exit(main())
    except KeyboardInterrupt:
        sys.exit('Interrupted; partial output preserved')
    except Exception as exc:
        sys.exit(f'Catalogue failed: {exc}')

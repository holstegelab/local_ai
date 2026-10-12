"""Small synthetic safety checks; no ResearchDrive access or model required."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fair_extract as fair


def spec():
    return {'source_id': 's1', 'source_file': 'Brain/table.tsv', 'sheet': 'table',
            'header_row': 2, 'columns': ['Donor ID', 'region', 'WES_done', 'Study'],
            'read_columns': [1, 2, 3, 4], 'semantic_columns': [2, 3],
            'id_columns': [{'column': 'Donor ID', 'column_indices': [1],
                            'namespace': 'donorid', 'scope_columns': []}]}


def proposal():
    return {'schema_version': 1, 'source_file': 'Brain/table.tsv', 'sheet': 'table',
            'requires_review': True, 'bindings': [
                {'column_number': 2, 'header': 'region', 'target_field': 'brain_region',
                 'assay_group': None, 'assay_type': None, 'confidence': 'high',
                 'evidence': "Header 'region' supplied in brain metadata.", 'requires_review': True}],
            'value_mappings': [{'column_number': 2, 'raw_value': 'CTX',
                                'normalized_value': 'CTX', 'evidence': 'Preserve raw abbreviation.',
                                'requires_review': True}],
            'interpretation_notes': [], 'unresolved_questions': []}


def payload():
    return {'source_file': 'Brain/table.tsv', 'sheet': 'table',
            'columns': [{'column_number': 2, 'header': 'region'}],
            'preview_rows': [{'row': 3, 'values': {'2': 'CTX'}}]}


class TestFairExtract(unittest.TestCase):
    def saved_run(self, base):
        previous = base / 'previous'
        previous.mkdir()
        source = spec()
        fair.dump(previous / 'source_plan.json', {'sources': [source]})
        preview = payload()
        preview.update(source_id='s1')
        preview['columns'].append({'column_number': 3, 'header': 'WES_done'})
        fair.dump(previous / 'model_inputs.json', [preview])
        fair.dump(previous / 'summary.json', {'read_errors': 0, 'complete': False,
                  'source_write_access': False, 'model_network_access': False,
                  'finished_utc': '2026-10-12T12:00:00+00:00'})
        row = {'source_id': 's1', 'row': 3, 'ids': [{'column': 'Donor ID', 'id': '007', 'namespace': 'donorid'}],
               'values': {'1': '007', '2': 'CTX', '3': 'no'}, 'flags': {}}
        (previous / 'observed_rows.jsonl').write_text(json.dumps(row) + '\n')
        db = sqlite3.connect(previous / 'links.sqlite')
        db.execute('CREATE TABLE links(source TEXT,row INTEGER,column_name TEXT,identifier TEXT,namespace TEXT,person TEXT,status TEXT)')
        db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?)', ('s1', 3, 'Donor ID', '007', 'donorid', 'p1', 'confirmed'))
        db.commit()
        db.close()
        model, prompt = base / 'model.gguf', base / 'prompt.txt'
        model.write_bytes(b'fixture weights, not a real model')
        prompt.write_text('fixture prompt')
        return SimpleNamespace(resume_from=previous, output=base / 'resumed', root=None,
                               preview_rows=12, model=model, prompt=prompt, bwrap='bwrap',
                               context=8192, threads=1, gpu_layers=0, max_tokens=3072)

    def test_resume_preserves_old_run_and_never_reads_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.saved_run(Path(directory))
            before = {p.name: p.read_bytes() for p in args.resume_from.iterdir()}
            def model_results(command, log, payloads):
                self.assertNotIn('/researchdrive', command)
                self.assertEqual(len(payloads), 1)
                yield {'source_id': 's1', 'proposal': fair.validate_proposal(proposal(), payloads[0])}
            with patch.object(fair.shutil, 'which', return_value='/fixture/bwrap'), \
                 patch.object(fair, 'preflight_model'), patch.object(fair.inventory, 'check_sandbox'), \
                 patch.object(fair.inventory, 'child_lines', side_effect=model_results), \
                 patch.object(fair, 'read_source', side_effect=AssertionError('Raw source must not be read')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fair.run(args), 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in args.resume_from.iterdir()})
            summary = json.loads((args.output / 'summary.json').read_text())
            self.assertFalse(summary['source_tables_reread'])
            self.assertTrue(summary['complete'])
            self.assertTrue((args.output / 'person_overview.csv').is_file())

    def test_resume_rejects_existing_output_or_incomplete_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.saved_run(Path(directory))
            args.output = args.resume_from
            with self.assertRaisesRegex(ValueError, 'outside the previous run'):
                fair.run(args)
            args.output = Path(directory) / 'new-output'
            path = args.resume_from / 'summary.json'
            data = json.loads(path.read_text())
            data['read_errors'] = 1
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'read errors'):
                fair.run(args)
            self.assertFalse(args.output.exists())

    def test_gpu_failure_happens_before_source_reads_or_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            args = self.saved_run(base)
            args.resume_from = None
            args.catalog = base / 'previous'
            args.root = base / 'raw-source'
            args.root.mkdir()
            args.source_map = args.limit_sources = None
            args.plan_only = False
            args.allow_non_mount = True
            with patch.object(fair, 'make_plan', return_value={'sources': []}), \
                 patch.object(fair.shutil, 'which', return_value='/fixture/bwrap'), \
                 patch.object(fair, 'preflight_model', side_effect=RuntimeError('No NVIDIA GPU devices are visible')), \
                 patch.object(fair, 'read_source', side_effect=AssertionError('Source must not be read')):
                with self.assertRaisesRegex(RuntimeError, 'No NVIDIA'):
                    fair.run(args)
            self.assertFalse(args.output.exists())

    def test_fair_model_never_receives_raw_mount(self):
        with self.assertRaises(ValueError):
            fair.sandbox('bwrap', 'model', root=Path('/raw-data'))
        with self.assertRaises(ValueError):
            fair.sandbox('bwrap', 'read')
        with self.assertRaises(ValueError):
            fair.sandbox('bwrap', 'write', root=Path('/raw-data'))

    def test_source_mount_is_read_only_and_network_isolated(self):
        command = fair.sandbox('bwrap', 'read', root=Path('/raw-data'))
        i = command.index('/raw-data')
        self.assertEqual(command[i - 1:i + 2], ['--ro-bind', '/raw-data', '/researchdrive'])
        self.assertIn('--unshare-all', command)
        self.assertNotIn('--share-net', command)
        self.assertNotIn('--bind', command)
        settings = SimpleNamespace(context=8192, threads=1, gpu_layers=0, max_tokens=3072)
        model_command = fair.sandbox('bwrap', 'model', args=settings)
        self.assertNotIn('/researchdrive', model_command)

    def test_read_only_probe_refuses_unmarked_or_writable_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / 'existing-fixture.txt'
            existing.write_bytes(b'fixture unchanged\n')
            with self.assertRaises((OSError, RuntimeError)):
                fair.inventory.verify_read_only_fixture(root)
            self.assertEqual(set(p.name for p in root.iterdir()), {'existing-fixture.txt'})
            (root / '__codex_read_only_fixture__').write_text('Disposable sandbox test; never a research source.')
            with self.assertRaisesRegex(RuntimeError, 'unexpectedly allowed create'):
                fair.inventory.verify_read_only_fixture(root)
            self.assertEqual(existing.read_bytes(), b'fixture unchanged\n')

    def test_proposal_validation(self):
        self.assertEqual(fair.validate_proposal(proposal(), payload()), proposal())
        for mutate in (
            lambda item: item['bindings'][0].update(column_number=99),
            lambda item: item['value_mappings'][0].update(raw_value='unseen'),
            lambda item: item['value_mappings'][0].update(normalized_value='data_verified'),
            lambda item: item['bindings'][0].pop('assay_type'),
            lambda item: item.update(generated_python='print(1)'),
            lambda item: item['value_mappings'].append(copy.deepcopy(item['value_mappings'][0])),
        ):
            item = proposal()
            mutate(item)
            with self.assertRaises(ValueError):
                fair.validate_proposal(item, payload())

    def test_rows_zero_padding_formula_and_physical_row(self):
        source = spec()
        donor = SimpleNamespace(value=7, data_type='n', number_format='0000')
        formula = SimpleNamespace(value='=SOMEFUNCTION()', data_type='f')
        rows = iter([['preamble'], source['columns'], [donor, 'CTX', formula, 'A']])
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            fair.extract_rows(source, rows)
        observed = [json.loads(line) for line in capture.getvalue().splitlines()]
        record = next(item for item in observed if item['kind'] == 'row')
        self.assertEqual(record['row'], 3)
        self.assertEqual(record['ids'][0]['id'], '0007')
        self.assertEqual(record['values']['1'], '0007')
        self.assertNotIn('3', record['values'])
        self.assertEqual(record['flags']['3'], 'formula_or_unreadable_cell')

    def test_header_change_stops(self):
        source = spec()
        with self.assertRaises(ValueError):
            fair.extract_rows(source, iter([['preamble'], ['different']]))

    def test_current_namespace_scope(self):
        source = spec()
        source['id_columns'][0]['scope_columns'] = ['Study']
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            fair.extract_rows(source, iter([[], source['columns'], ['007', 'CTX', 'no', 'A']]))
        observed = [json.loads(line) for line in capture.getvalue().splitlines()]
        record = next(item for item in observed if item['kind'] == 'row')
        self.assertEqual(record['ids'][0]['namespace'], 'donorid::scope=[["Study","A"]]')

    def test_existing_links_stale_conflicting_and_scoped(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE links(source TEXT,row INTEGER,column_name TEXT,identifier TEXT,namespace TEXT,person TEXT,status TEXT)')
        db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?)', ('s1', 3, 'Donor ID', '007', 'donorid', 'p1', 'confirmed'))
        record = {'source_id': 's1', 'row': 3, 'ids': [{'column': 'Donor ID', 'id': '007', 'namespace': 'donorid'}], 'flags': {}}
        self.assertEqual(fair.row_identity(db, record)[:2], ('p1', 'confirmed_existing_catalog_link'))
        record['ids'][0]['id'] = '008'
        self.assertEqual(fair.row_identity(db, record)[:2], (None, 'stale_catalog_link'))
        record['ids'][0]['id'] = '007'
        record['ids'][0]['namespace'] = 'donorid::scope=[["Study","other"]]'
        self.assertEqual(fair.row_identity(db, record)[:2], (None, 'stale_catalog_link'))
        record['ids'][0]['namespace'] = 'donorid'
        db.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?)', ('s1', 3, 'Sample ID', 'X', 'sample', 'p2', 'confirmed'))
        record['ids'].append({'column': 'Sample ID', 'id': 'X', 'namespace': 'sample'})
        self.assertEqual(fair.row_identity(db, record)[:2], (None, 'conflicting_row_people'))
        record['ids'][1]['id'] = 'changed'
        self.assertEqual(fair.row_identity(db, record)[:2], (None, 'stale_catalog_link'))

    def test_negatives_and_planned_do_not_establish_genetics(self):
        binding = {'assay_type': 'wes', 'target_field': 'assay_status'}
        for raw, normalized in [('0', None), ('1', None), ('no', 'reported_not_done'),
                                ('todo', 'planned'), ('planned', None), ('yes', 'location_reported')]:
            self.assertFalse(fair.assay_evidence(binding, raw, normalized))
        self.assertTrue(fair.assay_evidence(binding, 'done', 'reported_completed'))
        binding['target_field'] = 'assay_id'
        self.assertTrue(fair.assay_evidence(binding, 'WES_0001', None))
        self.assertFalse(fair.assay_evidence(binding, '0', None))
        binding.update(target_field='assay_platform', assay_type='long_read_unspecified')
        self.assertFalse(fair.assay_evidence(binding, 'PacBio', None))

    def test_identifiers_withheld_from_preview(self):
        for header in ['Participant ID', 'sample_name', 'Specimen Label', 'block',
                       'aliquot', 'DATA_PATH_GWAS', 'Collection date', 'DNA', 'Donor']:
            self.assertTrue(fair.preview_identifier_header(header), header)
        for header in ['Brain_region', 'Material', 'WES_done', 'GWAS_chip']:
            self.assertFalse(fair.preview_identifier_header(header), header)

    def test_plan_from_schemas_without_source_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = {'kind': 'schema', 'status': 'selected', 'role': 'data',
                      'path': 'Brain/nonexistent.tsv', 'sheet': 'table', 'header_row': 1,
                      'columns': ['Donor ID', 'region', 'block', 'WES_done', 'WES_id', 'WGS_status'],
                      'id_columns': [{'column': 'Donor ID', 'column_indices': [1], 'namespace': 'donorid', 'scope_columns': []}]}
            (root / 'schemas.jsonl').write_text(json.dumps(schema) + '\n')
            plan = fair.make_plan(root)
            self.assertEqual(len(plan['sources']), 1)
            self.assertEqual(plan['sources'][0]['read_columns'], [1, 2, 3, 4, 5, 6])
            self.assertTrue({4, 5, 6} <= set(plan['sources'][0]['semantic_columns']))


if __name__ == '__main__':
    unittest.main()

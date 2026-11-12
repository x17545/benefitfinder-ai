"""Complete generations, bounded raw verification and safe CLI recovery."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from backend.tests.gov24_fixtures import gov24_fields, gov24_plan
from benefitfinder.application.collection import RawSnapshot, SourceRequest, SourceResponse
from benefitfinder.infrastructure.collection_files import read_collection_file
from benefitfinder.infrastructure.local_files import JsonFileSnapshotStore
from benefitfinder.ingestion.collected_generations import CollectedGenerationError
from benefitfinder.ingestion.gov24_generations import load_gov24_generation
from benefitfinder.jobs.__main__ import main


class Gov24JobsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.plan = self.data / 'plan.json'
        self.plan.write_text(json.dumps(gov24_plan(), ensure_ascii=False))
        self.fields = gov24_fields()
        self.calls = []
        self.fail_detail = False
        self.missing = False
        self.root = self.data / 'processed/f08-gov24'

    def fetch(self, request):
        self.calls.append(request)
        endpoint = request.url.rsplit('/', 1)[-1]
        if self.fail_detail and endpoint == 'serviceDetail':
            return SourceResponse(401, b'PRIVATE key error', 'application/json')
        data = [] if self.missing else [self.fields[endpoint]]
        body = json.dumps({'page': 1, 'perPage': 1, 'currentCount': len(data),
                           'matchCount': len(data), 'totalCount': 50_000,
                           'data': data}, ensure_ascii=False).encode()
        return SourceResponse(200, body, 'application/json')

    def command(self, action: str = 'refresh-gov24', **env):
        args = [action, '--data-dir', str(self.data)]
        if action == 'refresh-gov24':
            args += ['--plan', str(self.plan)]
        output = StringIO()
        fetcher = Mock()
        fetcher.fetch.side_effect = self.fetch
        settings = {'GOV24_API_KEY': 'PRIVATE-canary-key'} if action == 'refresh-gov24' else {}
        with patch.dict('os.environ', {**settings, **env}, clear=True), \
             patch('benefitfinder.infrastructure.gov24_http.Gov24HttpFetcher', return_value=fetcher), \
             redirect_stdout(output), redirect_stderr(output):
            code = main(args)
        self.assertNotIn('PRIVATE', output.getvalue())
        return code

    def load(self):
        return load_gov24_generation(lambda relative, limit:
                                     read_collection_file(self.data, relative, limit))

    def test_same_second_snapshots_preserve_previous_metadata(self) -> None:
        import hashlib

        store = JsonFileSnapshotStore(self.data / 'snapshots')
        at = datetime(2026, 11, 13, tzinfo=timezone.utc)
        refs = []
        for index, endpoint in enumerate(('serviceList', 'serviceList', 'serviceDetail')):
            body = b'{}'
            snapshot = RawSnapshot('gov24', SourceRequest('https://example.test/' + endpoint, {}),
                at + timedelta(microseconds=index % 2), 200, 'application/json',
                hashlib.sha256(body).hexdigest(), body)
            refs.append(store.save(snapshot))
        self.assertEqual(len(set(refs)), 3)
        meta = self.data / 'snapshots' / Path(refs[0]).with_suffix('.meta.json')
        self.assertEqual(json.loads(meta.read_text())['fetched_at'], at.isoformat())

    def test_complete_generation_preserves_three_raw_bodies_and_rejects_tampering(self) -> None:
        self.assertEqual(self.command(), 0)
        batch = self.load()
        self.assertEqual(batch.source_name, 'gov24')
        self.assertEqual(batch.records[0].fields, self.fields)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all('serviceKey' not in call.params for call in self.calls))
        paths = list((self.data / 'raw/f08-gov24').glob('gov24-serviceDetail/snapshots/*.json'))
        raw = next(path for path in paths if not path.name.endswith('.meta.json'))
        raw.write_bytes(raw.read_bytes().replace('20'.encode(), '30'.encode()))
        with self.assertRaises(CollectedGenerationError):
            self.load()

    def test_partial_failure_preserves_prior_generation_and_age_scope(self) -> None:
        self.assertEqual(self.command(), 0)
        previous = json.loads((self.root / 'status.json').read_text())['last_success']
        self.fail_detail = True
        self.assertEqual(self.command(), 1)
        self.assertEqual(json.loads((self.root / 'status.json').read_text())['last_success'], previous)
        self.assertEqual(self.load().records[0].fields, self.fields)
        self.fail_detail = False
        self.fields['supportConditions'].update(JA0110=60, JA0111=80)
        self.assertEqual(self.command(), 0)
        self.assertEqual(self.load().records, ())

    def test_changed_scope_is_rejected_before_fetch_and_database_replay_needs_no_key(self) -> None:
        self.assertEqual(self.command(), 0)
        self.plan.write_text(json.dumps(gov24_plan('B')))
        self.calls.clear()
        self.assertEqual(self.command(), 1)
        self.assertEqual(self.calls, [])
        with patch('benefitfinder.jobs.__main__.PostgresCollectedPolicies') as repository:
            repository.return_value.save.return_value = Mock(new=1, changed=0, unchanged=0, missing=0)
            self.assertEqual(self.command('ingest-gov24', COLLECTION_WRITE_DATABASE_URL='fictional'), 0)
            repository.return_value.save.assert_called_once()
        self.assertEqual(self.calls, [])

    def test_failed_database_handoff_prevents_new_network_collection(self) -> None:
        self.assertEqual(self.command(), 0)
        self.calls.clear()
        with patch('benefitfinder.jobs.__main__.PostgresCollectedPolicies') as repository:
            repository.return_value.save.side_effect = RuntimeError('PRIVATE database secret')
            self.assertEqual(self.command(COLLECTION_WRITE_DATABASE_URL='fictional'), 1)
        self.assertEqual(self.calls, [])

    def test_confirmed_absence_produces_complete_empty_generation(self) -> None:
        self.assertEqual(self.command(), 0)
        self.missing = True
        self.assertEqual(self.command(), 0)
        self.assertEqual(self.load().records, ())

    def test_confirmed_composite_category_can_map_to_both_mvp_categories(self) -> None:
        plan = gov24_plan()
        plan['scope']['category_values'] = {
            'employment': ['fictional-composite'], 'housing': ['fictional-composite']}
        plan['policies'][0]['categories'] = ['employment', 'housing']
        plan['targets'][0]['documents'][0]['scope_guards'] = {
            'serviceCategory': 'fictional-composite'}
        self.plan.write_text(json.dumps(plan))
        self.fields['serviceList']['서비스분야'] = 'fictional-composite'
        self.assertEqual(self.command(), 0)
        self.assertEqual(self.load().records[0].fields, self.fields)


if __name__ == '__main__':
    unittest.main()

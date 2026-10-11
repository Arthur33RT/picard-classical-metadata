"""Pure asynchronous retrieval, cache, traversal and failure regression tests."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

folder = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('retrieval_test_package', folder / 'retrieval.py')
package = type(sys)('retrieval_test_package')
package.__path__ = [str(folder)]
sys.modules[package.__name__] = package
from retrieval_test_package.retrieval import EntityBroker, ReleaseCrawl
from retrieval_test_package.core import SemanticMapProjector


def fixture():
    return {
        ('release', 'release'): {'id': 'release', 'title': 'Album', 'media': [{
            'position': 1, 'tracks': [{'position': 1, 'recording': {'id': 'r1'}},
                                      {'position': 2, 'recording': {'id': 'r2'}}]}]},
        ('recording', 'r1'): {'id': 'r1', 'title': 'One', 'relations': [{
            'type': 'performance', 'direction': 'forward', 'work': {'id': 'child'}}]},
        ('recording', 'r2'): {'id': 'r2', 'title': 'Two', 'relations': []},
        ('work', 'child'): {'id': 'child', 'title': 'Movement', 'relations': [
            {'type': 'parts', 'direction': 'backward', 'work': {'id': 'parent'}},
            {'type': 'performance', 'direction': 'backward', 'recording': {'id': 'unrelated'}},
            {'type': 'novel unknown link', 'work': {'id': 'not-followed'}},
        ]},
        ('work', 'parent'): {'id': 'parent', 'title': 'Composition', 'relations': [
            {'type': 'composer', 'artist': {'id': 'composer', 'name': 'Composer'}},
        ]},
    }


class RetrievalTests(unittest.TestCase):
    def run_crawl(self, data, folder=None, server='server', asynchronous=False):
        self.calls, self.pending, self.results = [], [], []
        def fetch(entity, mbid, callback):
            self.calls.append((entity, mbid))
            result = data.get((entity, mbid))
            if asynchronous:
                self.pending.append((callback, result))
            else:
                callback(result, None if result else 'not_found')
        broker = EntityBroker(server, folder, fetch)
        crawl = ReleaseCrawl('release', broker, lambda result, errors: self.results.append((result, errors)))
        crawl.start()
        while self.pending:
            callback, result = self.pending.pop()  # deliberately reverse completion order
            callback(result, None if result else 'not_found')
        self.assertEqual(len(self.results), 1)
        return broker, self.results[0]

    def test_async_crawl_and_unknown_preservation(self):
        broker, (semantic, errors) = self.run_crawl(fixture(), asynchronous=True)
        self.assertFalse(errors)
        self.assertEqual(set(self.calls), set(fixture()))
        self.assertEqual(broker.active, 0)
        self.assertTrue(any(entry['relationship']['type'] == 'novel unknown link'
                            for entry in semantic['other_unclassified']))
        tags = SemanticMapProjector(semantic).project_recording('r1')
        self.assertEqual(tags['classical_work_path'], ['Composition', 'Movement'])
        self.assertEqual(tags['classical_composer'], ['Composer'])
        self.assertEqual(SemanticMapProjector(semantic).project_recording('r2')['classical_status'], 'unresolved_work')

    def test_complete_embedded_recordings_skip_individual_requests(self):
        data = fixture()
        for track in data['release', 'release']['media'][0]['tracks']:
            rid = track['recording']['id']
            complete = dict(data['recording', rid])
            complete.setdefault('artist-credit', [])
            complete.setdefault('isrcs', [])
            track['recording'] = complete
        _, (semantic, errors) = self.run_crawl(data, asynchronous=True)
        self.assertFalse(errors)
        self.assertFalse(any(kind == 'recording' for kind, _ in self.calls))
        tags = SemanticMapProjector(semantic).project_recording('r1')
        self.assertEqual(tags['classical_work_path'], ['Composition', 'Movement'])

    def test_incomplete_embedded_recording_uses_individual_fallback(self):
        data = fixture()
        complete = dict(data['recording', 'r1'])
        complete.setdefault('artist-credit', [])
        complete.setdefault('isrcs', [])
        data['release', 'release']['media'][0]['tracks'][0]['recording'] = complete
        _, (_, errors) = self.run_crawl(data, asynchronous=True)
        self.assertFalse(errors)
        recording_calls = [mbid for kind, mbid in self.calls if kind == 'recording']
        self.assertEqual(recording_calls, ['r2'])

    def test_persistent_cache_and_server_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_crawl(fixture(), folder)
            broker, _ = self.run_crawl(fixture(), folder)
            self.assertFalse(self.calls)
            self.assertEqual(broker.hits, len(fixture()))
            self.run_crawl(fixture(), folder, server='other')
            self.assertEqual(len(self.calls), len(fixture()))

    def test_failed_work_preserves_recording(self):
        data = fixture()
        del data['work', 'parent']
        _, (semantic, errors) = self.run_crawl(data)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(semantic['recordings']), 2)

    def test_failed_release_finishes_once(self):
        _, (semantic, errors) = self.run_crawl({})
        self.assertIsNone(semantic)
        self.assertEqual(len(errors), 1)

    def test_parent_cycle_is_bounded(self):
        data = fixture()
        data['work', 'parent']['relations'].append({
            'type': 'parts', 'direction': 'backward', 'work': {'id': 'child'}})
        _, (semantic, errors) = self.run_crawl(data)
        self.assertFalse(errors)
        self.assertEqual(len(semantic['works']), 2)
        self.assertEqual(len(self.calls), len(data))

    def test_inflight_requests_are_shared_and_bounded(self):
        pending, results = [], []
        broker = EntityBroker('server', None, lambda e, i, cb: pending.append(cb), concurrency=1)
        broker.get('work', 'one', lambda d, e: results.append(d))
        broker.get('work', 'one', lambda d, e: results.append(d))
        broker.get('work', 'two', lambda d, e: results.append(d))
        self.assertEqual(len(pending), 1)
        pending.pop(0)({'id': 'one'}, None)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(pending), 1)
        pending.pop(0)({'id': 'two'}, None)
        self.assertEqual(len(results), 3)
        self.assertEqual(broker.active, 0)

    def test_cache_failures_do_not_fail_retrieval(self):
        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / 'file'
            blocked.write_text('file not directory')
            broker, (semantic, errors) = self.run_crawl(fixture(), blocked)
            self.assertTrue(broker.cache_errors)
            self.assertFalse(errors)
            self.assertIsNotNone(semantic)

    def test_shared_source_preserves_both_discovery_edges(self):
        data = fixture()
        data['recording', 'r2']['relations'] = [{
            'type': 'performance', 'work': {'id': 'sibling'}}]
        source = {'type': 'orchestration', 'direction': 'backward', 'work': {'id': 'source'}}
        data['work', 'child']['relations'].append(source)
        data['work', 'sibling'] = {'id': 'sibling', 'title': 'Sibling', 'relations': [source]}
        data['work', 'source'] = {'id': 'source', 'title': 'Original', 'relations': []}
        _, (semantic, errors) = self.run_crawl(data, asynchronous=True)
        work = next(w for w in semantic['works'] if w['identity']['id'] == 'source')
        self.assertEqual({d['source_work_id'] for d in work['discovered_by']}, {'child', 'sibling'})
        for rid in ('r1', 'r2'):
            self.assertEqual(SemanticMapProjector(semantic).project_recording(rid)['classical_source_work'], ['Original'])


if __name__ == '__main__':
    unittest.main()

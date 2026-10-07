import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sca_archive', ROOT / 'scripts' / 'archive.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['sca_archive'] = mod
spec.loader.exec_module(mod)


class ArchiveMergeTests(unittest.TestCase):
    def test_hidden_personality_never_erases_last_known(self):
        first = {
            'character_id': 'abc-def-123',
            'name': 'Test Bot',
            'personality': 'Brave and sarcastic',
            'scenario': 'A workshop',
        }
        record, changed = mod.observe_bot(None, first, source='character-api', at='2026-09-28T00:00:00Z')
        self.assertTrue(changed)
        second = {
            'character_id': 'abc-def-123',
            'name': 'Test Bot',
            'personality': None,
            'scenario': 'A workshop',
        }
        record, changed = mod.observe_bot(record, second, source='character-api', at='2026-09-29T00:00:00Z')
        self.assertTrue(changed)
        self.assertEqual(record['lastKnown']['personality'], 'Brave and sarcastic')
        self.assertTrue(any(e['path'] == 'personality' and e['kind'] == 'hidden-or-empty' for e in record['fieldHistory']))

    def test_omitted_definition_is_recorded_but_preserved(self):
        first = {'character_id': 'abc-def-456', 'name': 'Bot', 'personality': 'Original definition'}
        record, _ = mod.observe_bot(None, first, source='character-api', at='2026-09-28T00:00:00Z')
        second = {'character_id': 'abc-def-456', 'name': 'Bot'}
        record, changed = mod.observe_bot(record, second, source='character-api', at='2026-09-29T00:00:00Z')
        self.assertTrue(changed)
        self.assertEqual(record['lastKnown']['personality'], 'Original definition')
        self.assertTrue(any(e['path'] == 'personality' and e['kind'] == 'hidden-or-missing' for e in record['fieldHistory']))

    def test_zero_and_false_are_meaningful(self):
        merged = mod.merge_last_known({'rating_score': 5, 'is_nsfw': True}, {'rating_score': 0, 'is_nsfw': False})
        self.assertEqual(merged['rating_score'], 0)
        self.assertFalse(merged['is_nsfw'])

    def test_listing_observation_only_marks_restore_candidate(self):
        first = {'character_id': 'abc-def-789', 'name': 'Bot'}
        record, _ = mod.observe_bot(None, first, source='typesense:latest', at='2026-09-28T00:00:00Z')
        record['status'] = {'current': 'deleted', 'since': '2026-09-29T00:00:00Z'}
        record.setdefault('availabilityHistory', []).append({
            'status': 'deleted',
            'from': '2026-09-29T00:00:00Z',
            'source': 'character-api:repeated-404',
        })
        record, changed = mod.observe_bot(record, first, source='typesense:latest', at='2026-09-30T00:00:00Z')
        self.assertTrue(changed)
        self.assertEqual(record['status']['current'], 'deleted')
        self.assertEqual(record['status']['restoreCandidate']['source'], 'typesense:latest')
        self.assertEqual(record['availabilityHistory'][-1].get('status'), 'deleted')

    def test_character_api_observation_restores_deleted_record(self):
        first = {'character_id': 'abc-def-790', 'name': 'Bot'}
        record, _ = mod.observe_bot(None, first, source='typesense:latest', at='2026-09-28T00:00:00Z')
        record['status'] = {'current': 'deleted', 'since': '2026-09-29T00:00:00Z'}
        record, changed = mod.observe_bot(record, first, source='character-api', at='2026-09-30T00:00:00Z')
        self.assertTrue(changed)
        self.assertEqual(record['status']['current'], 'public')
        self.assertEqual(record['availabilityHistory'][-1]['status'], 'public')
        self.assertEqual(record['availabilityHistory'][-1]['source'], 'character-api')

    def test_token_count_change_is_preserved(self):
        first = {
            'character_id': 'abc-def-token',
            'name': 'Token Bot',
            'token_count': 1200,
        }
        record, _ = mod.observe_bot(None, first, source='typesense', at='2026-10-01T00:00:00Z')
        second = {
            'character_id': 'abc-def-token',
            'name': 'Token Bot',
            'token_count': 1450,
        }
        record, changed = mod.observe_bot(record, second, source='typesense', at='2026-10-02T00:00:00Z')
        self.assertTrue(changed)
        self.assertEqual(record['metrics']['latest']['token_count'], 1450)
        self.assertTrue(any(
            e.get('path') == 'token_count' and e.get('from') == 1200 and e.get('to') == 1450
            for e in record['fieldHistory']
        ))
        self.assertTrue(any(
            e.get('token_count') == 1450 for e in record['metrics']['history']
        ))

    def test_avatar_normalization(self):
        self.assertEqual(
            mod.normalize_avatar_url('avatars/foo.webp'),
            'https://cdn.nd-api.com/avatars/foo.webp?class=avatar256x256'
        )


if __name__ == '__main__':
    unittest.main()

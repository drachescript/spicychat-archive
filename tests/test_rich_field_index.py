import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rich_field_index as rich


class RichFieldIndexTests(unittest.TestCase):
    def test_detects_last_known_rich_fields(self):
        record = {
            "id": "11111111-1111-1111-1111-111111111111",
            "lastKnown": {
                "persona": "personality text",
                "scenario": "scenario text",
                "dialogue": "dialogue text",
            },
            "current": {},
        }
        self.assertEqual(rich.field_mask(record), 7)
        self.assertEqual(
            rich.field_flags(record),
            {"personality": True, "scenario": True, "dialogue": True},
        )

    def test_falls_back_to_current_sources(self):
        record = {
            "id": "22222222-2222-2222-2222-222222222222",
            "lastKnown": {"persona": "", "scenario": "", "dialogue": ""},
            "current": {
                "character-api": {
                    "personality": "saved personality",
                    "scenario": "",
                    "example_dialogues": "saved example",
                }
            },
        }
        self.assertEqual(rich.field_mask(record), rich.PERSONALITY | rich.DIALOGUE)

    def test_empty_fields_do_not_count(self):
        record = {
            "id": "33333333-3333-3333-3333-333333333333",
            "lastKnown": {"persona": "   ", "scenario": "", "dialogue": []},
            "current": {},
        }
        self.assertEqual(rich.field_mask(record), 0)


if __name__ == "__main__":
    unittest.main()

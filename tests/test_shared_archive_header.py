"""Keep navigation shared and Rulebooks separate from Lorebooks."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class SharedArchiveHeaderTests(unittest.TestCase):
    def test_public_pages_load_one_shared_nav_module(self):
        candidates=[ROOT/'index.html', *sorted(ROOT.glob('*/index.html'))]
        for page in candidates:
            if 'admin' in page.parts:
                continue
            contents=page.read_text(encoding='utf-8')
            if 'class="archive-unified-header"' not in contents and 'class="site-header"' not in contents:
                continue
            with self.subTest(page=page.relative_to(ROOT).as_posix()):
                self.assertIn('/assets/site-shell.js',contents)

    def test_deleted_rulebooks_have_separate_browse_route(self):
        html=(ROOT/'deleted-rulebooks'/'index.html').read_text(encoding='utf-8')
        script=(ROOT/'assets'/'rulebooks.js').read_text(encoding='utf-8')
        self.assertIn('data-page="deleted-rulebooks"',html)
        self.assertIn("removedView = document.body.dataset.page === 'deleted-rulebooks'",script)
        self.assertIn("x.status==='not-public'",script)
        self.assertIn('No longer public',script)
        self.assertIn('not prove deletion',script)

    def test_shared_nav_covers_both_public_and_missing_items(self):
        source=(ROOT/'assets'/'site-shell.js').read_text(encoding='utf-8')
        for link in ('/deleted/', '/deleted-lorebooks/', '/deleted-rulebooks/',
                     '/bots/', '/lorebooks/', '/rulebooks/'):
            self.assertIn(link,source)
        self.assertIn("className = 'archive-unified-header sca-shared-header'",source)

    def test_rulebook_images_have_fallback_and_safe_refs(self):
        source=(ROOT/'assets'/'rulebooks.js').read_text(encoding='utf-8')
        self.assertIn('data-rulebook-cover',source)
        self.assertIn('rulebook-cover-empty',source)
        self.assertIn('cdn.nd-api.com',source)
        self.assertIn('bindBrokenCovers',source)
        self.assertIn('data-rb-tag-action="exclude"', source)
        self.assertIn('rb-match', source)
        self.assertIn('rulebook-cover-refs.json', source)


if __name__ == '__main__':
    unittest.main()

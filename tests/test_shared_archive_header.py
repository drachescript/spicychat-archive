"""Keep navigation shared and Rulebooks separate from Lorebooks."""
from pathlib import Path
import re
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

    def test_shared_primary_nav_order_and_more(self):
        source=(ROOT/'assets'/'site-shell.js').read_text(encoding='utf-8')
        match=re.search(r'const primary = \[(.*?)\];',source,re.DOTALL)
        self.assertIsNotNone(match)
        links=re.findall(r"\['([^']+)', '([^']+)'\]",match.group(1))
        self.assertEqual(links,[
            ('/', 'Home'), ('/deleted/', 'Deleted bots'),
            ('/deleted-lorebooks/', 'Deleted Lorebooks'),
            ('/deleted-rulebooks/', 'Deleted Rulebooks'),
            ('/recovery/', 'Recovery Lab'), ('/stats/', 'Stats'),
            ('/faq/', 'FAQ')
        ])
        self.assertIn("summary.textContent = 'More'",source)

    def test_rulebook_filters_share_collapsed_bot_sidebar_layout(self):
        source=(ROOT/'assets'/'rulebooks.js').read_text(encoding='utf-8')
        styles=(ROOT/'assets'/'archive.css').read_text(encoding='utf-8')
        for expected in ('layout filters-collapsed rulebook-browse-layout',
                         'aside id="rb-filters"', 'rb-toggle-filters',
                         'Show filters', 'Hide filters',
                         'sca-rulebook-filters-open', 'rb-tag-groups'):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)
        self.assertIn('rulebook-browse-layout.filters-collapsed', styles)

    def test_unlisted_rulebook_cards_preserve_metadata_without_claiming_saved_rules(self):
        source=(ROOT/'assets'/'rulebooks.js').read_text(encoding='utf-8')
        self.assertIn("x.status==='public'||x.status==='not-public'", source)
        self.assertIn('Historical public listing only', source)
        self.assertIn('not the Rulebook definitions', source)
        self.assertIn("https://spicychat.ai/rulebook/", source)

    def test_rulebook_images_have_fallback_and_safe_refs(self):
        source=(ROOT/'assets'/'rulebooks.js').read_text(encoding='utf-8')
        self.assertIn('data-rulebook-cover',source)
        self.assertIn('rulebook-cover-empty',source)
        self.assertIn('cdn.nd-api.com',source)
        self.assertIn('bindBrokenCovers',source)
        self.assertIn('rb-tag-groups',source)
        self.assertIn('matchesRulebookTags',source)
        self.assertIn('selectedExclude',source)


if __name__ == '__main__':
    unittest.main()

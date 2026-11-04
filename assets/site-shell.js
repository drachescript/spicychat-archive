// Shared public Archive top bar. Edit the navigation here, not in every HTML page.
(() => {
  'use strict';
  const primary = [
    ['/', 'Home'],
    ['/deleted/', 'Deleted bots'],
    ['/deleted-lorebooks/', 'Deleted Lorebooks'],
    ['/deleted-rulebooks/', 'Deleted Rulebooks'],
    ['/bots/', 'Public bots'],
    ['/lorebooks/', 'Public Lorebooks'],
    ['/rulebooks/', 'Public Rulebooks'],
  ];
  const secondary = [
    ['/stats/', 'Stats'],
    ['/restored/', 'Restored bots'],
    ['/activity/', 'Archive activity'],
    ['/changes/', 'Recently changed'],
    ['/search/', 'Search'],
    ['/recovery/', 'Recovery Lab'],
    ['/merge/', 'Merge Lab'],
    ['/name-trends/', 'Names & trends'],
    ['/tag-stats/', 'Tag stats'],
    ['/health/', 'Archive health'],
    ['/faq/', 'FAQ'],
    ['https://github.com/drachescript/spicychat-archive', 'GitHub'],
  ];
  const current = location.pathname.replace(/\/index\.html$/, '/');
  const section = current === '/rulebook/' ? '/rulebooks/'
    : current === '/lorebook/' ? '/lorebooks/'
    : current === '/bot/' ? '/bots/' : current;
  function link([href, name]) {
    const active = section === href;
    const external = href.startsWith('https:');
    const a = document.createElement('a');
    a.href = href;
    a.textContent = name;
    if (active) { a.classList.add('active'); a.setAttribute('aria-current','page'); }
    if (external) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }
  function build() {
    const old = document.querySelector('header.archive-unified-header, header.site-header');
    if (!old || old.classList.contains('sca-shared-header')) return;
    const header = document.createElement('header');
    header.className = 'archive-unified-header sca-shared-header';
    const inner = document.createElement('div'); inner.className = 'archive-head-inner';
    const brand = document.createElement('a');
    brand.className = 'archive-head-brand'; brand.href = '/'; brand.setAttribute('aria-label', 'SpicyChat Archive home');
    brand.innerHTML = '<span class="archive-head-mark">SA</span><span class="archive-head-title"><b>SpicyChat Archive</b><small>Independent historical archive</small></span>';
    const nav = document.createElement('nav'); nav.className = 'archive-head-links'; nav.setAttribute('aria-label','Main navigation');
    primary.forEach(item => nav.append(link(item)));
    const more = document.createElement('details'); more.className = 'archive-more';
    const summary = document.createElement('summary');summary.textContent = 'More';
    const list = document.createElement('div');list.className = 'archive-more-list';
    secondary.forEach(item => list.append(link(item)));
    more.append(summary,list);nav.append(more);
    inner.append(brand,nav);header.append(inner);
    old.replaceWith(header);
    document.addEventListener('click', ev => {
      if (!more.contains(ev.target) && more.open) more.open = false;
    });
  }
  function init() {
    if (!document.querySelector('header.archive-unified-header, header.site-header')) return;
    if (document.querySelector('link[data-sca-shell]')) return;
    const css = document.createElement('link');
    css.rel = 'stylesheet'; css.href = '/assets/site-shell.css';css.dataset.scaShell = '1';
    css.addEventListener('load',build,{once:true});
    document.head.append(css);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();

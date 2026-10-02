(() => {
  'use strict';

  const PREF_KEY = 'sca-translate-bots-v1';
  const TRANSLATION_ENDPOINT = 'https://spicychat-archive-import.dragongraf.workers.dev/api/translate';

  function enabled() {
    try { return localStorage.getItem(PREF_KEY) !== '0'; }
    catch { return true; }
  }

  function setEnabled(value) {
    try { localStorage.setItem(PREF_KEY, value ? '1' : '0'); } catch {}
    document.documentElement.dataset.translateBots = value ? 'on' : 'off';
  }

  // app.js translates visible cards automatically. Keep that behavior as the
  // default, but stop translation requests completely when the user turns it off.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function controlledArchiveFetch(input, init) {
    let url = '';
    try { url = typeof input === 'string' ? input : String(input?.url || input || ''); } catch {}
    if (!enabled() && url.startsWith(TRANSLATION_ENDPOINT)) {
      return Promise.resolve(new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      }));
    }
    return nativeFetch(input, init);
  };

  function renderControl() {
    const filters = document.querySelector('#filters .filter-scroll');
    if (!filters || document.querySelector('#archive-translation-control')) return;

    const section = document.createElement('section');
    section.className = 'filter-section';
    section.id = 'archive-translation-control';
    section.innerHTML = `
      <h3>Text</h3>
      <label class="checkline">
        <input id="translate-bots" type="checkbox" ${enabled() ? 'checked' : ''}>
        <span class="filter-note">Translate bots to English</span>
      </label>
      <div class="filter-note">Translates non-English bot names/titles and shows the detected source language. The original text stays available on hover.</div>`;

    const sections = [...filters.querySelectorAll(':scope > .filter-section')];
    const images = sections.find(node => node.querySelector('h3')?.textContent.trim() === 'Images');
    if (images) images.after(section);
    else filters.append(section);

    section.querySelector('#translate-bots')?.addEventListener('change', event => {
      setEnabled(Boolean(event.currentTarget.checked));
      // Reload so any in-flight translation work cannot race the new preference,
      // and so enabling immediately translates the current page too.
      location.reload();
    });
  }

  setEnabled(enabled());
  renderControl();

  const observer = new MutationObserver(() => renderControl());
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();

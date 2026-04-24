/**
 * MyMem — shared frontend JS
 * Handles: dark/light mode, wikilink resolution, search autocomplete
 */

// ---------------------------------------------------------------------------
// Dark / light mode
// ---------------------------------------------------------------------------

(function () {
  const html       = document.documentElement;
  const toggleBtn  = document.getElementById('theme-toggle');
  const iconMoon   = document.getElementById('icon-moon');
  const iconSun    = document.getElementById('icon-sun');

  const stored = localStorage.getItem('mymem-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = stored ? stored === 'dark' : prefersDark;

  function applyTheme(dark) {
    html.classList.toggle('dark', dark);
    if (iconMoon) iconMoon.classList.toggle('hidden', dark);
    if (iconSun)  iconSun.classList.toggle('hidden', !dark);
  }

  applyTheme(isDark);

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const dark = !html.classList.contains('dark');
      applyTheme(dark);
      localStorage.setItem('mymem-theme', dark ? 'dark' : 'light');
    });
  }
})();

// ---------------------------------------------------------------------------
// Wikilink resolution — mark broken links gray
// ---------------------------------------------------------------------------

(async function () {
  const wikilinks = document.querySelectorAll('a.wikilink');
  if (!wikilinks.length) return;

  try {
    const pages   = await fetch('/api/pages').then(r => r.json());
    const slugSet = new Set(pages.map(p => p.title.toLowerCase().replace(/ /g, '-')));

    wikilinks.forEach(a => {
      const slug = a.getAttribute('href')?.replace('/wiki/', '') || '';
      if (!slugSet.has(slug)) a.classList.add('broken');
    });
  } catch (_) {
    // Non-fatal — wikilinks just won't be marked broken
  }
})();

// ---------------------------------------------------------------------------
// Keyboard shortcut: / → focus search input
// ---------------------------------------------------------------------------

document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName || '')) {
    e.preventDefault();
    const inp = document.getElementById('q-input') ||
                document.getElementById('page-search') ||
                document.getElementById('topic-input');
    inp?.focus();
  }
});

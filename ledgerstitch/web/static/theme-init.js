// LedgerStitch — theme-init.js: runs before styles.css so the page never flashes the wrong theme.
// A classic (non-module) script because the CSP forbids inline scripts.
// Order: ?theme=dark|light (screenshots, demos) → the viewer’s saved choice → the OS setting.
(function () {
  var t = null;
  try { t = new URLSearchParams(location.search).get('theme'); } catch (e) { t = null; }
  if (t !== 'dark' && t !== 'light') {
    try { t = localStorage.getItem('ls-theme'); } catch (e) { t = null; }
  }
  if (t !== 'dark' && t !== 'light') {
    t = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.setAttribute('data-theme', t);
})();

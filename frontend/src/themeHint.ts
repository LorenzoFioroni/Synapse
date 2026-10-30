// Run synchronously before first paint so a saved dark theme does not flash white.
try {
  const theme = localStorage.getItem('zc.themeHint');
  if (theme) document.documentElement.dataset.theme = JSON.parse(theme);
} catch { /* Storage can be unavailable, or the cached value invalid. */ }

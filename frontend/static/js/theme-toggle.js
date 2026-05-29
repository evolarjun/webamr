/* Dark-mode toggle -- persisted via localStorage */
(function () {
  var STORAGE_KEY = 'webamr-theme';

  function getPreferred() {
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') return stored;
    // Fall back to OS preference
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }
    return 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);
    // Update icon
    var icon = document.querySelector('.theme-toggle__icon');
    if (icon) icon.textContent = theme === 'dark' ? '\u263E' : '\u2600';
  }

  // Apply immediately (before paint) to avoid flash
  applyTheme(getPreferred());

  // Build toggle widget once DOM is ready
  document.addEventListener('DOMContentLoaded', function () {
    var toggle = document.createElement('label');
    toggle.className = 'theme-toggle';
    toggle.title = 'Toggle dark mode';
    toggle.setAttribute('aria-label', 'Toggle dark mode');

    var icon = document.createElement('span');
    icon.className = 'theme-toggle__icon';
    icon.textContent = getPreferred() === 'dark' ? '\u263E' : '\u2600';

    var track = document.createElement('span');
    track.className = 'theme-toggle__track';

    var knob = document.createElement('span');
    knob.className = 'theme-toggle__knob';

    track.appendChild(knob);
    toggle.appendChild(icon);
    toggle.appendChild(track);

    toggle.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });

    document.body.appendChild(toggle);
  });
})();

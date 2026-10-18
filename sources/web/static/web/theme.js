/* Theme: apply the stored or preferred theme before first paint, then wire the toggles. */
(function () {
  var KEY = 'mitaroku-theme';

  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }

  function preferred() {
    var saved = stored();
    if (saved === 'dark' || saved === 'light') {
      return saved;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      var label = theme === 'dark' ? button.dataset.labelLight : button.dataset.labelDark;
      button.setAttribute('aria-label', label);
      button.title = label;
    });
  }

  apply(preferred());

  document.addEventListener('DOMContentLoaded', function () {
    apply(preferred());
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      button.addEventListener('click', function () {
        var next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        try {
          localStorage.setItem(KEY, next);
        } catch (e) {
          /* storage unavailable: the choice lasts for this page only */
        }
        apply(next);
      });
    });
  });
})();

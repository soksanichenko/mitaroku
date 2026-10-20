/* The one-time tip about "Mark all up to here". Once dismissed (or once the action is
   used) it never shows again: the choice is kept in localStorage and mirrored on <html>
   as data-hint-mark-up-to, which the stylesheet reads. The tip element is swapped in by
   HTMX now and then, and a swap would wipe anything set on the element itself, so the
   state lives on <html>, which no swap touches. With storage blocked the choice lasts
   for this page only. */
(function () {
  var KEY = 'mitaroku.hint.markUpTo';
  var root = document.documentElement;

  try {
    if (localStorage.getItem(KEY) === 'dismissed') {
      root.dataset.hintMarkUpTo = 'dismissed';
    }
  } catch (e) {
    /* storage unavailable: the tip is shown as the page says */
  }

  window.mtDismissMarkUpToHint = function () {
    root.dataset.hintMarkUpTo = 'dismissed';
    try {
      localStorage.setItem(KEY, 'dismissed');
    } catch (e) {
      /* storage unavailable: dismissed for this page only */
    }
  };
})();

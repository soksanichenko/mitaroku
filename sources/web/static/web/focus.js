/* Keyboard focus across HTMX swaps. A swap that replaces the element the user just
   used drops the focus to <body>; mtFocusAfter(id, fallbackId) says where it should go
   once the swap has settled: the element with `id` (the same control in the new markup),
   or the fallback (a heading, with tabindex="-1") when that control is gone. */
(function () {
  var pending = null;

  window.mtFocusAfter = function (id, fallbackId) {
    pending = { id: id, fallbackId: fallbackId || null };
  };

  document.addEventListener('htmx:afterSettle', function () {
    if (!pending) {
      return;
    }
    var target = document.getElementById(pending.id);
    if (!target && pending.fallbackId) {
      target = document.getElementById(pending.fallbackId);
    }
    pending = null;
    if (target) {
      target.focus();
    }
  });

  ['htmx:responseError', 'htmx:sendError', 'htmx:timeout'].forEach(function (name) {
    document.addEventListener(name, function () {
      pending = null;
    });
  });
})();

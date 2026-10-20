/* An expired session. Behind the portal, nginx answers an HTMX request without a valid
   session with 401 and the page cannot show a login form in a fragment, so the whole
   page is reloaded and the portal sends the user to the sign-in. At most one reload per
   10 seconds (kept in sessionStorage), so a 401 that survives the reload cannot loop;
   without storage there is no reload at all. */
(function () {
  var KEY = 'mt-session-reload';
  var WINDOW_MS = 10000;

  document.addEventListener('htmx:responseError', function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (!xhr || xhr.status !== 401) {
      return;
    }
    var now = Date.now();
    var last = 0;
    try {
      last = Number(sessionStorage.getItem(KEY)) || 0;
    } catch (error) {
      return;
    }
    if (now - last < WINDOW_MS) {
      return;
    }
    try {
      sessionStorage.setItem(KEY, String(now));
    } catch (error) {
      return;
    }
    window.location.reload();
  });
})();

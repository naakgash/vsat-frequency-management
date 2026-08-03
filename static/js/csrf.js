/* Send Django's CSRF token on every non-GET HTMX request.
 *
 * Specification section 21.3. Without this, every HTMX POST in the allocation wizard
 * would be rejected. Declared once here rather than per-template.
 */
(function () {
  "use strict";

  function readCookie(name) {
    const prefix = name + "=";
    for (const part of document.cookie.split(";")) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) {
        return decodeURIComponent(trimmed.slice(prefix.length));
      }
    }
    return null;
  }

  document.body.addEventListener("htmx:configRequest", function (event) {
    if (event.detail.verb && event.detail.verb.toUpperCase() !== "GET") {
      const token = readCookie("csrftoken");
      if (token) {
        event.detail.headers["X-CSRFToken"] = token;
      }
    }
  });
})();

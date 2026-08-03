/* Specification information popover.
 *
 * Specification section 2 and acceptance criterion 26.3: the information button must be
 * accessible and usable without relying only on hover.
 *
 * Hand-written rather than using Bootstrap's popover because the requirement is
 * specific: keyboard operable, correct ARIA state, dismissible with Escape, and the
 * content present in the DOM whether or not it is visible. Bootstrap builds its popover
 * content from a data attribute at show time, which puts the description outside the
 * accessibility tree until the user opens it.
 *
 * Progressive enhancement: with JavaScript disabled every popover stays hidden but the
 * dictionary screen still lists every description, so the information remains reachable.
 */
(function () {
  "use strict";

  var OPEN_CLASS = "spec-popover--open";

  function popoverFor(button) {
    return document.getElementById(button.getAttribute("aria-controls"));
  }

  function close(button) {
    var popover = popoverFor(button);
    if (!popover) return;
    button.setAttribute("aria-expanded", "false");
    popover.hidden = true;
    popover.classList.remove(OPEN_CLASS);
  }

  function closeAll(except) {
    document.querySelectorAll("[data-spec-info]").forEach(function (button) {
      if (button !== except) close(button);
    });
  }

  function toggle(button) {
    var popover = popoverFor(button);
    if (!popover) return;

    var isOpen = button.getAttribute("aria-expanded") === "true";
    closeAll(button);

    if (isOpen) {
      close(button);
      return;
    }

    button.setAttribute("aria-expanded", "true");
    popover.hidden = false;
    popover.classList.add(OPEN_CLASS);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-spec-info]");
    if (button) {
      event.preventDefault();
      toggle(button);
      return;
    }
    // A click anywhere else dismisses. Clicks inside a popover are left alone so its
    // text stays selectable.
    if (!event.target.closest(".spec-popover")) {
      closeAll(null);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;

    var open = document.querySelector('[data-spec-info][aria-expanded="true"]');
    if (open) {
      close(open);
      // Focus returns to the trigger, so keyboard users are not dropped at the top of
      // the document after dismissing.
      open.focus();
    }
  });
})();

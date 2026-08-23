/* Keep the filter rail pointed at the view you are actually in.
 *
 * The rail is rendered once, on a full page load, and lives outside #results --
 * so after an htmx view switch its "Clear all filters" href still names the
 * view you *were* in. Clearing filters on the map then dropped you back to the
 * grid, which is not what "clear filters" means.
 *
 * The server already renders the right href for a full page load and for the
 * no-JS path; this only corrects it after a swap. #results carries the current
 * view on every swap, so that is the one source of truth.
 */
(function () {
  "use strict";

  function syncReset() {
    const results = document.getElementById("results");
    const reset = document.querySelector("a.reset");
    if (!results || !reset) return;

    const view = results.dataset.view;
    // Clearing filters means dropping every parameter -- except the view,
    // which is a property of the page rather than of the filter set.
    reset.setAttribute(
      "href",
      view && view !== "grid" ? `/?view=${encodeURIComponent(view)}` : "/",
    );
  }

  document.addEventListener("DOMContentLoaded", syncReset);
  document.body.addEventListener("htmx:afterSwap", syncReset);
})();

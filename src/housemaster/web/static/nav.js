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

  const STORE = "housemaster-filters-v1:";

  /** The filter parameters, read off the markup rather than listed again here.
   *  views.FILTER_KEYS is the single definition; a copy kept in this file would
   *  drift the moment a filter is added, and drift silently. */
  function state() {
    const results = document.getElementById("results");
    if (!results) return null;
    return {
      keys: (results.dataset.filterKeys || "").split(",").filter(Boolean),
      offering: results.dataset.offering || "buy",
      view: results.dataset.view || "grid",
    };
  }

  /** Remember the current filters for this side of the market.
   *
   *  Buy and rent are stored apart: a EUR 350 000 ceiling and a EUR 1 800 one
   *  are the same parameter meaning different things, and crossing them would
   *  silently empty the result set.
   *
   *  Saved on interaction only, never on arrival -- otherwise opening a link
   *  someone shared would quietly overwrite your own filters. Empty values are
   *  dropped, because the form submits every input whether or not it is set and
   *  a stored `q=&price_min=` would count as "there is something to restore".
   */
  function saveFilters() {
    const here = state();
    if (!here) return;
    const current = new URLSearchParams(window.location.search);
    const keep = new URLSearchParams();
    for (const key of here.keys) {
      for (const value of current.getAll(key)) {
        if (value !== "") keep.append(key, value);
      }
    }
    try {
      window.localStorage.setItem(STORE + here.offering, keep.toString());
    } catch (e) {
      /* private mode: this visit simply is not remembered */
    }
  }

  /** Clearing filters has to forget them too.
   *
   *  Otherwise the restore on the next bare URL puts them straight back and the
   *  button looks broken -- the feature fighting the person using it. */
  function bindReset() {
    const reset = document.querySelector("a.reset");
    if (!reset || reset.dataset.bound === "1") return;
    reset.dataset.bound = "1";
    reset.addEventListener("click", () => {
      const here = state();
      try {
        window.localStorage.removeItem(STORE + (here ? here.offering : "buy"));
      } catch (e) {
        /* nothing was stored anyway */
      }
    });
  }

  function syncReset() {
    const results = document.getElementById("results");
    const reset = document.querySelector("a.reset");
    if (!results || !reset) return;

    // Clearing filters drops every parameter except the two that are
    // properties of the *page* rather than of the filter set: which view you
    // are in, and whether you are looking at sales or rentals.
    const keep = new URLSearchParams();
    if (results.dataset.view && results.dataset.view !== "grid") {
      keep.set("view", results.dataset.view);
    }
    if (results.dataset.offering && results.dataset.offering !== "buy") {
      keep.set("offering", results.dataset.offering);
    }
    const query = keep.toString();
    reset.setAttribute("href", query ? `/?${query}` : "/");
  }

  function onLoad() {
    syncReset();
    bindReset();
  }

  function onSwap() {
    syncReset();
    bindReset();
    saveFilters();
  }

  document.addEventListener("DOMContentLoaded", onLoad);
  document.body.addEventListener("htmx:afterSwap", onSwap);
})();

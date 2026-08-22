/* Light / dark toggle.
 *
 * Three states, not two: an explicit choice is stored and wins, and no stored
 * choice means no attribute at all -- which leaves the CSS free to follow the
 * operating system via prefers-color-scheme. The inline script in <head>
 * applies a stored choice before first paint; this file only handles clicks.
 */
(function () {
  "use strict";

  const KEY = "housemaster-theme";
  const root = document.documentElement;

  const systemPrefersDark = () =>
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  const effectiveTheme = () =>
    root.getAttribute("data-theme") || (systemPrefersDark() ? "dark" : "light");

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {
      /* private mode: the choice simply does not outlive the tab */
    }
    // The map has its own basemap per theme; tell it to catch up.
    document.dispatchEvent(new CustomEvent("housemaster:themechange", { detail: { theme } }));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.addEventListener("click", () =>
        apply(effectiveTheme() === "dark" ? "light" : "dark"),
      );
    }
  });

  // Follow the OS while the user has expressed no preference of their own.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
      if (root.hasAttribute("data-theme")) return;
      document.dispatchEvent(
        new CustomEvent("housemaster:themechange", {
          detail: { theme: event.matches ? "dark" : "light" },
        }),
      );
    });
  }

  window.housemasterTheme = effectiveTheme;
})();

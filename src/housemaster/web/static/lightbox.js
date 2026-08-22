/* Photo viewer for the detail page.
 *
 * Progressive enhancement: the gallery is plain <a href="{full size}"> links,
 * which work on their own. This intercepts the click and shows the photo in an
 * overlay instead, with the whole set navigable rather than one image per tab.
 */
(function () {
  "use strict";

  let photos = [];
  let index = 0;
  let lastFocus = null;

  const $ = (id) => document.getElementById(id);

  function show(next) {
    if (!photos.length) return;
    index = Math.max(0, Math.min(next, photos.length - 1));

    const image = $("lightbox-image");
    image.src = photos[index].href;
    image.alt = photos[index].alt;

    $("lightbox-count").textContent = `${index + 1} / ${photos.length}`;
    $("lightbox-prev").disabled = index === 0;
    $("lightbox-next").disabled = index === photos.length - 1;
  }

  function open(startAt) {
    const box = $("lightbox");
    lastFocus = document.activeElement;
    box.hidden = false;
    box.classList.add("is-open");
    document.body.classList.add("lightbox-open");
    $("lightbox-where").textContent = document.querySelector("h1")?.textContent ?? "";
    show(startAt);
    $("lightbox-close").focus();
  }

  function close() {
    const box = $("lightbox");
    box.classList.remove("is-open");
    box.hidden = true;
    document.body.classList.remove("lightbox-open");
    // Free the decoded image rather than leaving a full-size photo resident.
    $("lightbox-image").removeAttribute("src");
    if (lastFocus) lastFocus.focus();
  }

  const isOpen = () => !$("lightbox").hidden;

  function collect() {
    // Only the detail page's gallery -- not every link on the site.
    return [...document.querySelectorAll(".gallery a")].map((a) => ({
      href: a.href,
      alt: a.querySelector("img")?.alt ?? "",
    }));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const box = $("lightbox");
    if (!box) return;

    document.addEventListener("click", (event) => {
      const link = event.target.closest(".gallery a");
      if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      photos = collect();
      open(photos.findIndex((p) => p.href === link.href));
    });

    $("lightbox-close").addEventListener("click", close);
    $("lightbox-prev").addEventListener("click", () => show(index - 1));
    $("lightbox-next").addEventListener("click", () => show(index + 1));

    // Clicking the backdrop dismisses; clicking the photo itself does not.
    box.addEventListener("click", (event) => {
      if (event.target === box || event.target.classList.contains("lightbox-stage")) close();
    });

    document.addEventListener("keydown", (event) => {
      if (!isOpen()) return;
      if (event.key === "Escape") close();
      else if (event.key === "ArrowLeft") show(index - 1);
      else if (event.key === "ArrowRight") show(index + 1);
      else if (event.key === "Tab") {
        // Keep focus inside the overlay while it is modal.
        const stops = [$("lightbox-close"), $("lightbox-prev"), $("lightbox-next")].filter(
          (b) => !b.disabled,
        );
        const at = stops.indexOf(document.activeElement);
        const step = event.shiftKey ? -1 : 1;
        stops[(at + step + stops.length) % stops.length].focus();
        event.preventDefault();
      }
    });
  });
})();

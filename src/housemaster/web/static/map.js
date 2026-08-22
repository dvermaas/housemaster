/* HouseMaster map view.
 *
 * Basemap tiles come from OpenFreeMap (no API key, no account, no limits);
 * every marker is our own cached data. The basemap is deliberately restyled
 * toward the Plattegrond palette so the houses are the only saturated thing on
 * screen, exactly as in the grid.
 *
 * The one structural idea: the map instance is created ONCE and kept alive.
 * htmx swaps #results on every filter change, but the map node carries
 * hx-preserve, so we only push new data into the existing source -- the
 * viewport the user panned to survives.
 */
(function () {
  "use strict";

  const DEN_HAAG = [4.3007, 52.0705];

  /** Which OpenFreeMap style the current theme wants.
   *
   *  Read from the `--basemap` custom property rather than duplicated here, so
   *  the light/dark mapping lives in one place: the stylesheet. */
  function styleUrl() {
    const name =
      getComputedStyle(document.documentElement).getPropertyValue("--basemap").trim() ||
      "positron";
    return `https://tiles.openfreemap.org/styles/${name}`;
  }

  // Mirrors the NEN scale in app.css. Kept in sync by hand, which is fine for
  // a closed vocabulary that has not changed since the 2021 relabelling.
  const ENERGY = {
    "A+++++": "#0a6b34", "A++++": "#0a6b34", "A+++": "#12833f",
    "A++": "#189a4a", "A+": "#37ac4f", A: "#4caf50", B: "#8cc63f",
    C: "#c8d400", D: "#ffd500", E: "#fbb03b", F: "#f1662a", G: "#e30613",
  };
  const UNKNOWN = "#c4c7c0";

  let map = null;
  let popup = null;
  let pendingUrl = null;

  const paintByLabel = () => {
    // ["match", input, key, value, ..., fallback]
    const expression = ["match", ["get", "label"]];
    for (const [label, colour] of Object.entries(ENERGY)) {
      expression.push(label, colour);
    }
    expression.push(UNKNOWN);
    return expression;
  };

  function styleBasemap() {
    // Nudge OpenFreeMap's Positron toward our paper-and-graphite palette.
    // Wrapped because layer ids are the provider's, not ours, and a style
    // update upstream must not break the map.
    const tweak = (id, prop, value) => {
      try {
        if (map.getLayer(id)) map.setPaintProperty(id, prop, value);
      } catch (_) {
        /* the provider renamed a layer; the default style still works */
      }
    };
    const css = (token) =>
      getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    tweak("background", "background-color", css("--paper"));
    tweak("water", "fill-color", css("--paper-sunk"));
    tweak("landcover-grass", "fill-color", css("--paper-sunk"));
    tweak("park", "fill-color", css("--paper-sunk"));
  }

  /** Idempotent: a theme change calls this again on the new style, and
   *  setStyle's diffing may or may not have kept the old source. Adding one
   *  that already exists throws, which would abort before the layers land and
   *  leave a map with no houses on it. */
  function addHouses(url) {
    for (const id of ["houses", "houses-halo"]) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    if (map.getSource("houses")) map.removeSource("houses");

    map.addSource("houses", { type: "geojson", data: url });

    map.addLayer({
      id: "houses-halo",
      type: "circle",
      source: "houses",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 5, 14, 9, 17, 14],
        "circle-color": "#ffffff",
        "circle-opacity": 0.9,
      },
    });

    map.addLayer({
      id: "houses",
      type: "circle",
      source: "houses",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3.2, 14, 6, 17, 10],
        "circle-color": paintByLabel(),
        "circle-stroke-width": 1,
        "circle-stroke-color": "#16181c",
        "circle-stroke-opacity": 0.55,
      },
    });
  }

  /** Layer-scoped listeners survive the layer being removed and re-added, so
   *  these are bound once per map -- binding them inside addHouses would make
   *  every theme change add another copy and fire the popup twice. */
  function bindHouseEvents() {
    map.on("click", "houses", (event) => showCard(event.features[0]));
    map.on("mouseenter", "houses", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "houses", () => {
      map.getCanvas().style.cursor = "";
    });
  }

  async function showCard(feature) {
    const id = feature.properties.id;
    const [lng, lat] = feature.geometry.coordinates;

    if (popup) popup.remove();
    popup = new maplibregl.Popup({
      closeButton: true,
      maxWidth: "300px",
      offset: 12,
      className: "housepop",
    })
      .setLngLat([lng, lat])
      .setHTML('<div class="popcard-loading">loading…</div>')
      .addTo(map);

    try {
      const response = await fetch(`/house/${id}/card`);
      if (!response.ok) throw new Error(String(response.status));
      popup.setHTML(await response.text());
    } catch (_) {
      popup.setHTML('<div class="popcard-loading">Could not load this house.</div>');
    }
  }

  /** Fit the view to the data. The bounds arrive precomputed from SQL on the
   *  #results element, so this needs no second download of the GeoJSON and
   *  cannot race the source load.
   *
   *  Only ever done once: refitting on every filter change would yank the map
   *  away from wherever the user had panned. */
  function fit(bounds) {
    if (!bounds) return;
    const [x1, y1, x2, y2] = bounds.split(",").map(Number);
    if ([x1, y1, x2, y2].some(Number.isNaN)) return;
    map.fitBounds(
      [
        [x1, y1],
        [x2, y2],
      ],
      { padding: 56, maxZoom: 15, animate: false },
    );
  }

  function setData(url) {
    if (!map) return;
    const source = map.getSource("houses");
    if (source) source.setData(url);
    else pendingUrl = url;
  }

  function init(container, url, bounds, camera) {
    map = new maplibregl.Map({
      container,
      style: styleUrl(),
      center: camera ? camera.center : DEN_HAAG,
      zoom: camera ? camera.zoom : 12,
      bearing: camera ? camera.bearing : 0,
      pitch: camera ? camera.pitch : 0,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }));

    map.on("load", () => {
      styleBasemap();
      addHouses(pendingUrl || url);
      bindHouseEvents();
      if (!camera) fit(bounds);
      pendingUrl = null;
    });
  }

  /** Drop a map whose container the DOM no longer holds.
   *
   *  Switching to the grid removes #map entirely -- hx-preserve can only keep a
   *  node that exists in both the old and new markup. Coming back gives us a
   *  fresh, empty #map while this closure still points at the dead instance, so
   *  it has to be torn down explicitly. `remove()` also frees the WebGL
   *  context, which browsers hand out only a handful of. */
  function dropIfDetached() {
    if (!map) return;
    const container = map.getContainer();
    if (container && document.body.contains(container)) return;
    map.remove();
    map = null;
    popup = null;
    pendingUrl = null;
  }

  /** Called on first paint and after every htmx swap. */
  function sync() {
    const results = document.getElementById("results");
    if (!results || results.dataset.view !== "map") {
      dropIfDetached();
      return;
    }
    const container = document.getElementById("map");
    if (!container) return;

    dropIfDetached();
    const url = results.dataset.geojson;
    if (!map) {
      init(container, url, results.dataset.bounds);
    } else {
      setData(url);
      // The preserved node may have been detached and reinserted by the swap.
      map.resize();
    }
  }

  /** Swap the basemap when the theme changes.
   *
   *  Rebuilt rather than restyled. `setStyle()` looks like the obvious answer,
   *  but it discards our layers and there is no reliable moment to put them
   *  back: `style.load` is not emitted by MapLibre 5, and `styledata` also
   *  fires for the *outgoing* style, so a re-add lands on the style that is
   *  about to be thrown away and the houses silently vanish.
   *
   *  A full rebuild has one code path -- the same one first paint uses -- and
   *  restoring the camera makes it invisible to the user. Theme changes are
   *  rare and deliberate, so the cost is not worth a subtler mechanism. */
  function retheme() {
    if (!map) return;
    const results = document.getElementById("results");
    if (!results || results.dataset.view !== "map") return;

    const camera = {
      center: map.getCenter(),
      zoom: map.getZoom(),
      bearing: map.getBearing(),
      pitch: map.getPitch(),
    };
    const container = map.getContainer();
    map.remove();
    map = null;
    popup = null;
    pendingUrl = null;
    init(container, results.dataset.geojson, null, camera);
  }

  document.addEventListener("DOMContentLoaded", sync);
  document.body.addEventListener("htmx:afterSwap", sync);
  document.addEventListener("housemaster:themechange", retheme);
})();

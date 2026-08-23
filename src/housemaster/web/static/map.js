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
 *
 * Two data layers, with different lifetimes:
 *   houses  -- the filtered set, reloaded on every swap
 *   buurten -- outlines, a context layer; constant URL, loaded once, toggled
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

  const HOOD_KEY = "housemaster-hoods";
  const HOOD_LAYERS = ["hoods-fill", "hoods-line"];

  let map = null;
  let popup = null;
  let pendingUrl = null;
  let hoodsOn = false;
  let hovered = null;

  const css = (token) =>
    getComputedStyle(document.documentElement).getPropertyValue(token).trim();

  function hoodsWanted() {
    try {
      return localStorage.getItem(HOOD_KEY) === "1";
    } catch (_) {
      return false; // private mode: the overlay simply starts off
    }
  }

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
    tweak("background", "background-color", css("--paper"));
    tweak("water", "fill-color", css("--paper-sunk"));
    tweak("landcover-grass", "fill-color", css("--paper-sunk"));
    tweak("park", "fill-color", css("--paper-sunk"));
  }

  /** Colour a buurt by funda's own price level for it.
   *
   *  The five colours come from CSS custom properties, like --basemap does, so
   *  the light/dark ramp lives in the stylesheet and a theme rebuild picks up
   *  the other half for free.
   *
   *  A `step`, not an `interpolate`: the scale arrives as quantile edges from
   *  SQL, because Den Haag's buurt prices are skewed enough that even spacing
   *  draws as one flat wash. See db.neighbourhood_price_scale. */
  function rampExpression(scale) {
    const colours = [1, 2, 3, 4, 5].map((i) => css(`--choro-${i}`));
    const missing = css("--choro-none");
    // scale is (lo, b1..b4, hi); the interior edges are the step boundaries.
    const edges = scale ? scale.slice(1, -1) : [];
    if (!edges.length) return colours[2] || missing;

    const steps = [];
    edges.forEach((edge, i) => steps.push(edge, colours[i + 1]));
    return [
      "case",
      ["==", ["get", "price_m2"], null],
      missing,
      ["step", ["to-number", ["get", "price_m2"]], colours[0], ...steps],
    ];
  }

  /** Outlines go in BEFORE the houses so the markers always sit on top -- a
   *  translucent wash over a house dot would defeat the point of both. */
  function addBoundaries(url, scale) {
    for (const id of HOOD_LAYERS) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    if (map.getSource("hoods")) map.removeSource("hoods");
    if (!url) return;

    map.addSource("hoods", { type: "geojson", data: url });
    const visibility = hoodsOn ? "visible" : "none";

    map.addLayer({
      id: "hoods-fill",
      type: "fill",
      source: "hoods",
      layout: { visibility },
      paint: {
        "fill-color": rampExpression(scale),
        // Transparent enough to read streets and water straight through it.
        "fill-opacity": [
          "case",
          ["boolean", ["feature-state", "hover"], false],
          Number(css("--choro-fill-hover")) || 0.52,
          Number(css("--choro-fill")) || 0.34,
        ],
      },
    });

    map.addLayer({
      id: "hoods-line",
      type: "line",
      source: "hoods",
      layout: { visibility, "line-join": "round" },
      paint: {
        "line-color": css("--choro-5"),
        "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0.8],
        "line-opacity": 0.7,
      },
    });
  }

  /** Bound once per map, like the house handlers, for the same reason. */
  function bindHoodEvents() {
    const readout = () => document.getElementById("hood-readout");

    map.on("mousemove", "hoods-fill", (event) => {
      const feature = event.features && event.features[0];
      if (!feature || feature.id === hovered) return;
      clearHover();
      hovered = feature.id;
      map.setFeatureState({ source: "hoods", id: hovered }, { hover: true });

      const node = readout();
      if (!node) return;
      const price = feature.properties.price_m2;
      node.textContent = price
        ? `${feature.properties.name} · € ${Number(price).toLocaleString("nl-NL")}/m²`
        : feature.properties.name;
      node.hidden = false;
    });

    map.on("mouseleave", "hoods-fill", () => {
      clearHover();
      const node = readout();
      if (node) node.hidden = true;
    });
  }

  function clearHover() {
    if (hovered === null || !map || !map.getSource("hoods")) return;
    map.setFeatureState({ source: "hoods", id: hovered }, { hover: false });
    hovered = null;
  }

  /** Reflect `hoodsOn` into the map, the button and the ramp legend. */
  function applyHoods() {
    if (map) {
      for (const id of HOOD_LAYERS) {
        if (map.getLayer(id)) {
          map.setLayoutProperty(id, "visibility", hoodsOn ? "visible" : "none");
        }
      }
      if (!hoodsOn) clearHover();
    }
    const button = document.getElementById("hood-toggle");
    if (button) button.setAttribute("aria-pressed", String(hoodsOn));
    const ramp = document.getElementById("hood-ramp");
    if (ramp) ramp.hidden = !hoodsOn;
    const readout = document.getElementById("hood-readout");
    if (readout && !hoodsOn) readout.hidden = true;
  }

  /** The toggle lives outside #map, so htmx replaces the element on every
   *  swap. Rebinding a fresh node is simpler and safer than trying to preserve
   *  it, and the state itself lives in localStorage rather than in the DOM. */
  function bindToggle() {
    const button = document.getElementById("hood-toggle");
    if (!button || button.dataset.bound === "1") return;
    button.dataset.bound = "1";
    button.addEventListener("click", () => {
      hoodsOn = !hoodsOn;
      try {
        localStorage.setItem(HOOD_KEY, hoodsOn ? "1" : "0");
      } catch (_) {
        /* private mode: the toggle still works for this page view */
      }
      applyHoods();
    });
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

  function init(container, cfg, camera) {
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
      // Order matters: outlines first, houses on top of them.
      addBoundaries(cfg.shapes, cfg.scale);
      addHouses(pendingUrl || cfg.houses);
      bindHoodEvents();
      bindHouseEvents();
      applyHoods();
      if (!camera) fit(cfg.bounds);
      pendingUrl = null;
    });
  }

  /** Everything the map needs, read off the swapped-in #results element. */
  function readConfig(results) {
    const scale = (results.dataset.hoodScale || "")
      .split(",")
      .map(Number)
      .filter((n) => !Number.isNaN(n));
    return {
      houses: results.dataset.geojson,
      shapes: results.dataset.shapes,
      scale: scale.length >= 3 ? scale : null,
      bounds: results.dataset.bounds,
    };
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
    hovered = null;
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
    // A remembered "on" must not survive into a cache with no outlines yet:
    // the button is disabled server-side, so honour that over localStorage.
    const toggle = document.getElementById("hood-toggle");
    hoodsOn = hoodsWanted() && !(toggle && toggle.disabled);
    const cfg = readConfig(results);
    if (!map) {
      init(container, cfg);
    } else {
      setData(cfg.houses);
      // The preserved node may have been detached and reinserted by the swap.
      map.resize();
    }
    // The controls are outside #map, so the swap replaced them: rebind and
    // restore. Safe before the style loads -- applyHoods skips missing layers.
    bindToggle();
    applyHoods();
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
    hovered = null;
    init(container, readConfig(results), camera);
  }

  document.addEventListener("DOMContentLoaded", sync);
  document.body.addEventListener("htmx:afterSwap", sync);
  document.addEventListener("housemaster:themechange", retheme);
})();

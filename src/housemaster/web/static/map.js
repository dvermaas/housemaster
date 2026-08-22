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

  const STYLE = "https://tiles.openfreemap.org/styles/positron";
  const DEN_HAAG = [4.3007, 52.0705];

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
    tweak("background", "background-color", "#f7f7f4");
    tweak("water", "fill-color", "#e4e8e6");
    tweak("landcover-grass", "fill-color", "#eef0ea");
    tweak("park", "fill-color", "#eef0ea");
  }

  function addHouses(url) {
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

  function init(container, url, bounds) {
    map = new maplibregl.Map({
      container,
      style: STYLE,
      center: DEN_HAAG,
      zoom: 12,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }));

    map.on("load", () => {
      styleBasemap();
      addHouses(pendingUrl || url);
      fit(bounds);
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

  document.addEventListener("DOMContentLoaded", sync);
  document.body.addEventListener("htmx:afterSwap", sync);
})();

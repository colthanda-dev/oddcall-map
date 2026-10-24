/* Shared map helpers. Dark basemap, state lines, category colors, pin popups. */
window.OC = (function () {
  var CAT = { uap: '#F07A2A', light: '#E0B341', cryptid: '#7DBB6E', apparition: '#9B8CDB', sound: '#5FB3C9', animal: '#C98B5F', other: '#8A877A' };
  var TIER_OP = { field_verified: 1, witness_direct: .95, witness_anonymous: .85, unverified_public: .55, secondhand: .55, disputed: .4 };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function base(el, opts) {
    opts = opts || {};
    var map = L.map(el, { zoomControl: true, minZoom: 3, maxZoom: 17, scrollWheelZoom: opts.scroll !== false, worldCopyJump: false, attributionControl: true });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', { subdomains: 'abcd', maxZoom: 19, attribution: '&copy; OpenStreetMap &copy; CARTO' }).addTo(map);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', { subdomains: 'abcd', maxZoom: 19, pane: 'shadowPane' }).addTo(map);
    fetch('https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-10m.json').then(function (r) { return r.json(); }).then(function (topo) {
      var feats = topojson.feature(topo, topo.objects.states).features;
      L.geoJSON({ type: 'FeatureCollection', features: feats }, { interactive: false, style: { color: '#5A5B50', weight: 1, fill: false } }).addTo(map);
    }).catch(function () { });
    return map;
  }
  function pin(p, latlng) {
    var m = L.circleMarker(latlng, { radius: 6, color: '#0E0F0C', weight: 1.5, fillColor: CAT[p.category] || CAT.other, fillOpacity: TIER_OP[p.credibility_tier] || .7 });
    var where = [p.place, p.county ? p.county + ' Co.' : null, p.state].filter(Boolean).join(', ');
    m.bindPopup('<div class="k">' + esc(p.category) + (p.subcategory ? ' · ' + esc(p.subcategory) : '') + ' · ' + esc(p.event_date || 'date not recorded') + '</div>'
      + '<b>' + esc(where || 'location withheld') + '</b><br>' + esc(p.snippet || p.narrative || '') + '<br><span class="k">' + esc(p.credibility_label) + '</span> · <a href="' + esc(p.url) + '">Open report</a>');
    return m;
  }
  function load(map, url, group) {
    group = group || L.layerGroup().addTo(map);
    return fetch(url).then(function (r) { return r.json(); }).then(function (gj) {
      group.clearLayers();
      (gj.features || []).forEach(function (f) {
        if (!f.geometry || !f.geometry.coordinates) return;
        var c = f.geometry.coordinates;
        group.addLayer(pin(f.properties, [c[1], c[0]]));
      });
      return gj.features.length;
    });
  }
  return { base: base, pin: pin, load: load, esc: esc, CAT: CAT };
})();

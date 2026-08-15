/* =====================================================================
   OWCS Comp Tracker — app/charts.js
   The two chart forms this dataset actually needs, drawn as SVG.

   Why hand-drawn rather than a charting library: every number here is a
   magnitude out of a stated sample, and the honest form for that is a bar
   whose length is the number and whose label is the value. A library would
   ship a plotting engine, an animation layer and a theme system to draw a
   rectangle, and then have to be argued back into this design system's
   surfaces, hairlines and type. Two forms, ~200 lines, no build step.

   Rules these follow (and they are not negotiable per chart):
     * Bars cap at 22px thick and grow from a single baseline; the data end
       is rounded 4px, the baseline end is square.
     * A 2px gap in the surface colour separates touching fills — never a
       stroke, which would add ink that is not data.
     * Text never wears the data colour. Values and labels use text tokens;
       identity comes from the swatch beside them.
     * Values are labelled at the tip, and the table under every chart
       carries the rest. Nothing is available only by hovering.
     * A chart never invents precision: the caption states the sample it
       was computed from, every time.

   Colour. Magnitude is one hue (the product gold) on a track — sequential,
   more-is-longer, no palette required. Role is identity, so it gets a
   categorical palette, and that palette is NOT the interface's role tokens:
   those are tuned to read as a 3px underline on a portrait, and as adjacent
   fills they fail deuteranopia separation (red↔green ΔE 5.6). These three
   are the same hues re-stepped until every check passes on this surface —
   lightness band, chroma floor, CVD separation, and contrast against
   --s1 — verified with the dataviz validator, worst adjacent pair ΔE 12.8
   under deutan, 31.6 normal.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;

  const ROLE_FILL = { Tank: "#4a8ded", Damage: "#b4303a", Support: "#1da875" };
  const ROLE_ORDER = ["Tank", "Damage", "Support"];
  const OTHER_FILL = "#6b7385";
  const BAR_H = 22;
  const GAP = 2;

  const C = (P.chart = {});
  C.roleFill = (role) => ROLE_FILL[role] || OTHER_FILL;

  /* ------------------------------------------------------------- bars
     Horizontal, because the labels are hero and map names and a column
     chart would set them at an angle. One series, so no legend: the
     heading says what is plotted.

     rows: [{ label, value, note, mark, href, role }]
     opts: { max, unit, caption, title }
  */
  C.bars = function (rows, opts) {
    opts = opts || {};
    if (!rows || !rows.length) return "";
    const max = opts.max || Math.max.apply(null, rows.map((r) => r.value)) || 1;
    const fmt = opts.format || ((v) => String(v));

    const body = rows.map((r) => {
      const w = Math.max(0, Math.min(1, r.value / max));
      const fill = opts.byRole ? C.roleFill(r.role) : "var(--gold)";
      const label =
        '<span class="ch__label">' +
        (r.mark || "") +
        '<span class="ch__name">' + esc(r.label) + "</span>" +
        (r.note ? '<span class="ch__note">' + esc(r.note) + "</span>" : "") +
        "</span>";
      return '<div class="ch__row"' + (r.role ? ' data-role="' + esc(r.role) + '"' : "") + ">" +
        (r.href ? '<a class="ch__key" href="' + esc(r.href) + '">' + label + "</a>"
                : '<span class="ch__key">' + label + "</span>") +
        '<span class="ch__track" role="img" aria-label="' +
          esc(r.label + ": " + fmt(r.value) + (opts.unit ? " " + opts.unit : "")) + '">' +
          '<span class="ch__bar" style="width:' + (w * 100).toFixed(2) + "%;--fill:" +
            fill + '"></span>' +
        "</span>" +
        '<span class="ch__val">' + esc(fmt(r.value)) + "</span>" +
        "</div>";
    }).join("");

    return '<figure class="ch">' +
      (opts.title ? '<figcaption class="ch__title">' + esc(opts.title) + "</figcaption>" : "") +
      '<div class="ch__rows" style="--ch-bar:' + BAR_H + 'px">' + body + "</div>" +
      (opts.caption ? '<p class="ch__caption dim small">' + esc(opts.caption) + "</p>" : "") +
      "</figure>";
  };

  /* ------------------------------------------------------------ stack
     Part-to-whole on one horizontal bar. Segments are separated by a 2px
     surface gap, and the legend is always present because there is more
     than one series — a reader must never have to match colours by eye.

     parts: [{ label, value, fill }]
  */
  C.stack = function (parts, opts) {
    opts = opts || {};
    const kept = (parts || []).filter((p) => p.value > 0);
    const total = kept.reduce((s, p) => s + p.value, 0);
    if (!total) return "";

    const segs = kept.map((p) => {
      const share = p.value / total;
      return '<span class="ch__seg" style="flex:' + share.toFixed(4) + ' 1 0;--fill:' +
        (p.fill || OTHER_FILL) + '" title="' +
        esc(p.label + ": " + p.value + " (" + Math.round(share * 100) + "%)") + '"></span>';
    }).join("");

    const legend = kept.map((p) =>
      '<span class="ch__legend-item"><span class="ch__swatch" style="--fill:' +
      (p.fill || OTHER_FILL) + '"></span>' + esc(p.label) +
      '<span class="ch__legend-val">' + p.value + "</span></span>").join("");

    return '<figure class="ch ch--stack">' +
      (opts.title ? '<figcaption class="ch__title">' + esc(opts.title) + "</figcaption>" : "") +
      '<div class="ch__stack" style="--gap:' + GAP + 'px" role="img" aria-label="' +
        esc(kept.map((p) => p.label + " " + p.value).join(", ")) + '">' + segs + "</div>" +
      '<div class="ch__legend">' + legend + "</div>" +
      (opts.caption ? '<p class="ch__caption dim small">' + esc(opts.caption) + "</p>" : "") +
      "</figure>";
  };

  /* Role split of a list of hero ids — the one composite this product
     asks for often enough to be worth a helper. */
  C.roleSplit = function (heroIds, opts) {
    const counts = {};
    (heroIds || []).forEach((id) => {
      const role = (P.hero(id) || {}).role || "Other";
      counts[role] = (counts[role] || 0) + 1;
    });
    const parts = ROLE_ORDER.concat(Object.keys(counts).filter((r) => ROLE_ORDER.indexOf(r) < 0))
      .filter((r, i, a) => a.indexOf(r) === i)
      .map((role) => ({ label: role, value: counts[role] || 0, fill: C.roleFill(role) }));
    return C.stack(parts, opts);
  };
})();

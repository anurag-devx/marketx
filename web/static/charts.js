/* Small SVG charts, no libraries: sparkline, price chart (line or candles + averages + volume), bars-with-line. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fmt = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

  function niceTicks(lo, hi, n = 5) {
    if (!(hi > lo)) { hi = lo + 1; }
    const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function sparkline(values, opt = {}) {
    const w = opt.w || 160, h = opt.h || 36, vals = (values || []).filter((v) => v != null);
    if (vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const up = vals[vals.length - 1] >= vals[0];
    const col = opt.color || (up ? css("--up") : css("--down"));
    const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - 2 - ((v - lo) / span) * (h - 4)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none"><polyline fill="none" stroke="${col}" stroke-width="1.6" points="${pts}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  /* bars: {t:[dates], o,h,l,c,v, sma50, sma200}; opt: {mode:"line"|"candle", height} */
  function priceChart(host, bars, opt = {}) {
    host.innerHTML = "";
    host.classList.add("chart");
    const tagW = (opt.levels || []).some((l) => l.label) ? 52 : 0;          // room on the right for level tags (E1, SL, T1 ...)
    const W = host.clientWidth || 900, H = opt.height || 380, padL = 8, padR = 58 + tagW, padT = 10, volH = 64, gap = 10, axisH = 20;
    const priceH = H - volH - gap - axisH - padT;
    const n = bars.t.length;
    if (!n) { host.innerHTML = '<div class="empty">No prices.</div>'; return; }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H });
    host.appendChild(svg);
    const lows = opt.mode === "candle" ? bars.l : bars.c, highs = opt.mode === "candle" ? bars.h : bars.c;
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < n; i++) { lo = Math.min(lo, lows[i]); hi = Math.max(hi, highs[i]);
      for (const s of [bars.sma50, bars.sma200, bars.vwap]) if (s && s[i] != null) { lo = Math.min(lo, s[i]); hi = Math.max(hi, s[i]); } }
    (opt.levels || []).forEach((lv) => { if (lv.fit && lv.price != null) { lo = Math.min(lo, lv.price); hi = Math.max(hi, lv.price); } });
    const pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
    const x = (i) => padL + ((i + 0.5) / n) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * priceH;
    const vmax = Math.max(...bars.v) || 1, vTop = padT + priceH + gap;
    const vy = (v) => vTop + volH - (v / vmax) * volH;
    const line = css("--line"), up = css("--up"), down = css("--down"), acc = css("--accent");
    for (const t of niceTicks(lo, hi, 5)) {
      el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: line, "stroke-width": 1 }, svg);
      const tx = el("text", { x: W - padR + 8, y: y(t) + 4 }, svg); tx.textContent = fmt(t, t >= 1000 ? 0 : 2);
    }
    // date labels
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      if (opt.intraday) continue;
      const d = new Date(bars.t[i]);
      const tx = el("text", { x: x(i), y: H - 5, "text-anchor": "middle" }, svg);
      tx.textContent = d.toLocaleDateString("en-IN", spanDays(bars.t) > 400 ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" });
    }
    if (opt.intraday) {                                      // time labels; a dashed line and the date where each day starts
      const days = []; bars.t.forEach((t, i) => { if (!i || t.slice(0, 10) !== bars.t[i - 1].slice(0, 10)) days.push(i); });
      const mon = (t) => new Date(t.slice(0, 10) + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
      if (days.length > 1) days.forEach((i, k) => { if (i) el("line", { x1: x(i) - 0.5 * (W - padL - padR) / n, x2: x(i) - 0.5 * (W - padL - padR) / n, y1: padT, y2: vTop + volH, stroke: line, "stroke-dasharray": "2 3" }, svg);
        if (days.length <= 12 || k % Math.ceil(days.length / 10) === 0) { const tx = el("text", { x: x(i), y: H - 5, "text-anchor": "start" }, svg); tx.textContent = mon(bars.t[i]); } });
      else for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 7))) { const tx = el("text", { x: x(i), y: H - 5, "text-anchor": "middle" }, svg); tx.textContent = bars.t[i].slice(11, 16); }
    }
    // volume
    const bw = Math.max(1, ((W - padL - padR) / n) * 0.7);
    for (let i = 0; i < n; i++) {
      const isUp = bars.c[i] >= bars.o[i];
      el("rect", { x: x(i) - bw / 2, y: vy(bars.v[i]), width: bw, height: Math.max(0.5, vTop + volH - vy(bars.v[i])),
                   fill: isUp ? up : down, opacity: 0.45 }, svg);
    }
    // price
    if (opt.mode === "candle") {
      for (let i = 0; i < n; i++) {
        const isUp = bars.c[i] >= bars.o[i], col = isUp ? up : down;
        el("line", { x1: x(i), x2: x(i), y1: y(bars.h[i]), y2: y(bars.l[i]), stroke: col, "stroke-width": 1 }, svg);
        const top = y(Math.max(bars.o[i], bars.c[i])), bot = y(Math.min(bars.o[i], bars.c[i]));
        el("rect", { x: x(i) - bw / 2, y: top, width: bw, height: Math.max(1, bot - top), fill: col }, svg);
      }
    } else {
      const pts = bars.c.map((v, i) => `${x(i)},${y(v)}`).join(" ");
      const area = `${x(0)},${padT + priceH} ${pts} ${x(n - 1)},${padT + priceH}`;
      const gid = "g" + Math.random().toString(36).slice(2, 8);
      const defs = el("defs", {}, svg), lg = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el("stop", { offset: "0%", "stop-color": acc, "stop-opacity": 0.16 }, lg);
      el("stop", { offset: "100%", "stop-color": acc, "stop-opacity": 0 }, lg);
      el("polygon", { points: area, fill: `url(#${gid})` }, svg);
      el("polyline", { points: pts, fill: "none", stroke: acc, "stroke-width": 1.8 }, svg);
    }
    const sma = (s, col, dash) => {
      if (!s) return;
      const pts = s.map((v, i) => v == null ? null : `${x(i)},${y(v)}`).filter(Boolean).join(" ");
      if (pts) el("polyline", { points: pts, fill: "none", stroke: col, "stroke-width": 1.2, "stroke-dasharray": dash || "" }, svg);
    };
    sma(bars.sma50, "#f59e0b"); sma(bars.sma200, css("--faint"), "4 3"); if (bars.vwap) { let seg = [];                      // VWAP restarts each day: one line per day
      const flush = () => { if (seg.length) el("polyline", { points: seg.join(" "), fill: "none", stroke: "#0ea5e9", "stroke-width": 1.4 }, svg); seg = []; };
      bars.vwap.forEach((v, i) => { if (i && bars.t[i].slice(0, 10) !== bars.t[i - 1].slice(0, 10)) flush(); if (v != null) seg.push(`${x(i)},${y(v)}`); }); flush(); }
    const pick = (c) => c === "up" ? up : c === "down" ? down : c === "faint" ? css("--faint") : c === "blue" ? "#0ea5e9" : c && c[0] === "#" ? c : acc;
    // plan zones (shaded price bands over the last part of the chart)
    (opt.zones || []).forEach((z) => { const a0 = x(z.from ?? 0), lo = Math.min(z.lo, z.hi), hi = Math.max(z.lo, z.hi);
      if (!(hi >= lo) || hi < lo || !isFinite(lo)) return;
      el("rect", { x: a0, y: y(hi), width: Math.max(1, x(n - 1) - a0 + 6), height: Math.max(1, y(lo) - y(hi)), fill: pick(z.color), opacity: z.opacity ?? 0.08 }, svg); });
    // levels: dashed lines with an optional tag at the right edge; tags are nudged apart so they never overlap
    const tagY = new Map(); { let last = -1e9; (opt.levels || []).filter((l) => l.label && l.price != null).map((l) => [l, y(l.price)]).sort((a, b) => a[1] - b[1])
      .forEach(([l, yy]) => { const v = Math.max(yy, last + 17); tagY.set(l, v); last = v; }); }
    (opt.levels || []).forEach((lv) => { if (lv.price == null || lv.price < lo || lv.price > hi) return; const col = pick(lv.color), yy = y(lv.price), ty = tagY.get(lv) ?? yy;
      el("line", { x1: x(lv.from ?? 0), x2: lv.label ? W - tagW : x(lv.to ?? n - 1) + 6, y1: yy, y2: yy, stroke: col, "stroke-dasharray": lv.dash ?? "4 3", "stroke-width": lv.width ?? 1.4 }, svg);
      if (lv.label) { const w = Math.min(tagW - 4, 7 * String(lv.label).length + 10), x0 = W - tagW + 2;
        if (Math.abs(ty - yy) > 1) el("line", { x1: W - tagW, x2: x0, y1: yy, y2: ty, stroke: col, "stroke-width": 1 }, svg);
        el("rect", { x: x0, y: ty - 8, width: w, height: 16, rx: 3, fill: col }, svg);
        const t = el("text", { x: x0 + 5, y: ty + 4, style: "fill:#fff", "font-weight": 600, "font-size": 10.5 }, svg); t.textContent = lv.label; } });
    const seen = {};
    (opt.marks || []).forEach((m) => { const k = (seen[m.i] = (seen[m.i] || 0) + 1) - 1, c0 = pick(m.color), cx = x(m.i), d = m.dir === "down" ? -1 : 1, cy = y(m.price) + d * k * 26;
      el("polygon", { points: `${cx},${cy + d * 2} ${cx - 8},${cy + d * 15} ${cx + 8},${cy + d * 15}`, fill: c0 }, svg);
      if (m.label) { const t = el("text", { x: cx, y: cy + d * 26, "text-anchor": "middle", "font-size": 11.5, "font-weight": 600, style: `fill:${c0}`, "paint-order": "stroke", stroke: css("--surface"), "stroke-width": 3 }, svg); t.textContent = m.label; } });
    // hover
    const cross = el("line", { y1: padT, y2: vTop + volH, stroke: css("--faint"), "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
    const dotc = el("circle", { r: 3.5, fill: acc, visibility: "hidden" }, svg);
    const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
    const overlay = el("rect", { x: padL, y: 0, width: W - padL - padR, height: H, fill: "transparent" }, svg);
    overlay.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.min(n - 1, Math.max(0, Math.round(((px - padL) / (W - padL - padR)) * n - 0.5)));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      dotc.setAttribute("cx", x(i)); dotc.setAttribute("cy", y(bars.c[i])); dotc.setAttribute("visibility", "visible");
      const prev = i > 0 ? bars.c[i - 1] : bars.c[i], ch = 100 * (bars.c[i] / prev - 1);
      tip.innerHTML = (opt.intraday ? `${new Date(bars.t[i].slice(0, 10) + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" })} ${bars.t[i].slice(11, 16)} · `
        : `${new Date(bars.t[i]).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · `) +
        (opt.mode === "candle" ? `O ${fmt(bars.o[i])} H ${fmt(bars.h[i])} L ${fmt(bars.l[i])} ` : "") +
        `C <b>${fmt(bars.c[i])}</b> <span>${ch >= 0 ? "+" : ""}${fmt(ch)}%</span>`;
      tip.hidden = false;
      tip.style.left = `${(x(i) / W) * r.width}px`; tip.style.top = `${(y(bars.c[i]) / H) * r.height}px`;
    });
    overlay.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dotc.setAttribute("visibility", "hidden"); tip.hidden = true; });
    // vertical markers (e.g. "saved here")
    (opt.vmarks || []).forEach((m) => { if (m.i == null || m.i < 0 || m.i >= n) return; el("line", { x1: x(m.i), x2: x(m.i), y1: padT, y2: vTop + volH, stroke: acc, "stroke-dasharray": "5 4", "stroke-width": 1.4 }, svg);
      if (m.label) { const t = el("text", { x: x(m.i) + 4, y: padT + 12, "font-size": 10.5, "font-weight": 600, style: `fill:${acc}` }, svg); t.textContent = m.label; } });
    // scales for overlays (chart workspace drawings): data <-> screen
    const plotW = W - padL - padR;
    host._map = { svg, W, H, n, padL, padR, padT, priceH, lo, hi, t: bars.t, overlay, x, y,
      i: (px) => Math.min(n - 1, Math.max(0, Math.round(((px - padL) / plotW) * n - 0.5))),
      p: (py) => lo + (1 - (py - padT) / priceH) * (hi - lo) };
    return host._map;
  }

  /* labels[], bars[] (left axis), line[] (right axis) */
  function barLine(host, labels, bars, line, opt = {}) {
    host.innerHTML = ""; host.classList.add("chart");
    const W = host.clientWidth || 700, H = opt.height || 220, padL = 50, padR = 50, padT = 10, axisH = 22, ph = H - padT - axisH, n = labels.length;
    if (!n) return;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    const bmax = Math.max(...bars.filter((v) => v != null), 1);
    const lv = line.filter((v) => v != null), lmin = Math.min(0, ...lv), lmax = Math.max(...lv, 1);
    const x = (i) => padL + ((i + 0.5) / n) * (W - padL - padR), bw = ((W - padL - padR) / n) * 0.56;
    const yb = (v) => padT + ph - (v / bmax) * ph, yl = (v) => padT + ph - ((v - lmin) / (lmax - lmin || 1)) * ph;
    for (const t of niceTicks(0, bmax, 4)) {
      el("line", { x1: padL, x2: W - padR, y1: yb(t), y2: yb(t), stroke: css("--line") }, svg);
      const tx = el("text", { x: padL - 6, y: yb(t) + 4, "text-anchor": "end" }, svg); tx.textContent = fmt(t, 0);
    }
    for (const t of niceTicks(lmin, lmax, 4)) { const tx = el("text", { x: W - padR + 6, y: yl(t) + 4 }, svg); tx.textContent = fmt(t, 0); }
    bars.forEach((v, i) => { if (v != null) el("rect", { x: x(i) - bw / 2, y: yb(v), width: bw, height: padT + ph - yb(v), rx: 3, fill: css("--accent"), opacity: 0.22 }, svg); });
    const pts = line.map((v, i) => v == null ? null : `${x(i)},${yl(v)}`).filter(Boolean);
    el("polyline", { points: pts.join(" "), fill: "none", stroke: css("--accent"), "stroke-width": 2 }, svg);
    line.forEach((v, i) => { if (v != null) el("circle", { cx: x(i), cy: yl(v), r: 3, fill: css("--accent") }, svg); });
    labels.forEach((l, i) => { const tx = el("text", { x: x(i), y: H - 6, "text-anchor": "middle" }, svg); tx.textContent = l; });
  }


  /* Generic chart from a server spec:
     {type:"line", x:[dates|labels], series:[{name, y:[], color?, dash?, area?, axis?:"right"}], ysuffix?, zero?, height?}
     {type:"bars", x:[...], series:[{name, y:[], color?}] (stacked, negatives below 0), line?:{name,y}, height?}
     {type:"hbar", labels:[], values:[], suffix?:"%", height?} */
  const PALETTE = () => [css("--accent"), "#f59e0b", "#0891b2", "#db2777", "#65a30d", css("--faint")];
  function isDate(v) { return typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v); }
  function xlabel(v, span) { if (!isDate(v)) return String(v); const d = new Date(v);
    return d.toLocaleDateString("en-IN", span === 0 ? { day: "numeric", month: "short", year: "numeric" } : span > 330 ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" }); }
  function spanDays(x) { return isDate(x[0]) && isDate(x[x.length - 1]) ? (new Date(x[x.length - 1]) - new Date(x[0])) / 864e5 : 0; }

  function lineChart(host, spec) {
    host.innerHTML = ""; host.classList.add("chart");
    const W = host.clientWidth || 800, H = spec.height || 280, padL = 8, padT = 10, axisH = 22;
    const right = spec.series.some((s) => s.axis === "right");
    const padR = 58, padR2 = right ? 58 : 0;
    const n = spec.x.length; if (!n) return;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    const range = (ss) => { let lo = Infinity, hi = -Infinity; ss.forEach((s) => s.y.forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
      if (spec.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); } if (!(hi > lo)) { hi = lo + 1; } const p = (hi - lo) * 0.06; return [lo - (spec.zero && lo === 0 ? 0 : p), hi + p]; };
    if (spec.log) { spec = { ...spec, series: spec.series.map((s) => ({ ...s, raw: s.y, y: s.y.map((v) => v != null && v > 0 ? Math.log10(v) : null) })) }; }
    const L = spec.series.filter((s) => s.axis !== "right"), R = spec.series.filter((s) => s.axis === "right");
    const [lo, hi] = range(L.length ? L : R), [lo2, hi2] = R.length ? range(R) : [0, 1];
    const plotR = W - padR - padR2 + (right ? 0 : 0);
    const x = (i) => padL + (n === 1 ? 0.5 : i / (n - 1)) * (plotR - padL - 4);
    const ph = H - padT - axisH, y = (v) => padT + (1 - (v - lo) / (hi - lo)) * ph, y2 = (v) => padT + (1 - (v - lo2) / (hi2 - lo2)) * ph;
    const sfx = spec.ysuffix || "";
    for (const t of niceTicks(lo, hi, 5)) { el("line", { x1: padL, x2: plotR, y1: y(t), y2: y(t), stroke: css("--line") }, svg);
      const tx = el("text", { x: plotR + 6, y: y(t) + 4 }, svg); tx.textContent = spec.log ? fmt(Math.pow(10, t), Math.pow(10, t) < 10 ? 1 : 0) : fmt(t, Math.abs(hi - lo) < 10 ? 1 : 0) + sfx; }
    if (R.length) for (const t of niceTicks(lo2, hi2, 4)) { const tx = el("text", { x: plotR + 52, y: y2(t) + 4 }, svg); tx.textContent = fmt(t, Math.abs(hi2 - lo2) < 10 ? 1 : 0); }
    if (spec.zero && lo < 0) el("line", { x1: padL, x2: plotR, y1: y(0), y2: y(0), stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg);
    (spec.hlines || []).forEach((h) => el("line", { x1: padL, x2: plotR, y1: y(h), y2: y(h), stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg));
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) { const tx = el("text", { x: x(i), y: H - 5, "text-anchor": i === 0 ? "start" : "middle" }, svg); tx.textContent = xlabel(spec.x[i], spanDays(spec.x)); }
    (spec.vlines || []).forEach((v) => { const i = spec.x.findIndex((d) => String(d) >= String(v.x)); if (i < 0) return;
      el("line", { x1: x(i), x2: x(i), y1: padT, y2: padT + ph, stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg);
      if (v.label) { const tx = el("text", { x: x(i) + 4, y: padT + 10 }, svg); tx.textContent = v.label; } });
    const pal = PALETTE();
    spec.series.forEach((s, k) => {
      const col = s.color || pal[k % pal.length], yy = s.axis === "right" ? y2 : y;
      const pts = s.y.map((v, i) => v == null ? null : `${x(i)},${yy(v)}`).filter(Boolean);
      if (s.area && pts.length) { const gid = "a" + Math.random().toString(36).slice(2, 8), defs = el("defs", {}, svg), lg = el("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
        el("stop", { offset: "0%", "stop-color": col, "stop-opacity": 0.18 }, lg); el("stop", { offset: "100%", "stop-color": col, "stop-opacity": 0 }, lg);
        el("polygon", { points: `${x(0)},${padT + ph} ${pts.join(" ")} ${x(n - 1)},${padT + ph}`, fill: `url(#${gid})` }, svg); }
      el("polyline", { points: pts.join(" "), fill: "none", stroke: col, "stroke-width": s.width || 1.8, "stroke-dasharray": s.dash ? "4 3" : "" }, svg);
    });
    hover(host, svg, W, H, n, x, (i) => `${xlabel(spec.x[i], 0)} · ` + spec.series.map((s) => { const v = (s.raw || s.y)[i]; return `${s.name ? s.name + " " : ""}<b>${v == null ? "—" : fmt(v, 2)}${s.axis === "right" ? "" : sfx}</b>`; }).join(" · "), padT, padT + ph);
    legend(host, spec.series, pal);
  }

  function barsChart(host, spec) {
    host.innerHTML = ""; host.classList.add("chart");
    const W = host.clientWidth || 800, H = spec.height || 280, padL = 8, padR = 58, padT = 10, axisH = 22, ph = H - padT - axisH, n = spec.x.length;
    if (!n) return;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    let lo = 0, hi = 0;
    for (let i = 0; i < n; i++) { let p = 0, m = 0; spec.series.forEach((s) => { const v = s.y[i] || 0; if (v >= 0) p += v; else m += v; }); hi = Math.max(hi, p); lo = Math.min(lo, m); }
    if (!(hi > lo)) hi = lo + 1;
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * ph, x = (i) => padL + ((i + 0.5) / n) * (W - padL - padR), bw = Math.max(1, Math.min(spec.maxBar || 56, ((W - padL - padR) / n) * 0.72));
    for (const t of niceTicks(lo, hi, 5)) { el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: css("--line") }, svg); const tx = el("text", { x: W - padR + 6, y: y(t) + 4 }, svg); tx.textContent = fmt(spec.signed ? t : Math.abs(t), spec.decimals ?? 0); }
    const pal = [css("--up"), css("--down"), css("--accent"), "#f59e0b"];
    for (let i = 0; i < n; i++) { let p = 0, m = 0; spec.series.forEach((s, k) => { const v = s.y[i]; if (!v) return; const col = s.color || pal[k % pal.length];
      if (v >= 0) { el("rect", { x: x(i) - bw / 2, y: y(p + v), width: bw, height: Math.max(0.5, y(p) - y(p + v)), fill: col, opacity: 0.85 }, svg); p += v; }
      else { el("rect", { x: x(i) - bw / 2, y: y(m), width: bw, height: Math.max(0.5, y(m + v) - y(m)), fill: col, opacity: 0.85 }, svg); m += v; } }); }
    if (spec.line) { const L = spec.line.y, l2 = Math.min(...L.filter((v) => v != null)), h2 = Math.max(...L.filter((v) => v != null)); const yl = (v) => padT + (1 - (v - l2) / ((h2 - l2) || 1)) * ph;
      el("polyline", { points: L.map((v, i) => v == null ? null : `${x(i)},${yl(v)}`).filter(Boolean).join(" "), fill: "none", stroke: css("--accent"), "stroke-width": 1.4 }, svg);
      if (n <= 12) L.forEach((v, i) => { if (v == null) return; el("circle", { cx: x(i), cy: yl(v), r: 3, fill: css("--accent") }, svg);
        const t = el("text", { x: x(i) + bw / 2 + 4, y: yl(v) + 4, "font-weight": 600 }, svg); t.textContent = `${v > 0 ? "+" : ""}${fmt(v, 2)}${spec.line.suffix ?? "%"}`; }); }
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) { const tx = el("text", { x: x(i), y: H - 5, "text-anchor": "middle" }, svg); tx.textContent = xlabel(spec.x[i], spanDays(spec.x)); }
    hover(host, svg, W, H, n, x, (i) => `${xlabel(spec.x[i], 0)} · ` + spec.series.map((s) => `${s.name} <b>${fmt(Math.abs(s.y[i] || 0), 0)}</b>`).join(" · ") + (spec.line ? ` · ${spec.line.name} <b>${fmt(spec.line.y[i], 2)}</b>` : ""), padT, padT + ph);
    legend(host, spec.series.concat(spec.line ? [{ name: spec.line.name, color: css("--accent") }] : []), pal);
  }

  function hbar(host, spec) {
    host.innerHTML = ""; host.classList.add("chart");
    const n = spec.labels.length, row = 24, W = host.clientWidth || 700, labW = Math.min(240, W * 0.36), H = n * row + 8, padR = 60;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    const vals = spec.values, mx = Math.max(...vals.map((v) => Math.abs(v || 0)), 1e-9), neg = vals.some((v) => v < 0);
    const negL = neg ? 52 : 0, x0 = neg ? labW + negL + (W - labW - negL - padR) / 2 : labW, sc = (neg ? (W - labW - negL - padR) / 2 : W - labW - padR) / mx, sfx = spec.suffix ?? "%";
    vals.forEach((v, i) => { const yy = 4 + i * row, col = v >= 0 ? css("--up") : css("--down");
      const t = el("text", { x: labW - 8, y: yy + 15, "text-anchor": "end" }, svg); t.textContent = String(spec.labels[i]).slice(0, 34);
      el("rect", { x: v >= 0 ? x0 : x0 + v * sc, y: yy + 3, width: Math.max(1, Math.abs(v) * sc), height: row - 8, rx: 3, fill: spec.color || col, opacity: 0.8 }, svg);
      const tv = el("text", { x: v >= 0 ? x0 + v * sc + 5 : x0 + v * sc - 5, y: yy + 15, "text-anchor": v >= 0 ? "start" : "end" }, svg); tv.textContent = `${v > 0 && neg ? "+" : ""}${fmt(v, spec.decimals ?? 1)}${sfx}`; });
    if (neg) el("line", { x1: x0, x2: x0, y1: 0, y2: H, stroke: css("--faint") }, svg);
  }

  function hover(host, svg, W, H, n, x, text, top, bot) {
    const cross = el("line", { y1: top, y2: bot, stroke: css("--faint"), "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
    const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
    const ov = el("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent" }, svg);
    ov.addEventListener("mousemove", (ev) => { const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      let i = 0, best = Infinity; for (let k = 0; k < n; k++) { const d = Math.abs(x(k) - px); if (d < best) { best = d; i = k; } }
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      tip.innerHTML = text(i); tip.hidden = false; tip.style.left = `${Math.min(r.width - 120, Math.max(120, (x(i) / W) * r.width))}px`; tip.style.top = `${(top / H) * r.height + 4}px`; });
    ov.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
  }
  function legend(host, series, pal) {
    if (series.length < 2) return;
    const d = document.createElement("div"); d.className = "legend";
    d.innerHTML = series.map((s, k) => `<span><i style="background:${s.color || pal[k % pal.length]}"></i>${String(s.name || "").replace(/[<>&]/g, "")}</span>`).join("");
    host.appendChild(d);
  }
  // robust z-score (median / MAD) so the outliers themselves don't hide the outliers
  function robustZ(v) { const s = [...v].sort((a, b) => a - b), med = s[Math.floor(s.length / 2)];
    const mad = [...v.map((x) => Math.abs(x - med))].sort((a, b) => a - b)[Math.floor(v.length / 2)] || 1e-9; return v.map((x) => (x - med) / (1.4826 * mad)); }

  /* scatter: {points:[{x,y,label,sym}], xlabel, ylabel, height}; marks outliers (far from the trend line). Returns the points with .out set. */
  function scatter(host, spec) {
    host.innerHTML = ""; host.classList.add("chart");
    const P = spec.points || []; if (P.length < 3) { host.innerHTML = '<div class="empty">Not enough points.</div>'; return P; }
    const W = host.clientWidth || 700, H = spec.height || 320, padL = 54, padR = 14, padT = 10, padB = 38;
    const xs = P.map((p) => p.x), ys = P.map((p) => p.y), n = P.length;
    const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
    const sxx = xs.reduce((a, x) => a + (x - mx) ** 2, 0), b = sxx ? xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0) / sxx : 0, a0 = my - b * mx;
    const zr = robustZ(P.map((p) => p.y - (a0 + b * p.x))), zx = robustZ(xs);
    P.forEach((p, i) => (p.out = Math.abs(zr[i]) > 3.5 || Math.abs(zx[i]) > 3.5));
    const r = (v) => { let lo = Math.min(...v), hi = Math.max(...v); if (!(hi > lo)) hi = lo + 1; const p = (hi - lo) * 0.06; return [lo - p, hi + p]; };
    const [x0, x1] = r(xs), [y0, y1] = r(ys);
    const X = (v) => padL + ((v - x0) / (x1 - x0)) * (W - padL - padR), Y = (v) => padT + (1 - (v - y0) / (y1 - y0)) * (H - padT - padB);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    const dec = (lo, hi) => Math.abs(hi - lo) < 10 ? 1 : 0;
    for (const t of niceTicks(y0, y1, 5)) { el("line", { x1: padL, x2: W - padR, y1: Y(t), y2: Y(t), stroke: css("--line") }, svg); const tx = el("text", { x: padL - 6, y: Y(t) + 4, "text-anchor": "end" }, svg); tx.textContent = fmt(t, dec(y0, y1)); }
    for (const t of niceTicks(x0, x1, 6)) { const tx = el("text", { x: X(t), y: H - padB + 16, "text-anchor": "middle" }, svg); tx.textContent = fmt(t, dec(x0, x1)); }
    if (y0 < 0 && y1 > 0) el("line", { x1: padL, x2: W - padR, y1: Y(0), y2: Y(0), stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg);
    if (x0 < 0 && x1 > 0) el("line", { x1: X(0), x2: X(0), y1: padT, y2: H - padB, stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg);
    el("line", { x1: X(x0), y1: Y(a0 + b * x0), x2: X(x1), y2: Y(a0 + b * x1), stroke: css("--accent"), "stroke-width": 1, "stroke-dasharray": "5 4", opacity: 0.7 }, svg);
    const lx = el("text", { x: (padL + W - padR) / 2, y: H - 4, "text-anchor": "middle" }, svg); lx.textContent = spec.xlabel || "";
    const ly = el("text", { x: 12, y: padT + (H - padT - padB) / 2, transform: `rotate(-90 12 ${padT + (H - padT - padB) / 2})`, "text-anchor": "middle" }, svg); ly.textContent = spec.ylabel || "";
    const tip = document.createElement("div"); tip.className = "tip"; tip.hidden = true; host.appendChild(tip);
    P.forEach((p) => {
      const c = el("circle", { cx: X(p.x), cy: Y(p.y), r: p.out ? 5 : 3.5, fill: p.out ? css("--down") : css("--accent"), opacity: p.out ? 0.95 : 0.55, stroke: p.out ? css("--surface") : "none" }, svg);
      if (p.out && p.label) { const t = el("text", { x: X(p.x) + 7, y: Y(p.y) + 4, "font-weight": 600 }, svg); t.textContent = String(p.label).slice(0, 14); }
      c.addEventListener("mouseenter", () => { const rr = svg.getBoundingClientRect(); tip.innerHTML = `<b>${String(p.label || "").replace(/[<>&]/g, "")}</b> · ${fmt(p.x, 2)} · ${fmt(p.y, 2)}${p.out ? " · outlier" : ""}`;
        tip.hidden = false; tip.style.left = `${Math.min(rr.width - 120, Math.max(120, (X(p.x) / W) * rr.width))}px`; tip.style.top = `${(Y(p.y) / H) * rr.height - 34}px`; });
      c.addEventListener("mouseleave", () => (tip.hidden = true));
      if (p.sym) { c.style.cursor = "pointer"; c.addEventListener("click", () => (location.hash = `#/stock/${encodeURIComponent(p.sym)}`)); }
    });
    const lg = document.createElement("div"); lg.className = "legend";
    lg.innerHTML = `<span><i style="background:${css("--accent")}"></i>${n} points</span><span><i style="background:${css("--down")}"></i>outliers (far from the trend line)</span><span>dashed line = trend</span>`;
    host.appendChild(lg);
    return P;
  }

  /* histogram: {values, xlabel, bins}; returns {outliers: values outside 1.5×IQR fences} */
  function histogram(host, spec) {
    host.innerHTML = ""; host.classList.add("chart");
    const v = (spec.values || []).filter((x) => isFinite(x)).sort((a, b) => a - b), n = v.length;
    if (n < 3) { host.innerHTML = '<div class="empty">Not enough values.</div>'; return { outliers: [] }; }
    const qn = (p) => v[Math.min(n - 1, Math.floor(p * (n - 1)))], q1 = qn(0.25), q3 = qn(0.75), iqr = q3 - q1, lo = q1 - 1.5 * iqr, hi = q3 + 1.5 * iqr;
    const k = spec.bins || Math.min(40, Math.max(8, Math.round(Math.sqrt(n)))), a = v[0], b = v[n - 1] > a ? v[n - 1] : a + 1, w = (b - a) / k;
    const cnt = new Array(k).fill(0); v.forEach((x) => cnt[Math.min(k - 1, Math.floor((x - a) / w))]++);
    const W = host.clientWidth || 700, H = spec.height || 300, padL = 40, padR = 14, padT = 10, padB = 38, mxc = Math.max(...cnt);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H }); host.appendChild(svg);
    const X = (x) => padL + ((x - a) / (b - a)) * (W - padL - padR), Y = (c) => padT + (1 - c / mxc) * (H - padT - padB);
    for (const t of niceTicks(0, mxc, 4)) { el("line", { x1: padL, x2: W - padR, y1: Y(t), y2: Y(t), stroke: css("--line") }, svg); const tx = el("text", { x: padL - 6, y: Y(t) + 4, "text-anchor": "end" }, svg); tx.textContent = fmt(t, 0); }
    cnt.forEach((c, i) => { const x0 = a + i * w, out = x0 + w < lo || x0 > hi;
      el("rect", { x: X(x0) + 1, y: Y(c), width: Math.max(1, X(x0 + w) - X(x0) - 2), height: Math.max(0, Y(0) - Y(c)), fill: out ? css("--down") : css("--accent"), opacity: 0.75 }, svg); });
    for (const t of niceTicks(a, b, 6)) { const tx = el("text", { x: X(t), y: H - padB + 16, "text-anchor": "middle" }, svg); tx.textContent = fmt(t, Math.abs(b - a) < 10 ? 1 : 0); }
    [[q1, "25%"], [qn(0.5), "median"], [q3, "75%"]].forEach(([x, l]) => { el("line", { x1: X(x), x2: X(x), y1: padT, y2: H - padB, stroke: css("--faint"), "stroke-dasharray": "3 3" }, svg);
      const t = el("text", { x: X(x) + 3, y: padT + 10 }, svg); t.textContent = l; });
    const lx = el("text", { x: (padL + W - padR) / 2, y: H - 4, "text-anchor": "middle" }, svg); lx.textContent = `${spec.xlabel || ""} · ${n} values`;
    return { outliers: v.filter((x) => x < lo || x > hi) };
  }

  function draw(host, spec) {
    if (spec.type === "scatter") return scatter(host, spec);
    if (spec.type === "hist") return histogram(host, spec);
    if (spec.type === "line") return lineChart(host, spec);
    if (spec.type === "bars") return barsChart(host, spec);
    if (spec.type === "hbar") return hbar(host, spec);
    if (spec.type === "price") return priceChart(host, spec.bars, spec);
    if (spec.type === "barline") return barLine(host, spec.x, spec.bars, spec.line, spec);
  }

  window.MXCharts = { sparkline, priceChart, barLine, lineChart, barsChart, hbar, scatter, histogram, draw };
})();

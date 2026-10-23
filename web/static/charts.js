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
    const W = host.clientWidth || 900, H = opt.height || 380, padL = 8, padR = 58, padT = 10, volH = 64, gap = 10, axisH = 20;
    const priceH = H - volH - gap - axisH - padT;
    const n = bars.t.length;
    if (!n) { host.innerHTML = '<div class="empty">No prices.</div>'; return; }
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H });
    host.appendChild(svg);
    const lows = opt.mode === "candle" ? bars.l : bars.c, highs = opt.mode === "candle" ? bars.h : bars.c;
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < n; i++) { lo = Math.min(lo, lows[i]); hi = Math.max(hi, highs[i]);
      for (const s of [bars.sma50, bars.sma200]) if (s && s[i] != null) { lo = Math.min(lo, s[i]); hi = Math.max(hi, s[i]); } }
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
      const d = new Date(bars.t[i]);
      const tx = el("text", { x: x(i), y: H - 5, "text-anchor": "middle" }, svg);
      tx.textContent = d.toLocaleDateString("en-IN", n > 400 ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" });
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
    sma(bars.sma50, "#f59e0b"); sma(bars.sma200, css("--faint"), "4 3");
    (opt.levels || []).forEach((lv) => el("line", { x1: x(lv.from ?? 0), x2: x(lv.to ?? n - 1), y1: y(lv.price), y2: y(lv.price), stroke: lv.color === "up" ? up : lv.color === "down" ? down : acc, "stroke-dasharray": "4 3", "stroke-width": 1.4 }, svg));
    (opt.marks || []).forEach((m) => { const c0 = m.color === "up" ? up : m.color === "down" ? down : acc, cx = x(m.i), cy = y(m.price), d = m.dir === "down" ? -1 : 1;
      el("polygon", { points: `${cx},${cy + d * 2} ${cx - 6},${cy + d * 12} ${cx + 6},${cy + d * 12}`, fill: c0 }, svg); });
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
      tip.innerHTML = `${new Date(bars.t[i]).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · ` +
        (opt.mode === "candle" ? `O ${fmt(bars.o[i])} H ${fmt(bars.h[i])} L ${fmt(bars.l[i])} ` : "") +
        `C <b>${fmt(bars.c[i])}</b> <span>${ch >= 0 ? "+" : ""}${fmt(ch)}%</span>`;
      tip.hidden = false;
      tip.style.left = `${(x(i) / W) * r.width}px`; tip.style.top = `${(y(bars.c[i]) / H) * r.height}px`;
    });
    overlay.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dotc.setAttribute("visibility", "hidden"); tip.hidden = true; });
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
    for (const t of niceTicks(lo, hi, 5)) { el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: css("--line") }, svg); const tx = el("text", { x: W - padR + 6, y: y(t) + 4 }, svg); tx.textContent = fmt(Math.abs(t), 0); }
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
  function draw(host, spec) {
    if (spec.type === "line") return lineChart(host, spec);
    if (spec.type === "bars") return barsChart(host, spec);
    if (spec.type === "hbar") return hbar(host, spec);
    if (spec.type === "price") return priceChart(host, spec.bars, spec);
    if (spec.type === "barline") return barLine(host, spec.x, spec.bars, spec.line, spec);
  }

  window.MXCharts = { sparkline, priceChart, barLine, lineChart, barsChart, hbar, draw };
})();

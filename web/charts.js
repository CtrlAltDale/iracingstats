/* Inline-SVG chart builders. No dependencies.
 *
 * Follows the data-viz house rules: 2px lines, >=8px markers with a 2px surface
 * ring, 4px rounded data-ends on bars anchored to the baseline, a 2px gap
 * between adjacent bars, recessive grid/axes, selective direct labels (never a
 * number on every mark), and a hover layer on every plotted form.
 */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const el = (n, a) => {
    const e = document.createElementNS(NS, n);
    for (const k in (a || {})) e.setAttribute(k, a[k]);
    return e;
  };

  // ---- shared tooltip -----------------------------------------------------
  let tip;
  function showTip(html, x, y) {
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'tooltip';
      document.body.appendChild(tip);
    }
    tip.innerHTML = html;
    tip.style.display = 'block';
    // Flip before the pointer near the right/bottom edge so it never clips.
    const r = tip.getBoundingClientRect();
    const left = x + 14 + r.width > innerWidth ? x - r.width - 14 : x + 14;
    const top = y + 14 + r.height > innerHeight ? y - r.height - 14 : y + 14;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { if (tip) tip.style.display = 'none'; }

  function niceTicks(min, max, count) {
    if (min === max) { min = Math.min(0, min); max = max || 1; }
    const span = max - min;
    const raw = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag)
      .find(s => s >= raw) || 10 * mag;
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const out = [];
    for (let v = lo; v <= hi + step / 1e6; v += step) out.push(+v.toFixed(10));
    return out;
  }

  /* Line chart. opts:
   *   data[], x(d)->label, y(d)->number, tooltip(d)->html
   *   yFormat, color (css var name), rule {value,label}, labelEvery
   */
  function lineChart(host, opts) {
    host.innerHTML = '';
    const data = opts.data.filter(d => opts.y(d) != null);
    if (!data.length) { host.innerHTML = '<p class="empty">No data</p>'; return; }

    // Measure the host so the viewBox maps 1:1 to CSS pixels -- a fixed
    // viewBox scaled to a full-width card makes the chart absurdly tall.
    const W = Math.max(320, host.clientWidth || 720), H = opts.height || 220;
    const M = { t: 14, r: 16, b: 28, l: 44 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;

    const ys = data.map(opts.y);
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (opts.rule) { lo = Math.min(lo, opts.rule.value); hi = Math.max(hi, opts.rule.value); }
    const ticks = niceTicks(Math.min(lo, opts.baseZero ? 0 : lo), hi, 4);
    const yMin = ticks[0], yMax = ticks[ticks.length - 1];

    const px = i => M.l + (data.length === 1 ? iw / 2 : (i / (data.length - 1)) * iw);
    const py = v => M.t + ih - ((v - yMin) / (yMax - yMin || 1)) * ih;

    const svg = el('svg', {
      class: 'chart', viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: 'xMidYMid meet', role: 'img'
    });

    ticks.forEach(t => {
      svg.appendChild(el('line', {
        class: 'grid-line', x1: M.l, x2: W - M.r, y1: py(t), y2: py(t)
      }));
      const lab = el('text', { x: M.l - 8, y: py(t) + 4, 'text-anchor': 'end' });
      lab.textContent = opts.yFormat ? opts.yFormat(t) : t;
      svg.appendChild(lab);
    });

    // x labels: thinned, and the forced last label evicts its neighbour if
    // they would sit on top of each other (e.g. 08-14 / 08-15).
    const every = Math.max(1, Math.ceil(data.length / 8));
    const idx = [];
    for (let i = 0; i < data.length; i += every) idx.push(i);
    const last = data.length - 1;
    if (idx[idx.length - 1] !== last) {
      if (last - idx[idx.length - 1] < every / 2) idx.pop();
      idx.push(last);
    }
    idx.forEach(i => {
      const t = el('text', { x: px(i), y: H - 8, 'text-anchor': 'middle' });
      t.textContent = opts.x(data[i]);
      svg.appendChild(t);
    });

    svg.appendChild(el('line', {
      class: 'axis-line', x1: M.l, x2: W - M.r, y1: M.t + ih, y2: M.t + ih
    }));

    if (opts.rule) {
      svg.appendChild(el('line', {
        x1: M.l, x2: W - M.r, y1: py(opts.rule.value), y2: py(opts.rule.value),
        stroke: 'var(--muted)', 'stroke-width': 1, 'stroke-dasharray': '4 4'
      }));
      const rl = el('text', {
        x: W - M.r, y: py(opts.rule.value) - 6, 'text-anchor': 'end'
      });
      rl.textContent = opts.rule.label;
      svg.appendChild(rl);
    }

    const color = opts.color || 'var(--series-1)';
    const dAttr = data.map((d, i) => `${i ? 'L' : 'M'}${px(i)},${py(opts.y(d))}`).join(' ');
    svg.appendChild(el('path', { class: 'series-line', d: dAttr, stroke: color }));

    const cross = el('line', { class: 'crosshair', y1: M.t, y2: M.t + ih, opacity: 0 });
    svg.appendChild(cross);

    data.forEach((d, i) => {
      svg.appendChild(el('circle', {
        class: 'marker', cx: px(i), cy: py(opts.y(d)), r: 4, fill: color
      }));
    });

    // Label only the endpoints — never a number on every point.
    [[0, 'start'], [data.length - 1, 'end']].forEach(([i, anchor]) => {
      if (data.length < 2) return;
      const t = el('text', {
        class: 'value-label', x: px(i) + (anchor === 'start' ? 6 : -6),
        y: py(opts.y(data[i])) - 10, 'text-anchor': anchor === 'start' ? 'start' : 'end'
      });
      t.textContent = opts.yFormat ? opts.yFormat(opts.y(data[i])) : opts.y(data[i]);
      svg.appendChild(t);
    });

    const hit = el('rect', { class: 'hit', x: M.l, y: M.t, width: iw, height: ih });
    svg.appendChild(hit);
    const focus = el('circle', {
      cx: 0, cy: 0, r: 6, fill: color, stroke: 'var(--surface-1)',
      'stroke-width': 2, opacity: 0, 'pointer-events': 'none'
    });
    svg.appendChild(focus);

    hit.addEventListener('mousemove', ev => {
      const box = svg.getBoundingClientRect();
      const rel = (ev.clientX - box.left) / box.width * W;
      let best = 0, bd = Infinity;
      data.forEach((d, i) => { const dd = Math.abs(px(i) - rel); if (dd < bd) { bd = dd; best = i; } });
      const d = data[best];
      cross.setAttribute('x1', px(best)); cross.setAttribute('x2', px(best));
      cross.setAttribute('opacity', 1);
      focus.setAttribute('cx', px(best)); focus.setAttribute('cy', py(opts.y(d)));
      focus.setAttribute('opacity', 1);
      showTip(opts.tooltip(d), ev.clientX, ev.clientY);
    });
    hit.addEventListener('mouseleave', () => {
      cross.setAttribute('opacity', 0); focus.setAttribute('opacity', 0); hideTip();
    });

    host.appendChild(svg);
  }

  /* Bar chart (vertical). opts: data[], x(d), y(d), tooltip(d), color, yFormat */
  function barChart(host, opts) {
    host.innerHTML = '';
    const data = opts.data;
    if (!data.length) { host.innerHTML = '<p class="empty">No data</p>'; return; }

    const W = Math.max(320, host.clientWidth || 720), H = opts.height || 200;
    const M = { t: 14, r: 16, b: 28, l: 44 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;

    const ticks = niceTicks(0, Math.max(...data.map(opts.y)), 4);
    const yMax = ticks[ticks.length - 1];
    const slot = iw / data.length;
    const GAP = 2;                       // 2px surface gap between adjacent bars
    const bw = Math.max(1, slot - GAP);
    const py = v => M.t + ih - (v / (yMax || 1)) * ih;

    const svg = el('svg', {
      class: 'chart', viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: 'xMidYMid meet', role: 'img'
    });

    ticks.forEach(t => {
      svg.appendChild(el('line', { class: 'grid-line', x1: M.l, x2: W - M.r, y1: py(t), y2: py(t) }));
      const lab = el('text', { x: M.l - 8, y: py(t) + 4, 'text-anchor': 'end' });
      lab.textContent = opts.yFormat ? opts.yFormat(t) : t;
      svg.appendChild(lab);
    });

    const every = Math.max(1, Math.ceil(data.length / 14));
    data.forEach((d, i) => {
      if (i % every) return;
      const t = el('text', { x: M.l + i * slot + slot / 2, y: H - 8, 'text-anchor': 'middle' });
      t.textContent = opts.x(d);
      svg.appendChild(t);
    });

    svg.appendChild(el('line', { class: 'axis-line', x1: M.l, x2: W - M.r, y1: M.t + ih, y2: M.t + ih }));

    const color = opts.color || 'var(--series-1)';
    data.forEach((d, i) => {
      const v = opts.y(d), h = Math.max(1, M.t + ih - py(v));
      const g = el('g');
      const bar = el('rect', {
        class: 'bar', x: M.l + i * slot + GAP / 2, y: py(v),
        width: bw, height: h, fill: opts.colorFor ? opts.colorFor(d) : color
      });
      g.appendChild(bar);
      // hit target spans the full slot height so hovering is forgiving
      const hit = el('rect', {
        class: 'hit', x: M.l + i * slot, y: M.t, width: slot, height: ih
      });
      hit.addEventListener('mousemove', ev => {
        bar.setAttribute('opacity', 0.75);
        showTip(opts.tooltip(d), ev.clientX, ev.clientY);
      });
      hit.addEventListener('mouseleave', () => { bar.setAttribute('opacity', 1); hideTip(); });
      g.appendChild(hit);
      svg.appendChild(g);
    });

    host.appendChild(svg);
  }

  /* Diverging bar chart: values that go both ways about a zero baseline.
   *
   * A gain and a loss are opposite in kind, not just in size, so this is a
   * diverging encoding -- two hues either side of a neutral zero line, never a
   * single ramp. Every bar is labelled with its value: the sign is then carried
   * by the number as well as the colour, which is what keeps it readable for a
   * colourblind reader and is required anyway because the positive hue sits
   * below 3:1 against the light surface.
   *
   * opts: data[], x(d), y(d), tooltip(d), yFormat, posColor, negColor, height
   */
  function divergingBarChart(host, opts) {
    host.innerHTML = '';
    const data = opts.data;
    if (!data.length) { host.innerHTML = '<p class="empty">No data</p>'; return; }

    const W = Math.max(320, host.clientWidth || 720), H = opts.height || 230;
    const M = { t: 20, r: 16, b: 40, l: 48 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;

    const vals = data.map(opts.y);
    const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
    const lo = ticks[0], hi = ticks[ticks.length - 1];
    const py = v => M.t + ih - ((v - lo) / ((hi - lo) || 1)) * ih;
    const zero = py(0);

    const slot = iw / data.length;
    const GAP = 2;
    const bw = Math.max(1, slot - GAP);

    const svg = el('svg', {
      class: 'chart', viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: 'xMidYMid meet', role: 'img'
    });

    ticks.forEach(t => {
      svg.appendChild(el('line', { class: 'grid-line', x1: M.l, x2: W - M.r, y1: py(t), y2: py(t) }));
      const lab = el('text', { x: M.l - 8, y: py(t) + 4, 'text-anchor': 'end' });
      lab.textContent = opts.yFormat ? opts.yFormat(t) : t;
      svg.appendChild(lab);
    });

    const pos = opts.posColor || 'var(--series-3)';
    const neg = opts.negColor || 'var(--series-2)';

    data.forEach((d, i) => {
      const v = opts.y(d);
      const top = v >= 0 ? py(v) : zero;
      const h = Math.max(1, Math.abs(zero - py(v)));
      const g = el('g');
      const bar = el('rect', {
        class: 'bar', x: M.l + i * slot + GAP / 2, y: top,
        width: bw, height: h, fill: v >= 0 ? pos : neg
      });
      g.appendChild(bar);

      // Direct label, outside the bar so it never sits on the fill.
      const lab = el('text', {
        class: 'bar-value', x: M.l + i * slot + slot / 2,
        y: v >= 0 ? top - 6 : top + h + 14, 'text-anchor': 'middle'
      });
      lab.textContent = opts.yFormat ? opts.yFormat(v) : v;
      g.appendChild(lab);

      const cat = el('text', {
        x: M.l + i * slot + slot / 2, y: H - 10, 'text-anchor': 'middle'
      });
      cat.textContent = opts.x(d);
      g.appendChild(cat);

      const hit = el('rect', { class: 'hit', x: M.l + i * slot, y: M.t, width: slot, height: ih });
      hit.addEventListener('mousemove', ev => {
        bar.setAttribute('opacity', 0.75);
        showTip(opts.tooltip(d), ev.clientX, ev.clientY);
      });
      hit.addEventListener('mouseleave', () => { bar.setAttribute('opacity', 1); hideTip(); });
      g.appendChild(hit);
      svg.appendChild(g);
    });

    // Zero baseline drawn last so it reads above the fills.
    svg.appendChild(el('line', { class: 'axis-line', x1: M.l, x2: W - M.r, y1: zero, y2: zero }));

    host.appendChild(svg);
  }

  global.Charts = { lineChart, barChart, divergingBarChart, hideTip };
})(window);

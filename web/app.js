/* iRacingStats browser UI. Vanilla JS, no build step. */
(function () {
  'use strict';

  const BREAK_EVEN = 0.20;   // empirical, from observed SR movements
  const $ = s => document.querySelector(s);
  const fmt1 = v => v == null ? '–' : (+v).toFixed(1);
  const fmt3 = v => v == null ? '–' : (+v).toFixed(3);
  const int = v => v == null ? '–' : (+v).toLocaleString();
  let DATA = null;

  /* Incident rate is shown as colour PLUS the number — never colour alone. */
  function rateCell(v) {
    if (v == null) return '–';
    const c = v < BREAK_EVEN ? 'var(--good)'
      : v < 0.35 ? 'var(--warning)' : 'var(--critical)';
    return `<span class="rate"><span class="dot" style="background:${c}"></span>${fmt3(v)}</span>`;
  }

  /* iRacing writes 'N/A' for tracks with no named config — not a real value. */
  const cfg = v => (!v || v === 'N/A') ? '' : v;

  /* 92.665 -> 1:32.665 — lap times read as m:ss, never raw seconds. */
  function lap(v) {
    if (v == null) return '–';
    const m = Math.floor(v / 60), s = v - m * 60;
    return m ? `${m}:${s.toFixed(3).padStart(6, '0')}` : s.toFixed(3);
  }
  /* Gap to the class benchmark: colour plus the signed number, never colour alone. */
  function gapCell(r) {
    if (r.gap == null) return '–';
    const c = r.gap_pct < 0.5 ? 'var(--good)'
      : r.gap_pct < 2 ? 'var(--warning)' : 'var(--critical)';
    const sign = r.gap >= 0 ? '+' : '';
    return `<span class="rate"><span class="dot" style="background:${c}"></span>`
      + `${sign}${r.gap.toFixed(3)}s <span class="tag">${sign}${r.gap_pct.toFixed(1)}%</span></span>`;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // ---- generic sortable table --------------------------------------------
  function table(el, cols, rows, opts) {
    opts = opts || {};
    const state = el._sort || (el._sort = { key: opts.sortKey, dir: opts.sortDir || -1 });

    const sorted = rows.slice();
    if (state.key) {
      const col = cols.find(c => c.key === state.key);
      sorted.sort((a, b) => {
        let x = col.sortVal ? col.sortVal(a) : a[col.key];
        let y = col.sortVal ? col.sortVal(b) : b[col.key];
        if (x == null) return 1;
        if (y == null) return -1;
        if (typeof x === 'string') return state.dir * x.localeCompare(y);
        return state.dir * (x - y);
      });
    }

    const head = cols.map(c => {
      const on = state.key === c.key;
      const arrow = on ? `<span class="arrow">${state.dir < 0 ? '▼' : '▲'}</span>` : '';
      return `<th class="${c.num ? 'num' : ''}" data-key="${c.key}"${on ? ' aria-sort="' + (state.dir < 0 ? 'descending' : 'ascending') + '"' : ''}>${esc(c.label)} ${arrow}</th>`;
    }).join('');

    const body = sorted.map(r => {
      const tds = cols.map(c =>
        `<td class="${c.num ? 'num' : ''}">${c.html ? c.html(r) : esc(c.fmt ? c.fmt(r[c.key]) : r[c.key])}</td>`
      ).join('');
      return `<tr class="${opts.onRow ? 'clickable' : ''}" data-id="${r[opts.idKey] != null ? r[opts.idKey] : ''}">${tds}</tr>`;
    }).join('');

    el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;

    el.querySelectorAll('th').forEach(th => th.onclick = () => {
      const k = th.dataset.key;
      if (state.key === k) state.dir *= -1; else { state.key = k; state.dir = -1; }
      table(el, cols, rows, opts);
    });
    if (opts.onRow) {
      el.querySelectorAll('tbody tr').forEach(tr =>
        tr.onclick = () => opts.onRow(tr.dataset.id));
    }
  }

  /* One small chart per discipline.
   *
   * iRacing keeps a SEPARATE rating for each discipline, so a single line
   * across all of them is not a summary -- it is two unrelated scales drawn as
   * though they were one series, jumping every time the category changes.
   * Small multiples, each with its own axis, is the honest form: the ranges
   * genuinely differ (sports car 566-1713 against formula 1164-1479 here).
   */
  function perDiscipline(host, rows, valueOf, opts) {
    host.innerHTML = '';
    const by = new Map();
    rows.forEach(d => {
      if (valueOf(d) == null) return;
      const k = d.category || 'Other';
      (by.get(k) || by.set(k, []).get(k)).push(d);
    });
    const groups = [...by.entries()].sort((a, b) => b[1].length - a[1].length);
    if (!groups.length) return false;

    groups.forEach(([cat, ds]) => {
      const wrap = document.createElement('div');
      wrap.className = 'mini';
      const first = valueOf(ds[0]), last = valueOf(ds[ds.length - 1]);
      const delta = last - first;
      wrap.innerHTML = `<h3>${esc(cat)}</h3>
        <p class="note">${ds.length} race${ds.length === 1 ? '' : 's'} ·
          ${opts.fmt(first)} → ${opts.fmt(last)}
          (${delta >= 0 ? '+' : ''}${opts.fmt(delta)})</p>`;
      const box = document.createElement('div');
      wrap.appendChild(box);
      host.appendChild(wrap);
      // Under three points there is no shape to read; the line above says it.
      if (ds.length < 3) {
        box.innerHTML = '<p class="empty">Too few races to plot.</p>';
        return;
      }
      Charts.lineChart(box, {
        data: ds, x: d => d.day.slice(5), y: valueOf, height: 190,
        color: opts.color, yFormat: opts.axisFmt || opts.fmt,
        tooltip: opts.tooltip
      });
    });
    return true;
  }

  // ---- overview ----------------------------------------------------------
  function renderOverview() {
    const s = DATA.summary, p = DATA.profile;
    $('#who').textContent = p.name || 'iRacingStats';
    // name comes from the capture layer and first/last race from the export;
    // an export-only database has the latter but not the former, and a brand
    // new one may have neither.
    $('#span').textContent = p.first_race && p.last_race
      ? `${p.first_race.slice(0, 10)} – ${p.last_race.slice(0, 10)} · cust ${p.cust_id}`
      : `cust ${p.cust_id}`;

    const tiles = [
      ['Race starts', int(s.starts), `${int(s.sessions_total)} sessions total`],
      ['Wins', int(s.wins), `${int(s.podiums)} podiums · ${int(s.top5)} top 5`],
      ['Laps', int(s.laps), `${int(s.laps_led)} led`],
      ['Avg finish', fmt1(s.avg_finish), `avg SoF ${int(s.avg_sof)}`],
      ['Incidents / lap', fmt3(s.inc_per_lap), `${int(s.incidents)} total`],
      ['Series raced', int(s.series_count), `${int(DATA.tracks.length)} tracks`],
      s.has_capture_layer
        ? ['Telemetry', int(s.telemetry_races), `of ${int(s.starts)} races`]
        : ['Podium rate', s.starts ? (s.podiums / s.starts * 100).toFixed(0) + '%' : '–',
           `${int(s.wins)} wins from ${int(s.starts)}`],
      s.has_capture_layer
        ? ['Drivers met', int(s.rivals), 'on captured grids']
        : ['Laps led', int(s.laps_led),
           s.laps ? (s.laps_led / s.laps * 100).toFixed(1) + '% of laps run' : '–']
    ];
    $('#tiles').innerHTML = tiles.map(([l, v, f]) =>
      `<div class="tile"><div class="label">${esc(l)}</div>
       <div class="value">${esc(v)}</div><div class="foot">${esc(f)}</div></div>`).join('');

    Charts.lineChart($('#chart-monthly'), {
      data: DATA.monthly, x: d => d.month.slice(2), y: d => d.ipl,
      yFormat: v => v.toFixed(2), baseZero: true,
      rule: { value: BREAK_EVEN, label: 'break-even ≈ 0.20' },
      tooltip: d => `<div class="t-title">${d.month}</div>
        <div class="t-row">Incidents/lap <b>${fmt3(d.ipl)}</b></div>
        <div class="t-row">Races <b>${d.races}</b> · Laps <b>${d.laps}</b></div>
        <div class="t-row">Incidents <b>${d.inc}</b></div>`
    });

    // Safety rating, also per discipline -- iRacing tracks a separate licence
    // and SR for each, so one combined line would splice unrelated series.
    const srAfter = (DATA.progress || []).filter(d => d.sr_after != null);
    if (srAfter.length) {
      $('#sr-title').textContent = 'Safety rating';
      $('#sr-note').textContent =
        `After each race, per discipline. ${srAfter.length} races.`;
      $('#chart-sr').hidden = true;
      perDiscipline($('#sr-charts'), srAfter, d => d.sr_after, {
        color: 'var(--series-2)',
        fmt: v => v.toFixed(2),
        tooltip: d => `<div class="t-title">${d.day}</div>
          <div class="t-row">SR <b>${d.sr_before.toFixed(2)}</b> →
            <b>${d.sr_after.toFixed(2)}</b>
            (${d.sr_after - d.sr_before >= 0 ? '+' : ''}${(d.sr_after - d.sr_before).toFixed(2)})</div>`
      });
    } else if (!DATA.sr.length) {
      $('#chart-sr').innerHTML = '<p class="empty">Safety rating over time needs '
        + 'either per-race exports or session telemetry — the Results Archive '
        + 'export does not carry it.</p>';
    } else {
      const sr = DATA.sr.filter(d => d.category === 'SportsCar');
      Charts.lineChart($('#chart-sr'), {
        data: sr.length ? sr : DATA.sr, x: d => d.day.slice(5), y: d => d.sr_high,
        color: 'var(--series-2)', yFormat: v => v.toFixed(1),
        tooltip: d => `<div class="t-title">${d.day}</div>
          <div class="t-row">Licence <b>${esc(d.lic)}</b></div>
          <div class="t-row">${esc(d.category)}</div>`
      });
    }

    Charts.barChart($('#chart-pos'), {
      data: DATA.positions, x: d => d.position, y: d => d.n,
      colorFor: d => d.position === 1 ? 'var(--series-3)' : 'var(--series-1)',
      tooltip: d => `<div class="t-title">P${d.position}</div>
        <div class="t-row">Finishes <b>${d.n}</b></div>
        <div class="t-row"><b>${(d.n / DATA.summary.starts * 100).toFixed(1)}%</b> of starts</div>`
    });

    table($('#t-seasons'), [
      { key: 'season', label: 'Season' },
      { key: 'races', label: 'Races', num: true },
      { key: 'wins', label: 'Wins', num: true },
      { key: 'avg_pos', label: 'Avg pos', num: true, fmt: fmt1 },
      { key: 'laps', label: 'Laps', num: true, fmt: int },
      { key: 'inc', label: 'Inc', num: true, fmt: int },
      { key: 'ipl', label: 'Inc/lap', num: true, html: r => rateCell(r.ipl) }
    ], DATA.seasons, { sortKey: 'season', sortDir: 1 });
  }

  // ---- races -------------------------------------------------------------
  const RACE_COLS = [
    { key: 'day', label: 'Date' },
    {
      key: 'series_name', label: 'Series',
      html: r => `<span class="trunc" title="${esc(r.series_name)}">${esc(r.series_name)}</span>`
    },
    {
      key: 'track_name', label: 'Track',
      html: r => `<span class="trunc" title="${esc(r.track_name)}">${esc(r.track_name)}</span>`
        + (cfg(r.track_config_name) ? ` <span class="tag">${esc(r.track_config_name)}</span>` : '')
    },
    {
      key: 'car_name', label: 'Car',
      html: r => `<span class="trunc" title="${esc(r.car_name)}">${esc(r.car_name)}</span>`
    },
    {
      // iRacing counts CLASS wins, so a multiclass class win can read as e.g.
      // P5 overall. Flag it rather than leaving a green "P5" unexplained.
      key: 'position', label: 'Pos', num: true,
      html: r => {
        const p = `P${r.position}`;
        if (!r.is_win) return p;
        return r.class_position === r.position
          ? `<span class="win">${p}</span>`
          : `<span class="win">${p}</span> <span class="tag">class win</span>`;
      }
    },
    { key: 'num_drivers', label: 'Field', num: true },
    { key: 'start_pos', label: 'Start', num: true },
    { key: 'laps_complete', label: 'Laps', num: true },
    { key: 'incidents', label: 'Inc', num: true },
    { key: 'ipl', label: 'Inc/lap', num: true, html: r => rateCell(r.ipl) },
    { key: 'sof', label: 'SoF', num: true, fmt: int },
    {
      key: 'capture_dir', label: 'Tele', num: true,
      html: r => r.capture_dir ? '<span class="tag">yes</span>' : '',
      sortVal: r => r.capture_dir ? 1 : 0
    }
  ];

  function filteredRaces() {
    const q = $('#f-search').value.trim().toLowerCase();
    const cat = $('#f-cat').value, season = $('#f-season').value;
    const tele = $('#f-tele').checked, win = $('#f-win').checked;
    return DATA.races.filter(r => {
      if (cat && r.license_category !== cat) return false;
      if (season && `${r.season_year} S${r.season_quarter}` !== season) return false;
      if (tele && !r.capture_dir) return false;
      if (win && !r.is_win) return false;
      if (q) {
        const hay = `${r.series_name} ${r.track_name} ${r.car_name} ${cfg(r.track_config_name)}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderRaces() {
    const rows = filteredRaces();
    $('#f-count').textContent =
      `${rows.length} of ${DATA.races.length} races · ${rows.reduce((a, r) => a + (r.laps_complete || 0), 0).toLocaleString()} laps`;
    table($('#t-races'), RACE_COLS, rows,
      { sortKey: 'day', sortDir: -1, idKey: 'subsession_id', onRow: openRace });
  }

  // ---- race drawer -------------------------------------------------------
  async function openRace(id) {
    const back = document.createElement('div');
    back.className = 'drawer-back';
    back.innerHTML = '<div class="drawer"><p class="empty">Loading…</p></div>';
    back.onclick = ev => { if (ev.target === back) close(); };
    document.body.appendChild(back);
    const close = () => { back.remove(); Charts.hideTip(); document.removeEventListener('keydown', onKey); };
    const onKey = e => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);

    let d;
    try {
      d = await (await fetch('/api/race/' + id)).json();
    } catch (e) {
      back.querySelector('.drawer').innerHTML = '<p class="empty">Failed to load</p>';
      return;
    }
    const r = d.race;
    const grid = d.grid.filter(g => !g.car_is_ai);
    const box = back.querySelector('.drawer');
    box.innerHTML = `
      <button class="ghost close">Close</button>
      <h2>${esc(r.series_name)}</h2>
      <div class="sub">${esc(r.track_name)}${cfg(r.track_config_name) ? ' · ' + esc(r.track_config_name) : ''}
        · ${esc(r.start_time.slice(0, 16).replace('T', ' '))} UTC
        · ${esc(r.season_year)} S${esc(r.season_quarter)} week ${esc(r.race_week_num)}</div>
      <div class="tiles">
        <div class="tile"><div class="label">Finish</div><div class="value">P${r.position}</div>
          <div class="foot">from P${r.starting_position + 1} of ${r.num_drivers}</div></div>
        <div class="tile"><div class="label">Laps</div><div class="value">${r.laps_complete}</div>
          <div class="foot">of ${r.event_laps_complete} · ${r.laps_led} led</div></div>
        <div class="tile"><div class="label">Incidents</div><div class="value">${r.incidents}</div>
          <div class="foot">${fmt3(r.laps_complete ? r.incidents / r.laps_complete : null)} per lap</div></div>
        <div class="tile"><div class="label">SoF</div><div class="value">${int(r.event_strength_of_field)}</div>
          <div class="foot">${esc(r.car_name)}</div></div>
      </div>
      <div class="card">
        <h2>Result</h2>
        <p class="note">${d.capture
          ? 'Full grid reconstructed from the captured <code>session.yaml</code>. iRating shown is each driver\'s value at session start.'
          : 'No telemetry capture for this race — only your own result line is stored.'}</p>
        <div class="table-wrap"><table id="t-grid"></table></div>
      </div>`;
    box.querySelector('.close').onclick = close;

    if (grid.length) {
      table(box.querySelector('#t-grid'), [
        { key: 'position', label: 'Pos', num: true },
        { key: 'user_name', label: 'Driver' },
        { key: 'car_number', label: '#', num: true },
        { key: 'car_screen_name', label: 'Car' },
        { key: 'irating', label: 'iR', num: true, fmt: int },
        { key: 'lic_string', label: 'Lic' },
        { key: 'laps_complete', label: 'Laps', num: true },
        { key: 'laps_led', label: 'Led', num: true },
        { key: 'fastest_time', label: 'Best', num: true, fmt: v => v == null ? '–' : (+v).toFixed(3) },
        { key: 'incidents', label: 'Inc', num: true },
        { key: 'reason_out_str', label: 'Out' }
      ], grid, { sortKey: 'position', sortDir: 1 });
      box.querySelectorAll('#t-grid tbody tr').forEach((tr, i) => {
        const g = grid.slice().sort((a, b) => a.position - b.position)[i];
        if (g && g.cust_id === DATA.profile.cust_id) tr.classList.add('me');
      });
    } else {
      box.querySelector('#t-grid').outerHTML =
        '<p class="empty">No captured grid for this race.</p>';
    }
  }

  // ---- aggregate tabs ----------------------------------------------------
  const AGG_COLS = label => [
    { key: 'name', label },
    { key: 'races', label: 'Races', num: true },
    { key: 'wins', label: 'Wins', num: true },
    { key: 'avg_pos', label: 'Avg pos', num: true, fmt: fmt1 },
    { key: 'laps', label: 'Laps', num: true, fmt: int },
    { key: 'inc', label: 'Inc', num: true, fmt: int },
    { key: 'ipl', label: 'Inc/lap', num: true, html: r => rateCell(r.ipl) }
  ];

  const PACE_COLS = [
    {
      key: 'track', label: 'Track',
      html: r => `<span class="trunc" title="${esc(r.track)}">${esc(r.track)}</span>`
        + (cfg(r.config) ? ` <span class="tag">${esc(r.config)}</span>` : '')
    },
    {
      key: 'car', label: 'Car',
      html: r => `<span class="trunc" title="${esc(r.car)}">${esc(r.car)}</span>`
    },
    { key: 'car_class', label: 'Class', html: r => r.car_class ? esc(r.car_class) : '–' },
    { key: 'pb', label: 'Your best', num: true, html: r => lap(r.pb) },
    { key: 'class_best', label: 'Class best', num: true, html: r => lap(r.class_best) },
    { key: 'gap_pct', label: 'Gap to class', num: true, html: gapCell },
    {
      key: 'field_best', label: 'Field best', num: true,
      html: r => r.field_best
        ? lap(r.field_best) + (r.num_classes > 1 ? ' <span class="tag">multi</span>' : '')
        : '–'
    },
    {
      // Laps behind the PB: a 1-lap sample is an out-lap, not real pace, and a
      // huge gap next to laps=1 explains itself.
      key: 'laps', label: 'Laps', num: true,
      html: r => r.laps == null ? '–'
        : (r.laps <= 2 ? `<span class="tag" title="too few laps to be representative">${r.laps}</span>` : r.laps)
    },
    { key: 'tele_sessions', label: 'Sessions', num: true, html: r => r.tele_sessions || '–' },
    { key: 'races', label: 'Races', num: true, html: r => r.races || '–' },
    { key: 'last_raced', label: 'Last raced', html: r => r.last_raced || r.pb_last || '–' }
  ];

  function filteredPace() {
    const q = $('#p-search').value.trim().toLowerCase();
    const tr = $('#p-track').value, ca = $('#p-car').value, only = $('#p-pb').checked;
    return DATA.pace.filter(r => {
      if (only && r.pb == null) return false;
      if (tr && r.track !== tr) return false;
      if (ca && r.car !== ca) return false;
      if (q && !`${r.track} ${r.car} ${cfg(r.config)}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  function renderPace() {
    const rows = filteredPace();
    const withPb = rows.filter(r => r.pb != null).length;
    $('#p-count').textContent = withPb
      ? `${rows.length} track+car combos · ${withPb} with a lap of yours`
      : `${rows.length} track+car combos · field best only — your own lap `
        + `times are not in the Results Archive export`;
    table($('#t-pace'), PACE_COLS, rows, { sortKey: 'gap_pct', sortDir: 1 });
  }

  // ---- teams -------------------------------------------------------------
  let TEAMS = null;

  const lapTime = t => t == null ? '–' : (t >= 60
    ? `${Math.floor(t / 60)}:${(t % 60).toFixed(3).padStart(6, '0')}`
    : t.toFixed(3));

  async function renderTeams() {
    const host = $('#teams-body');
    if (!TEAMS) {
      host.innerHTML = '<p class="empty">Working…</p>';
      try { TEAMS = await (await fetch('/api/teams')).json(); }
      catch (e) { host.innerHTML = '<p class="empty">Failed to load</p>'; return; }
    }
    if (!TEAMS.available || !TEAMS.teams.length) {
      host.innerHTML = `<div class="card"><h2>No team entries</h2>
        <p class="note">Team races are read from the capture layer. If you have
        driven for a team, import the per-race exports — a solo start and a team
        entry are told apart by the team id, which the Results Archive export
        does not carry.</p></div>`;
      return;
    }

    host.innerHTML = TEAMS.teams.map(t => {
      const crew = t.crew.map(c => `<tr${c.is_me ? ' class="me"' : ''}>
          <td>${esc(c.name)}</td>
          <td class="num">${int(c.races)}</td>
          <td class="num">${int(c.laps)}</td>
          <td class="num">${c.laps_led ? int(c.laps_led) : '–'}</td>
          <td class="num">${int(c.incidents)}</td>
          <td class="num">${lapTime(c.best)}</td></tr>`).join('');

      const events = t.events.map(e => {
        const rows_ = e.roster.map(d => `<tr${d.is_me ? ' class="me"' : ''}>
            <td>${esc(d.name)}</td>
            <td class="num">${int(d.laps_complete)}</td>
            <td class="num">${d.laps_led ? int(d.laps_led) : '–'}</td>
            <td class="num">${d.incidents == null ? '–' : int(d.incidents)}</td>
            <td class="num">${lapTime(d.fastest_time)}</td>
            <td class="num">${d.ir_after == null ? '–'
              : `${d.ir_before} → ${d.ir_after}`}</td>
            <td>${esc(d.reason_out_str || '')}</td></tr>`).join('');
        const teamLaps = e.roster.reduce((a, d) => a + (d.laps_complete || 0), 0);
        return `<details class="event">
          <summary>
            <span class="ev-day">${esc(e.day)}</span>
            <span class="ev-name">${esc(e.series_name || e.track || 'Race')}</span>
            <span class="ev-meta">${esc(e.track || '')}${e.config &&
              e.config !== e.track ? ' · ' + esc(e.config) : ''}</span>
            <span class="ev-pos">P${int(e.position)}<span class="ev-of"> of ${int(e.field)}</span></span>
          </summary>
          <p class="note">${int(e.roster.length)} driver${e.roster.length === 1 ? '' : 's'} ·
            ${int(teamLaps)} laps between them${e.roster_from === 'telemetry'
              ? ' · crew read from a telemetry capture, which only records whoever was driving, so it may be incomplete'
              : ''}</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Driver</th><th class="num">Laps</th><th class="num">Led</th>
              <th class="num">Inc</th><th class="num">Best lap</th>
              <th class="num">iRating</th><th>Out</th></tr></thead>
            <tbody>${rows_}</tbody></table></div>
        </details>`;
      }).join('');

      return `<div class="card">
        <h2>${esc(t.name)}</h2>
        <p class="note">${int(t.races)} race${t.races === 1 ? '' : 's'} ·
          ${esc(t.first)} to ${esc(t.last)} · ${int(t.laps)} laps between the crew
          ${t.aka.length > 1 ? '· also entered as ' + t.aka.filter(a => a !== t.name).map(esc).join(', ') : ''}</p>
        <div class="table-wrap"><table>
          <thead><tr><th>Crew</th><th class="num">Races</th><th class="num">Laps</th>
            <th class="num">Led</th><th class="num">Inc</th><th class="num">Best lap</th></tr></thead>
          <tbody>${crew}</tbody></table></div>
        <div class="events">${events}</div>
      </div>`;
    }).join('');
  }

  // ---- insights ----------------------------------------------------------
  let INS = null;

  async function renderInsights() {
    const host = $('#insights-body');
    if (!INS) {
      host.innerHTML = '<p class="empty">Working…</p>';
      try { INS = await (await fetch('/api/insights')).json(); }
      catch (e) { host.innerHTML = '<p class="empty">Failed to load</p>'; return; }
    }
    if (!INS.available) {
      host.innerHTML = `<div class="card"><h2>Nothing to analyse yet</h2>
        <p class="note">Everything here compares your rating before and after a
        race. Only the per-race exports carry that — neither the Results Archive
        export nor a telemetry capture records a post-race value. Import some
        with <code>import_event_results.py</code> and this fills in.</p></div>`;
      return;
    }

    const s = INS.summary, st = INS.starts, pace = INS.pace;
    const clean = INS.by_incidents.find(b => b.bucket === '0');
    const messy = INS.by_incidents.find(b => b.bucket === '8-15');

    const tiles = [
      ['Races analysed', int(s.races), `${int(s.gains)} up · ${int(s.losses)} down`],
      ['Average gain', '+' + fmt1(s.avg_gain), `best race ${s.best >= 0 ? '+' : ''}${int(s.best)}`],
      ['Average loss', fmt1(s.avg_loss), `worst race ${int(s.worst)}`],
      ['Peak iRating', int(s.peak), 'highest recorded']
    ];

    host.innerHTML = `
      <div class="tiles">${tiles.map(([l, v, f]) =>
        `<div class="tile"><div class="label">${esc(l)}</div>
         <div class="value">${esc(v)}</div><div class="foot">${esc(f)}</div></div>`).join('')}
      </div>

      <div class="card">
        <h2>What a mistake costs</h2>
        <p class="note">Average iRating change by how many incident points you
          picked up. Green is a gain, orange a loss; every bar carries its own
          number so the sign never depends on the colour.</p>
        <div id="chart-ins-inc"></div>
      </div>

      <div class="grid-2">
        <div class="card">
          <h2>Where you finish</h2>
          <p class="note">By position as a share of the field, so a P10 of 12 and
            a P10 of 40 are not counted as the same result.</p>
          <div id="chart-ins-finish"></div>
        </div>
        <div class="card">
          <h2>Race pace</h2>
          <p class="note" id="ins-pace-note"></p>
          <div id="chart-ins-pace"></div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <h2>Qualifying against racecraft</h2>
          <p class="note">Whether you finish ahead of where you started.</p>
          <div class="tiles">
            <div class="tile"><div class="label">Places made up</div>
              <div class="value">${int(st.gained)}</div><div class="foot">races</div></div>
            <div class="tile"><div class="label">Held station</div>
              <div class="value">${int(st.held)}</div><div class="foot">races</div></div>
            <div class="tile"><div class="label">Places lost</div>
              <div class="value">${int(st.lost)}</div><div class="foot">races</div></div>
            <div class="tile"><div class="label">Net per race</div>
              <div class="value">${st.avg_net >= 0 ? '+' : ''}${fmt1(st.avg_net)}</div>
              <div class="foot">positions</div></div>
          </div>
        </div>
        <div class="card">
          <h2>By discipline</h2>
          <p class="note">Where the rating actually goes.</p>
          <div class="table-wrap"><table id="t-ins-cat"></table></div>
        </div>
      </div>`;

    Charts.divergingBarChart($('#chart-ins-inc'), {
      data: INS.by_incidents, x: d => d.bucket, y: d => d.avg_ir, height: 250,
      yFormat: v => (v > 0 ? '+' : '') + Math.round(v),
      tooltip: d => `<div class="t-title">${esc(d.bucket)} incident${d.bucket === '0' ? 's' : ' pts'}</div>
        <div class="t-row">Average iRating <b>${d.avg_ir >= 0 ? '+' : ''}${fmt1(d.avg_ir)}</b></div>
        <div class="t-row">Average finish <b>P${fmt1(d.avg_finish)}</b></div>
        <div class="t-row">${d.n} race${d.n === 1 ? '' : 's'}</div>`
    });

    Charts.divergingBarChart($('#chart-ins-finish'), {
      data: INS.by_finish, x: d => d.band, y: d => d.avg_ir, height: 230,
      yFormat: v => (v > 0 ? '+' : '') + Math.round(v),
      tooltip: d => `<div class="t-title">${esc(d.band)}</div>
        <div class="t-row">Average iRating <b>${d.avg_ir >= 0 ? '+' : ''}${fmt1(d.avg_ir)}</b></div>
        <div class="t-row">${d.n} race${d.n === 1 ? '' : 's'}</div>`
    });

    if (pace && pace.n) {
      $('#ins-pace-note').textContent =
        `Share of your own class that was faster than your best lap. Median `
        + `${fmt1(pace.median)}% · quickest in class ${pace.fastest_in_class} `
        + `times · inside the top quarter ${pace.top_quartile} times, `
        + `over ${pace.n} races.`;
      Charts.barChart($('#chart-ins-pace'), {
        data: pace.bins, x: d => d.bin, y: d => d.n, height: 230,
        tooltip: d => `<div class="t-title">${d.bin}–${d.bin + 10}% of class faster</div>
          <div class="t-row"><b>${d.n}</b> race${d.n === 1 ? '' : 's'}</div>`
      });
    } else {
      $('#chart-ins-pace').innerHTML = '<p class="empty">No comparable laps.</p>';
    }

    table($('#t-ins-cat'), [
      { key: 'category', label: 'Discipline' },
      { key: 'n', label: 'Races', num: true },
      { key: 'total_ir', label: 'Total iR', num: true,
        html: r => `${r.total_ir >= 0 ? '+' : ''}${int(r.total_ir)}` },
      { key: 'avg_ir', label: 'Per race', num: true,
        html: r => `${r.avg_ir >= 0 ? '+' : ''}${fmt1(r.avg_ir)}` },
      { key: 'avg_finish', label: 'Avg finish', num: true, fmt: fmt1 },
      { key: 'avg_inc', label: 'Avg inc', num: true, fmt: fmt1 }
    ], INS.by_category, { sortKey: 'n' });
  }

  // ---- incidents ---------------------------------------------------------
  let INC = null;

  const TYPE_COLOR = {
    'Off track':     'var(--series-1)',
    'Wall or spin':  'var(--series-2)',
    'Heavy contact': 'var(--critical)'
  };

  async function renderIncidents() {
    const host = $('#inc-body');
    if (!INC) {
      try { INC = await (await fetch('/api/incidents')).json(); }
      catch (e) { host.innerHTML = '<p class="empty">Failed to load</p>'; return; }
    }
    if (!INC.available) {
      host.innerHTML = `<div class="card"><h2>No incident data</h2>
        <p class="note">Where an incident happened — corner, lap, speed, type —
        only exists in telemetry, not in the Results Archive export. This tab
        fills in if you load an <code>incidents</code> table from your own
        telemetry archive; the rest of the site does not need it.</p></div>`;
      return;
    }
    const s = INC.summary;
    const t = k => (INC.by_type.find(x => x.type === k) || {}).n || 0;
    const pct = n => s.n ? (n / s.n * 100).toFixed(0) + '%' : '–';

    host.innerHTML = `
      <div class="tiles">
        <div class="tile"><div class="label">Scoring incidents</div>
          <div class="value">${int(s.n)}</div>
          <div class="foot">${int(s.pts)} points · ${int(s.sessions)} sessions</div></div>
        <div class="tile"><div class="label">Off track (1x)</div>
          <div class="value">${int(t('Off track'))}</div>
          <div class="foot">${pct(t('Off track'))} of incidents</div></div>
        <div class="tile"><div class="label">Wall / spin (2x)</div>
          <div class="value">${int(t('Wall or spin'))}</div>
          <div class="foot">${pct(t('Wall or spin'))} of incidents</div></div>
        <div class="tile"><div class="label">Heavy contact (4x)</div>
          <div class="value">${int(t('Heavy contact'))}</div>
          <div class="foot">${pct(t('Heavy contact'))} of incidents</div></div>
        <div class="tile"><div class="label">On lap 1</div>
          <div class="value">${pct(s.first_lap)}</div>
          <div class="foot">${int(s.first_lap)} incidents</div></div>
        <div class="tile"><div class="label">Avg speed</div>
          <div class="value">${fmt1(s.avg_kmh)}</div>
          <div class="foot">km/h at the moment</div></div>
      </div>

      <div class="grid-2">
        <div class="card">
          <h2>Where on the lap</h2>
          <p class="note">Distance around the lap, in 5% bins. Aggregated across
             every track, so read it as a shape, not a corner map — filter the
             table below by track for a specific circuit.</p>
          <div id="c-pos"></div>
        </div>
        <div class="card">
          <h2>Which lap</h2>
          <p class="note">Lap 1 is the classic danger lap; lap 10 is "10 or later".</p>
          <div id="c-lap"></div>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <h2>Speed at the moment of the incident</h2>
          <p class="note">Low speed usually means a spin or a pit fumble; high
             speed means a genuine moment.</p>
          <div id="c-speed"></div>
        </div>
        <div class="card">
          <h2>By session type</h2>
          <p class="note">Practice incidents count toward safety rating at a
             reduced weight, races at full.</p>
          <div class="table-wrap"><table id="t-inc-sess"></table></div>
        </div>
      </div>

      <div class="card">
        <h2>By track</h2>
        <p class="note">Where your incidents actually happen.</p>
        <div class="table-wrap"><table id="t-inc-track"></table></div>
      </div>

      <div class="card">
        <h2>Most recent incidents</h2>
        <p class="note">Position is % around the lap. Type is inferred from the
           size of the jump in the incident counter — 1x off track, 2x wall or
           spin, 4x heavy contact.</p>
        <div class="table-wrap"><table id="t-inc-recent"></table></div>
      </div>`;

    Charts.barChart($('#c-pos'), {
      data: INC.by_pos, x: d => (d.bin * 5) + '%', y: d => d.n,
      tooltip: d => `<div class="t-title">${d.bin * 5}–${d.bin * 5 + 5}% around the lap</div>
        <div class="t-row">Incidents <b>${d.n}</b></div>
        <div class="t-row">Off track <b>${d.p1}</b> · Wall/spin <b>${d.p2}</b> · Contact <b>${d.p4}</b></div>`
    });

    Charts.barChart($('#c-lap'), {
      data: INC.by_lap, x: d => d.lap_bin === 10 ? '10+' : d.lap_bin, y: d => d.n,
      colorFor: d => d.lap_bin === 1 ? 'var(--critical)' : 'var(--series-1)',
      tooltip: d => `<div class="t-title">Lap ${d.lap_bin === 10 ? '10 or later' : d.lap_bin}</div>
        <div class="t-row">Incidents <b>${d.n}</b></div>`
    });

    Charts.barChart($('#c-speed'), {
      data: INC.by_speed, x: d => d.bucket.slice(2), y: d => d.n,
      color: 'var(--series-3)',
      tooltip: d => `<div class="t-title">${d.bucket.slice(2)} km/h</div>
        <div class="t-row">Incidents <b>${d.n}</b></div>`
    });

    table($('#t-inc-sess'), [
      { key: 'session_type', label: 'Session' },
      { key: 'n', label: 'Incidents', num: true },
      { key: 'pts', label: 'Points', num: true }
    ], INC.by_session_type, { sortKey: 'n' });

    table($('#t-inc-track'), [
      {
        key: 'track_name', label: 'Track',
        html: r => `<span class="trunc" title="${esc(r.track_name)}">${esc(r.track_name)}</span>`
          + (cfg(r.config) ? ` <span class="tag">${esc(r.config)}</span>` : '')
      },
      { key: 'turns', label: 'Turns', num: true, html: r => r.turns || '–' },
      { key: 'sessions', label: 'Sessions', num: true },
      { key: 'n', label: 'Incidents', num: true },
      { key: 'pts', label: 'Points', num: true },
      { key: 'off_track', label: '1x', num: true },
      { key: 'spin', label: '2x', num: true },
      { key: 'contact', label: '4x', num: true },
      { key: 'avg_kmh', label: 'Avg km/h', num: true, fmt: fmt1 }
    ], INC.by_track, { sortKey: 'n' });

    table($('#t-inc-recent'), [
      { key: 'day', label: 'Date' },
      {
        key: 'track_name', label: 'Track',
        html: r => `<span class="trunc" title="${esc(r.track_name)}">${esc(r.track_name)}</span>`
      },
      { key: 'car_name', label: 'Car', html: r => `<span class="trunc">${esc(r.car_name || '')}</span>` },
      { key: 'session_type', label: 'Session', html: r => esc(r.session_type || '–') },
      { key: 'lap', label: 'Lap', num: true },
      { key: 'pct', label: 'Lap %', num: true, fmt: v => v == null ? '–' : v + '%' },
      {
        key: 'points', label: 'Type', num: false,
        html: r => `<span class="dot" style="background:${TYPE_COLOR[r.type] || 'var(--muted)'}"></span> `
          + `${esc(r.type)} <span class="tag">${r.points}x</span>`
      },
      { key: 'kmh', label: 'km/h', num: true, fmt: fmt1 },
      {
        key: 'surface', label: 'Surface', num: false,
        html: r => ({ 0: 'off track', 1: 'pit stall', 2: 'pit approach', 3: 'on track' })[r.surface] || '–'
      }
    ], INC.recent, { sortKey: 'day' });
  }

  function renderRest() {
    table($('#t-series'),
      AGG_COLS('Series').concat([
        { key: 'sof', label: 'SoF', num: true, fmt: int },
        { key: 'last_raced', label: 'Last raced' }
      ]), DATA.series, { sortKey: 'races' });
    table($('#t-tracks'), AGG_COLS('Track'), DATA.tracks, { sortKey: 'races' });
    table($('#t-cars'), AGG_COLS('Car'), DATA.cars, { sortKey: 'races' });
    table($('#t-rivals'), [
      { key: 'name', label: 'Driver' },
      { key: 'sessions', label: 'Shared sessions', num: true },
      { key: 'best_irating', label: 'Best iR seen', num: true, fmt: int },
      { key: 'lic', label: 'Licence' },
      { key: 'club', label: 'Club' },
      { key: 'cust_id', label: 'Cust ID', num: true }
    ], DATA.rivals, { sortKey: 'sessions' });

    // Who else was on the grid is only knowable from a captured session --
    // the Results Archive export is your own result line and nothing else.
    const rv = $('#tab-rivals');
    let note = rv.querySelector('.capture-note');
    if (!DATA.rivals.length && !note) {
      note = document.createElement('p');
      note.className = 'note capture-note';
      note.textContent = 'Who you shared a grid with is not in the Results '
        + 'Archive export — it only exists in per-session telemetry. This tab '
        + 'stays empty without one.';
      rv.prepend(note);
    }
  }

  // ---- shell -------------------------------------------------------------
  function selectTab(name) {
    let found = false;
    document.querySelectorAll('#tabs button').forEach(x => {
      const on = x.dataset.tab === name;
      x.setAttribute('aria-selected', on);
      found = found || on;
    });
    if (!found) return selectTab('overview');
    document.querySelectorAll('main section').forEach(s =>
      s.hidden = s.id !== 'tab-' + name);
    Charts.hideTip();
    if (name === 'incidents') renderIncidents();
    if (name === 'insights') renderInsights();
    if (name === 'teams') renderTeams();
  }

  function initTabs() {
    document.querySelectorAll('#tabs button').forEach(b =>
      b.onclick = () => {
        selectTab(b.dataset.tab);
        history.replaceState(null, '', '#' + b.dataset.tab);
      });
    // Deep-link: #races or ?tab=races
    const q = new URLSearchParams(location.search).get('tab');
    const want = q || location.hash.slice(1);
    if (want) selectTab(want);
  }

  function initTheme() {
    // ?theme=light|dark wins over the stored preference, so a link can pin a
    // look (and headless screenshots can select one).
    const q = new URLSearchParams(location.search).get('theme');
    const saved = q || localStorage.getItem('theme');
    if (saved) document.documentElement.dataset.theme = saved;
    $('#theme').onclick = () => {
      const cur = document.documentElement.dataset.theme
        || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem('theme', next);
      renderOverview();   // re-read CSS vars into the SVGs
    };
  }

  async function main() {
    initTabs();
    initTheme();
    DATA = await (await fetch('/api/bootstrap')).json();

    const cats = [...new Set(DATA.races.map(r => r.license_category).filter(Boolean))].sort();
    $('#f-cat').innerHTML = '<option value="">All categories</option>' +
      cats.map(c => `<option>${esc(c)}</option>`).join('');
    const seasons = [...new Set(DATA.races.map(r => `${r.season_year} S${r.season_quarter}`))].sort().reverse();
    $('#f-season').innerHTML = '<option value="">All seasons</option>' +
      seasons.map(s => `<option>${esc(s)}</option>`).join('');

    ['#f-search', '#f-cat', '#f-season', '#f-tele', '#f-win'].forEach(sel => {
      $(sel).addEventListener('input', renderRaces);
      $(sel).addEventListener('change', renderRaces);
    });

    const ptracks = [...new Set(DATA.pace.map(r => r.track).filter(Boolean))].sort();
    $('#p-track').innerHTML = '<option value="">All tracks</option>' +
      ptracks.map(t => `<option>${esc(t)}</option>`).join('');
    const pcars = [...new Set(DATA.pace.map(r => r.car).filter(Boolean))].sort();
    $('#p-car').innerHTML = '<option value="">All cars</option>' +
      pcars.map(t => `<option>${esc(t)}</option>`).join('');
    ['#p-search', '#p-track', '#p-car', '#p-pb'].forEach(sel => {
      $(sel).addEventListener('input', renderPace);
      $(sel).addEventListener('change', renderPace);
    });

    renderOverview();
    renderRaces();
    renderPace();
    renderRest();

    // ?race=<subsession_id> opens straight to that race — shareable link.
    const wantRace = new URLSearchParams(location.search).get('race');
    if (wantRace) openRace(wantRace);
    // SVGs are sized in CSS pixels, so they must be rebuilt when the width
    // changes; debounced so dragging a window edge stays cheap.
    let rz;
    addEventListener('resize', () => {
      Charts.hideTip();
      clearTimeout(rz);
      rz = setTimeout(renderOverview, 150);
    });
  }

  main();
})();

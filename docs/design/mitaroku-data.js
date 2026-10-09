(function () {
  if (window.MT) return;
  const TODAY = '2026-10-08';
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const P = iso => iso.split('-').map(Number);
  const fmt = iso => { const p = P(iso); return MON[p[1] - 1] + ' ' + p[2] + ', ' + p[0]; };
  const fmtShort = iso => { const p = P(iso); return MON[p[1] - 1] + ' ' + p[2]; };
  const dow = iso => DOW[new Date(iso + 'T12:00:00Z').getUTCDay()];
  const daysTo = iso => Math.round((Date.parse(iso) - Date.parse(TODAY)) / 864e5);
  const tint = (h, dark) => dark ? 'oklch(0.42 0.07 ' + h + ')' : 'oklch(0.8 0.08 ' + h + ')';

  // Rating sources: one field per title ({ source, value }); add a source here to support it everywhere.
  const sources = {
    TMDB: { full: 'TMDB user score', scale: 10 },
    MAL: { full: 'MyAnimeList score', scale: 10 },
    IMDb: { full: 'IMDb rating', scale: 10 },
  };
  const badge = (source, value) => {
    const s = sources[source] || { full: source + ' rating', scale: 10 };
    return { source, value, tip: s.full + ', out of ' + s.scale, label: s.full + ' ' + value + ' out of ' + s.scale };
  };

  const STATUSES = [['watching', 'Watching'], ['plan', 'Plan to watch'], ['completed', 'Completed'], ['dropped', 'Dropped']];
  const STATUS_LABEL = { watching: 'Watching', plan: 'Plan', completed: 'Completed', dropped: 'Dropped' };
  const TYPE_LABEL = { series: 'Series', anime: 'Anime', film: 'Film' };
  const GENRES = ['Drama', 'Mystery', 'Thriller', 'Comedy', 'Sci-Fi', 'Sports', 'Romance', 'Documentary'];
  const COUNTRIES = ['Iceland', 'Norway', 'Sweden', 'Finland', 'Denmark', 'France', 'Belgium', 'Japan', 'South Korea', 'United Kingdom'];
  const DEMONYM = { Iceland: 'Icelandic', Norway: 'Norwegian', Sweden: 'Swedish', Finland: 'Finnish', Denmark: 'Danish', France: 'French', Belgium: 'Belgian', Japan: 'Japanese', 'South Korea': 'Korean', 'United Kingdom': 'British' };
  const YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018];
  const PAGE = { 'salt-signal': 'show', 'starlit-relay': 'anime', 'quiet-meridian': 'movie' };

  const C = [
    ['salt-signal', 'series', 'Salt & Signal', 'Salt og merki', 2025, 225, 'TMDB', '7.9', 'Airing', 8, '48–52 min', ['Drama', 'Mystery', 'Thriller'], 'Iceland', 'A lighthouse keeper vanishes in a storm; his daughter starts hearing that night on the radio.'],
    ['starlit-relay', 'anime', 'Starlit Relay', '星屑リレー', 2022, 300, 'MAL', '8.3', 'Ended', 18, '24 min', ['Sci-Fi', 'Sports', 'Drama'], 'Japan', 'An orbital academy’s failing relay team races across a station that is being shut down.'],
    ['quiet-meridian', 'film', 'The Quiet Meridian', 'Le Méridien silencieux', 2026, 60, 'TMDB', '7.4', 'Released', 1, '1 h 52 min', ['Drama', 'Mystery'], 'France', 'A cartographer re-measures a border that no longer matches any map.'],
    ['harbor-lights', 'series', 'Harbor Lights', 'Havnelys', 2021, 200, 'TMDB', '7.8', 'Ended', 10, '45 min', ['Drama', 'Mystery'], 'Norway', 'A harbor master keeps a ledger of ships that never arrived.'],
    ['fjord-inquiry', 'series', 'The Fjord Inquiry', 'Fjordgranskningen', 2023, 180, 'TMDB', '8.1', 'Ended', 6, '58 min', ['Mystery', 'Thriller'], 'Sweden', 'A ferry accident reopens a thirty-year-old case.'],
    ['north-tide', 'series', 'North of the Tide', 'Nord for tidevannet', 2024, 160, 'TMDB', '7.2', 'Paused', 8, '50 min', ['Drama'], 'Norway', 'Two sisters inherit a salmon farm and a family feud.'],
    ['beacon-point', 'series', 'Beacon Point', '', 2026, 20, 'TMDB', '6.9', 'Airing', 10, '44 min', ['Mystery', 'Drama'], 'United Kingdom', 'A coastguard station picks up a distress call from a ship that sank in 1962.'],
    ['ice-mile', 'series', 'Ice Mile', 'Jäämaili', 2022, 240, 'TMDB', '7.5', 'Ended', 6, '42 min', ['Thriller'], 'Finland', 'A winter road across a frozen lake, and the trucks that stopped coming back.'],
    ['weather-station', 'series', 'Weather Station 9', 'Veðurstöð 9', 2020, 140, 'TMDB', '7.0', 'Ended', 8, '40 min', ['Drama', 'Comedy'], 'Iceland', 'Three meteorologists, one remote island, no relief crew.'],
    ['dead-air', 'series', 'Dead Air', '', 2022, 0, 'TMDB', '7.3', 'Ended', 8, '47 min', ['Mystery', 'Thriller'], 'United Kingdom', 'A late-night radio host starts taking calls from tomorrow.'],
    ['orbit-club', 'anime', 'Orbit Club', '軌道部', 2024, 270, 'MAL', '8.0', 'Airing', 12, '24 min', ['Sci-Fi', 'Comedy'], 'Japan', 'The smallest club at school tries to put a satellite into orbit.'],
    ['comet-baton', 'anime', 'Comet Baton', '彗星バトン', 2021, 320, 'MAL', '7.6', 'Ended', 24, '24 min', ['Sports', 'Drama'], 'Japan', 'A sprinter who lost her speed coaches the team that replaced her.'],
    ['lunar-lap', 'anime', 'Lunar Lap', '月面ラップ', 2025, 250, 'MAL', '7.1', 'Paused', 12, '24 min', ['Sports', 'Sci-Fi'], 'Japan', 'Kart racing on the far side of the Moon, broadcast to nobody.'],
    ['skyline-sprinters', 'anime', 'Skyline Sprinters', 'スカイライン・スプリンターズ', 2026, 10, 'MAL', '7.4', 'Airing', 12, '24 min', ['Sports'], 'Japan', 'Rooftop runners compete for the city’s last open sky.'],
    ['starway', 'anime', 'Starway', '星の道', 2020, 290, 'MAL', '6.8', 'Ended', 13, '23 min', ['Sci-Fi', 'Romance'], 'Japan', 'A tram line that runs between stars, and its last conductor.'],
    ['surveyors-daughter', 'film', 'The Surveyor’s Daughter', 'La Fille de l’arpenteur', 2024, 40, 'TMDB', '7.1', 'Released', 1, '1 h 47 min', ['Drama', 'Mystery'], 'France', 'A daughter retraces her father’s last survey.'],
    ['border-hours', 'film', 'Border Hours', 'Grensuren', 2023, 80, 'TMDB', '6.8', 'Released', 1, '1 h 38 min', ['Mystery'], 'Belgium', 'A customs post where time runs differently on each side.'],
    ['line-arden', 'film', 'A Line Through Arden', 'Une ligne à travers l’Ardenne', 2021, 100, 'TMDB', '7.6', 'Released', 1, '2 h 4 min', ['Drama', 'Mystery'], 'Belgium', 'A walking survey through a forest that refuses to be measured.'],
    ['late-clocks', 'film', 'The Late Clocks', 'Les Horloges en retard', 2025, 30, 'TMDB', '7.3', 'Released', 1, '1 h 41 min', ['Mystery', 'Comedy'], 'France', 'A village where every clock runs four minutes late.'],
    ['map-errors', 'film', 'Map of Small Errors', 'Carte des petites erreurs', 2022, 120, 'TMDB', '6.9', 'Released', 1, '1 h 55 min', ['Drama'], 'France', 'An archivist collects the mistakes on old maps.'],
    ['keepers-wife', 'film', 'The Keeper’s Wife', '', 2019, 210, 'TMDB', '7.0', 'Released', 1, '1 h 49 min', ['Drama'], 'United Kingdom', 'A lighthouse, a winter, and a marriage under strain.'],
    ['night-frequency', 'film', 'Night Frequency', 'Nattfrekvens', 2019, 190, 'TMDB', '6.6', 'Released', 1, '1 h 33 min', ['Thriller', 'Mystery'], 'Denmark', 'A radio amateur hears a crime before it happens.'],
  ];
  const catalog = C.map((r, i) => ({ id: r[0], type: r[1], title: r[2], orig: r[3], year: r[4], hue: r[5], rating: { source: r[6], value: r[7] }, airing: r[8], eps: r[9], runtime: r[10], genres: r[11], country: r[12], desc: r[13], pop: 990 - i * 17 }));

  // [status, watched, upcoming queue of [code, episode title, date|null]]
  const LIB = {
    'starlit-relay': ['watching', 12, [['S2E5', 'Anchor Leg', '2023-11-02'], ['S2E6', 'The Finish Line Is a Horizon', '2023-11-09'], ['OVA 2', 'Sora’s Day Off', '2024-02-14'], ['Movie', 'Starlit Relay: Last Orbit', '2024-12-01'], ['SP 2', 'Behind the Relay', '2023-12-28'], ['SP 3', 'Picture Drama Collection', '2024-05-10']]],
    'salt-signal': ['watching', 6, [['S2E2', 'Second Beacon', '2026-10-08'], ['S2E3', 'Landfall', '2026-10-15']]],
    'fjord-inquiry': ['watching', 3, [['S1E4', 'The Ferry Ledger', '2023-02-16'], ['S1E5', 'Undertow', '2023-02-23'], ['S1E6', 'Slack Water', '2023-03-02']]],
    'beacon-point': ['watching', 4, [['S1E5', 'Ninth Wave', '2026-10-09'], ['S1E6', 'Mayday Relay', '2026-10-16'], ['S1E7', 'Dead Reckoning', '2026-10-23']]],
    'orbit-club': ['watching', 9, [['E10', 'Perigee', '2026-10-11'], ['E11', 'Burn Window', '2026-10-18'], ['E12', 'Apogee', '2026-10-25']]],
    'lunar-lap': ['watching', 12, [['S2E1', 'Season 2', null]]],
    'harbor-lights': ['completed', 10],
    'quiet-meridian': ['plan', 0],
    'weather-station': ['completed', 8],
    'north-tide': ['dropped', 2],
    'ice-mile': ['plan', 0, [['S2E1', 'Thaw', '2026-11-20']]],
    'comet-baton': ['plan', 0],
    'skyline-sprinters': ['plan', 0, [['S1E1', 'First Light', '2026-10-14']]],
    'surveyors-daughter': ['completed', 1],
    'late-clocks': ['completed', 1],
    'line-arden': ['completed', 1],
    'border-hours': ['plan', 0],
    'starway': ['completed', 13],
    'keepers-wife': ['plan', 0],
  };
  const UP_EXTRA = [['quiet-meridian', 'premiere', 'Movie premiere', '', TODAY], ['comet-baton', 'premiere', 'Movie premiere', 'Comet Baton: Final Lap', null]];

  const SEE_ALSO = {
    show: [{ type: 'series', country: 'Iceland' }, { type: 'series', genre: 'Drama', year: 2025 }, { type: 'series', genre: 'Mystery' }, { type: 'series', genre: 'Drama', country: 'Iceland', year: 2025 }, { type: 'series', genre: 'Thriller', country: 'Iceland' }, { type: 'series', year: 2025 }],
    anime: [{ type: 'anime', genre: 'Sci-Fi' }, { type: 'anime', genre: 'Sports', year: 2022 }, { type: 'anime', country: 'Japan' }, { type: 'anime', year: 2022 }, { type: 'anime', genre: 'Drama' }, { type: 'anime', genre: 'Sports' }],
    movie: [{ type: 'film', country: 'France', genre: 'Drama' }, { type: 'film', genre: 'Mystery', year: 2026 }, { type: 'film', country: 'Belgium' }, { type: 'film', country: 'France', year: 2026 }, { type: 'film', genre: 'Drama', year: 2026 }, { type: 'film', country: 'France', genre: 'Mystery' }],
  };

  // Deterministic placeholder pool for Browse and Search
  let seed = 11;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
  const pick = a => a[Math.floor(rnd() * a.length)];
  const WA = ['Harbor', 'Winter', 'Silent', 'Northern', 'Glass', 'Iron', 'Paper', 'Last', 'Hollow', 'Amber', 'Distant', 'Lantern', 'Copper', 'Velvet', 'Broken', 'Midnight', 'Bright', 'Pale', 'Sunken', 'Grey'];
  const WB = ['Coast', 'Ledger', 'Station', 'Hours', 'Shore', 'Orchard', 'Archive', 'Crossing', 'Garden', 'Tide', 'Ridge', 'Season', 'Engine', 'Harvest', 'Republic', 'Weather', 'Line', 'Ferry', 'Market', 'Choir'];
  const used = new Set(catalog.map(t => t.title));
  const name = () => { for (let k = 0; k < 400; k++) { const n = (rnd() < 0.3 ? 'The ' : '') + pick(WA) + ' ' + pick(WB); if (!used.has(n)) { used.add(n); return n; } } return 'Untitled ' + used.size; };
  const pool = catalog.slice();
  let gi = 0;
  const gen = p => {
    const type = p.type || pick(['series', 'anime', 'film']);
    const country = p.country || (type === 'anime' ? 'Japan' : pick(COUNTRIES.filter(c => c !== 'Japan')));
    const g1 = p.genre || pick(type === 'film' ? GENRES.filter(g => g !== 'Sports') : GENRES);
    const g2 = pick(GENRES); const genres = g2 !== g1 && rnd() < 0.5 ? [g1, g2] : [g1];
    const year = p.year || pick(YEARS);
    gi++;
    pool.push({ id: 'g' + gi, type, title: name(), orig: '', year, hue: Math.floor(rnd() * 360), rating: { source: type === 'anime' ? 'MAL' : 'TMDB', value: (5.6 + rnd() * 2.8).toFixed(1) }, airing: type === 'film' ? 'Released' : pick(['Ended', 'Ended', 'Airing', 'Paused']), eps: type === 'film' ? 1 : 6 + Math.floor(rnd() * 18), runtime: type === 'film' ? '1 h ' + (30 + Math.floor(rnd() * 29)) + ' min' : (type === 'anime' ? '24 min' : 40 + Math.floor(rnd() * 20) + ' min'), genres, country, desc: 'Placeholder description for a fictional ' + (type === 'film' ? 'film' : type === 'anime' ? 'anime' : 'series') + ' from ' + country + '.', pop: Math.floor(rnd() * 900) });
  };
  [].concat(SEE_ALSO.show, SEE_ALSO.anime, SEE_ALSO.movie).forEach((p, i) => { const n = 6 + (i * 5) % 9; for (let k = 0; k < n; k++) gen(p); });
  for (let k = 0; k < 40; k++) gen({});
  const byId = {}; pool.forEach(t => { byId[t.id] = t; });
  pool.forEach((t, i) => { if (t.id[0] === 'g' && !LIB[t.id]) { if (i % 9 === 0) LIB[t.id] = ['completed', t.eps]; else if (i % 13 === 0) LIB[t.id] = ['plan', 0]; } });

  const COLL = [
    ['nordic-noir', 'Nordic Noir', 'Cold coasts, slow reveals and very long winters.', ['harbor-lights', 'weather-station', 'ice-mile', 'fjord-inquiry', 'north-tide', 'salt-signal', 'night-frequency']],
    ['lighthouses', 'Lighthouses & Keepers', 'Stories set at the edge of the map.', ['keepers-wife', 'weather-station', 'harbor-lights', 'salt-signal', 'beacon-point']],
    ['radio-mysteries', 'Radio Mysteries', 'Signals, static and voices from nowhere.', ['night-frequency', 'dead-air', 'salt-signal', 'beacon-point']],
    ['sports-anime', 'Sports Anime', 'Training arcs, rivalries and the final race.', ['comet-baton', 'starlit-relay', 'lunar-lap', 'skyline-sprinters']],
    ['space-stars', 'Space & Stars', 'Stations, orbits and long nights between planets.', ['starway', 'starlit-relay', 'orbit-club', 'lunar-lap']],
    ['original-anime', 'Original Anime', 'No manga, no novel: written for the screen.', ['starway', 'comet-baton', 'starlit-relay', 'skyline-sprinters']],
    ['french-mysteries', 'French Mysteries', 'Small towns, long lunches, one unanswered question.', ['line-arden', 'border-hours', 'surveyors-daughter', 'late-clocks', 'quiet-meridian']],
    ['slow-cinema', 'Slow Cinema', 'Long takes and room to think.', ['keepers-wife', 'line-arden', 'map-errors', 'late-clocks', 'quiet-meridian']],
  ];
  const collections = {};
  COLL.forEach(c => {
    const items = c[3].slice().sort((a, b) => byId[a].year - byId[b].year);
    const s = items.filter(id => byId[id].type !== 'film').length, f = items.length - s;
    collections[c[0]] = { id: c[0], title: c[1], desc: c[2], items, counts: [s ? s + ' series' : '', f ? f + (f === 1 ? ' film' : ' films') : ''].filter(Boolean).join(' · ') };
  });

  const NOTIF = [
    ['n1', '2026-10-08', 'aired', 'salt-signal', { code: 'S2E2', ep: 'Second Beacon' }],
    ['n2', '2026-10-08', 'released', 'quiet-meridian', {}],
    ['n3', '2026-10-08', 'changed', 'beacon-point', { code: 'S1E5', from: '2026-10-02', to: '2026-10-09' }],
    ['n4', '2026-10-05', 'announced', 'ice-mile', { code: 'Season 2', to: '2026-11-20' }],
    ['n5', '2026-10-04', 'aired', 'orbit-club', { code: 'E9', ep: 'Transfer Orbit' }],
    ['n6', '2026-10-03', 'changed', 'skyline-sprinters', { code: 'S1E1', from: '2026-10-21', to: '2026-10-14' }],
    ['n7', '2026-10-01', 'premiere', 'salt-signal', { code: 'Season 2', ep: 'Static' }],
    ['n8', '2026-10-01', 'aired', 'orbit-club', { code: 'E8', ep: 'Delta-v' }],
  ].map((n, i) => ({ id: n[0], day: n[1], type: n[2], titleId: n[3], x: n[4], initiallyRead: i >= 4 }));

  const state = { status: {}, progress: {}, qi: {}, recent: Object.keys(LIB).filter(id => !id.startsWith('g')), read: {}, lang: 'en' };
  Object.keys(LIB).forEach(id => { state.status[id] = LIB[id][0]; state.progress[id] = LIB[id][1]; byId[id].queue = LIB[id][2] || []; });
  NOTIF.forEach(n => { if (n.initiallyRead) state.read[n.id] = true; });

  const subs = new Set();
  const set = patch => { Object.assign(state, typeof patch === 'function' ? patch(state) : patch); subs.forEach(f => { try { f(); } catch (e) { console.error(e); } }); };

  const browseTitle = p => {
    const noun = { series: 'series', anime: 'anime', film: 'films' }[p.type] || 'titles';
    const words = [p.country ? DEMONYM[p.country] || p.country : '', p.genre ? p.genre.toLowerCase() : '', noun].filter(Boolean);
    let s = words.join(' ');
    if (p.year) s += ' of ' + p.year;
    if (!p.country && !p.genre && !p.type && !p.year) s = 'All titles';
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  const KEYS = ['type', 'genre', 'country', 'year'];
  const browseHref = p => { const q = KEYS.filter(k => p[k]).map(k => k + '=' + encodeURIComponent(p[k])).join('&'); return '#/browse' + (q ? '?' + q : ''); };
  const parseQuery = qs => { const o = {}; (qs || '').split('&').filter(Boolean).forEach(kv => { const i = kv.indexOf('='); const k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)); const v = i < 0 ? '' : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); o[k] = k === 'year' && v ? Number(v) : v; }); return o; };

  const MT = {
    TODAY, fmt, fmtShort, dow, daysTo, tint, sources, badge, STATUSES, STATUS_LABEL, TYPE_LABEL, GENRES, COUNTRIES, DEMONYM, YEARS, SEE_ALSO, KEYS,
    catalog, pool, collections, notifications: NOTIF, state,
    sub(f) { subs.add(f); return () => subs.delete(f); },
    set,
    go(h) { location.hash = h; },
    title: id => byId[id],
    hrefFor: t => '#/title/' + (PAGE[t.id] || (t.type === 'series' ? 'show' : t.type === 'film' ? 'movie' : 'anime')),
    statusOf: id => state.status[id] || null,
    setStatus(id, s) {
      const st = Object.assign({}, state.status), pr = Object.assign({}, state.progress);
      if (s) st[id] = s; else delete st[id];
      if (s === 'completed' && byId[id]) pr[id] = byId[id].eps;
      if (!s) delete pr[id];
      const recent = [id].concat(state.recent.filter(x => x !== id));
      set({ status: st, progress: pr, recent });
    },
    progress: id => state.progress[id] || 0,
    next(id) {
      const t = byId[id]; const q = (t && t.queue) || []; const e = q[state.qi[id] || 0];
      return e ? { code: e[0], t: e[1], d: e[2], aired: !!e[2] && e[2] <= TODAY } : null;
    },
    markNext(id) {
      const n = MT.next(id); if (!n || !n.aired) return null;
      const t = byId[id]; const p = Math.min(t.eps, MT.progress(id) + 1);
      const patch = { progress: Object.assign({}, state.progress, { [id]: p }), qi: Object.assign({}, state.qi, { [id]: (state.qi[id] || 0) + 1 }), recent: [id].concat(state.recent.filter(x => x !== id)) };
      if (p >= t.eps) patch.status = Object.assign({}, state.status, { [id]: 'completed' });
      set(patch);
      return n;
    },
    upcoming() {
      const out = [];
      Object.keys(state.status).forEach(id => {
        if (state.status[id] === 'dropped') return;
        const t = byId[id]; if (!t || !t.queue) return;
        let tba = false;
        t.queue.slice(state.qi[id] || 0).forEach(e => {
          if (e[2] && e[2] < TODAY) return;
          if (!e[2]) { if (tba) return; tba = true; }
          out.push({ id, kind: 'episode', code: e[0], ep: e[1], d: e[2] });
        });
      });
      UP_EXTRA.forEach(x => { if (state.status[x[0]] && state.status[x[0]] !== 'dropped') out.push({ id: x[0], kind: x[1], code: x[2], ep: x[3], d: x[4] }); });
      return out.sort((a, b) => (a.d || '9999') < (b.d || '9999') ? -1 : (a.d || '9999') > (b.d || '9999') ? 1 : 0);
    },
    isRead: n => !!state.read[n.id],
    unread: () => NOTIF.filter(n => !state.read[n.id]).length,
    markRead(id) { if (!state.read[id]) set({ read: Object.assign({}, state.read, { [id]: true }) }); },
    markAllRead() { const r = {}; NOTIF.forEach(n => { r[n.id] = true; }); set({ read: r }); },
    browseTitle, browseHref, parseQuery,
    browse(p, sort) {
      const list = pool.filter(t => (!p.type || t.type === p.type) && (!p.genre || t.genres.indexOf(p.genre) >= 0) && (!p.country || t.country === p.country) && (!p.year || t.year === Number(p.year)));
      const s = { popularity: (a, b) => b.pop - a.pop, release: (a, b) => b.year - a.year || b.pop - a.pop, title: (a, b) => a.title.localeCompare(b.title), rating: (a, b) => parseFloat(b.rating.value) - parseFloat(a.rating.value) }[sort || 'popularity'];
      return list.sort(s);
    },
    search(q) {
      const s = (q || '').trim().toLowerCase(); if (s.length < 2) return [];
      return pool.filter(t => (t.title + ' ' + t.orig + ' ' + t.genres.join(' ') + ' ' + t.country).toLowerCase().indexOf(s) >= 0).sort((a, b) => b.pop - a.pop);
    },
    collection: id => collections[id] || null,
  };
  window.MT = MT;
})();

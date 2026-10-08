// usage: node test.js <url> <prefix: local|live>
const { chromium } = require('playwright-core');
const URL_ = process.argv[2] || 'http://127.0.0.1:18936/';
const PRE = process.argv[3] || 'local';
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const PLACES = {Parbold: [53.5920, -2.7708], Wigan: [53.5450, -2.6325], Manchester: [53.4808, -2.2426], London: [51.5074, -0.1278]};
(async () => {
  const b = await chromium.launch({executablePath: '/usr/bin/google-chrome', headless: true});
  for (const [w, h, name] of [[390, 844, 'mobile'], [1280, 900, 'desktop']]) {
    const ctx = await b.newContext({viewport: {width: w, height: h}, deviceScaleFactor: name === 'mobile' ? 2 : 1, isMobile: name === 'mobile', hasTouch: name === 'mobile',
      permissions: ['geolocation'], geolocation: {latitude: PLACES.Parbold[0], longitude: PLACES.Parbold[1]}, timezoneId: 'Europe/London', locale: 'en-GB'});
    const p = await ctx.newPage(); const errs = [], bad = [];
    p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); }); p.on('pageerror', e => errs.push(e.message));
    p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url().slice(0, 120)); });
    if (name === 'mobile') { const cdp = await ctx.newCDPSession(p); await cdp.send('Network.enable');
      await cdp.send('Network.emulateNetworkConditions', {offline: false, latency: 150, downloadThroughput: 9e6 / 8, uploadThroughput: 3e6 / 8});
      await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4}); }
    const t0 = Date.now();
    await p.goto(URL_, {waitUntil: 'domcontentloaded'});
    await p.waitForFunction(() => /Tap Near me|Couldn't load|Gyms near/.test(document.getElementById('placeLine').textContent), null, {timeout: 30000});
    ok(/Tap Near me/.test(await p.textContent('#placeLine')), `${name}: gym data loaded in ${Date.now() - t0} ms`);
    for (const [city, ll] of Object.entries(PLACES)) {
      await ctx.setGeolocation({latitude: ll[0], longitude: ll[1]});
      const seq = await p.evaluate(() => window.__placeSeq || 0);
      await p.click('#nearBtn');
      await p.waitForFunction(q => (window.__placeSeq || 0) > q && window.__gym, seq, {timeout: 15000});
      await p.waitForTimeout(1200);
      const g = await p.evaluate(() => window.__gym);
      const sum = (await p.textContent('#summary')).replace(/\s+/g, ' ');
      ok(g.priced > 0, `${name}: Near me ${city} (${g.radius} mi): ${sum}\n      top: ${JSON.stringify(g.cheapest)}`);
      if (city === 'Parbold') {
        await p.evaluate(() => window.scrollTo(0, 0));
        await p.screenshot({path: PRE === 'live' ? (name === 'mobile' ? 'shots/mobile-live.png' : 'shots/desktop-live.png') : `shots/${PRE}-${name}-parbold.png`});
        for (const r of [2, 10, 25]) { await p.click(`#radBtns button[data-r="${r}"]`); await p.waitForTimeout(400); const x = await p.evaluate(() => window.__gym); console.log(`      radius ${r} mi: ${x.priced} priced, ${x.others} others, ${x.offers} with offers`); }
        await p.click('#radBtns button[data-r="10"]'); await p.waitForTimeout(300);
        await p.click('#fNC'); await p.waitForTimeout(300); const nc = await p.evaluate(() => window.__gym);
        await p.click('#f24'); await p.waitForTimeout(300); const n24 = await p.evaluate(() => window.__gym);
        await p.selectOption('#fMax', '25'); await p.waitForTimeout(300); const m25 = await p.evaluate(() => window.__gym);
        ok(nc.priced >= n24.priced && n24.priced >= m25.priced && m25.cheapest.every(c => c.from <= 25), `${name}: filters at 10 mi – no contract ${nc.priced}, +24/7 ${n24.priced}, +under £25 ${m25.priced}: ${JSON.stringify(m25.cheapest.map(c => c.n + ' £' + c.from))}`);
        await p.click('#fNC'); await p.click('#f24'); await p.selectOption('#fMax', ''); await p.click('#radBtns button[data-r="5"]'); await p.waitForTimeout(300);
        if (name === 'desktop') await p.screenshot({path: `shots/${PRE}-desktop-parbold-full.png`, fullPage: true});
      }
    }
    // postcode + town search
    for (const q of ['WN8 7AA', 'WN5 7XA', 'Wigan']) {
      const seq = await p.evaluate(() => window.__placeSeq || 0);
      await p.fill('#placeInput', q); await p.click('#placeGo');
      await p.waitForFunction(s => (window.__placeSeq || 0) > s, seq, {timeout: 15000}).catch(() => {});
      await p.waitForTimeout(800);
      const g = await p.evaluate(() => window.__gym); const line = await p.textContent('#placeLine');
      ok(g && g.priced > 0 && /^Gyms near/.test(line.trim()) && line.includes(q.split(' ')[0]), `${name}: search "${q}" -> ${line.replace(/ clear$/, '').trim()}: ${g.priced} priced, cheapest ${g.cheapest[0] && g.cheapest[0].n} £${g.cheapest[0] && g.cheapest[0].from}`);
    }
    const mk = await p.$$eval('.leaflet-interactive', e => e.length); ok(mk > 3, `${name}: map markers ${mk}`);
    await p.reload({waitUntil: 'domcontentloaded'}); await p.waitForFunction(() => window.__gym, null, {timeout: 20000});
    ok(/last used/.test(await p.textContent('#placeLine')), `${name}: last place remembered`);
    if (name === 'mobile') { const sw = await p.evaluate(() => document.documentElement.scrollWidth); ok(sw <= 392, `mobile: no sideways scroll (${sw})`); }
    ok(errs.length === 0, `${name}: no console errors ${errs.join(' | ')}`);
    ok(bad.length === 0, `${name}: no failed requests ${bad.join(' | ')}`);
    await ctx.close();
  }
  await b.close(); console.log(fails ? `${fails} FAILED` : 'ALL PASSED'); process.exit(fails ? 1 : 0);
})();

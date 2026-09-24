// UI de Playme v1.3.0 sin navegador (jsdom): botones next/prev de la lista,
// reporte de posicion para el ritmo de descarga y modal "Vaciar descargas".
// Uso: node tools/linux-only/ui-test.js   (requiere jsdom: npm i jsdom)
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { ({ JSDOM } = require('/tmp/jtest/node_modules/jsdom')); }

const HTML = process.env.PLAYME_HTML ||
  path.normalize(path.join(__dirname, '..', '..', 'app', 'src', 'main', 'python', 'static', 'index.html'));
const html = fs.readFileSync(HTML, 'utf8');
const calls = [];
const posts = () => calls.filter(c => c.indexOf('POST ') === 0);

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://127.0.0.1:8191/',
  beforeParse(w) {
    w.fetch = (url, opts) => {
      const m = ((opts && opts.method) || 'GET') + ' ' + url + (opts && opts.body ? ' ' + opts.body : '');
      calls.push(m);
      let body = { ok: true };
      if (String(url).indexOf('/api/state') >= 0)
        body = { ok: true, playing: true, paused: false, queue: [], current: { id: 'v2', title: 'Dos', duration: 100 }, mode: 'file', streaming: true, cache_bytes: 1024, mp3: false, current_index: 1 };
      if (String(url).indexOf('/api/conversions') >= 0)
        body = { ok: true, conversions: { abc: { status: 'ready', title: 'Demo' } } };
      if (String(url).indexOf('/api/play') >= 0)
        body = { ok: true, playing: true, resolving: false, current: { id: 'v3', title: 'Tres', duration: 100 }, mode: 'file', queue: [], current_index: 0 };
      return Promise.resolve({ json: () => Promise.resolve(body) });
    };
    w.Audio = function () {
      return { play: () => Promise.resolve(), pause: () => {}, load: () => {},
               addEventListener: () => {}, removeAttribute: () => {}, volume: 1, currentTime: 7.5, src: '' };
    };
    w.HTMLMediaElement.prototype.play = () => Promise.resolve();
  }
});
const w = dom.window, d = w.document;
const ok = [], bad = [];
const chk = (c, m) => (c ? ok : bad).push(m);
const $ = (id) => d.getElementById(id);
const wait = (ms) => new Promise(r => setTimeout(r, ms));

// lista de resultados con 3 temas (como tras buscar "shema israel)
$('results').innerHTML =
  '<div class="r" data-id="v1"><div class="rt">Uno</div><button class="rp" data-id="v1">▶</button></div>' +
  '<div class="r" data-id="v2"><div class="rt">Dos</div><button class="rp" data-id="v2">▶</button></div>' +
  '<div class="r" data-id="v3"><div class="rt">Tres</div><button class="rp" data-id="v3">▶</button></div>';

d.dispatchEvent(new w.Event('DOMContentLoaded'));

(async () => {
  await wait(20);
  chk(/v1\.3\.0/.test(d.querySelector('h1').textContent), 'titulo ' + d.querySelector('h1').textContent.trim());
  chk(d.getElementById('conn') === null, 'sin etiqueta usb');

  // estado: suena v2 (esta en la lista)
  w.state = { playing: true, paused: false, current: { id: 'v2', title: 'Dos', duration: 100 }, mode: 'file', streaming: true, cache_bytes: 1024, queue: [], current_index: 1, mp3: false };

  // 1) next en la lista -> reproduce v3 (antes no reproducia nada)
  let b = posts().length;
  $('nextBtn').disabled = false;
  $('nextBtn').click();
  await wait(40);
  const pn = posts().slice(b);
  chk(pn.some(c => c.indexOf('/api/play') >= 0 && c.indexOf('"v3"') >= 0),
    'next reproduce el SIGUIENTE de la lista (v3) -> ' + pn.join(' | '));

  // 2) prev en la lista -> reproduce v1 (no el next del backend)
  w.state = { playing: true, paused: false, current: { id: 'v2', title: 'Dos', duration: 100 }, mode: 'file', streaming: true, cache_bytes: 1024, queue: [], current_index: 1, mp3: false };
  b = posts().length;
  $('prevBtn').disabled = false;
  $('prevBtn').click();
  await wait(40);
  const pp = posts().slice(b);
  chk(pp.some(c => c.indexOf('/api/play') >= 0 && c.indexOf('"v1"') >= 0),
    'prev reproduce el ANTERIOR de la lista (v1) -> ' + pp.join(' | '));
  chk(!pp.some(c => c.indexOf('POST /api/prev') === 0), 'prev ya no usa la cola del backend');

  // 3) borde de la lista: no rompe
  w.state.current = { id: 'v3', title: 'Tres', duration: 100 };
  b = posts().length;
  w.stepT(1);
  await wait(10);
  chk($('status').textContent === 'Ultimo tema de la lista', 'ultimo tema -> ' + $('status').textContent);
  chk(posts().slice(b).length === 0, 'en el ultimo no manda play');
  w.state.current = { id: 'v1', title: 'Uno', duration: 100 };
  w.stepT(-1);
  await wait(10);
  chk($('status').textContent === 'Primer tema de la lista', 'primer tema -> ' + $('status').textContent);

  // 4) posicion de reproduccion (ritmo de descarga = posicion + 5 s)
  b = posts().length;
  w.audio = { src: 'stream', currentTime: 12.5 };
  w.state = { playing: true, paused: false, current: { id: 'v2' }, mode: 'file' };
  w.posSent = 0;
  w.sendPos(true);
  await wait(30);
  const pos = posts().slice(b).filter(c => c.indexOf('/api/position') >= 0);
  chk(pos.length === 1, 'sendPos manda POST /api/position -> ' + pos.join(','));

  // 5) sin tema actual no manda posicion
  b = posts().length;
  w.state = { playing: false, current: null };
  w.sendPos(true);
  await wait(20);
  chk(posts().slice(b).length === 0, 'sin tema actual no manda posicion');

  // 6) modal "Vaciar descargas" (sin window.confirm en el WebView)
  $('tabDl').click();
  await wait(40);
  chk(/Demo/.test($('dlList').textContent), 'lista de descargas -> ' + $('dlList').textContent.trim().slice(0, 30));
  $('clearDlBtn').click();
  chk($('cfm').style.display === 'flex', 'modal propio visible');
  b = posts().length;
  $('cfmYes').click();
  await wait(30);
  chk(posts().slice(b).some(c => c.indexOf('POST /api/clean/dl') === 0), 'POST /api/clean/dl -> ' + posts().slice(b).join(','));
  chk($('cfm').style.display === 'none', 'modal cerrado');

  console.log('OK ' + ok.length + ':');
  ok.forEach(m => console.log('  + ' + m));
  if (bad.length) { console.log('FALLA ' + bad.length + ':'); bad.forEach(m => console.log('  - ' + m)); }
  console.log(bad.length ? 'RESULT=FAIL' : 'RESULT=PASS');
  process.exit(bad.length ? 1 : 0);
})();

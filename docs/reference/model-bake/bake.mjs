// Offline research renderer; these tools are not application dependencies.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const modelName = 'mxmaster4mac-whitesilver-ar-360.glb';
const modelPath = path.resolve(
  process.env.MX4_MODEL_PATH ?? path.join(root, '..', 'models', modelName),
);
const realRoot = await fs.realpath(root);

function contained(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== '' && relative !== '..'
    && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function resourcePath(rawUrl) {
  let pathname;
  try {
    pathname = decodeURIComponent(rawUrl.split('?')[0]);
  } catch {
    return null;
  }
  if (!pathname.startsWith('/') || pathname.startsWith('//')
      || pathname.includes('\\') || pathname.includes('\0')) return null;
  const pieces = pathname.slice(1).split('/');
  if (pieces.some(piece => piece === '.' || piece === '..')) return null;
  if (pathname === '/mouse.glb') return { file: modelPath, model: true };
  const name = pathname === '/' ? 'viewer.html' : pathname.slice(1);
  const allowed = name === 'viewer.html' || name === 'wheel-island.json'
    || (name.startsWith('three/package/') && name.endsWith('.js'));
  if (!allowed || path.isAbsolute(name)) return null;
  const file = path.resolve(root, name);
  return contained(root, file) ? { file, model: false } : null;
}

const server = http.createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405).end('method not allowed');
    return;
  }
  try {
    const resource = resourcePath(request.url ?? '');
    if (!resource) throw new Error('outside static resource allowlist');
    const actual = await fs.realpath(resource.file);
    // The model is the sole explicit external-file route; static symlinks cannot escape.
    if (!resource.model && !contained(realRoot, actual)) throw new Error('outside root');
    const data = await fs.readFile(actual);
    const type = actual.endsWith('.js') ? 'text/javascript'
      : actual.endsWith('.html') ? 'text/html'
      : actual.endsWith('.json') ? 'application/json'
      : actual.endsWith('.glb') ? 'model/gltf-binary' : 'application/octet-stream';
    response.writeHead(200, { 'Content-Type': type, 'Content-Length': data.length });
    response.end(request.method === 'HEAD' ? undefined : data);
  } catch {
    response.writeHead(404).end('missing');
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  if (process.argv.includes('--self-check')) {
    const statuses = [];
    for (const [requestPath, expected] of [
      ['/', 200], ['/viewer.html', 200], ['/three/package/build/three.module.js', 200],
      ['/bake.mjs', 404], ['/manifest.json', 404], ['/../mx4-glb-prep-other/file.js', 404],
      ['/%2e%2e/secret', 404], ['/three/package/../viewer.html', 404],
      ['/three/package/%2e%2e/%2e%2e/viewer.html', 404],
      ['//etc/passwd', 404], ['/%2fetc/passwd', 404],
      ['/three/package/%5c..%5csecret.js', 404], ['/three/package/%00.js', 404],
    ]) {
      const status = await new Promise((resolve, reject) => {
        http.get(`${origin}`, { path: requestPath }, response => {
          response.resume();
          resolve(response.statusCode);
        }).on('error', reject);
      });
      assert.equal(status, expected, requestPath);
      statuses.push({ requestPath, status });
    }
    assert.equal(contained(root, `${root}-other/secret.js`), false);
    console.log(JSON.stringify({ selfCheck: 'passed', statuses }, null, 2));
  } else {
    const { chromium } = await import(pathToFileURL(process.env.MX4_PLAYWRIGHT_MODULE
      ?? '/home/dj/.npm/_npx/420ff84f11983ee5/node_modules/playwright-core/index.mjs').href);
    browser = await chromium.launch({
      executablePath: process.env.MX4_CHROMIUM
        ?? '/home/dj/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',
      headless: true,
      args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    });
    const page = await browser.newPage({
      viewport: { width: 768, height: 768 }, deviceScaleFactor: 1,
    });
    page.on('console', message => console.log(message.type(), message.text()));
    page.on('pageerror', error => console.error('pageerror', error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin
      ? route.continue() : route.abort());
    const params = new URLSearchParams();
    if (process.env.MX4_WHEEL) params.set('wheel', '1');
    if (process.argv.includes('--orbit')) params.set('size', '512');
    await page.goto(`${origin}/?${params}`);
    await page.waitForFunction(() => window.ready, { timeout: 30000 });
    console.log(await page.evaluate(() => window.modelInfo));
    if (process.argv.includes('--orbit')) {
      const sourceHash = createHash('sha256').update(await fs.readFile(modelPath)).digest('hex');
      assert.equal(sourceHash, '305d2b776c0c0d064406253e13c9f222b01b55abf68667bb93ceb588e4e4fd35');
      const sourceAnchors = JSON.parse(await fs.readFile(process.env.MX4_SURFACE_ANCHORS
        ?? path.join(root, '../../../src/mx4_linux/assets/mouse/surface-anchors.json'), 'utf8'));
      const atlas = {
        resolution: 512, angle_degrees: 9, samples: 17,
        source_sha256: sourceHash,
        source: 'User-supplied static GLB; authored camera orbit, no device motion telemetry.',
        projection: 'Orthographic, same target, extent, materials and environment as manifest.json. World Y camera orbit.',
        views: {},
      };
      for (const [view, x, y, z, roll] of [
        ['overview', -.15, .23, .13, 1.047198],
        ['underside', -.04, -.3, .07, 3.14159265],
      ]) {
        await page.evaluate(v => window.bake(...v), [x, y, z, .17, roll]);
        const picked = await page.evaluate(points => window.pick(points),
          Object.fromEntries(Object.entries(sourceAnchors[view]).map(([name, a]) => [name, a.normalized])));
        const points = Object.fromEntries(Object.entries(sourceAnchors[view]).map(([name, a]) => {
          const position = a.source_mm ?? picked[name]?.source_mm;
          assert(position, `Missing surface point: ${view}/${name}`);
          return [name, position];
        }));
        atlas.views[view] = [];
        for (let index = 0; index < atlas.samples; index++) {
          const pose = index / (atlas.samples - 1) * 2 - 1;
          const angle = pose * atlas.angle_degrees * Math.PI / 180;
          const file = `orbit-${view}-${String(index).padStart(2, '0')}.png`;
          const data = await page.evaluate(values => window.bake(...values),
            [x * Math.cos(angle) + z * Math.sin(angle), y,
              z * Math.cos(angle) - x * Math.sin(angle), .17, roll]);
          const bytes = Buffer.from(data.split(',')[1], 'base64');
          await fs.writeFile(path.join(root, file), bytes);
          const projected = await page.evaluate(values => window.projectSource(values), points);
          atlas.views[view].push({
            file, pose, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
            anchors: Object.fromEntries(Object.entries(projected).map(([name, a]) => [name, a.normalized])),
          });
        }
      }
      await fs.writeFile(path.join(root, 'orbit.json'), JSON.stringify(atlas, null, 2) + '\n');
      console.log('Rendered 34 native orbit samples and registered surface anchors.');
    } else {
    const views = process.argv[2] ? JSON.parse(process.argv[2]) : [
      ['overview', -.15, .23, .13, .17, 1.047198],
      ['underside', -.04, -.3, .07, .17, 3.14159265],
    ];
    for (const [name, x, y, z, extent = .17, roll = 0, exposure = 1, wheelAngle = 0] of views) {
      if (!/^[a-zA-Z0-9_-]+$/.test(name)) throw new Error('invalid output name');
      const data = await page.evaluate(values => window.bake(...values),
        [x, y, z, extent, roll, exposure, wheelAngle]);
      await fs.writeFile(path.join(root, `${name}.png`), Buffer.from(data.split(',')[1], 'base64'));
      const points = {
        main_wheel: [-1.51, 33.205, 34], wheel_mode: [-2.76, 3.36, 44],
        thumb_wheel: [-32.0, .227, 40.51], haptic_panel: [-43, -13, 22],
        extra_thumb_button: [-30, -13, 45], forward_button: [-36, 11, 32],
        back_button: [-37, -3, 30],
      };
      const anchors = await page.evaluate(values => window.projectSource(values), points);
      await fs.writeFile(path.join(root, `${name}-anchors.json`), JSON.stringify(anchors, null, 2));
      const handpoints = name === 'underside' ? {
        easy_switch: [280 / 768, 450 / 768], power: [265 / 768, 319 / 768],
      } : {
        main_wheel: [440 / 768, 247 / 768], wheel_mode: [462 / 768, 330 / 768],
        thumb_wheel: [365 / 768, 354 / 768], haptic_panel: [259 / 768, 415 / 768],
        extra_thumb_button: [319 / 768, 301 / 768],
        forward_button: [328 / 768, 349 / 768], back_button: [337 / 768, 389 / 768],
      };
      const picks = await page.evaluate(values => window.pick(values), handpoints);
      await fs.writeFile(path.join(root, `${name}-surface-anchors.json`), JSON.stringify(picks, null, 2));
      console.log(name);
    }
    }
  }
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}

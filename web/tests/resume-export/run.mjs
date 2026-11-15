/** Real export routes + real renderer, under the unmodified desktop CSP.
 * Fixture host does not claim full editor wiring, authentication, Electron save
 * dialog or installed-app coverage. No credentials, models or real user data.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { readFile, writeFile, mkdir, realpath } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { build } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright-core';
const require = createRequire(import.meta.url);
const { contentSecurityPolicy } = require('../../../desktop/capabilities.cjs');
assert.ok(contentSecurityPolicy.includes("frame-src 'none'"));
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const output = path.resolve(process.argv[2] || path.join(repo, 'artifacts/resume-export-browser'));
const dist = path.join(output, 'fixture-dist');
const apiPort = Number(process.env.RESUME_EXPORT_API_PORT || 18772);
const report = { scope: 'Production modal, PDF.js worker and actual export HTTP routes under desktop CSP; synthetic disposable database. Not full editor, authentication or installed Electron acceptance.', csp: contentSecurityPolicy, cases: [], assertions: [], pageErrors: [], cspViolations: [], networkViolations: [], failedRequests: [], consoleErrors: [], requests: [], screenshots: [], failures: [] };
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const check = (name, condition) => { assert.ok(condition, name); report.assertions.push(name); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(name, predicate) {
  for (let i = 0; i < 300; i++) { if (await predicate()) { report.assertions.push(name); return; } await sleep(50); }
  throw new Error(`Timed out: ${name}`);
}
let browser, server, api;
await mkdir(output, { recursive: true });
try {
  report.source = { commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim(), status: execFileSync('git', ['status', '--porcelain'], { cwd: repo, encoding: 'utf8' }), lockSha256: hash(await readFile(path.join(repo, 'web/package-lock.json'))) };
  report.source.files = {};
  for (const relative of ['desktop/capabilities.cjs', 'web/src/components/ResumeExportModal.tsx', 'web/src/components/ResumePdfPreview.tsx', 'web/src/services/resumeExport.ts', 'src/offerpilot/resume_export.py', 'src/offerpilot/resume_export_routes.py', 'web/tests/resume-export/main.tsx', 'web/tests/resume-export/run.mjs', 'web/tests/resume-export/serve_api.py']) report.source.files[relative] = hash(await readFile(path.join(repo, relative)));
  await build({ configFile: false, root: here, plugins: [react()], resolve: { alias: { '@': path.join(repo, 'web/src') } }, build: { outDir: dist, emptyOutDir: true } });
  // --build-only is useful on executors without an authorized browser surface.
  if (process.argv.includes('--build-only')) { report.buildOnly = true; }
  else {
    const python = process.env.PYTHON || path.join(repo, '.venv/bin/python');
    api = spawn(python, [path.join(here, 'serve_api.py'), String(apiPort)], { cwd: repo, env: { ...process.env, PYTHONPATH: path.join(repo, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
    api.stdout.on('data', data => process.stdout.write(data));
    api.stderr.on('data', data => process.stderr.write(data));
    let apiError;
    api.on('error', error => { apiError = error; });
    await until('real API ready', async () => {
      if (apiError) throw apiError;
      if (api.exitCode !== null) throw new Error(`API exited: ${api.exitCode}`);
      try { return (await fetch(`http://127.0.0.1:${apiPort}/openapi.json`)).ok; } catch { return false; }
    });
    server = createServer(async (req, res) => {
      try {
        res.setHeader('Content-Security-Policy', contentSecurityPolicy);
        const url = new URL(req.url, 'http://fixture');
        if (url.pathname.startsWith('/api/')) {
          if (req.method !== 'POST' || !/^\/api\/resumes\/[12]\/export(?:-preview)?$/.test(url.pathname)) throw new Error(`Unexpected API: ${req.method} ${url.pathname}`);
          const chunks = []; for await (const chunk of req) chunks.push(chunk);
          const body = Buffer.concat(chunks);
          report.requests.push({ method: req.method, path: url.pathname, body: JSON.parse(body) });
          const response = await fetch(`http://127.0.0.1:${apiPort}${url.pathname}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
          res.statusCode = response.status;
          for (const [key, value] of response.headers) if (!['transfer-encoding', 'connection', 'content-length'].includes(key)) res.setHeader(key, value);
          res.end(Buffer.from(await response.arrayBuffer()));
          return;
        }
        if (req.method !== 'GET') throw new Error('Unexpected method');
        if (url.pathname === '/favicon.ico') { res.statusCode = 204; res.end(); return; }
        const file = await realpath(path.join(dist, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname)));
        if (!file.startsWith(`${await realpath(dist)}${path.sep}`)) throw new Error('Outside fixture build');
        const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
        res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
        res.end(await readFile(file));
      } catch (error) { report.failures.push(String(error)); res.statusCode = 500; res.end('fixture error'); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({ headless: true, args: ['--disable-background-networking'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
    report.browser = browser.version();
    for (const locale of ['zh-CN', 'en-US']) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 1100 }, acceptDownloads: true, serviceWorkers: 'block' });
      let page, workerRelease;
      const result = { locale, status: 'running' }; report.cases.push(result);
      try {
        await context.exposeBinding('recordCspViolation', (_source, data) => report.cspViolations.push(data));
        await context.addInitScript(({ locale }) => {
          localStorage.setItem('offerpilot.locale', locale);
          document.addEventListener('securitypolicyviolation', event => window.recordCspViolation({ directive: event.violatedDirective, blockedURI: event.blockedURI }));
        }, { locale });
        let corrupt = false;
        const workerGate = new Promise(resolve => { workerRelease = resolve; });
        await context.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.origin !== origin && url.protocol !== 'blob:') { report.networkViolations.push(url.href); await route.abort(); return; }
          if (/pdf\.worker.*\.mjs$/.test(url.pathname)) await workerGate;
          if (corrupt && url.pathname.endsWith('/export-preview')) {
            const response = await route.fetch();
            await route.fulfill({ response, body: Buffer.from('This is not a PDF'), headers: { ...response.headers(), 'content-type': 'application/pdf' } });
          } else await route.continue();
        });
        page = await context.newPage();
        page.on('pageerror', error => report.pageErrors.push(String(error)));
        page.on('requestfailed', request => report.failedRequests.push({ url: request.url(), reason: request.failure()?.errorText }));
        page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
        const response = await page.goto(origin);
        check(`${locale}: exact desktop CSP header`, response.headers()['content-security-policy'] === contentSecurityPolicy);
        const zh = locale === 'zh-CN';
        await page.getByText(zh ? '导出已保存简历' : 'Export saved resume', { exact: true }).waitFor();
        check(`${locale}: localized disclosure`, await page.getByText(zh ? 'PDF 展示实际分页。DOCX 内容相同且可编辑，但 Word 和本地字体可能改变分页。' : 'PDF shows the actual page layout. DOCX has the same editable content, but Word and local fonts may change pagination.', { exact: true }).count() === 1);
        const button = name => page.getByRole('button', { name, exact: true });
        const generate = button(zh ? '生成 PDF 预览' : 'Generate PDF preview');
        const pdf = button(zh ? '下载 PDF' : 'Download PDF');
        const docx = button(zh ? '下载 DOCX' : 'Download DOCX');
        const review = page.getByRole('checkbox');
        const canvas = page.locator('canvas');
        const blocked = async label => check(`${locale}: ${label}`, await pdf.isDisabled() && await docx.isDisabled());
        const screenshot = async name => {
          const filename = `${locale}-${name}.png`;
          await page.screenshot({ path: path.join(output, filename), fullPage: true, animations: 'disabled' });
          report.screenshots.push({ file: filename, sha256: hash(await readFile(path.join(output, filename))) });
        };
        const ready = async label => {
          await until(`${locale}: ${label} render ready`, () => review.isEnabled());
          const pixels = await canvas.evaluate(node => {
            const data = node.getContext('2d').getImageData(0, 0, node.width, node.height).data;
            let dark = 0; for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 0 && data[i] < 180 && data[i + 1] < 180 && data[i + 2] < 180) dark++;
            return { dark, width: node.width, height: node.height };
          });
          check(`${locale}: ${label} canvas contains rendered ink`, pixels.dark > 200 && pixels.width > 500 && pixels.height > 500);
          check(`${locale}: no embedded frames or objects`, await page.locator('iframe,object,embed').count() === 0);
          await blocked(`${label} still requires explicit review`);
        };
        await blocked('initial downloads disabled');
        await generate.click();
        await until(`${locale}: loading checkbox visible`, () => review.count().then(count => count === 1));
        check(`${locale}: worker loading prevents review`, await review.isDisabled());
        check(`${locale}: localized rendering status`, await page.getByRole('status').filter({ hasText: zh ? '正在渲染 PDF 预览' : 'Rendering PDF preview' }).count() === 1);
        await blocked('worker loading prevents download');
        workerRelease();
        await ready('Chinese single-page');
        check(`${locale}: single page label`, await page.getByText(zh ? '第 1 页，共 1 页' : 'Page 1 of 1', { exact: true }).count() === 1);
        await screenshot('single-page');
        await review.check();
        for (const [format, control] of [['pdf', pdf], ['docx', docx]]) {
          const event = page.waitForEvent('download');
          await control.click();
          const download = await event;
          check(`${locale}: ${format} browser download filename`, download.suggestedFilename().endsWith(`.${format}`));
          const target = path.join(output, `${locale}-download.${format}`); await download.saveAs(target);
          const bytes = await readFile(target);
          check(`${locale}: actual ${format} bytes`, bytes.subarray(0, format === 'pdf' ? 5 : 2).toString() === (format === 'pdf' ? '%PDF-' : 'PK'));
          await until(`${locale}: download settles`, () => pdf.isEnabled());
        }
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('resume-fixture', { detail: { id: 2 } })));
        await generate.click();
        await ready('multipage first page');
        const next = button(zh ? '下一页' : 'Next page');
        check(`${locale}: real multipage document`, await next.isEnabled());
        await review.check(); await next.click();
        await blocked('page change invalidates review');
        await ready('multipage second page');
        check(`${locale}: page two accessible name`, (await canvas.getAttribute('aria-label')).includes('2'));
        check(`${locale}: page change cleared checkbox`, !(await review.isChecked()));
        await screenshot('second-page');
        await review.check();
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('resume-fixture', { detail: { blocked: true } })));
        await until(`${locale}: dirty transition disables generate`, () => generate.isDisabled());
        await blocked('dirty transition invalidates download');
        check(`${locale}: dirty removes old preview`, await canvas.count() === 0);
        await screenshot('dirty');
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('resume-fixture', { detail: { blocked: false } })));
        await until(`${locale}: clean transition permits fresh preview`, () => generate.isEnabled());
        await blocked('clean transition cannot reuse review');
        corrupt = true; await generate.click();
        await page.getByRole('alert').filter({ hasText: zh ? 'PDF 预览渲染失败' : 'PDF preview could not be rendered' }).waitFor();
        await blocked('invalid PDF fails closed');
        check(`${locale}: failed preview cannot be reviewed`, await review.count() === 0);
        await screenshot('render-failure');
        corrupt = false; await generate.click(); await ready('retry after render failure');
        check(`${locale}: retry requires fresh review`, !(await review.isChecked()));
        result.status = 'passed';
      } catch (error) {
        result.status = 'failed'; report.failures.push(error.stack || String(error));
        if (page) await page.screenshot({ path: path.join(output, `${locale}-failure.png`), fullPage: true }).catch(() => {});
      } finally { workerRelease?.(); await context.close(); }
    }
    check('both locales completed', report.cases.length === 2 && report.cases.every(item => item.status === 'passed'));
    check('actual PDF/DOCX download endpoints used with source fingerprints', ['zh-CN', 'en-US'].every(locale => ['pdf', 'docx'].every(format => report.requests.some(request => request.path === '/api/resumes/1/export' && request.body.locale === locale && request.body.format === format && /^[a-f0-9]{64}$/.test(request.body.source_fingerprint)))));
    for (const key of ['pageErrors', 'cspViolations', 'networkViolations', 'failedRequests', 'consoleErrors']) check(`zero ${key}`, report[key].length === 0);
  }
} catch (error) { report.failures.push(error.stack || String(error)); }
finally {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
  api?.kill();
  report.status = report.failures.length ? 'failed' : report.buildOnly ? 'build-only-not-browser-verified' : 'passed';
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`Resume export regression: ${report.status}; ${output}/report.json`);
  if (report.failures.length) process.exitCode = 1;
}

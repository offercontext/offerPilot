'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createCapabilities, localURL, externalURL, safeFilename, sourceDownload, contentSecurityPolicy } = require('../capabilities.cjs');
const origin = 'http://127.0.0.1:18420';
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const session = new EventEmitter();
  session.setPermissionCheckHandler = fn => session.check = fn;
  session.setPermissionRequestHandler = fn => session.request = fn;
  const contents = new EventEmitter();
  contents.url = origin; contents.getURL = () => contents.url;
  contents.mainFrame = {}; contents.setWindowOpenHandler = fn => contents.open = fn;
  contents.downloads = []; contents.downloadURL = url => contents.downloads.push(url);
  const win = { webContents: contents };
  const answers = []; const prompts = []; const opened = []; const saved = [];
  const dialog = {
    async showMessageBox(_win, options) { prompts.push(options); return { response: answers.shift() ?? 0 }; },
    showSaveDialogSync(_win, options) { saved.push(options); return answers.shift(); },
  };
  const capability = createCapabilities({ origin, desktopSession: session, isTrustedContents: c => c === contents,
    dialog, shell: { async openExternal(url) { opened.push(url); } }, BrowserWindow: { fromWebContents: () => win } });
  capability.installWindowPolicy(win);
  const details = { isMainFrame: true, requestingUrl: origin, mediaTypes: ['audio'], mediaType: 'audio' };
  const request = (permission = 'media', overrides = {}) => new Promise(resolve => session.request(contents, permission, resolve, { ...details, ...overrides }));
  function download(url = `blob:${origin}/id`, frame = contents.mainFrame, chain = [url]) {
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    const item = { getURLChain: () => chain, getInitiatorOrigin: () => origin, getFilename: () => '../../backup.json', setSavePath: p => item.path = p };
    session.emit('will-download', event, item, contents, frame);
    return { event, item };
  }
  return { session, contents, win, answers, prompts, opened, saved, dialog, details, request, download };
}
test('protocol and origin rules reject credential tricks, opaque and executable schemes', () => {
  assert.equal(localURL(`${origin}/api`, origin), true);
  assert.equal(localURL(`blob:${origin}/id`, origin, true), true);
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x', 'https://example.com', 'http://127.0.0.1:18421', `http://user@127.0.0.1:18420`]) assert.equal(localURL(url, origin, true), false);
  for (const url of ['mailto:x@y.com','ms-settings:display','file:///a',`${origin}/api`,'https://u:p@example.com','https://example.com/\nfoo']) assert.equal(externalURL(url, origin), null);
  assert.equal(externalURL('https://example.com/x', origin), 'https://example.com/x');
  assert.equal(sourceDownload(`${origin}/api/knowledge/sources/12/content`, origin), true);
  assert.equal(sourceDownload(`${origin}/api/knowledge/sources/12/assets/3/content`, origin), true);
  for (const suffix of ['/api/settings', '/api/knowledge/sources/12/content?url=https://evil.test', '/api/knowledge/sources/12/content#x']) assert.equal(sourceDownload(origin + suffix, origin), false);
});
test('save filenames strip paths, invalid Windows characters and reserved devices', () => {
  assert.equal(safeFilename('../../x.json'), 'x.json');
  assert.equal(safeFilename('C:\\private\\export.zip'), 'export.zip');
  assert.equal(safeFilename('CON.txt'), 'export-CON.txt');
  assert.equal(safeFilename('x.json:evil'), 'x.json_evil');
});
test('trusted same-origin blob export prompts once and writes only selected path; cancel writes nothing', () => {
  const f = fixture(); f.answers.push('/chosen/backup.json');
  const accepted = f.download();
  assert.equal(accepted.event.prevented, false); assert.equal(accepted.item.path, '/chosen/backup.json');
  assert.equal(f.saved[0].defaultPath, 'backup.json');
  assert.ok(f.saved[0].properties.includes('showOverwriteConfirmation'));
  const canceled = f.download(); assert.equal(canceled.event.prevented, true); assert.equal(canceled.item.path, undefined);
});
test('download denies cross-origin, cross-origin redirect, opaque blob and subframe before save dialog', () => {
  const f = fixture();
  for (const url of ['https://evil.test/file', 'blob:https://evil.test/id', 'blob:null/id', 'data:text/plain,x', 'file:///a']) assert.equal(f.download(url).event.prevented, true);
  assert.equal(f.download(`${origin}/a`, {}, [`${origin}/a`]).event.prevented, true);
  assert.equal(f.download(`${origin}/a`, f.contents.mainFrame, [`${origin}/a`, 'https://evil.test/a']).event.prevented, true);
  assert.equal(f.download(`${origin}/a`, null).event.prevented, true);
  f.contents.url = 'https://evil.test'; assert.equal(f.download().event.prevented, true);
  assert.equal(f.saved.length, 0);
});
test('only requested main-frame audio can be granted; checks require explicit confirmation', async () => {
  const f = fixture();
  assert.equal(f.session.check(f.contents, 'media', origin, f.details), false);
  assert.equal(await f.request(), false);
  f.answers.push(1); assert.equal(await f.request(), true);
  assert.equal(f.session.check(f.contents, 'media', origin, f.details), true);
  for (const overrides of [{mediaTypes:['video']},{mediaTypes:['audio','video']},{mediaTypes:[]},{isMainFrame:false},{requestingUrl:'https://evil.test'}]) assert.equal(await f.request('media', overrides), false);
  for (const permission of ['display-capture','geolocation','notifications','clipboard-read','openExternal','fileSystem']) assert.equal(await f.request(permission), false);
  assert.equal(f.session.check(f.contents, 'media', origin, {...f.details,mediaType:'video'}), false);
  assert.equal(f.session.check(f.contents, 'media', 'https://evil.test', f.details), false);
  f.contents.emit('did-start-navigation', {}, origin, false, true);
  assert.equal(f.session.check(f.contents, 'media', origin, f.details), false);
});
test('reload during microphone confirmation cannot grant permission to the new document', async () => {
  const f = fixture(); let resolve;
  f.dialog.showMessageBox = () => new Promise(r => resolve = r);
  const answer = f.request(); await flush();
  f.contents.emit('did-start-navigation', {}, origin, false, true);
  resolve({response:1}); assert.equal(await answer, false);
});
test('external windows always denied; browser opens only a confirmed HTTP(S) link', async () => {
  const f = fixture();
  assert.deepEqual(f.contents.open({url:'https://example.com'}), {action:'deny'}); await flush(); assert.equal(f.opened.length,0);
  f.answers.push(1); f.contents.open({url:'https://example.com/path'}); await flush(); assert.deepEqual(f.opened,['https://example.com/path']);
  f.contents.open({url:'file:///etc/passwd'}); f.contents.open({url:'javascript:evil()'}); await flush(); assert.equal(f.prompts.length,2);
  f.contents.open({url:`${origin}/api/knowledge/sources/1/content`}); assert.deepEqual(f.contents.downloads,[`${origin}/api/knowledge/sources/1/content`]);
  f.contents.open({url:`${origin}/api/settings`}); assert.equal(f.contents.downloads.length,1);
});
test('external redirects never open OS browser; navigation stays inside app pending confirmation', async () => {
  const f = fixture(); const event={count:0,preventDefault(){this.count++;}};
  f.contents.emit('will-redirect',event,'https://example.com'); await flush(); assert.equal(f.prompts.length,0);
  f.answers.push(1); f.contents.emit('will-navigate',event,'https://example.com'); await flush();
  assert.equal(event.count,2); assert.deepEqual(f.opened,['https://example.com/']);
});
test('CSP permits packaged WASM, denies JS eval, remote executable scripts, frames and broad data network', () => {
  assert.ok(contentSecurityPolicy.includes("script-src 'self' 'wasm-unsafe-eval';"));
  assert.ok(!contentSecurityPolicy.includes("'unsafe-eval'"));
  assert.ok(!contentSecurityPolicy.includes('https:;')); assert.ok(!contentSecurityPolicy.includes('*'));
  assert.ok(contentSecurityPolicy.includes("frame-src 'none'"));
});

test('main-process original download uses a bounded single-use ticket, invalidated by reload', () => {
  const f = fixture(); const url = `${origin}/api/knowledge/sources/12/content`;
  f.contents.open({url}); f.answers.push('/chosen/source.txt');
  const item = {getURLChain:()=>[url],getInitiatorOrigin:()=>'',getFilename:()=> 'source.txt',setSavePath:p=>item.path=p};
  const event={prevented:false,preventDefault(){this.prevented=true;}};
  f.session.emit('will-download',event,item,f.contents,null);
  assert.equal(event.prevented,false);assert.equal(item.path,'/chosen/source.txt');
  f.session.emit('will-download',event,item,f.contents,null);assert.equal(event.prevented,true);
  f.contents.open({url});f.contents.emit('did-start-navigation',{},origin,false,true);
  event.prevented=false;f.session.emit('will-download',event,item,f.contents,null);assert.equal(event.prevented,true);
});

test('clipboard text writes require per-request confirmation and never grant clipboard reads', async () => {
  const f=fixture();
  assert.equal(await f.request('clipboard-sanitized-write'), false);
  f.answers.push(1); assert.equal(await f.request('clipboard-sanitized-write'),true);
  assert.equal(f.session.check(f.contents,'clipboard-sanitized-write',origin,f.details),false);
  assert.equal(await f.request('clipboard-sanitized-write'),false);
  assert.equal(await f.request('clipboard-sanitized-write',{isMainFrame:false}),false);
  assert.equal(await f.request('clipboard-read'),false);
  assert.equal(await f.request('deprecated-sync-clipboard-read'),false);
});

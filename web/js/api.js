// Browser HTTP, Tauri IPC, or the original Windows pywebview bridge.
export const isBrowser = ['http:', 'https:'].includes(location.protocol)
  && location.hostname !== 'nai-style-lab.invalid' && !window.__TAURI__;
const ready = isBrowser || window.__TAURI__?.core?.invoke || window.pywebview?.api?.call ? Promise.resolve()
  : new Promise((resolve) => window.addEventListener('pywebviewready', resolve, { once: true }));
let pickedArchive = null;

async function readReply(response) {
  if (response.status === 401) {
    location.replace('/login' + location.hash);
    throw new Error('로그인이 필요합니다.');
  }
  const payload = await response.json();
  if (!payload?.ok) throw new Error(payload?.error || `요청에 실패했습니다 (${response.status}).`);
  return payload.data;
}

function pickArchive() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.zip,application/zip';
    input.hidden = true; document.body.append(input);
    const finish = (file) => { pickedArchive = file || null; input.remove(); resolve(file ? { name: file.name } : null); };
    input.addEventListener('change', () => finish(input.files[0]), { once: true });
    input.addEventListener('cancel', () => finish(null), { once: true });
    input.click();
  });
}

async function call(method, path, body = null) {
  // These browser actions must run within the original click gesture.
  if (isBrowser && method === 'POST') {
    if (path === '/api/data/pick') return pickArchive();
    if (path === '/api/open-novelai') { window.open('https://novelai.net/image', '_blank', 'noopener'); return null; }
    if (path === '/api/data/export') {
      const response = await fetch('/api/data/export', { credentials: 'same-origin' });
      if (!response.ok) return readReply(response);
      const url = URL.createObjectURL(await response.blob());
      const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition'))?.[1] || 'nai-style-lab.zip';
      const link = document.createElement('a'); link.href = url; link.download = name;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      return { path: name };
    }
    if (path === '/api/data/import') {
      const file = pickedArchive; pickedArchive = null;
      if (!file) throw new Error('불러올 ZIP 파일을 먼저 고르세요.');
      if (file.size > 256 * 1024 * 1024) throw new Error('불러올 파일은 최대 256 MiB입니다.');
      return readReply(await fetch(path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/zip' }, body: file }));
    }
  }
  await ready;
  if (isBrowser) {
    return readReply(await fetch(path, { method, credentials: 'same-origin',
      headers: body === null ? {} : { 'Content-Type': 'application/json' },
      body: body === null ? undefined : JSON.stringify(body) }));
  }
  let payload;
  try {
    payload = window.__TAURI__?.core?.invoke
      ? await window.__TAURI__.core.invoke('call', { method, path, body })
      : JSON.parse(await window.pywebview.api.call(method, path, body));
  } catch (error) {
    throw new Error(`앱 내부 오류가 났습니다: ${error?.message || error}`);
  }
  if (!payload?.ok) throw new Error(payload?.error || '요청에 실패했습니다.');
  return payload.data;
}

export const get = (path) => call('GET', path);
export const post = (path, body = {}) => call('POST', path, body);

let draftSeq = 0;
const clientId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
let latestDraft = null;
export function keepDraft(changes) {
  latestDraft = { seq: ++draftSeq, changes, client_id: clientId };
  post('/api/settings/draft', latestDraft).catch(() => {});
}
// Persist the last typing event even when the mobile browser closes or suspends the tab.
if (isBrowser) window.addEventListener('pagehide', () => {
  if (latestDraft) navigator.sendBeacon('/api/settings/draft', new Blob([JSON.stringify(latestDraft)], { type: 'application/json' }));
});

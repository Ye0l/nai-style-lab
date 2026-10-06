// Tauri IPC on Linux, pywebview on the original Windows launcher. No listening server.
// Both return the same {ok, data} / {ok: false, error} envelope from app/bridge.py.
const ready = window.__TAURI__?.core?.invoke || window.pywebview?.api?.call ? Promise.resolve()
  : new Promise((resolve) => window.addEventListener('pywebviewready', resolve, { once: true }));

async function call(method, path, body = null) {
  await ready;
  let payload = null;
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

// Settings typed but not saved yet: Python keeps the newest set and saves it if the window closes first.
let draftSeq = 0;
export function keepDraft(changes) {
  post('/api/settings/draft', { seq: ++draftSeq, changes }).catch(() => {});
}

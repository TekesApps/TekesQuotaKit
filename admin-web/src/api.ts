// Mount prefix in front of /admin, e.g. '/user-quota' behind a reverse proxy; '' when served at /admin.
export const basePath = location.pathname.replace(/\/admin\/?$/, '').replace(/\/$/, '');

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

const headers = (json: boolean): Record<string, string> => ({
  'X-Admin-Request': '1',
  'X-Admin-Base': basePath,
  ...(json ? { 'Content-Type': 'application/json' } : {}),
});

function errorMessage(status: number, body: unknown): string {
  const value = body as { message?: string; detail?: unknown; code?: string } | null;
  if (value?.message) return value.code ? `${value.message}（${value.code}）` : value.message;
  if (typeof value?.detail === 'string') return value.detail;
  if (Array.isArray(value?.detail)) return '参数格式或长度不正确，请检查输入';
  return status >= 500 ? '服务响应异常，请稍后重试' : '请求失败，请重试';
}

async function send(path: string, method: string, body?: unknown): Promise<Response> {
  const response = await fetch(`${basePath}/v1/admin${path}`, {
    method, credentials: 'same-origin', cache: 'no-store', headers: headers(body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    if (response.status === 401 && !path.startsWith('/session')) window.dispatchEvent(new Event('admin-session-expired'));
    throw new ApiError(response.status, errorMessage(response.status, result));
  }
  return response;
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  return (await send(path, method, body)).json() as Promise<T>;
}

export async function download(path: string, body: unknown, fallbackName: string): Promise<void> {
  const response = await send(path, 'POST', body);
  const disposition = response.headers.get('Content-Disposition') || '';
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] || fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const enc = encodeURIComponent;

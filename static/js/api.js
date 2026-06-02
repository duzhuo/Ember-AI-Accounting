/**
 * API call wrapper with authentication and error handling
 */

let authToken = null;
let onAuthError = null;

// ── Token Management ─────────────────────────────────────────────────────────

export function setAuthToken(token) {
    authToken = token;
}

export function getAuthToken() {
    return authToken;
}

export function setAuthErrorHandler(handler) {
    onAuthError = handler;
}

// ── Fetch Wrapper ────────────────────────────────────────────────────────────

export async function apiFetch(url, options = {}) {
    const headers = options.headers || {};

    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }

    if (!headers['Content-Type'] && options.body && !(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    const resp = await fetch(url, { ...options, headers });

    if (resp.status === 401) {
        if (onAuthError) onAuthError();
        throw new Error('登录已过期，请重新登录');
    }

    if (!resp.ok && !resp.headers.get('content-type')?.includes('text/event-stream')) {
        const errorData = await resp.json().catch(() => ({}));
        const errorMsg = errorData.error || errorData.detail;
        throw new Error(typeof errorMsg === 'string' ? errorMsg : `请求失败: ${resp.status}`);
    }

    return resp;
}

// ── SSE Consumer ─────────────────────────────────────────────────────────────

export async function consumeSSE(resp, { onProgress, onResult, onError, signal } = {}) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
        while (true) {
            if (signal?.aborted) {
                reader.cancel();
                throw new DOMException('Aborted', 'AbortError');
            }

            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;

                try {
                    const data = JSON.parse(line.slice(6));

                    switch (data.type) {
                        case 'progress':
                            onProgress?.(data.text);
                            break;
                        case 'result':
                            onResult?.(data);
                            break;
                        case 'error': {
                            const errMsg = typeof data.reply === 'string' ? data.reply : '处理失败';
                            onError?.(errMsg);
                            return; // Stop processing after error
                        }
                    }
                } catch {
                    // Ignore partial JSON
                }
            }
        }
    } catch (err) {
        reader.cancel().catch(() => {});
        throw err;
    }
}

// ── Convenience Methods ──────────────────────────────────────────────────────

export async function apiGet(url) {
    const resp = await apiFetch(url);
    return resp.json();
}

export async function apiPost(url, data) {
    const resp = await apiFetch(url, {
        method: 'POST',
        body: JSON.stringify(data),
    });
    return resp.json();
}

export async function apiPut(url, data) {
    const resp = await apiFetch(url, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
    return resp.json();
}

export async function apiDelete(url) {
    const resp = await apiFetch(url, { method: 'DELETE' });
    return resp.json();
}

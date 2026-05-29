'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let authToken   = localStorage.getItem('ember_token') || null;
let currentUser = null;
let currentVoucher = null;
let currentVoucherId = null;
let listFilter  = 'all';
let _uploadAbort = null;   // AbortController for active upload

// ── API helper ────────────────────────────────────────────────────────────────
async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }
    const resp = await fetch(path, { ...options, headers });
    if (resp.status === 401) { handleLogout(); throw new Error('未登录'); }
    return resp;
}

// ── SSE consumer ─────────────────────────────────────────────────────────────
async function consumeSSE(resp, { onProgress, onResult, onError, signal } = {}) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
        while (true) {
            if (signal?.aborted) { reader.cancel(); throw new DOMException('Aborted', 'AbortError'); }
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.type === 'progress') onProgress?.(data.text);
                    else if (data.type === 'result') onResult?.(data);
                    else if (data.type === 'error')  onError?.(data.reply || '处理失败');
                } catch { /* ignore partial */ }
            }
        }
    } catch (err) {
        reader.cancel().catch(() => {});
        throw err;
    }
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg, type = '') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast' + (type ? ' ' + type : '');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
}

// ── Screen navigation ─────────────────────────────────────────────────────────
function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    window.scrollTo(0, 0);
}

// ── Auth ──────────────────────────────────────────────────────────────────────
async function checkAuth() {
    if (!authToken) { showScreen('screen-login'); return; }
    try {
        const resp = await api('/api/auth/me');
        const data = await resp.json();
        if (data.user) {
            currentUser = data.user;
            document.getElementById('nav-user-home').textContent = data.user.display_name || data.user.username;
            showScreen('screen-home');
            loadRecentVouchers();
        } else {
            handleLogout();
        }
    } catch {
        handleLogout();
    }
}

function handleLogout() {
    authToken = null;
    currentUser = null;
    localStorage.removeItem('ember_token');
    showScreen('screen-login');
}

document.getElementById('login-form').addEventListener('submit', async e => {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    const errEl = document.getElementById('login-error');
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    errEl.textContent = '';
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    try {
        const resp = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const data = await resp.json();
        if (!resp.ok) { errEl.textContent = data.error || '登录失败'; return; }
        authToken = data.token;
        currentUser = data.user;
        localStorage.setItem('ember_token', authToken);
        document.getElementById('nav-user-home').textContent = data.user.display_name || data.user.username;
        showScreen('screen-home');
        loadRecentVouchers();
    } catch {
        errEl.textContent = '网络错误，请重试';
    } finally {
        btn.disabled = false;
        btn.textContent = '登录';
    }
});

document.getElementById('logout-btn').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
    handleLogout();
});

// ── Home screen ───────────────────────────────────────────────────────────────
document.getElementById('action-upload').addEventListener('click', () => showScreen('screen-upload'));
document.getElementById('action-list').addEventListener('click',   () => { showScreen('screen-list'); loadVoucherList(); });

async function loadRecentVouchers() {
    try {
        const resp = await api('/api/vouchers?limit=5&offset=0');
        const data = await resp.json();
        renderRecentList(data.vouchers || []);
    } catch { /* ignore */ }
}

function renderRecentList(vouchers) {
    const el = document.getElementById('recent-list');
    if (!vouchers.length) {
        el.innerHTML = '<p style="font-size:0.82rem;color:var(--text-secondary);text-align:center;padding:1rem 0">暂无凭证记录</p>';
        return;
    }
    el.innerHTML = vouchers.map(v => {
        const statusMap = { draft: ['badge-draft','草稿'], posted: ['badge-posted','已过账'], reversed: ['badge-reversed','已冲销'] };
        const [cls, label] = statusMap[v.status] || ['badge-draft', v.status];
        const icon = v.status === 'posted' ? '✅' : v.status === 'reversed' ? '↩️' : '📄';
        const iconBg = v.status === 'posted' ? 'green' : v.status === 'reversed' ? 'amber' : 'blue';
        return `
        <div class="recent-item" onclick="openVoucherDetail('${v.voucher_id}')">
            <div class="ri-icon ac-icon ${iconBg}">${icon}</div>
            <div class="ri-info">
                <div class="ri-id">${v.voucher_id}</div>
                <div class="ri-desc">${v.header_text || v.reference || '—'}</div>
            </div>
            <span class="badge ${cls}">${label}</span>
        </div>`;
    }).join('');
}

// ── Upload screen ─────────────────────────────────────────────────────────────
document.getElementById('upload-nav-back').addEventListener('click', () => showScreen('screen-home'));

// Camera capture (mobile)
document.getElementById('btn-camera').addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.capture = 'environment';
    input.onchange = e => { if (e.target.files[0]) handleFiles(e.target.files); };
    input.click();
});

// Album / file picker
document.getElementById('btn-album').addEventListener('click', () => {
    document.getElementById('file-input').click();
});
document.getElementById('file-input').addEventListener('change', e => {
    if (e.target.files.length) handleFiles(e.target.files);
    e.target.value = '';
});

// Drag & drop on the upload zone
const uploadZone = document.getElementById('upload-zone');
uploadZone.addEventListener('click', () => document.getElementById('file-input').click());
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
});

function handleFiles(files) {
    if (files.length === 1) {
        doUpload(files[0]);
    } else if (files.length > 1) {
        doBatchUpload(Array.from(files));
    }
}

async function doBatchUpload(files) {
    const progressCard = document.getElementById('progress-card');
    const progressText = document.getElementById('progress-text');
    const progressBar  = document.getElementById('progress-bar');
    const abortBtn     = document.getElementById('btn-abort');
    const batchResult  = document.getElementById('batch-result');
    const batchSummary = document.getElementById('batch-summary');
    const batchErrors  = document.getElementById('batch-errors');

    // Reset result card
    batchResult.style.display = 'none';

    const collectedIds = [];
    const errors = [];

    _uploadAbort = new AbortController();
    const { signal } = _uploadAbort;

    progressCard.classList.add('active');
    abortBtn.style.display = 'flex';

    for (let i = 0; i < files.length; i++) {
        if (signal.aborted) break;

        const file = files[i];
        progressText.textContent = `第 ${i + 1}/${files.length} 个：${file.name}`;
        progressBar.style.width = `${Math.round((i / files.length) * 80) + 10}%`;

        const maxBytes = 20 * 1024 * 1024;
        if (file.size > maxBytes) {
            errors.push(`${file.name}：超过 20MB`);
            continue;
        }

        const formData = new FormData();
        formData.append('file', file);

        try {
            const resp = await api('/api/upload', { method: 'POST', body: formData, signal });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.reply || err.error || '上传失败');
            }
            let fileResult = null;
            await consumeSSE(resp, {
                onProgress: () => {},
                onResult: data => { fileResult = data; },
                onError: msg => { throw new Error(msg); },
                signal,
            });
            // Extract voucher_id from result
            const vid = fileResult?.voucher?.voucher_id
                || fileResult?.vouchers?.[0]?.voucher_id
                || fileResult?.file?.voucher_id;
            if (vid) {
                collectedIds.push(vid);
            } else {
                errors.push(`${file.name}：未生成凭证`);
            }
        } catch (err) {
            if (err.name === 'AbortError') break;
            errors.push(`${file.name}：${err.message || '处理失败'}`);
        }
    }

    progressCard.classList.remove('active');
    progressBar.style.width = '0%';
    abortBtn.style.display = 'none';
    _uploadAbort = null;

    // Show result card
    const aborted = signal.aborted;
    const successCount = collectedIds.length;
    const totalProcessed = successCount + errors.length;

    batchSummary.textContent = aborted
        ? `已取消。已处理 ${totalProcessed} 个文件，生成 ${successCount} 张凭证草稿`
        : `共处理 ${files.length} 个文件，生成 ${successCount} 张凭证草稿`;

    batchErrors.innerHTML = errors.length
        ? errors.map(e => `<div class="batch-error-item">⚠ ${e}</div>`).join('')
        : '';

    const downloadBtn = document.getElementById('btn-download-csv');
    if (collectedIds.length > 0) {
        downloadBtn.style.display = 'block';
        downloadBtn.onclick = async () => {
            downloadBtn.disabled = true;
            downloadBtn.textContent = '准备中…';
            try {
                const resp = await api(`/api/export/csv?ids=${collectedIds.join(',')}`);
                if (!resp.ok) throw new Error('导出失败');
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'sap_export.csv';
                a.click();
                URL.revokeObjectURL(url);
                downloadBtn.textContent = '⬇ 下载 SAP CSV';
            } catch (err) {
                showToast(err.message || '导出失败', 'error');
                downloadBtn.textContent = '⬇ 下载 SAP CSV';
            } finally {
                downloadBtn.disabled = false;
            }
        };
    } else {
        downloadBtn.style.display = 'none';
    }

    batchResult.style.display = 'block';
}

document.getElementById('btn-batch-reset').addEventListener('click', () => {
    document.getElementById('batch-result').style.display = 'none';
    document.getElementById('progress-bar').style.width = '0%';
});

async function doUpload(file) {
    const progressCard = document.getElementById('progress-card');
    const progressText = document.getElementById('progress-text');
    const progressBar  = document.getElementById('progress-bar');
    const abortBtn     = document.getElementById('btn-abort');

    // Hide batch result if visible
    document.getElementById('batch-result').style.display = 'none';

    const maxBytes = 20 * 1024 * 1024;
    if (file.size > maxBytes) { showToast('文件超过 20MB 限制', 'error'); return; }

    // Set up AbortController
    _uploadAbort = new AbortController();
    const { signal } = _uploadAbort;

    progressCard.classList.add('active');
    progressText.textContent = '正在上传…';
    progressBar.style.width = '10%';
    abortBtn.style.display = 'flex';

    const formData = new FormData();
    formData.append('file', file);

    let result = null;
    try {
        const resp = await api('/api/upload', { method: 'POST', body: formData, signal });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.reply || err.error || '上传失败');
        }
        progressBar.style.width = '40%';
        await consumeSSE(resp, {
            onProgress: text => {
                progressText.textContent = text;
                progressBar.style.width = '70%';
            },
            onResult: data => { result = data; },
            onError:  msg  => { throw new Error(msg); },
            signal,
        });
    } catch (err) {
        progressCard.classList.remove('active');
        progressBar.style.width = '0%';
        abortBtn.style.display = 'none';
        _uploadAbort = null;
        if (err.name === 'AbortError') {
            showToast('已取消', '');
        } else {
            showToast(err.message || '上传失败', 'error');
        }
        return;
    }

    progressCard.classList.remove('active');
    progressBar.style.width = '0%';
    abortBtn.style.display = 'none';
    _uploadAbort = null;

    if (!result) {
        showToast('未能识别有效凭证信息', 'error');
        return;
    }

    setTimeout(() => {
        if (result.voucher) {
            currentVoucher   = result.voucher;
            currentVoucherId = result.voucher.voucher_id || result.voucher_id;
            renderVoucherDetail(result.voucher);
            showScreen('screen-voucher');
        } else {
            showToast(result.reply || '处理完成', 'success');
        }
    }, 400);
}

document.getElementById('btn-abort').addEventListener('click', () => {
    if (_uploadAbort) {
        _uploadAbort.abort();
    }
});

// ── Voucher detail screen ─────────────────────────────────────────────────────
document.getElementById('voucher-nav-back').addEventListener('click', () => {
    showScreen('screen-home');
    loadRecentVouchers();
});

async function openVoucherDetail(voucherId) {
    try {
        const resp = await api(`/api/vouchers/${voucherId}`);
        const data = await resp.json();
        if (data.voucher) {
            currentVoucher   = data.voucher;
            currentVoucherId = voucherId;
            renderVoucherDetail(data.voucher);
            showScreen('screen-voucher');
        }
    } catch { showToast('加载凭证失败', 'error'); }
}

function renderVoucherDetail(v) {
    const statusMap = { draft: ['badge-draft','草稿'], posted: ['badge-posted','已过账'], reversed: ['badge-reversed','已冲销'] };
    const [cls, label] = statusMap[v.status] || ['badge-draft', v.status];

    // Header
    document.getElementById('v-id').textContent     = v.voucher_id || '—';
    document.getElementById('v-date').textContent   = v.document_date || v.posting_date || '—';
    document.getElementById('v-status').className   = 'badge ' + cls;
    document.getElementById('v-status').textContent = label;
    document.getElementById('v-desc').textContent   = v.header_text || v.reference || '—';

    // KV info
    document.getElementById('v-company').textContent  = v.company_code || '—';
    document.getElementById('v-doctype').textContent  = v.document_type || '—';
    document.getElementById('v-ref').textContent      = v.reference || '—';

    // Confidence
    const conf = parseFloat(v.confidence || 0);
    const confDot = document.getElementById('v-conf-dot');
    const confTxt = document.getElementById('v-conf-txt');
    confDot.className = 'confidence-dot ' + (conf >= 0.8 ? 'conf-high' : conf >= 0.5 ? 'conf-medium' : 'conf-low');
    confTxt.textContent = `置信度 ${(conf * 100).toFixed(0)}%`;

    // Warnings
    const warnEl = document.getElementById('v-warnings');
    const warnings = v.warnings || [];
    if (warnings.length) {
        warnEl.textContent = '⚠️ ' + warnings.join('；');
        warnEl.classList.add('active');
    } else {
        warnEl.classList.remove('active');
    }

    // Line items
    const rows = v.rows || [];
    const linesEl = document.getElementById('v-lines');
    linesEl.innerHTML = rows.map(r => {
        const isDebit = (r.dc === 'S' || r.debit > 0);
        const amount  = r.debit || r.credit || 0;
        return `
        <div class="line-item">
            <div class="li-account">${r.account_code || ''} ${r.account_name || ''}</div>
            <div class="li-sub">${r.text || r.assignment || ''}</div>
            <div class="li-amount-row">
                <span class="${isDebit ? 'li-debit' : 'li-credit'}">
                    ${isDebit ? '借' : '贷'} ¥${Number(amount).toLocaleString('zh-CN', {minimumFractionDigits:2})}
                </span>
                <span class="li-dc-badge">${r.tax_code || r.dc || ''}</span>
            </div>
        </div>`;
    }).join('');

    // Action bar
    const confirmBtn = document.getElementById('btn-confirm');
    const alreadyDone = v.status === 'posted' || v.status === 'reversed';
    confirmBtn.disabled = alreadyDone;
    confirmBtn.style.display = alreadyDone ? 'none' : 'flex';
    document.getElementById('btn-confirmed-label').style.display = alreadyDone ? 'flex' : 'none';
}

document.getElementById('btn-confirm').addEventListener('click', async () => {
    if (!currentVoucherId) return;
    const btn = document.getElementById('btn-confirm');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 过账中…';
    try {
        const resp = await api('/api/confirm', {
            method: 'POST',
            body: JSON.stringify({ voucher_id: currentVoucherId }),
        });
        const data = await resp.json();
        if (data.status === 'posted' || data.status === 'already_posted') {
            showToast('过账成功 ✓', 'success');
            // Refresh detail
            await openVoucherDetail(currentVoucherId);
        } else {
            throw new Error(data.message || data.error || '过账失败');
        }
    } catch (err) {
        showToast(err.message, 'error');
        btn.disabled = false;
        btn.innerHTML = '✓ 确认过账';
    }
});

// ── Voucher list screen ───────────────────────────────────────────────────────
document.getElementById('list-nav-back').addEventListener('click', () => showScreen('screen-home'));

document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        listFilter = tab.dataset.filter;
        loadVoucherList();
    });
});

async function loadVoucherList() {
    const listEl = document.getElementById('voucher-list');
    const emptyEl = document.getElementById('list-empty');
    listEl.innerHTML = '<p style="text-align:center;padding:2rem;color:var(--text-secondary);font-size:0.85rem">加载中…</p>';
    emptyEl.classList.remove('active');

    try {
        const status = listFilter === 'all' ? '' : `&status=${listFilter}`;
        const resp = await api(`/api/vouchers?limit=50&offset=0${status}`);
        const data = await resp.json();
        const vouchers = data.vouchers || [];

        if (!vouchers.length) {
            listEl.innerHTML = '';
            emptyEl.classList.add('active');
            return;
        }

        const statusMap = {
            draft:    ['badge-draft','草稿','📄','blue'],
            posted:   ['badge-posted','已过账','✅','green'],
            reversed: ['badge-reversed','已冲销','↩️','amber'],
        };

        listEl.innerHTML = vouchers.map(v => {
            const [cls, label, icon, iconCls] = statusMap[v.status] || ['badge-draft','未知','📄','blue'];
            return `
            <div class="recent-item" onclick="openVoucherDetail('${v.voucher_id}')">
                <div class="ri-icon ac-icon ${iconCls}">${icon}</div>
                <div class="ri-info">
                    <div class="ri-id">${v.voucher_id}</div>
                    <div class="ri-desc">${v.header_text || v.reference || '—'}</div>
                </div>
                <span class="badge ${cls}">${label}</span>
            </div>`;
        }).join('');
    } catch {
        listEl.innerHTML = '<p style="text-align:center;padding:2rem;color:var(--accent-danger);font-size:0.85rem">加载失败，请重试</p>';
    }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
checkAuth();

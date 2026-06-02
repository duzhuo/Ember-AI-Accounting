/**
 * A2UI Action handlers
 * Handles client-side and server-side actions
 */

import { apiFetch } from '../api.js';
import { appStore } from '../state.js';
import { bus, EVENTS } from '../event-bus.js';
import { showToast } from '../common.js';

// ── Action Handler Registry ──────────────────────────────────────────────────

const actionHandlers = new Map();

/**
 * Register an action handler
 * @param {string} eventName - Event name
 * @param {Function} handler - Handler function(eventData, extraData, btnEl)
 */
export function registerActionHandler(eventName, handler) {
    actionHandlers.set(eventName, handler);
}

/**
 * Get an action handler
 * @param {string} eventName - Event name
 * @returns {Function|null} Handler function
 */
export function getActionHandler(eventName) {
    return actionHandlers.get(eventName);
}

// ── Main Action Dispatcher ───────────────────────────────────────────────────

/**
 * Handle an A2UI action
 * @param {Object} action - Action configuration
 * @param {Object} extraData - Additional data
 * @param {HTMLElement} btnEl - Button element
 */
export async function handleAction(action, extraData = {}, btnEl = null) {
    if (!action?.event) return;

    const eventName = action.event.name;
    const eventData = { ...(action.event.data || {}), ...extraData };

    // Check for registered handler
    const handler = actionHandlers.get(eventName);
    if (handler) {
        try {
            await handler(eventData, extraData, btnEl);
        } catch (err) {
            console.error(`Action handler error for "${eventName}":`, err);
            showToast('操作失败: ' + err.message, 'error');
        }
        return;
    }

    // Default: send to server
    await sendToServer(eventName, eventData, btnEl);
}

// ── Server Action Handler ────────────────────────────────────────────────────

/**
 * Send action to server
 * @param {string} eventName - Event name
 * @param {Object} eventData - Event data
 * @param {HTMLElement} btnEl - Button element
 */
async function sendToServer(eventName, eventData, btnEl = null) {
    const payload = {
        event: eventName,
        data: eventData,
    };

    // Disable button
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.dataset.origText = btnEl.textContent;
        btnEl.textContent = '处理中...';
    }

    try {
        const resp = await apiFetch('/api/a2ui-action', {
            method: 'POST',
            body: JSON.stringify(payload),
        });

        const data = await resp.json();

        // Handle file picker request
        if (data.status === 'open_file_picker') {
            openFilePicker(data.accept, data.max_size);
            return;
        }

        // Handle redirect
        if (data.redirect) {
            window.location.href = data.redirect;
            return;
        }

        // Handle toast
        if (data.toast) {
            showToast(data.toast.message, data.toast.type);
        }

        // Handle A2UI response
        if (data.a2ui) {
            bus.emit(EVENTS.VIEW_SWITCH, { a2ui: data.a2ui });
        }
    } catch (err) {
        console.error('Server action error:', err);
        showToast('操作失败: ' + err.message, 'error');
    } finally {
        // Re-enable button
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = btnEl.dataset.origText || '操作';
        }
    }
}

// ── File Picker ──────────────────────────────────────────────────────────────

function openFilePicker(accept, maxSize) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept || '*/*';
    input.multiple = true;

    input.addEventListener('change', async () => {
        const files = Array.from(input.files);
        if (files.length === 0) return;

        // Check file size
        if (maxSize) {
            const oversized = files.filter((f) => f.size > maxSize);
            if (oversized.length > 0) {
                showToast(`文件大小超过限制 (${Math.round(maxSize / 1024 / 1024)}MB)`, 'error');
                return;
            }
        }

        // Upload files
        bus.emit(EVENTS.LOADING_START);
        try {
            for (const file of files) {
                const formData = new FormData();
                formData.append('file', file);

                const resp = await apiFetch('/api/upload', {
                    method: 'POST',
                    body: formData,
                });

                const data = await resp.json();
                if (data.toast) {
                    showToast(data.toast.message, data.toast.type);
                }
            }
        } catch (err) {
            showToast('上传失败: ' + err.message, 'error');
        } finally {
            bus.emit(EVENTS.LOADING_END);
        }
    });

    input.click();
}

// ── Built-in Action Handlers ─────────────────────────────────────────────────

// Navigation
registerActionHandler('back_to_voucher_list', () => {
    bus.emit(EVENTS.VIEW_SWITCH, { view: 'voucher_list' });
});

registerActionHandler('edit_voucher', (data) => {
    const voucherId = data.voucherId;
    if (voucherId) {
        bus.emit(EVENTS.VOUCHER_LOAD, { voucherId, mode: 'edit' });
    }
});

// User management
registerActionHandler('user_edit', (data) => {
    bus.emit(EVENTS.USER_UPDATE, { userId: data.user_id });
});

registerActionHandler('user_reset_password', (data) => {
    bus.emit(EVENTS.USER_RESET_PASSWORD, { userId: data.user_id, username: data.username });
});

registerActionHandler('user_delete', (data) => {
    if (confirm(`确定要删除用户「${data.username}」吗？`)) {
        bus.emit(EVENTS.USER_DELETE, { userId: data.user_id });
    }
});

// Voucher actions
registerActionHandler('confirm_voucher', async (data) => {
    showToast('正在过账...', 'info');
    await sendToServer('confirm_voucher', data);
});

registerActionHandler('batch_post_vouchers', async (data) => {
    const selectedIds = data.voucherIds || [];
    if (selectedIds.length === 0) {
        showToast('请先勾选要过账的凭证', 'error');
        return;
    }
    showToast(`正在批量过账 ${selectedIds.length} 个凭证...`, 'info');
    await sendToServer('batch_post_vouchers', data);
});

registerActionHandler('reverse_voucher', async (data) => {
    const reason = prompt('请输入冲销原因：');
    if (!reason) return;
    data.reason = reason;
    showToast('正在冲销凭证...', 'info');
    await sendToServer('reverse_voucher', data);
});

registerActionHandler('export_voucher_pdf', async (data) => {
    const voucherId = data.voucherId;
    if (!voucherId) return;

    showToast(`正在生成凭证 ${voucherId} 的 PDF...`, 'info');

    try {
        const resp = await apiFetch(`/api/vouchers/${voucherId}/pdf`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `voucher_${voucherId}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('PDF 已下载', 'success');
    } catch (err) {
        showToast('PDF 下载失败: ' + err.message, 'error');
    }
});

// Search
registerActionHandler('search_vouchers', async (data) => {
    await sendToServer('search_vouchers', data);
});

// File operations
registerActionHandler('open_file_picker', (data) => {
    openFilePicker(data.accept, data.max_size);
});

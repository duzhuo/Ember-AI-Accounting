/**
 * Common utility functions shared between desktop and mobile
 */

// ── HTML Escaping ────────────────────────────────────────────────────────────

export function escHtml(str) {
    if (!str) return '';
    if (typeof str !== 'string') {
        str = String(str);
    }
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Time Formatting ──────────────────────────────────────────────────────────

export function formatTime(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

export function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString('zh-CN');
}

// ── Status Mapping ───────────────────────────────────────────────────────────

export const STATUS_MAP = {
    draft: { label: '草稿', class: 'badge-draft', icon: '📄', color: 'blue' },
    pending_approval: { label: '待审批', class: 'badge-pending', icon: '⏳', color: 'amber' },
    posted: { label: '已过账', class: 'badge-posted', icon: '✅', color: 'green' },
    reversed: { label: '已冲销', class: 'badge-reversed', icon: '↩️', color: 'amber' },
};

export function getStatusInfo(status) {
    return STATUS_MAP[status] || STATUS_MAP.draft;
}

// ── Role Mapping ─────────────────────────────────────────────────────────────

export const ROLE_MAP = {
    admin: { label: '管理员', class: 'role-admin' },
    reviewer: { label: '复核人', class: 'role-reviewer' },
    user: { label: '普通用户', class: 'role-user' },
};

export function getRoleInfo(role) {
    return ROLE_MAP[role] || ROLE_MAP.user;
}

// ── Toast Notification ───────────────────────────────────────────────────────

let toastTimeout = null;

export function showToast(msg, type = '') {
    const toast = document.getElementById('toast');
    if (!toast) return;

    toast.textContent = msg;
    toast.className = 'toast' + (type ? ' ' + type : '');
    toast.classList.add('show');

    if (toastTimeout) clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => toast.classList.remove('show'), 2500);
}

// ── Number Formatting ────────────────────────────────────────────────────────

export function formatAmount(amount) {
    return Number(amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 });
}

export function formatPercent(value) {
    return (parseFloat(value) * 100).toFixed(0) + '%';
}

// ── Debounce ─────────────────────────────────────────────────────────────────

export function debounce(fn, delay = 300) {
    let timer = null;
    return function (...args) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

// ── UUID Generation ──────────────────────────────────────────────────────────

export function generateId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

// ── Safe JSON Parse ──────────────────────────────────────────────────────────

export function safeJsonParse(str, fallback = null) {
    try {
        return JSON.parse(str);
    } catch {
        return fallback;
    }
}

// ── Delay ────────────────────────────────────────────────────────────────────

export function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

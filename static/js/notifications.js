/**
 * Notification system module
 */

import { apiFetch, apiGet, apiPost, apiDelete } from './api.js';
import { appStore } from './state.js';
import { formatTime, escHtml } from './common.js';
import { icon } from './icons.js';

// ── Notification Polling ─────────────────────────────────────────────────────

let pollTimer = null;
const POLL_INTERVAL = 30000; // 30 seconds

/**
 * Start notification polling
 */
export function startNotificationPolling() {
    stopNotificationPolling();
    updateNotificationBadge();
    pollTimer = setInterval(updateNotificationBadge, POLL_INTERVAL);
}

/**
 * Stop notification polling
 */
export function stopNotificationPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

/**
 * Update notification badge count
 */
export async function updateNotificationBadge() {
    try {
        const data = await apiGet('/api/notifications/unread-count');
        const count = data.count || 0;
        appStore.set('notificationCount', count);
    } catch {
        // Silently fail
    }
}

// ── Notification API ─────────────────────────────────────────────────────────

/**
 * Get notification list
 * @param {Object} options
 * @param {boolean} options.unreadOnly - Only get unread notifications
 * @param {number} options.limit - Max notifications to return
 * @returns {Promise<Array>} Notification list
 */
export async function getNotifications({ unreadOnly = false, limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (unreadOnly) params.set('unread_only', 'true');
    if (limit) params.set('limit', limit.toString());

    const data = await apiGet(`/api/notifications?${params}`);
    return data.notifications || [];
}

/**
 * Mark a notification as read
 * @param {string} notificationId
 * @returns {Promise<void>}
 */
export async function markAsRead(notificationId) {
    await apiPost(`/api/notifications/${notificationId}/read`);
    await updateNotificationBadge();
}

/**
 * Mark all notifications as read
 * @returns {Promise<number>} Number of notifications marked
 */
export async function markAllAsRead() {
    const data = await apiPost('/api/notifications/read-all');
    await updateNotificationBadge();
    return data.marked || 0;
}

/**
 * Delete a notification
 * @param {string} notificationId
 * @returns {Promise<void>}
 */
export async function deleteNotification(notificationId) {
    await apiDelete(`/api/notifications/${notificationId}`);
    await updateNotificationBadge();
}

// ── Notification Types ───────────────────────────────────────────────────────

export const NOTIFICATION_TYPES = {
    approval_request: {
        icon: 'clock',
        iconClass: 'type-approval_request',
        label: '审批请求',
    },
    approval_approved: {
        icon: 'check',
        iconClass: 'type-approval_approved',
        label: '审批通过',
    },
    approval_rejected: {
        icon: 'x',
        iconClass: 'type-approval_rejected',
        label: '审批驳回',
    },
    system: {
        icon: 'mail',
        iconClass: 'type-system',
        label: '系统消息',
    },
};

/**
 * Get notification type info
 * @param {string} type - Notification type
 * @returns {Object} Type info
 */
export function getNotificationTypeInfo(type) {
    return NOTIFICATION_TYPES[type] || NOTIFICATION_TYPES.system;
}

// ── Notification Rendering ───────────────────────────────────────────────────

/**
 * Render a single notification item
 * @param {Object} notification - Notification data
 * @param {Object} options - Rendering options
 * @param {Function} options.onClick - Click handler
 * @param {Function} options.onDelete - Delete handler
 * @returns {HTMLElement} Notification element
 */
export function renderNotificationItem(notification, { onClick, onDelete } = {}) {
    const typeInfo = getNotificationTypeInfo(notification.type);
    const time = formatTime(notification.created_at);

    const item = document.createElement('div');
    item.className = `notification-item${notification.is_read ? '' : ' unread'}`;
    item.dataset.id = notification.id;

    item.innerHTML = `
        <div class="notification-icon ${typeInfo.iconClass}">${icon(typeInfo.icon, 18)}</div>
        <div class="notification-body">
            <div class="notification-title">${escHtml(notification.title)}</div>
            <div class="notification-text">${escHtml(notification.body)}</div>
            <div class="notification-time">${time}</div>
        </div>
        <button class="notification-delete-btn" title="删除">
            ${icon('close', 14)}
        </button>
    `;

    // Click handler
    if (onClick) {
        item.addEventListener('click', (e) => {
            if (e.target.closest('.notification-delete-btn')) return;
            onClick(notification);
        });
    }

    // Delete handler
    const deleteBtn = item.querySelector('.notification-delete-btn');
    if (onDelete) {
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            onDelete(notification);
        });
    }

    return item;
}

/**
 * Render notification list
 * @param {Array} notifications - Notification list
 * @param {Object} options - Rendering options
 * @param {Function} options.onClick - Click handler
 * @param {Function} options.onDelete - Delete handler
 * @returns {HTMLElement} List container
 */
export function renderNotificationList(notifications, options = {}) {
    const container = document.createElement('div');
    container.className = 'notification-list';

    if (notifications.length === 0) {
        container.innerHTML = `
            <div class="notification-empty">
                ${icon('bell', 48)}
                <p>暂无通知</p>
            </div>
        `;
        return container;
    }

    notifications.forEach((notification) => {
        const item = renderNotificationItem(notification, options);
        container.appendChild(item);
    });

    return container;
}

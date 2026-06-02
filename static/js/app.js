/**
 * Main application entry point
 * Uses shared modules for auth, state, events, etc.
 */

import { appStore } from './state.js';
import { bus, EVENTS } from './event-bus.js';
import { icon } from './icons.js';
import { escHtml, formatTime, showToast } from './common.js';
import { apiFetch, apiGet, apiPost, apiDelete, consumeSSE } from './api.js';
import * as auth from './auth.js';
import * as notifications from './notifications.js';
import { viewManager } from './views/view-manager.js';
import { A2UIRenderer } from './a2ui/renderer.js';
import { handleAction as handleA2UIAction } from './a2ui/actions.js';
import * as chat from './chat/chat.js';
import * as voucherView from './views/voucher.js';
import * as rulesView from './views/rules.js';
import * as usersView from './views/users.js';

// ── Application State ────────────────────────────────────────────────────────

let sessionId = null;
let isProcessing = false;

// ── DOM Elements ─────────────────────────────────────────────────────────────

const elements = {};

function cacheElements() {
    elements.loginOverlay = document.getElementById('loginOverlay');
    elements.appContainer = document.getElementById('appContainer');
    elements.chatHistory = document.getElementById('chatHistory');
    elements.userInput = document.getElementById('userInput');
    elements.sendBtn = document.getElementById('sendBtn');
    elements.userDisplayName = document.getElementById('userDisplayName');
    elements.notificationBadge = document.getElementById('notificationBadge');
    elements.toast = document.getElementById('toast');
}

// ── Initialization ───────────────────────────────────────────────────────────

async function init() {
    cacheElements();

    // Initialize view manager
    viewManager.init();

    // Initialize chat module
    chat.init();

    // Setup event listeners
    setupAuthEvents();
    setupChatEvents();
    setupNotificationEvents();
    setupUIEvents();
    setupViewEvents();

    // Check authentication
    const isAuthenticated = await auth.checkAuth();
    if (isAuthenticated) {
        showApp();
    } else {
        showLogin();
    }
}

// ── Auth Event Handlers ──────────────────────────────────────────────────────

function setupAuthEvents() {
    // Login form
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('loginUsername').value.trim();
            const password = document.getElementById('loginPassword').value;

            if (!username || !password) {
                showLoginError('请输入用户名和密码');
                return;
            }

            const submitBtn = loginForm.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            submitBtn.textContent = '登录中...';

            try {
                await auth.login(username, password);
                showApp();
            } catch (err) {
                showLoginError(err.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = '登录';
            }
        });
    }

    // Logout button
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            await auth.logout();
            showLogin();
        });
    }

    // Auth error handler
    bus.on(EVENTS.AUTH_ERROR, () => {
        showLogin();
    });
}

// Chat events are handled by the chat module
function setupChatEvents() {
    // Chat module handles its own event binding
}

// ── Notification Event Handlers ──────────────────────────────────────────────

function setupNotificationEvents() {
    // Notification button
    const notificationBtn = document.getElementById('notificationBtn');
    if (notificationBtn) {
        notificationBtn.addEventListener('click', () => {
            viewManager.switchTo('notifications');
            loadNotifications();
        });
    }

    // Subscribe to notification count changes
    appStore.subscribe('notificationCount', (count) => {
        updateNotificationBadge(count);
    });

    // Start polling after login
    bus.on(EVENTS.AUTH_LOGIN, () => {
        notifications.startNotificationPolling();
    });

    bus.on(EVENTS.AUTH_LOGOUT, () => {
        notifications.stopNotificationPolling();
    });
}

// ── UI Event Handlers ────────────────────────────────────────────────────────

function setupUIEvents() {
    // Password change modal
    const changePasswordBtn = document.getElementById('changePasswordBtn');
    if (changePasswordBtn) {
        changePasswordBtn.addEventListener('click', showPasswordModal);
    }

    // Toast events
    bus.on(EVENTS.TOAST_SHOW, ({ message, type }) => {
        showToast(message, type);
    });
}

function setupViewEvents() {
    // View switch events
    bus.on(EVENTS.VIEW_SWITCH, (data) => {
        const { view, a2ui, refresh } = data;

        // Handle A2UI rendering
        if (a2ui) {
            handleA2UIResponse(a2ui);
            return;
        }

        // Handle view refresh
        if (refresh) {
            refreshCurrentView(view);
            return;
        }

        // Switch view
        if (view) {
            viewManager.switchTo(view);
        }
    });

    // Voucher events
    bus.on(EVENTS.VOUCHER_LOAD, (data) => {
        const { voucherId, voucher, mode } = data;
        if (voucher) {
            voucherView.renderVoucherDetail(voucher);
            viewManager.switchTo('voucher');
        } else if (voucherId) {
            voucherView.loadVoucherDetail(voucherId);
        }
    });

    // User events
    bus.on(EVENTS.USER_UPDATE, (data) => {
        usersView.openEditUserModal(data.userId);
    });

    bus.on(EVENTS.USER_DELETE, async (data) => {
        try {
            await apiDelete(`/api/users/${data.userId}`);
            showToast('用户已删除', 'success');
            refreshCurrentView('user_list');
        } catch (err) {
            showToast('删除失败: ' + err.message, 'error');
        }
    });

    bus.on(EVENTS.USER_RESET_PASSWORD, async (data) => {
        try {
            const result = await apiPost(`/api/users/${data.userId}/reset-password`);
            if (result.new_password) {
                usersView.showResetPasswordResult(data.username, result.new_password);
            }
        } catch (err) {
            showToast('重置密码失败: ' + err.message, 'error');
        }
    });
}

async function refreshCurrentView(view) {
    switch (view) {
        case 'user_list':
            try {
                const data = await apiGet('/api/users');
                usersView.renderUserList(data);
            } catch (err) {
                showToast('刷新失败', 'error');
            }
            break;
        case 'rules':
            // Refresh rules
            break;
        case 'voucher_list':
            // Refresh voucher list
            break;
    }
}

// ── Login/Logout UI ──────────────────────────────────────────────────────────

function showLogin() {
    elements.loginOverlay.style.display = 'flex';
    elements.appContainer.style.display = 'none';
    notifications.stopNotificationPolling();
}

function showApp() {
    elements.loginOverlay.style.display = 'none';
    elements.appContainer.style.display = 'grid';

    const user = appStore.get('currentUser');
    if (user) {
        elements.userDisplayName.textContent = user.display_name || user.username;
        applyRolePermissions(user.role);
    }

    // Load chat history
    chat.loadChatHistory();

    notifications.startNotificationPolling();
    bus.emit(EVENTS.AUTH_LOGIN);
}

function showLoginError(message) {
    const errorEl = document.getElementById('loginError');
    if (errorEl) {
        errorEl.textContent = message;
        errorEl.style.display = 'block';
    }
}

function applyRolePermissions(role) {
    // Apply role-based UI visibility
    const hintCards = document.querySelectorAll('.hint-card');
    hintCards.forEach((card) => {
        const hint = card.dataset.hint || '';
        if (role === 'admin') {
            card.style.display = hint.includes('用户') ? '' : 'none';
        } else if (role === 'reviewer') {
            card.style.display = hint.includes('凭证') || hint.includes('规则') ? '' : 'none';
        } else {
            card.style.display = hint.includes('用户') ? 'none' : '';
        }
    });
}

// Chat functions are now in the chat module

function handleA2UIResponse(a2ui) {
    if (!a2ui?.messages) return;

    const dynamicContainer = document.getElementById('viewDynamic');
    if (!dynamicContainer) return;

    try {
        const renderer = new A2UIRenderer(dynamicContainer);
        renderer.setActionHandler(handleA2UIAction);
        renderer.processMessages(a2ui.messages);

        // Switch to dynamic view
        viewManager.switchTo('dynamic');
    } catch (err) {
        console.error('A2UI render error:', err);
        showToast('界面渲染失败', 'error');
    }
}

// ── Notifications ────────────────────────────────────────────────────────────

async function loadNotifications() {
    const container = document.getElementById('notificationContent');
    if (!container) return;

    const filter = appStore.get('notifFilter') || 'all';
    const notifs = await notifications.getNotifications({
        unreadOnly: filter === 'unread',
    });

    container.innerHTML = '';

    // Toolbar
    const toolbar = document.createElement('div');
    toolbar.className = 'notification-toolbar';
    toolbar.innerHTML = `
        <div class="notification-filters">
            <button class="notification-filter-btn ${filter === 'all' ? 'active' : ''}" data-filter="all">全部</button>
            <button class="notification-filter-btn ${filter === 'unread' ? 'active' : ''}" data-filter="unread">未读</button>
        </div>
        <button class="btn btn-sm btn-secondary" id="markAllReadBtn">全部已读</button>
    `;
    container.appendChild(toolbar);

    // Filter buttons
    toolbar.querySelectorAll('.notification-filter-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            appStore.set('notifFilter', btn.dataset.filter);
            await loadNotifications();
        });
    });

    // Mark all read
    toolbar.querySelector('#markAllReadBtn').addEventListener('click', async () => {
        await notifications.markAllAsRead();
        await loadNotifications();
    });

    // Notification list
    const list = notifications.renderNotificationList(notifs, {
        onClick: async (notif) => {
            if (!notif.is_read) {
                await notifications.markAsRead(notif.id);
            }
            if (notif.target_type === 'voucher' && notif.target_id) {
                if (notif.type === 'approval_request') {
                    // Open approval modal
                } else {
                    // Load voucher
                }
            }
        },
        onDelete: async (notif) => {
            await notifications.deleteNotification(notif.id);
            await loadNotifications();
        },
    });
    container.appendChild(list);
}

function updateNotificationBadge(count) {
    if (elements.notificationBadge) {
        if (count > 0) {
            elements.notificationBadge.textContent = count > 99 ? '99+' : count;
            elements.notificationBadge.style.display = 'flex';
        } else {
            elements.notificationBadge.style.display = 'none';
        }
    }
}

// ── Password Modal ───────────────────────────────────────────────────────────

function showPasswordModal() {
    // Implementation for password change modal
    console.log('Show password modal');
}

// ── Start Application ────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', init);

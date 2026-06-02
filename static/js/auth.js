/**
 * Authentication module
 */

import { apiFetch, setAuthToken as setApiToken } from './api.js';
import { appStore, setCurrentUser, setAuthToken } from './state.js';

// ── Auth State ───────────────────────────────────────────────────────────────

const TOKEN_KEY = 'ember_token';

/**
 * Load token from localStorage
 * @returns {string|null} Stored token
 */
export function loadToken() {
    return localStorage.getItem(TOKEN_KEY);
}

/**
 * Save token to localStorage
 * @param {string} token - Auth token
 */
export function saveToken(token) {
    if (token) {
        localStorage.setItem(TOKEN_KEY, token);
    } else {
        localStorage.removeItem(TOKEN_KEY);
    }
}

/**
 * Clear all auth state
 */
export function clearAuth() {
    localStorage.removeItem(TOKEN_KEY);
    setAuthToken(null);
    setApiToken(null);
    setCurrentUser(null);
}

// ── Auth Actions ─────────────────────────────────────────────────────────────

/**
 * Check if user is authenticated
 * @returns {Promise<boolean>} True if authenticated
 */
export async function checkAuth() {
    const token = loadToken();
    if (!token) return false;

    setAuthToken(token);
    setApiToken(token);

    try {
        const resp = await apiFetch('/api/auth/me');
        const data = await resp.json();

        if (data.user) {
            setCurrentUser(data.user);
            return true;
        }
    } catch {
        // Token invalid or expired
    }

    clearAuth();
    return false;
}

/**
 * Login with username and password
 * @param {string} username
 * @param {string} password
 * @returns {Promise<Object>} User data
 */
export async function login(username, password) {
    const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
    });

    const data = await resp.json();

    if (!resp.ok) {
        const errorMsg = typeof data.error === 'string' ? data.error : '登录失败';
        throw new Error(errorMsg);
    }

    if (!data.token || !data.user) {
        throw new Error('登录响应数据不完整');
    }

    // Save auth state - token must be set before any subsequent API calls
    saveToken(data.token);
    setApiToken(data.token);
    setAuthToken(data.token);
    setCurrentUser(data.user);

    return data.user;
}

/**
 * Logout current user
 */
export async function logout() {
    try {
        await apiFetch('/api/auth/logout', { method: 'POST' });
    } catch {
        // Ignore errors
    }

    clearAuth();
}

/**
 * Change current user's password
 * @param {string} oldPassword
 * @param {string} newPassword
 * @returns {Promise<void>}
 */
export async function changePassword(oldPassword, newPassword) {
    const resp = await apiFetch('/api/auth/password', {
        method: 'PUT',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });

    const data = await resp.json();

    if (!resp.ok) {
        const errorMsg = typeof data.error === 'string' ? data.error : '修改密码失败';
        throw new Error(errorMsg);
    }

    // Update must_change_password flag
    const user = appStore.get('currentUser');
    if (user) {
        appStore.set('currentUser', { ...user, must_change_password: false });
    }
}

// ── Permission Helpers ───────────────────────────────────────────────────────

/**
 * Check if current user has a specific role
 * @param {string} role - Role to check
 * @returns {boolean}
 */
export function hasRole(role) {
    const user = appStore.get('currentUser');
    return user?.role === role;
}

/**
 * Check if current user is admin
 * @returns {boolean}
 */
export function isAdmin() {
    return hasRole('admin');
}

/**
 * Check if current user is reviewer
 * @returns {boolean}
 */
export function isReviewer() {
    return hasRole('reviewer');
}

/**
 * Check if current user is regular user
 * @returns {boolean}
 */
export function isUser() {
    return hasRole('user');
}

/**
 * Get current user's role
 * @returns {string|null}
 */
export function getUserRole() {
    return appStore.get('currentUser')?.role || null;
}

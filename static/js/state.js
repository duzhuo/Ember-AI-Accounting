/**
 * Simple reactive state management
 */

export class Store {
    constructor(initialState = {}) {
        this.state = { ...initialState };
        this.listeners = new Map();
    }

    /**
     * Get a state value
     * @param {string} key - State key
     * @returns {*} State value
     */
    get(key) {
        return this.state[key];
    }

    /**
     * Set a state value and notify listeners
     * @param {string} key - State key
     * @param {*} value - New value
     */
    set(key, value) {
        const oldValue = this.state[key];

        // Check if value actually changed
        const hasChanged = this._hasValueChanged(oldValue, value);

        this.state[key] = value;

        if (hasChanged) {
            this.notify(key, value, oldValue);
        }
    }

    /**
     * Check if two values are different
     * @private
     */
    _hasValueChanged(oldVal, newVal) {
        // Same reference
        if (oldVal === newVal) return false;

        // If either is null/undefined/primitive, use strict equality
        if (!oldVal || !newVal || typeof oldVal !== 'object' || typeof newVal !== 'object') {
            return oldVal !== newVal;
        }

        // For objects, do a shallow key comparison
        const oldKeys = Object.keys(oldVal);
        const newKeys = Object.keys(newVal);
        if (oldKeys.length !== newKeys.length) return true;

        return oldKeys.some(key => oldVal[key] !== newVal[key]);
    }

    /**
     * Update multiple state values
     * @param {Object} updates - Key-value pairs to update
     */
    update(updates) {
        Object.entries(updates).forEach(([key, value]) => {
            this.set(key, value);
        });
    }

    /**
     * Get all state
     * @returns {Object} Current state
     */
    getAll() {
        return { ...this.state };
    }

    /**
     * Subscribe to state changes
     * @param {string} key - State key to watch
     * @param {Function} callback - Callback function(newValue, oldValue)
     * @returns {Function} Unsubscribe function
     */
    subscribe(key, callback) {
        if (!this.listeners.has(key)) {
            this.listeners.set(key, new Set());
        }
        this.listeners.get(key).add(callback);

        // Return unsubscribe function
        return () => {
            const callbacks = this.listeners.get(key);
            if (callbacks) {
                callbacks.delete(callback);
            }
        };
    }

    /**
     * Subscribe to any state change
     * @param {Function} callback - Callback function(key, newValue, oldValue)
     * @returns {Function} Unsubscribe function
     */
    subscribeAll(callback) {
        return this.subscribe('*', callback);
    }

    /**
     * Notify listeners of a state change
     * @param {string} key - State key
     * @param {*} newValue - New value
     * @param {*} oldValue - Old value
     */
    notify(key, newValue, oldValue) {
        // Notify key-specific listeners
        const callbacks = this.listeners.get(key);
        if (callbacks) {
            callbacks.forEach((cb) => {
                try {
                    cb(newValue, oldValue);
                } catch (err) {
                    console.error(`State listener error for key "${key}":`, err);
                }
            });
        }

        // Notify wildcard listeners
        const wildcardCallbacks = this.listeners.get('*');
        if (wildcardCallbacks) {
            wildcardCallbacks.forEach((cb) => {
                try {
                    cb(key, newValue, oldValue);
                } catch (err) {
                    console.error('State wildcard listener error:', err);
                }
            });
        }
    }

    /**
     * Reset state to initial values
     * @param {Object} initialState - Initial state object
     */
    reset(initialState = {}) {
        const oldState = this.state;
        this.state = { ...initialState };

        // Notify all listeners
        Object.keys(oldState).forEach((key) => {
            if (oldState[key] !== this.state[key]) {
                this.notify(key, this.state[key], oldState[key]);
            }
        });
    }
}

// ── Create App Store ─────────────────────────────────────────────────────────

export const appStore = new Store({
    // Auth state
    authToken: null,
    currentUser: null,
    isLoggedIn: false,

    // Session state
    sessionId: null,
    isProcessing: false,

    // View state
    currentView: 'empty',
    viewHistory: [],

    // Voucher state
    currentVoucherId: null,
    isPosted: false,

    // Notification state
    notificationCount: 0,
    notifFilter: 'all',

    // UI state
    pendingFile: null,
    forcePasswordChange: false,
});

// ── Convenience Accessors ────────────────────────────────────────────────────

export function getAuthToken() {
    return appStore.get('authToken');
}

export function setAuthToken(token) {
    appStore.set('authToken', token);
}

export function getCurrentUser() {
    return appStore.get('currentUser');
}

export function setCurrentUser(user) {
    appStore.set('currentUser', user);
    appStore.set('isLoggedIn', !!user);
}

export function getSessionId() {
    return appStore.get('sessionId');
}

export function setSessionId(id) {
    appStore.set('sessionId', id);
}

export function getCurrentView() {
    return appStore.get('currentView');
}

export function setCurrentView(view) {
    appStore.set('currentView', view);
}

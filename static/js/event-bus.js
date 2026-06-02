/**
 * Event bus for decoupled communication between modules
 */

export class EventBus {
    constructor() {
        this.handlers = new Map();
    }

    /**
     * Subscribe to an event
     * @param {string} event - Event name
     * @param {Function} handler - Event handler
     * @returns {Function} Unsubscribe function
     */
    on(event, handler) {
        if (!this.handlers.has(event)) {
            this.handlers.set(event, new Set());
        }
        this.handlers.get(event).add(handler);

        // Return unsubscribe function
        return () => this.off(event, handler);
    }

    /**
     * Subscribe to an event once
     * @param {string} event - Event name
     * @param {Function} handler - Event handler
     * @returns {Function} Unsubscribe function
     */
    once(event, handler) {
        const wrapper = (...args) => {
            this.off(event, wrapper);
            handler(...args);
        };
        return this.on(event, wrapper);
    }

    /**
     * Emit an event
     * @param {string} event - Event name
     * @param {*} data - Event data
     */
    emit(event, data) {
        const handlers = this.handlers.get(event);
        if (handlers) {
            handlers.forEach((handler) => {
                try {
                    handler(data);
                } catch (err) {
                    console.error(`Event handler error for "${event}":`, err);
                }
            });
        }
    }

    /**
     * Unsubscribe from an event
     * @param {string} event - Event name
     * @param {Function} handler - Event handler to remove
     */
    off(event, handler) {
        const handlers = this.handlers.get(event);
        if (handlers) {
            handlers.delete(handler);
            if (handlers.size === 0) {
                this.handlers.delete(event);
            }
        }
    }

    /**
     * Remove all handlers for an event
     * @param {string} event - Event name
     */
    removeAll(event) {
        if (event) {
            this.handlers.delete(event);
        } else {
            this.handlers.clear();
        }
    }
}

// ── Create App Event Bus ─────────────────────────────────────────────────────

export const bus = new EventBus();

// ── Common Event Names ───────────────────────────────────────────────────────

export const EVENTS = {
    // Auth events
    AUTH_LOGIN: 'auth:login',
    AUTH_LOGOUT: 'auth:logout',
    AUTH_ERROR: 'auth:error',

    // View events
    VIEW_SWITCH: 'view:switch',
    VIEW_BACK: 'view:back',

    // Chat events
    CHAT_MESSAGE: 'chat:message',
    CHAT_RESPONSE: 'chat:response',
    CHAT_ERROR: 'chat:error',

    // Voucher events
    VOUCHER_LOAD: 'voucher:load',
    VOUCHER_SAVE: 'voucher:save',
    VOUCHER_SUBMIT: 'voucher:submit',
    VOUCHER_APPROVE: 'voucher:approve',
    VOUCHER_REJECT: 'voucher:reject',
    VOUCHER_POST: 'voucher:post',
    VOUCHER_REVERSE: 'voucher:reverse',

    // Notification events
    NOTIFICATION_RECEIVED: 'notification:received',
    NOTIFICATION_READ: 'notification:read',
    NOTIFICATION_ALL_READ: 'notification:all_read',

    // User events
    USER_CREATE: 'user:create',
    USER_UPDATE: 'user:update',
    USER_DELETE: 'user:delete',
    USER_RESET_PASSWORD: 'user:reset_password',

    // Rule events
    RULE_CREATE: 'rule:create',
    RULE_UPDATE: 'rule:update',
    RULE_DELETE: 'rule:delete',

    // UI events
    TOAST_SHOW: 'toast:show',
    MODAL_OPEN: 'modal:open',
    MODAL_CLOSE: 'modal:close',
    LOADING_START: 'loading:start',
    LOADING_END: 'loading:end',
};

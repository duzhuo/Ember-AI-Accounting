/**
 * View manager for handling view switching and navigation
 */

import { appStore } from '../state.js';
import { bus, EVENTS } from '../event-bus.js';

// ── View Configuration ───────────────────────────────────────────────────────

export const VIEW_CONFIG = {
    empty: {
        title: '',
        showHeader: false,
    },
    voucher: {
        title: '凭证详情',
        showHeader: true,
    },
    voucher_list: {
        title: '凭证记录',
        showHeader: true,
    },
    rules: {
        title: '凭证规则',
        showHeader: true,
    },
    rule_edit: {
        title: '编辑规则',
        showHeader: true,
    },
    user_list: {
        title: '用户管理',
        showHeader: true,
    },
    dynamic: {
        title: '',
        showHeader: false,
    },
    notifications: {
        title: '消息通知',
        showHeader: true,
    },
};

// ── View Manager Class ───────────────────────────────────────────────────────

export class ViewManager {
    constructor() {
        this.viewHistory = [];
        this.currentView = 'empty';
        this.viewElements = new Map();
        this.headerElement = null;
        this.titleElement = null;
    }

    /**
     * Initialize view manager
     */
    init() {
        // Cache DOM elements
        this.headerElement = document.getElementById('viewHeader');
        this.titleElement = document.getElementById('viewTitle');

        // Cache view elements
        document.querySelectorAll('.view-content').forEach((el) => {
            const viewName = el.id.replace('view', '').toLowerCase();
            this.viewElements.set(viewName, el);
        });

        // Setup back button
        const backBtn = document.getElementById('viewBackBtn');
        if (backBtn) {
            backBtn.addEventListener('click', () => this.back());
        }

        // Subscribe to state changes
        appStore.subscribe('currentView', (view) => {
            this.updateViewVisibility(view);
        });
    }

    /**
     * Switch to a view
     * @param {string} viewName - View name
     * @param {Object} options - Options
     * @param {boolean} options.pushHistory - Whether to push to history (default: true)
     */
    switchTo(viewName, { pushHistory = true } = {}) {
        const config = VIEW_CONFIG[viewName];
        if (!config) {
            console.warn(`Unknown view: ${viewName}`);
            return;
        }

        // Push current view to history
        if (pushHistory && this.currentView !== viewName) {
            this.viewHistory.push(this.currentView);
        }

        // Update state
        this.currentView = viewName;
        appStore.set('currentView', viewName);

        // Update header
        if (config.showHeader) {
            this.headerElement.style.display = 'flex';
            this.titleElement.textContent = config.title;
        } else {
            this.headerElement.style.display = 'none';
        }

        // Emit event
        bus.emit(EVENTS.VIEW_SWITCH, { view: viewName, config });
    }

    /**
     * Go back to previous view
     */
    back() {
        const prevView = this.viewHistory.pop();
        if (prevView) {
            this.switchTo(prevView, { pushHistory: false });
        } else {
            this.switchTo('empty', { pushHistory: false });
        }
    }

    /**
     * Update view visibility
     * @param {string} activeView - Active view name
     */
    updateViewVisibility(activeView) {
        this.viewElements.forEach((el, viewName) => {
            if (viewName === activeView) {
                el.classList.add('active');
            } else {
                el.classList.remove('active');
            }
        });
    }

    /**
     * Get current view
     * @returns {string} Current view name
     */
    getCurrentView() {
        return this.currentView;
    }

    /**
     * Get view history
     * @returns {Array} View history
     */
    getHistory() {
        return [...this.viewHistory];
    }

    /**
     * Clear view history
     */
    clearHistory() {
        this.viewHistory = [];
    }
}

// ── Singleton Instance ───────────────────────────────────────────────────────

export const viewManager = new ViewManager();

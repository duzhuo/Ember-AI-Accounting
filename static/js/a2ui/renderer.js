/**
 * A2UI Renderer - Declarative UI rendering protocol
 * Renders components based on server-side declarations
 */

import { escHtml, formatTime, formatAmount } from '../common.js';
import { icon } from '../icons.js';

// ── A2UI Renderer Class ─────────────────────────────────────────────────────

export class A2UIRenderer {
    constructor(container) {
        this.container = container;
        this.dataModel = {};
        this.components = new Map();
        this.actionHandler = null;
    }

    /**
     * Set action handler function
     * @param {Function} handler - Action handler function(action, extraData)
     */
    setActionHandler(handler) {
        this.actionHandler = handler;
    }

    /**
     * Process A2UI messages
     * @param {Array} messages - A2UI messages
     */
    processMessages(messages) {
        for (const msg of messages) {
            if (msg.createSurface) {
                this.createSurface(msg.createSurface);
            } else if (msg.updateComponents) {
                this.updateComponents(msg.updateComponents);
            } else if (msg.updateDataModel) {
                this.updateDataModel(msg.updateDataModel);
            }
        }
    }

    /**
     * Create a surface
     * @param {Object} surface - Surface configuration
     */
    createSurface(surface) {
        // Surface creation is handled by updateComponents
    }

    /**
     * Update components
     * @param {Object} data - Component data
     */
    updateComponents(data) {
        const { root, components } = data;

        // Store components
        this.components.clear();
        for (const comp of components) {
            this.components.set(comp.id, comp);
        }

        // Find top-level components (not referenced as children)
        const childIds = new Set();
        for (const comp of components) {
            if (comp.children) {
                comp.children.forEach((id) => childIds.add(id));
            }
        }

        const topLevel = components.filter((comp) => !childIds.has(comp.id));

        // Render
        this.container.innerHTML = '';
        for (const comp of topLevel) {
            const el = this.renderComponent(comp);
            if (el) {
                this.container.appendChild(el);
            }
        }
    }

    /**
     * Update data model
     * @param {Object} data - Data model updates
     */
    updateDataModel(data) {
        Object.assign(this.dataModel, data);
    }

    /**
     * Resolve a value (path or literal)
     * @param {*} val - Value to resolve
     * @returns {*} Resolved value
     */
    resolveValue(val) {
        if (val && typeof val === 'object' && val.path) {
            return this.getNestedValue(this.dataModel, val.path);
        }
        return val;
    }

    /**
     * Get nested value from object
     * @param {Object} obj - Object to traverse
     * @param {string} path - Dot-separated path
     * @returns {*} Value at path
     */
    getNestedValue(obj, path) {
        return path.split('.').reduce((current, key) => current?.[key], obj);
    }

    /**
     * Render a component
     * @param {Object} comp - Component configuration
     * @returns {HTMLElement} Rendered element
     */
    renderComponent(comp) {
        const renderers = {
            Text: this.renderText.bind(this),
            Image: this.renderImage.bind(this),
            Icon: this.renderIcon.bind(this),
            Row: this.renderRow.bind(this),
            Column: this.renderColumn.bind(this),
            List: this.renderList.bind(this),
            Card: this.renderCard.bind(this),
            Tabs: this.renderTabs.bind(this),
            Modal: this.renderModal.bind(this),
            Divider: this.renderDivider.bind(this),
            Button: this.renderButton.bind(this),
            TextField: this.renderTextField.bind(this),
            CheckBox: this.renderCheckBox.bind(this),
            ChoicePicker: this.renderChoicePicker.bind(this),
            Slider: this.renderSlider.bind(this),
            DateTimeInput: this.renderDateTimeInput.bind(this),
            DataTable: this.renderDataTable.bind(this),
            KeyValue: this.renderKeyValue.bind(this),
            FilterTabs: this.renderFilterTabs.bind(this),
            Badge: this.renderBadge.bind(this),
            SearchInput: this.renderSearchInput.bind(this),
        };

        const renderer = renderers[comp.component];
        if (renderer) {
            return renderer(comp);
        }

        console.warn(`Unknown A2UI component: ${comp.component}`);
        return null;
    }

    /**
     * Render children of a component
     * @param {Array} childIds - Child component IDs
     * @returns {DocumentFragment} Fragment with rendered children
     */
    renderChildren(childIds) {
        const fragment = document.createDocumentFragment();
        for (const id of childIds) {
            const comp = this.components.get(id);
            if (comp) {
                const el = this.renderComponent(comp);
                if (el) {
                    fragment.appendChild(el);
                }
            }
        }
        return fragment;
    }

    // ── Component Renderers ─────────────────────────────────────────────────────

    renderText(comp) {
        const el = document.createElement('div');
        el.className = `a2ui-text a2ui-text-${comp.variant || 'body'}`;
        el.textContent = this.resolveValue(comp.text) || '';
        if (comp.align) el.style.textAlign = comp.align;
        return el;
    }

    renderImage(comp) {
        const el = document.createElement('img');
        el.className = 'a2ui-image';
        el.src = this.resolveValue(comp.src) || '';
        el.alt = this.resolveValue(comp.alt) || '';
        if (comp.width) el.style.width = comp.width;
        if (comp.height) el.style.height = comp.height;
        return el;
    }

    renderIcon(comp) {
        const el = document.createElement('span');
        el.className = 'a2ui-icon';
        el.innerHTML = icon(comp.name, comp.size || 16);
        return el;
    }

    renderRow(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-row';
        if (comp.gap) el.style.gap = comp.gap;
        if (comp.align) el.style.alignItems = comp.align;
        if (comp.justify) el.style.justifyContent = comp.justify;
        if (comp.children) {
            el.appendChild(this.renderChildren(comp.children));
        }
        return el;
    }

    renderColumn(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-column';
        if (comp.gap) el.style.gap = comp.gap;
        if (comp.children) {
            el.appendChild(this.renderChildren(comp.children));
        }
        return el;
    }

    renderList(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-list';
        const items = this.resolveValue(comp.items) || [];
        for (const item of items) {
            const itemEl = document.createElement('div');
            itemEl.className = 'a2ui-list-item';
            if (comp.itemTemplate) {
                // Render item template with data binding
                itemEl.textContent = JSON.stringify(item);
            }
            el.appendChild(itemEl);
        }
        return el;
    }

    renderCard(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-card';
        if (comp.children) {
            el.appendChild(this.renderChildren(comp.children));
        }
        return el;
    }

    renderTabs(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-tabs';
        const tabs = this.resolveValue(comp.tabs) || [];
        const activeTab = this.resolveValue(comp.active) || tabs[0]?.key;

        for (const tab of tabs) {
            const tabEl = document.createElement('button');
            tabEl.className = `a2ui-tab ${tab.key === activeTab ? 'active' : ''}`;
            tabEl.textContent = tab.label;
            tabEl.addEventListener('click', () => {
                if (comp.action) {
                    this.handleAction(comp.action, { active: tab.key });
                }
            });
            el.appendChild(tabEl);
        }
        return el;
    }

    renderModal(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-modal-overlay';
        el.innerHTML = `
            <div class="a2ui-modal">
                <div class="a2ui-modal-header">
                    <h3>${escHtml(comp.title || '')}</h3>
                    <button class="a2ui-modal-close">${icon('close', 18)}</button>
                </div>
                <div class="a2ui-modal-body"></div>
            </div>
        `;

        const closeBtn = el.querySelector('.a2ui-modal-close');
        closeBtn.addEventListener('click', () => el.remove());

        const body = el.querySelector('.a2ui-modal-body');
        if (comp.children) {
            body.appendChild(this.renderChildren(comp.children));
        }

        return el;
    }

    renderDivider(comp) {
        const el = document.createElement('hr');
        el.className = 'a2ui-divider';
        return el;
    }

    renderButton(comp) {
        const el = document.createElement('button');
        el.className = `a2ui-btn a2ui-btn-${comp.variant || 'default'}`;
        el.disabled = comp.disabled || false;

        // Button text from child component (supports both child and children)
        const childRefs = comp.children || (comp.child ? [comp.child] : []);
        if (childRefs.length > 0) {
            el.appendChild(this.renderChildren(childRefs));
        } else {
            el.textContent = comp.label || '';
        }

        if (comp.action) {
            el.addEventListener('click', () => {
                if (!el.disabled) {
                    this.handleAction(comp.action, {}, el);
                }
            });
        }

        return el;
    }

    renderTextField(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-text-field';
        el.innerHTML = `
            <label>${escHtml(comp.label || '')}</label>
            <input type="${comp.type || 'text'}"
                   placeholder="${escHtml(comp.placeholder || '')}"
                   value="${escHtml(this.resolveValue(comp.value) || '')}"
                   ${comp.required ? 'required' : ''}
                   ${comp.disabled ? 'disabled' : ''}>
        `;
        return el;
    }

    renderCheckBox(comp) {
        const el = document.createElement('label');
        el.className = 'a2ui-checkbox';
        el.innerHTML = `
            <input type="checkbox"
                   ${comp.checked ? 'checked' : ''}
                   ${comp.disabled ? 'disabled' : ''}>
            <span>${escHtml(comp.label || '')}</span>
        `;
        return el;
    }

    renderChoicePicker(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-choice-picker';
        const choices = this.resolveValue(comp.choices) || [];
        const selected = this.resolveValue(comp.selected);

        for (const choice of choices) {
            const btn = document.createElement('button');
            btn.className = `a2ui-choice ${choice.key === selected ? 'active' : ''}`;
            btn.textContent = choice.label;
            btn.addEventListener('click', () => {
                if (comp.action) {
                    this.handleAction(comp.action, { value: choice.key });
                }
            });
            el.appendChild(btn);
        }
        return el;
    }

    renderSlider(comp) {
        const el = document.createElement('input');
        el.type = 'range';
        el.className = 'a2ui-slider';
        el.min = comp.min || 0;
        el.max = comp.max || 100;
        el.value = this.resolveValue(comp.value) || 0;
        return el;
    }

    renderDateTimeInput(comp) {
        const el = document.createElement('input');
        el.type = comp.type || 'date';
        el.className = 'a2ui-datetime';
        el.value = this.resolveValue(comp.value) || '';
        return el;
    }

    renderDataTable(comp) {
        const wrapper = document.createElement('div');
        wrapper.className = 'a2ui-table-wrapper';
        const table = document.createElement('table');
        table.className = 'a2ui-table';

        const actionColumns = comp.actionColumns || {};

        // Header
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        for (const col of (comp.columns || [])) {
            const th = document.createElement('th');
            th.textContent = col.label || col.key;
            if (col.align) th.style.textAlign = col.align;
            if (col.width) th.style.width = col.width;
            headerRow.appendChild(th);
        }
        thead.appendChild(headerRow);
        table.appendChild(thead);

        // Body
        const tbody = document.createElement('tbody');
        for (const row of (comp.rows || [])) {
            const tr = document.createElement('tr');
            for (const col of (comp.columns || [])) {
                const td = document.createElement('td');

                if (actionColumns[col.key]) {
                    // Render action buttons
                    const actions = actionColumns[col.key];
                    const btnGroup = document.createElement('div');
                    btnGroup.className = 'a2ui-action-btn-group';

                    for (const actionDef of actions) {
                        const btn = document.createElement('button');
                        btn.className = 'a2ui-action-btn';
                        btn.title = actionDef.label || '';
                        btn.innerHTML = actionDef.icon || '';
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            const action = this.resolveActionTemplate(actionDef.action, row);
                            this.handleAction(action);
                        });
                        btnGroup.appendChild(btn);
                    }

                    td.appendChild(btnGroup);
                } else {
                    td.textContent = row[col.key] ?? '';
                    if (col.align) td.style.textAlign = col.align;
                }

                tr.appendChild(td);
            }

            // Row click action
            if (comp.rowAction) {
                tr.className = 'a2ui-table-clickable';
                tr.addEventListener('click', (e) => {
                    if (e.target.closest('.a2ui-action-btn-group')) return;
                    const action = this.resolveActionTemplate(comp.rowAction, row);
                    this.handleAction(action);
                });
            }

            tbody.appendChild(tr);
        }
        table.appendChild(tbody);

        wrapper.appendChild(table);
        return wrapper;
    }

    renderKeyValue(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-key-value';
        const items = this.resolveValue(comp.items) || [];

        for (const item of items) {
            const row = document.createElement('div');
            row.className = 'a2ui-kv-row';
            row.innerHTML = `
                <span class="a2ui-kv-label">${escHtml(item.label)}</span>
                <span class="a2ui-kv-value">${escHtml(String(item.value))}</span>
            `;
            el.appendChild(row);
        }
        return el;
    }

    renderFilterTabs(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-filter-tabs';
        const tabs = this.resolveValue(comp.tabs) || [];
        const active = this.resolveValue(comp.active);

        for (const tab of tabs) {
            const btn = document.createElement('button');
            btn.className = `a2ui-filter-tab ${tab.key === active ? 'active' : ''}`;
            btn.textContent = tab.label;
            btn.addEventListener('click', () => {
                if (comp.action) {
                    this.handleAction(comp.action, { active: tab.key });
                }
            });
            el.appendChild(btn);
        }
        return el;
    }

    renderBadge(comp) {
        const el = document.createElement('span');
        el.className = `a2ui-badge a2ui-badge-${comp.variant || 'default'}`;
        el.textContent = this.resolveValue(comp.text) || '';
        return el;
    }

    renderSearchInput(comp) {
        const el = document.createElement('div');
        el.className = 'a2ui-search';
        el.innerHTML = `
            <input type="text" class="a2ui-search-field"
                   placeholder="${escHtml(comp.placeholder || '搜索...')}">
            <button class="a2ui-search-btn">${icon('search', 16)}</button>
        `;

        const input = el.querySelector('input');
        const btn = el.querySelector('button');

        const doSearch = () => {
            if (comp.action) {
                this.handleAction(comp.action, { keyword: input.value });
            }
        };

        btn.addEventListener('click', doSearch);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') doSearch();
        });

        return el;
    }

    // ── Action Handling ─────────────────────────────────────────────────────────

    /**
     * Resolve action template with row data
     * @param {Object} template - Action template
     * @param {Object} rowData - Row data for substitution
     * @returns {Object} Resolved action
     */
    resolveActionTemplate(template, rowData) {
        if (!template) return null;

        const resolved = JSON.parse(JSON.stringify(template));

        // Replace placeholders in event data
        if (resolved.event?.data) {
            for (const [key, value] of Object.entries(resolved.event.data)) {
                if (typeof value === 'string' && value.startsWith('{') && value.endsWith('}')) {
                    const field = value.slice(1, -1);
                    resolved.event.data[key] = rowData[field] || '';
                }
            }
        }

        return resolved;
    }

    /**
     * Handle an action
     * @param {Object} action - Action configuration
     * @param {Object} extraData - Additional data
     * @param {HTMLElement} btnEl - Button element (optional)
     */
    handleAction(action, extraData = {}, btnEl = null) {
        if (this.actionHandler) {
            this.actionHandler(action, extraData, btnEl);
        }
    }
}

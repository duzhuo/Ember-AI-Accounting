document.addEventListener('DOMContentLoaded', () => {
    // ── Auth State ────────────────────────────────────────────────────────────
    let authToken = localStorage.getItem('ember_token') || null;
    let currentUser = null;

    // ── DOM Elements ──────────────────────────────────────────────────────────
    const loginOverlay = document.getElementById('loginOverlay');
    const appContainer = document.getElementById('appContainer');
    const loginForm = document.getElementById('loginForm');
    const loginError = document.getElementById('loginError');
    const userBadge = document.getElementById('userBadge');
    const userDisplayName = document.getElementById('userDisplayName');
    const logoutBtn = document.getElementById('logoutBtn');

    const chatHistory = document.getElementById('chatHistory');
    const userInput = document.getElementById('userInput');
    const sendBtn = document.getElementById('sendBtn');
    const uploadBtn = document.getElementById('uploadBtn');
    const fileInput = document.getElementById('fileInput');

    // Voucher workspace
    const voucherWorkspaceContent = document.getElementById('voucherWorkspaceContent');
    const sourceDataContent = document.getElementById('sourceDataContent');
    const voucherRows = document.getElementById('voucherRows');
    const totalDebitEl = document.getElementById('totalDebit');
    const totalCreditEl = document.getElementById('totalCredit');
    const createTransactionBtn = document.getElementById('createTransactionBtn');
    const sourceBadge = document.getElementById('sourceBadge');
    const toggleSourceBtn = document.getElementById('toggleSourceData');

    // View system
    const viewHeader = document.getElementById('viewHeader');
    const viewTitle = document.getElementById('viewTitle');
    const viewBackBtn = document.getElementById('viewBackBtn');

    // ── App State ─────────────────────────────────────────────────────────────
    let isProcessing = false;
    let sessionId = null;
    let pendingFile = null;
    let currentVoucherId = null;
    let isPosted = false;
    let currentView = 'empty';  // Track current view
    let viewHistory = [];       // View history stack for back navigation

    // ── View Management ──────────────────────────────────────────────────────

    const VIEW_CONFIG = {
        empty:       { title: '',              showHeader: false },
        voucher:     { title: '凭证详情',       showHeader: true, icon: 'ph-receipt' },
        voucher_list:{ title: '凭证记录',       showHeader: true, icon: 'ph-clock-counter-clockwise' },
        rules:       { title: '凭证规则',       showHeader: true, icon: 'ph-list-dashes' },
        rule_edit:   { title: '编辑规则',       showHeader: true, icon: 'ph-pencil-simple' },
        user_list:   { title: '用户管理',       showHeader: true, icon: 'ph-users' },
        dynamic:     { title: '',              showHeader: false },
    };

    function switchView(viewName, options = {}) {
        const pushHistory = options.pushHistory !== false;
        if (pushHistory && currentView !== viewName) {
            viewHistory.push(currentView);
        }
        currentView = viewName;

        // Hide all views
        document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));

        // Show target view
        const config = VIEW_CONFIG[viewName] || {};
        const targetId = viewName === 'voucher_list' ? 'viewVoucherList'
                       : viewName === 'user_list' ? 'viewUserList'
                       : viewName === 'rules' ? 'viewRules'
                       : viewName === 'rule_edit' ? 'viewRuleEdit'
                       : viewName === 'voucher' ? 'viewVoucher'
                       : viewName === 'dynamic' ? 'viewDynamic'
                       : 'viewEmpty';
        const target = document.getElementById(targetId);
        if (target) target.classList.add('active');

        // Update header
        if (config.showHeader) {
            viewHeader.style.display = 'flex';
            viewTitle.innerHTML = `<i class="ph ${config.icon || ''}"></i> ${config.title}`;
        } else {
            viewHeader.style.display = 'none';
        }
    }

    viewBackBtn.addEventListener('click', () => {
        const prevView = viewHistory.pop();
        if (prevView) {
            switchView(prevView, { pushHistory: false });
        } else {
            switchView('empty', { pushHistory: false });
        }
    });

    // ── API Helper ────────────────────────────────────────────────────────────

    async function apiFetch(url, options = {}) {
        const headers = options.headers || {};
        if (authToken) {
            headers['Authorization'] = `Bearer ${authToken}`;
        }
        if (!headers['Content-Type'] && options.body && !(options.body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
        }
        const resp = await fetch(url, { ...options, headers });
        if (resp.status === 401) {
            handleLogout();
            throw new Error('登录已过期，请重新登录');
        }
        if (!resp.ok && !resp.headers.get('content-type')?.includes('text/event-stream')) {
            let msg = `请求失败 (${resp.status})`;
            try {
                const err = await resp.json();
                msg = err.error || err.detail || err.reply || msg;
            } catch {}
            throw new Error(msg);
        }
        return resp;
    }

    // ── Auth Logic ────────────────────────────────────────────────────────────

    async function checkAuth() {
        if (!authToken) {
            showLogin();
            return;
        }
        try {
            const resp = await apiFetch('/api/auth/me');
            const data = await resp.json();
            if (data.user) {
                currentUser = data.user;
                sessionId = null;
                chatHistory.innerHTML = '';
                showApp();
            } else {
                handleLogout();
            }
        } catch {
            handleLogout();
        }
    }

    function showLogin() {
        loginOverlay.style.display = 'flex';
        appContainer.style.display = 'none';
    }

    function showApp() {
        loginOverlay.style.display = 'none';
        appContainer.style.display = 'grid';
        userDisplayName.textContent = currentUser.display_name || currentUser.username;
        loadChatHistory();
    }

    async function loadChatHistory() {
        try {
            const resp = await apiFetch('/api/my-chat-history?limit=50');
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data.messages || data.messages.length === 0) return;

            sessionId = data.session_id;
            for (const msg of data.messages) {
                if (msg.role === 'user') {
                    addMessage(msg.content, 'user');
                } else if (msg.role === 'assistant') {
                    // Skip metadata-only messages (like pending actions)
                    const meta = msg.metadata || {};
                    if (meta.pending_action) continue;
                    addMessage(msg.content, 'ai');
                }
            }
        } catch (err) {
            console.warn('Failed to load chat history:', err);
        }
    }

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('loginUsername').value.trim();
        const password = document.getElementById('loginPassword').value;
        const submitBtn = document.getElementById('loginSubmitBtn');

        submitBtn.disabled = true;
        submitBtn.textContent = '登录中...';
        loginError.style.display = 'none';

        try {
            const resp = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            });
            const data = await resp.json();

            if (!resp.ok) {
                loginError.textContent = data.error || '登录失败';
                loginError.style.display = 'block';
                return;
            }

            authToken = data.token;
            currentUser = data.user;
            localStorage.setItem('ember_token', authToken);
            sessionId = null;
            chatHistory.innerHTML = '';
            if (voucherWorkspaceContent) voucherWorkspaceContent.style.display = 'none';
            showApp();
        } catch (err) {
            loginError.textContent = '网络错误，请重试';
            loginError.style.display = 'block';
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = '登录';
        }
    });

    function handleLogout() {
        authToken = null;
        currentUser = null;
        sessionId = null;
        currentVoucherId = null;
        isPosted = false;
        pendingFile = null;
        isProcessing = false;
        viewHistory = [];
        currentView = 'empty';
        chatHistory.innerHTML = '';
        if (voucherWorkspaceContent) voucherWorkspaceContent.style.display = 'none';
        localStorage.removeItem('ember_token');
        showLogin();
    }

    logoutBtn.addEventListener('click', async () => {
        try {
            await apiFetch('/api/auth/logout', { method: 'POST' });
        } catch {}
        handleLogout();
    });

    // ── Chat Logic ────────────────────────────────────────────────────────────

    async function sendMessage() {
        const text = userInput.value.trim();
        if (!text && !pendingFile) return;
        if (isProcessing) return;

        addMessage(text || '上传了文件', 'user');
        userInput.value = '';
        resizeTextarea();

        isProcessing = true;
        showTypingIndicator();

        try {
            if (pendingFile) {
                await handleFileUpload(pendingFile, text);
                pendingFile = null;
            } else {
                await processAIResponse(text);
            }
        } catch (err) {
            // Clean up any leftover streaming message
            const leftoverStream = document.getElementById('streaming-message');
            if (leftoverStream) leftoverStream.remove();
            removeTypingIndicator();
            if (err.message && err.message.includes('登录已过期')) {
                addMessage('登录已过期，请重新登录。', 'ai');
            } else {
                addMessage('请求出错：' + (err.message || '未知错误'), 'ai');
            }
            console.error('[sendMessage] error:', err);
        } finally {
            isProcessing = false;
        }
    }

    function addMessage(content, type = 'ai') {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${type}-message`;
        let icon = type === 'ai' ? 'ph-fire' : 'ph-user';
        msgDiv.innerHTML = `
            <div class="avatar"><i class="ph ${icon}"></i></div>
            <div class="content">${formatContent(content)}</div>
        `;
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function formatContent(text) {
        return text.replace(/\n/g, '<br>');
    }

    async function consumeSSE(resp, { onDelta, onProgress, onResult, onError }) {
        if (!resp.body) throw new Error('响应体为空 (status=' + resp.status + ')');
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.type === 'delta') onDelta?.(data.text);
                    else if (data.type === 'progress') onProgress?.(data.text);
                    else if (data.type === 'result') onResult?.(data);
                    else if (data.type === 'error') onError?.(data);
                } catch (e) {
                    console.warn('SSE parse error:', e, line);
                }
            }
        }
        if (buffer.trim() && buffer.startsWith('data: ')) {
            try {
                const data = JSON.parse(buffer.slice(6));
                if (data.type === 'result') onResult?.(data);
                else if (data.type === 'error') onError?.(data);
            } catch (e) {
                console.warn('SSE final buffer parse error:', e, buffer);
            }
        }
    }

    function resizeTextarea() {
        userInput.style.height = 'auto';
        userInput.style.height = userInput.scrollHeight + 'px';
    }

    // ── A2UI Renderer ────────────────────────────────────────────────────────

    class A2UIRenderer {
        constructor(container) {
            this.container = container;
            this.components = {};  // id → component definition
            this.elements = {};    // id → DOM element
            this.dataModel = {};   // application state
        }

        processMessages(messages) {
            for (const msg of messages) {
                if (msg.createSurface) this.createSurface(msg.createSurface);
                if (msg.updateComponents) this.updateComponents(msg.updateComponents);
                if (msg.updateDataModel) this.updateDataModel(msg.updateDataModel);
            }
        }

        createSurface({ surfaceId, catalogId }) {
            this.surfaceId = surfaceId;
            this.catalogId = catalogId;
        }

        updateComponents({ surfaceId, components }) {
            for (const comp of components) {
                this.components[comp.id] = comp;
            }
            // Collect all child IDs to find top-level components
            const childIds = new Set();
            for (const comp of components) {
                if (comp.children) comp.children.forEach(id => childIds.add(id));
                if (comp.child) childIds.add(comp.child);
            }
            // Render all top-level components (not referenced as children)
            this.container.innerHTML = '';
            const topComponents = components.filter(c => !childIds.has(c.id));
            console.log('[A2UI] Rendering', topComponents.length, 'top-level components:', topComponents.map(c => c.id));
            for (const comp of topComponents) {
                const el = this.renderComponent(comp);
                if (el) this.container.appendChild(el);
            }
        }

        updateDataModel({ path, value }) {
            if (path === '/') {
                Object.assign(this.dataModel, value);
            } else {
                const keys = path.split('/').filter(Boolean);
                let obj = this.dataModel;
                for (let i = 0; i < keys.length - 1; i++) {
                    if (!obj[keys[i]]) obj[keys[i]] = {};
                    obj = obj[keys[i]];
                }
                obj[keys[keys.length - 1]] = value;
            }
        }

        resolveValue(val) {
            if (val == null) return '';
            if (typeof val === 'object') {
                if (val.path) {
                    return val.path.split('/').filter(Boolean).reduce((o, k) => o?.[k], this.dataModel) ?? '';
                }
                if (val.literalString) return val.literalString;
            }
            return val;
        }

        renderComponent(comp) {
            const type = comp.component;
            const renderer = this.getRenderer(type);
            if (renderer) return renderer(comp);
            // Unknown component: render as text fallback
            const div = document.createElement('div');
            div.className = 'a2ui-unknown';
            div.textContent = `[${type}]`;
            return div;
        }

        getRenderer(type) {
            const map = {
                'Text': this.renderText.bind(this),
                'Image': this.renderImage.bind(this),
                'Icon': this.renderIcon.bind(this),
                'Row': this.renderRow.bind(this),
                'Column': this.renderColumn.bind(this),
                'List': this.renderList.bind(this),
                'Card': this.renderCard.bind(this),
                'Tabs': this.renderTabs.bind(this),
                'Modal': this.renderModal.bind(this),
                'Divider': this.renderDivider.bind(this),
                'Button': this.renderButton.bind(this),
                'TextField': this.renderTextField.bind(this),
                'CheckBox': this.renderCheckBox.bind(this),
                'ChoicePicker': this.renderChoicePicker.bind(this),
                'Slider': this.renderSlider.bind(this),
                'DateTimeInput': this.renderDateTimeInput.bind(this),
                // Custom extensions
                'DataTable': this.renderDataTable.bind(this),
                'KeyValue': this.renderKeyValue.bind(this),
                'FilterTabs': this.renderFilterTabs.bind(this),
                'Badge': this.renderBadge.bind(this),
            };
            return map[type];
        }

        // ── Basic Catalog Renderers ──

        renderText(comp) {
            const text = this.resolveValue(comp.text);
            const variant = comp.variant || 'body';
            const tagMap = { h1: 'h1', h2: 'h2', h3: 'h3', h4: 'h4', h5: 'h5', caption: 'small', body: 'p' };
            const el = document.createElement(tagMap[variant] || 'p');
            el.className = `a2ui-text a2ui-text-${variant}`;
            el.innerHTML = this.formatText(text);
            return el;
        }

        formatText(text) {
            if (!text) return '';
            // Basic markdown: **bold**, *italic*, `code`, line breaks
            return text
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.+?)\*/g, '<em>$1</em>')
                .replace(/`(.+?)`/g, '<code>$1</code>')
                .replace(/\n/g, '<br>');
        }

        renderImage(comp) {
            const el = document.createElement('img');
            el.className = 'a2ui-image';
            el.src = this.resolveValue(comp.url);
            el.alt = this.resolveValue(comp.description) || '';
            if (comp.fit) el.style.objectFit = comp.fit;
            return el;
        }

        renderIcon(comp) {
            const el = document.createElement('i');
            el.className = 'a2ui-icon ph';
            const name = this.resolveValue(comp.name);
            if (typeof name === 'string') el.className += ` ph-${name}`;
            return el;
        }

        renderRow(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-row';
            this.renderChildren(comp.children, el);
            return el;
        }

        renderColumn(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-column';
            this.renderChildren(comp.children, el);
            return el;
        }

        renderList(comp) {
            const ordered = comp.ordered || false;
            const el = document.createElement(ordered ? 'ol' : 'ul');
            el.className = 'a2ui-list';
            const items = comp.items || [];
            for (const item of items) {
                const li = document.createElement('li');
                if (typeof item === 'string') {
                    li.textContent = this.resolveValue(item);
                } else if (item.component) {
                    li.appendChild(this.renderComponent(item));
                } else {
                    li.textContent = this.resolveValue(item);
                }
                el.appendChild(li);
            }
            return el;
        }

        renderCard(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-card';
            if (comp.title) {
                const title = document.createElement('div');
                title.className = 'a2ui-card-title';
                title.textContent = this.resolveValue(comp.title);
                el.appendChild(title);
            }
            const body = document.createElement('div');
            body.className = 'a2ui-card-body';
            this.renderChildren(comp.children, body);
            el.appendChild(body);
            return el;
        }

        renderTabs(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-tabs';
            const tabs = comp.tabs || [];
            const activeTab = comp.active || '';
            for (const tab of tabs) {
                const btn = document.createElement('button');
                btn.className = 'a2ui-tab' + (tab.key === activeTab ? ' active' : '');
                btn.textContent = this.resolveValue(tab.label || tab.key);
                btn.addEventListener('click', () => {
                    if (comp.action) this.handleAction(comp.action, { active: tab.key });
                });
                el.appendChild(btn);
            }
            return el;
        }

        renderModal(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-modal-overlay';
            const inner = document.createElement('div');
            inner.className = 'a2ui-modal';
            if (comp.title) {
                const title = document.createElement('div');
                title.className = 'a2ui-modal-title';
                title.textContent = this.resolveValue(comp.title);
                inner.appendChild(title);
            }
            this.renderChildren(comp.children, inner);
            el.appendChild(inner);
            return el;
        }

        renderDivider() {
            return document.createElement('hr');
        }

        renderButton(comp) {
            const el = document.createElement('button');
            const variant = comp.variant || 'secondary';
            el.className = `a2ui-btn a2ui-btn-${variant}`;
            if (comp.disabled) el.disabled = true;
            // Button text from child component
            if (comp.child && this.components[comp.child]) {
                el.appendChild(this.renderComponent(this.components[comp.child]));
            }
            el.addEventListener('click', () => {
                if (comp.action && !el.disabled) this.handleAction(comp.action, {}, el);
            });
            return el;
        }

        renderTextField(comp) {
            const wrapper = document.createElement('div');
            wrapper.className = 'a2ui-textfield';
            if (comp.label) {
                const label = document.createElement('label');
                label.textContent = this.resolveValue(comp.label);
                wrapper.appendChild(label);
            }
            const input = document.createElement('input');
            input.type = comp.inputType || 'text';
            input.placeholder = this.resolveValue(comp.placeholder) || '';
            if (comp.value) input.value = this.resolveValue(comp.value);
            wrapper.appendChild(input);
            return wrapper;
        }

        renderCheckBox(comp) {
            const wrapper = document.createElement('label');
            wrapper.className = 'a2ui-checkbox';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = !!comp.value;
            wrapper.appendChild(input);
            if (comp.label) {
                const span = document.createElement('span');
                span.textContent = this.resolveValue(comp.label);
                wrapper.appendChild(span);
            }
            return wrapper;
        }

        renderChoicePicker(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-choice-picker';
            const choices = comp.choices || [];
            for (const choice of choices) {
                const btn = document.createElement('button');
                btn.className = 'a2ui-choice' + (choice.key === comp.value ? ' active' : '');
                btn.textContent = this.resolveValue(choice.label || choice.key);
                btn.addEventListener('click', () => {
                    if (comp.action) this.handleAction(comp.action, { value: choice.key });
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
            el.value = comp.value || 50;
            return el;
        }

        renderDateTimeInput(comp) {
            const wrapper = document.createElement('div');
            wrapper.className = 'a2ui-datetime';
            if (comp.label) {
                const label = document.createElement('label');
                label.textContent = this.resolveValue(comp.label);
                wrapper.appendChild(label);
            }
            const input = document.createElement('input');
            input.type = comp.enableTime ? 'datetime-local' : 'date';
            if (comp.value) input.value = this.resolveValue(comp.value);
            wrapper.appendChild(input);
            return wrapper;
        }

        // ── Custom Extension Renderers ──

        renderDataTable(comp) {
            const wrapper = document.createElement('div');
            wrapper.className = 'a2ui-table-wrapper';
            const table = document.createElement('table');
            table.className = 'a2ui-table';

            // Header
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            for (const col of (comp.columns || [])) {
                const th = document.createElement('th');
                th.textContent = col.label || col.key;
                if (col.align) th.style.textAlign = col.align;
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
                    td.textContent = row[col.key] ?? '';
                    if (col.align) td.style.textAlign = col.align;
                    tr.appendChild(td);
                }
                // Row click action
                if (comp.rowAction) {
                    tr.className = 'a2ui-table-clickable';
                    tr.addEventListener('click', () => {
                        const action = this.resolveActionTemplate(comp.rowAction, row);
                        this.handleAction(action);
                    });
                }
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);

            // Footer
            if (comp.footer) {
                const tfoot = document.createElement('tfoot');
                const footerRow = document.createElement('tr');
                footerRow.className = 'a2ui-table-footer';
                const values = comp.footer.values || [];
                // First cell spans for label
                if (comp.footer.label) {
                    const labelTd = document.createElement('td');
                    labelTd.textContent = comp.footer.label;
                    labelTd.colSpan = Math.max(1, (comp.columns || []).length - values.length);
                    footerRow.appendChild(labelTd);
                }
                for (const val of values) {
                    const td = document.createElement('td');
                    td.textContent = val;
                    footerRow.appendChild(td);
                }
                tfoot.appendChild(footerRow);
                table.appendChild(tfoot);
            }

            wrapper.appendChild(table);
            return wrapper;
        }

        renderKeyValue(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-kv';
            for (const pair of (comp.pairs || [])) {
                const item = document.createElement('div');
                item.className = 'a2ui-kv-item';
                const label = document.createElement('label');
                label.textContent = pair.label;
                const value = document.createElement('div');
                value.className = 'value';
                value.textContent = this.resolveValue(pair.value);
                item.appendChild(label);
                item.appendChild(value);
                el.appendChild(item);
            }
            return el;
        }

        renderFilterTabs(comp) {
            const el = document.createElement('div');
            el.className = 'a2ui-filter-tabs';
            const tabs = comp.tabs || [];
            const active = comp.active || '';
            for (const tab of tabs) {
                const btn = document.createElement('button');
                btn.className = 'a2ui-filter-tab' + (tab.key === active ? ' active' : '');
                btn.textContent = tab.label;
                btn.addEventListener('click', () => {
                    // Update active state visually
                    el.querySelectorAll('.a2ui-filter-tab').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    // Fire action
                    if (comp.action) this.handleAction(comp.action, { active: tab.key });
                });
                el.appendChild(btn);
            }
            return el;
        }

        renderBadge(comp) {
            const el = document.createElement('span');
            const variant = comp.variant || 'info';
            el.className = `a2ui-badge a2ui-badge-${variant}`;
            el.textContent = this.resolveValue(comp.text);
            return el;
        }

        // ── Helpers ──

        resolveActionTemplate(actionTemplate, rowData) {
            // Deep clone and replace {key} placeholders with row values
            const resolve = (val) => {
                if (typeof val === 'string') {
                    return val.replace(/\{(\w+)\}/g, (_, key) => rowData[key] ?? '');
                }
                if (Array.isArray(val)) return val.map(resolve);
                if (val && typeof val === 'object') {
                    const out = {};
                    for (const [k, v] of Object.entries(val)) out[k] = resolve(v);
                    return out;
                }
                return val;
            };
            return resolve(actionTemplate);
        }

        renderChildren(childIds, container) {
            if (!childIds) return;
            for (const childId of childIds) {
                const childComp = this.components[childId];
                if (childComp) {
                    const el = this.renderComponent(childComp);
                    if (el) container.appendChild(el);
                }
            }
        }

        handleAction(action, extraData = {}, btnEl = null) {
            if (action?.event) {
                const eventName = action.event.name;

                // Client-side navigation: back to voucher list
                if (eventName === 'back_to_voucher_list') {
                    refreshVoucherList();
                    return;
                }

                // Client-side: edit voucher — load data and render edit form
                if (eventName === 'edit_voucher') {
                    const voucherId = action.event.data?.voucherId;
                    if (voucherId) loadVoucherEditForm(voucherId);
                    return;
                }

                const payload = {
                    event: eventName,
                    data: { ...(action.event.data || {}), ...extraData },
                };
                // Disable button immediately to prevent double-click
                if (btnEl) {
                    btnEl.disabled = true;
                    btnEl.dataset.origText = btnEl.textContent;
                    btnEl.textContent = '处理中...';
                }
                // Show loading feedback for confirm actions
                if (eventName === 'confirm_voucher') {
                    addMessage('正在过账...', 'ai');
                }
                fetch('/api/a2ui-action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(authToken ? { 'Authorization': 'Bearer ' + authToken } : {}),
                    },
                    body: JSON.stringify(payload),
                }).then(r => r.json()).then(data => {
                    // Handle file picker request for attachment upload
                    if (data.status === 'open_file_picker') {
                        openAttachmentPicker(data.voucherId, data.accept);
                        if (btnEl) {
                            btnEl.disabled = false;
                            btnEl.textContent = btnEl.dataset.origText || '上传附件';
                        }
                        return;
                    }
                    // Show server message to user
                    if (data.message) {
                        addMessage(data.message, 'ai');
                    }
                    // Re-render A2UI if returned
                    if (data.a2ui?.messages && data.a2ui.messages.length > 0) {
                        this.processMessages(data.a2ui.messages);
                    } else if (data.status === 'posted' || data.status === 'already_posted') {
                        // Mark button as done
                        if (btnEl) {
                            btnEl.textContent = '已过账';
                            btnEl.classList.remove('a2ui-btn-primary');
                            btnEl.classList.add('a2ui-btn-secondary');
                        }
                    }
                }).catch(err => {
                    console.error('A2UI action error:', err);
                    addMessage('操作失败：' + (err.message || '网络错误'), 'ai');
                    // Re-enable button on error
                    if (btnEl) {
                        btnEl.disabled = false;
                        btnEl.textContent = btnEl.dataset.origText || '确认并记账';
                    }
                });
            }
        }
    }

    // ── Process AI Response ─────────────────────────────────────────────────

    async function processAIResponse(input) {
        const resp = await apiFetch('/api/chat', {
            method: 'POST',
            body: JSON.stringify({ message: input, session_id: sessionId }),
        });

        let finalData = null;
        let errorData = null;
        const streamMsg = addStreamingMessage();

        await consumeSSE(resp, {
            onDelta: (text) => appendStreamingText(streamMsg, text),
            onResult: (data) => { finalData = data; },
            onError: (data) => { errorData = data; },
        });

        if (errorData) {
            streamMsg.remove();
            throw new Error(errorData.reply || '服务器处理出错');
        }
        if (!finalData) {
            streamMsg.remove();
            throw new Error('未收到服务器响应，请检查网络连接');
        }

        sessionId = finalData.session_id;
        finalizeStreamingMessage(streamMsg, finalData.reply);

        // A2UI path: render declarative components
        if (finalData.a2ui?.messages && finalData.a2ui.messages.length > 0) {
            console.log('[A2UI] Received', finalData.a2ui.messages.length, 'messages');
            const dynamicContainer = document.getElementById('viewDynamic');
            if (dynamicContainer) {
                try {
                    const renderer = new A2UIRenderer(dynamicContainer);
                    window._a2uiRenderer = renderer;
                    renderer.processMessages(finalData.a2ui.messages);
                    switchView('dynamic');
                    return;
                } catch (e) {
                    console.error('A2UI render error, falling back to legacy view:', e);
                    dynamicContainer.innerHTML = '';
                }
            }
        }

        // Fallback: legacy view routing
        const view = finalData.view;
        if (view === 'voucher' && finalData.voucher) {
            currentVoucherId = finalData.voucher.voucher_id;
            activateWorkspace(finalData.voucher);
            switchView('voucher');
        } else if (view === 'rules') {
            renderRules(finalData.rules || [], finalData.rule_mgmt?.action);
            switchView('rules');
            if (finalData.rule_mgmt) {
                if (finalData.rule_mgmt.action === 'create') {
                    openAddRuleModal(finalData.rule_mgmt.rule_type);
                }
            }
        } else if (view === 'voucher_list' && finalData.view_data) {
            renderVoucherList(finalData.view_data.vouchers, finalData.view_data.total, finalData.view_data.status_filter);
            switchView('voucher_list');
        } else if (view === 'user_list' && finalData.view_data) {
            renderUserList(finalData.view_data.users);
            switchView('user_list');
        }
    }

    async function handleFileUpload(file, extraMessage) {
        const formData = new FormData();
        formData.append('file', file);
        if (sessionId) formData.append('session_id', sessionId);

        const resp = await apiFetch('/api/upload', {
            method: 'POST',
            body: formData,
        });

        let finalData = null;
        let errorData = null;
        const streamMsg = addStreamingMessage();

        await consumeSSE(resp, {
            onProgress: (text) => {
                const textEl = streamMsg.querySelector('.streaming-text');
                if (textEl) textEl.textContent = text;
            },
            onResult: (data) => { finalData = data; },
            onError: (data) => { errorData = data; },
        });

        if (errorData) {
            streamMsg.remove();
            throw new Error(errorData.reply || '文件处理出错');
        }
        if (!finalData) {
            streamMsg.remove();
            throw new Error('未收到服务器响应');
        }

        sessionId = finalData.session_id;
        finalizeStreamingMessage(streamMsg, finalData.reply);

        if (finalData.file) {
            showSourceData(finalData.file);
        }

        // A2UI path
        if (finalData.a2ui?.messages && finalData.a2ui.messages.length > 0) {
            const dynamicContainer = document.getElementById('viewDynamic');
            if (dynamicContainer) {
                try {
                    const renderer = new A2UIRenderer(dynamicContainer);
                    window._a2uiRenderer = renderer;
                    renderer.processMessages(finalData.a2ui.messages);
                    switchView('dynamic');
                    if (finalData.vouchers && finalData.vouchers.length > 1) {
                        addMessage(`共生成 ${finalData.vouchers.length} 张凭证，当前显示最后一张。`, 'ai');
                    }
                    return;
                } catch (e) {
                    console.error('A2UI render error, falling back to legacy view:', e);
                    dynamicContainer.innerHTML = '';
                }
            }
        }

        // Fallback: legacy view
        if (finalData.vouchers && finalData.vouchers.length > 0) {
            const lastVoucher = finalData.vouchers[finalData.vouchers.length - 1];
            currentVoucherId = lastVoucher.voucher_id;
            activateWorkspace(lastVoucher);
            switchView('voucher');

            if (finalData.vouchers.length > 1) {
                addMessage(`共生成 ${finalData.vouchers.length} 张凭证，当前显示最后一张。`, 'ai');
            }
        }
    }

    async function confirmVoucher() {
        if (!currentVoucherId || isPosted) return;

        const btnText = createTransactionBtn.innerHTML;
        createTransactionBtn.innerHTML = `<i class="ph ph-spinner"></i> 过账中...`;
        createTransactionBtn.disabled = true;

        try {
            const resp = await apiFetch('/api/confirm', {
                method: 'POST',
                body: JSON.stringify({ session_id: sessionId, voucher_id: currentVoucherId }),
            });
            const data = await resp.json();

            isPosted = true;
            createTransactionBtn.innerHTML = `<i class="ph ph-check"></i> 已记账`;
            createTransactionBtn.style.background = '#059669';

            addMessage(data.message, 'ai');
        } catch (err) {
            createTransactionBtn.innerHTML = btnText;
            createTransactionBtn.style.background = '';
            createTransactionBtn.disabled = false;
            addMessage('过账失败，请重试。', 'ai');
        }
    }

    // ── Workspace Logic ────────────────────────────────────────────────────────

    function showSourceData(fileInfo) {
        const name = fileInfo.name || '';
        const ext = name.split('.').pop().toLowerCase();
        const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];
        let iconClass = 'ph-file-xls';
        if (imageExts.includes(ext)) iconClass = 'ph-image';
        else if (ext === 'pdf') iconClass = 'ph-file-pdf';

        sourceDataContent.innerHTML = `
            <div class="source-item">
                <i class="ph ${iconClass} icon"></i>
                <div class="details">
                    <div class="name">${name}</div>
                    <div class="meta">${fileInfo.size_kb} KB • 刚刚上传</div>
                </div>
            </div>
        `;
        sourceBadge.textContent = '1 份文件';
    }

    function activateWorkspace(voucherData) {
        voucherWorkspaceContent.style.display = 'block';

        isPosted = false;
        createTransactionBtn.innerHTML = '确认并记账 <i class="ph ph-check-circle"></i>';
        createTransactionBtn.style.background = '';
        createTransactionBtn.disabled = false;

        document.getElementById('voucherIdField').textContent = voucherData.voucher_id || '—';
        document.getElementById('companyCodeField').textContent = voucherData.company_code || '—';
        document.getElementById('docTypeField').textContent = voucherData.document_type || '—';
        document.getElementById('referenceField').textContent = voucherData.reference || '—';
        document.getElementById('headerTextField').textContent = voucherData.header_text || '—';
        document.getElementById('confidenceField').textContent = voucherData.confidence || '—';

        if (voucherData.document_date) document.getElementById('docDateField').value = voucherData.document_date;
        if (voucherData.posting_date) document.getElementById('postDateField').value = voucherData.posting_date;

        const warningsBar = document.getElementById('warningsBar');
        if (voucherData.warnings && voucherData.warnings.length > 0) {
            warningsBar.innerHTML = '⚠️ ' + voucherData.warnings.join('；');
            warningsBar.style.display = 'block';
        } else {
            warningsBar.style.display = 'none';
        }

        renderVoucher(voucherData.rows);
    }

    function renderVoucher(rows) {
        voucherRows.innerHTML = '';
        let totalDr = 0;
        let totalCr = 0;

        rows.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="col-no">${row.line_no}</td>
                <td class="col-acct"><input type="text" value="${row.account_code}"></td>
                <td class="col-name"><input type="text" value="${row.account_name}"></td>
                <td class="col-dc"><span class="dc-badge ${row.debit_credit === 'S' ? 'dc-debit' : 'dc-credit'}">${row.debit_credit === 'S' ? '借' : '贷'}</span></td>
                <td class="col-amt"><input type="number" value="${row.debit}" class="amount-input" step="0.01"></td>
                <td class="col-amt"><input type="number" value="${row.credit}" class="amount-input" step="0.01"></td>
                <td class="col-cur">${row.currency}</td>
                <td class="col-code"><input type="text" value="${row.customer_code}"></td>
                <td class="col-name"><input type="text" value="${row.customer_name}"></td>
                <td class="col-code"><input type="text" value="${row.tax_code}"></td>
                <td class="col-code"><input type="text" value="${row.profit_center}"></td>
                <td class="col-code"><input type="text" value="${row.cost_center}"></td>
                <td class="col-code"><input type="text" value="${row.assignment}"></td>
                <td class="col-text"><input type="text" value="${row.text}"></td>
                <td><button class="icon-btn-small delete-row-btn" title="删除"><i class="ph ph-trash"></i></button></td>
            `;
            tr.querySelector('.delete-row-btn').addEventListener('click', () => { tr.remove(); recalcTotals(); });
            tr.querySelectorAll('.amount-input').forEach(inp => inp.addEventListener('input', recalcTotals));
            voucherRows.appendChild(tr);
            totalDr += row.debit;
            totalCr += row.credit;
        });

        updateTotals(totalDr, totalCr);
    }

    function recalcTotals() {
        let totalDr = 0, totalCr = 0;
        voucherRows.querySelectorAll('tr').forEach(tr => {
            const inputs = tr.querySelectorAll('.amount-input');
            if (inputs.length >= 2) {
                totalDr += parseFloat(inputs[0].value) || 0;
                totalCr += parseFloat(inputs[1].value) || 0;
            }
        });
        updateTotals(totalDr, totalCr);
    }

    function updateTotals(dr, cr) {
        totalDebitEl.textContent = dr.toFixed(2);
        totalCreditEl.textContent = cr.toFixed(2);
    }

    // ── Typing Indicator ──────────────────────────────────────────────────────

    function showTypingIndicator() {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message ai-message';
        msgDiv.id = 'typing-indicator';
        msgDiv.innerHTML = `
            <div class="avatar"><i class="ph ph-fire"></i></div>
            <div class="content">
                <div class="typing-dots"><span>.</span><span>.</span><span>.</span></div>
            </div>
        `;
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function removeTypingIndicator() {
        const el = document.getElementById('typing-indicator');
        if (el) el.remove();
    }

    // ── Streaming Message ──────────────────────────────────────────────────────

    function addStreamingMessage() {
        removeTypingIndicator();
        const msgDiv = document.createElement('div');
        msgDiv.className = 'message ai-message';
        msgDiv.id = 'streaming-message';
        msgDiv.innerHTML = `
            <div class="avatar"><i class="ph ph-fire"></i></div>
            <div class="content"><span class="streaming-text"></span><span class="streaming-cursor">▍</span></div>
        `;
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return msgDiv;
    }

    function appendStreamingText(msgEl, text) {
        const textEl = msgEl.querySelector('.streaming-text');
        if (textEl) textEl.textContent += text;
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function finalizeStreamingMessage(msgEl, reply) {
        if (!msgEl) return;
        const cursorEl = msgEl.querySelector('.streaming-cursor');
        if (cursorEl) cursorEl.remove();
        const textEl = msgEl.querySelector('.streaming-text');
        if (textEl && reply) {
            textEl.innerHTML = formatContent(reply);
        } else if (textEl) {
            textEl.innerHTML = formatContent(textEl.textContent);
        }
        msgEl.removeAttribute('id');
    }

    // ── Event Listeners ───────────────────────────────────────────────────────

    sendBtn.addEventListener('click', sendMessage);
    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    userInput.addEventListener('input', resizeTextarea);
    uploadBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            pendingFile = e.target.files[0];
            userInput.value = `📎 ${pendingFile.name}`;
            resizeTextarea();
        }
    });

    // Drag & Drop
    const chatPanel = document.querySelector('.sidebar');
    chatPanel.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); chatPanel.classList.add('drag-over'); });
    chatPanel.addEventListener('dragleave', (e) => { e.preventDefault(); e.stopPropagation(); if (!chatPanel.contains(e.relatedTarget)) chatPanel.classList.remove('drag-over'); });
    chatPanel.addEventListener('drop', (e) => {
        e.preventDefault(); e.stopPropagation(); chatPanel.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) { pendingFile = files[0]; userInput.value = `📎 ${pendingFile.name}`; resizeTextarea(); sendMessage(); }
    });

    createTransactionBtn.addEventListener('click', confirmVoucher);

    // Hint cards
    document.querySelectorAll('.hint-card').forEach(card => {
        card.addEventListener('click', () => {
            const hint = card.dataset.hint;
            if (hint) {
                userInput.value = hint;
                resizeTextarea();
                sendMessage();
            }
        });
    });

    // Toggle source data section
    if (toggleSourceBtn) {
        toggleSourceBtn.addEventListener('click', () => {
            const isVisible = sourceDataContent.style.display !== 'none';
            sourceDataContent.style.display = isVisible ? 'none' : 'block';
            toggleSourceBtn.innerHTML = isVisible ? '<i class="ph ph-caret-down"></i>' : '<i class="ph ph-caret-up"></i>';
        });
    }

    // ── Dynamic View Renderers ─────────────────────────────────────────────────

    // --- Voucher List View ---
    function renderVoucherList(vouchers, total, statusFilter) {
        const container = document.getElementById('voucherListContent');
        const statusLabel = statusFilter === 'draft' ? '草稿' : statusFilter === 'posted' ? '已过账' : '全部';

        let html = `
            <div class="view-toolbar">
                <div class="view-toolbar-info">
                    <span class="view-count">${total} 条${statusLabel}凭证</span>
                </div>
                <div class="view-toolbar-actions">
                    <select id="vlStatusFilter" class="history-select">
                        <option value="" ${!statusFilter ? 'selected' : ''}>全部状态</option>
                        <option value="draft" ${statusFilter === 'draft' ? 'selected' : ''}>草稿</option>
                        <option value="posted" ${statusFilter === 'posted' ? 'selected' : ''}>已过账</option>
                    </select>
                    <button class="btn btn-secondary" id="vlRefreshBtn"><i class="ph ph-arrows-clockwise"></i> 刷新</button>
                </div>
            </div>
        `;

        if (!vouchers || vouchers.length === 0) {
            html += `<div class="view-empty-state"><i class="ph ph-receipt"></i><p>暂无凭证记录</p></div>`;
        } else {
            html += `<div class="history-list">`;
            vouchers.forEach(v => {
                const statusBadge = v.status === 'posted'
                    ? '<span class="status-badge status-posted">已过账</span>'
                    : '<span class="status-badge status-draft">草稿</span>';
                const createdAt = new Date(v.created_at).toLocaleString('zh-CN');
                const postedInfo = v.posted_at
                    ? `<span class="history-meta-item"><i class="ph ph-check-circle"></i> 过账: ${new Date(v.posted_at).toLocaleString('zh-CN')}</span>`
                    : '';
                const postBtn = v.status === 'draft'
                    ? `<button class="btn btn-primary btn-sm voucher-post-btn" data-vid="${v.voucher_id}"><i class="ph ph-check-circle"></i> 过账</button>`
                    : '';

                html += `
                    <div class="history-card voucher-card-clickable" data-vid="${v.voucher_id}">
                        <div class="history-card-header">
                            <div class="history-card-title">
                                <span class="voucher-id-badge">${v.voucher_id}</span>
                                ${statusBadge}
                            </div>
                            <div class="history-card-actions">
                                ${postBtn}
                                <span class="history-meta"><i class="ph ph-clock"></i> ${createdAt}</span>
                            </div>
                        </div>
                        <div class="history-card-body">
                            <div class="history-meta-row">
                                <span class="history-meta-item"><i class="ph ph-buildings"></i> ${v.company_code || '—'}</span>
                                <span class="history-meta-item"><i class="ph ph-file-text"></i> ${v.document_type || '—'}</span>
                                <span class="history-meta-item"><i class="ph ph-calendar"></i> ${v.document_date || '—'}</span>
                                <span class="history-meta-item"><i class="ph ph-user"></i> ${v.user_display_name || '—'}</span>
                            </div>
                            <div class="history-meta-row">
                                <span class="history-meta-item"><i class="ph ph-text-aa"></i> ${v.header_text || '—'}</span>
                                ${postedInfo}
                            </div>
                        </div>
                    </div>
                `;
            });
            html += `</div>`;
        }

        container.innerHTML = html;

        // Bind events for the dynamically created filter/refresh
        const statusFilterEl = document.getElementById('vlStatusFilter');
        const refreshBtn = document.getElementById('vlRefreshBtn');
        if (statusFilterEl) {
            statusFilterEl.addEventListener('change', () => {
                const status = statusFilterEl.value || null;
                refreshVoucherList(status);
            });
        }
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                const status = statusFilterEl ? (statusFilterEl.value || null) : null;
                refreshVoucherList(status);
            });
        }

        // Click card to view detail
        container.querySelectorAll('.voucher-card-clickable').forEach(card => {
            card.addEventListener('click', (e) => {
                // Don't navigate if clicking the post button
                if (e.target.closest('.voucher-post-btn')) return;
                const vid = card.dataset.vid;
                fetchVoucherDetail(vid);
            });
        });

        // Post buttons
        container.querySelectorAll('.voucher-post-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const vid = btn.dataset.vid;
                postVoucherFromList(vid, btn);
            });
        });
    }

    async function fetchVoucherDetail(voucherId) {
        try {
            const resp = await apiFetch(`/api/vouchers/${voucherId}`);
            const data = await resp.json();
            if (data.error) {
                addMessage(`加载凭证失败：${data.error}`, 'ai');
                return;
            }
            const v = data.voucher;
            currentVoucherId = v.voucher_id;
            activateWorkspace(v);
            // If already posted, update button state
            if (v.status === 'posted') {
                isPosted = true;
                createTransactionBtn.innerHTML = '<i class="ph ph-check"></i> 已记账';
                createTransactionBtn.style.background = '#059669';
                createTransactionBtn.disabled = true;
            }
            switchView('voucher');
        } catch (err) {
            addMessage('加载凭证详情失败，请重试。', 'ai');
        }
    }

    async function postVoucherFromList(voucherId, btn) {
        const origHTML = btn.innerHTML;
        btn.innerHTML = '<i class="ph ph-spinner ph-spin"></i>';
        btn.disabled = true;

        try {
            const resp = await apiFetch('/api/confirm', {
                method: 'POST',
                body: JSON.stringify({ voucher_id: voucherId }),
            });
            const data = await resp.json();

            if (data.status === 'posted') {
                addMessage(data.message, 'ai');
                // Refresh the list to reflect updated status
                const statusFilterEl = document.getElementById('vlStatusFilter');
                const status = statusFilterEl ? (statusFilterEl.value || null) : null;
                refreshVoucherList(status);
            } else {
                addMessage(data.message || '过账失败', 'ai');
                btn.innerHTML = origHTML;
                btn.disabled = false;
            }
        } catch (err) {
            addMessage('过账失败，请重试。', 'ai');
            btn.innerHTML = origHTML;
            btn.disabled = false;
        }
    }

    async function refreshVoucherList(status) {
        try {
            const resp = await apiFetch('/api/a2ui-action', {
                method: 'POST',
                body: JSON.stringify({ event: 'filter_vouchers', data: { active: status || '' } }),
            });
            const data = await resp.json();
            if (data.a2ui?.messages) {
                const dynamicContainer = document.getElementById('viewDynamic');
                if (dynamicContainer) {
                    const renderer = new A2UIRenderer(dynamicContainer);
                    window._a2uiRenderer = renderer;
                    renderer.processMessages(data.a2ui.messages);
                    switchView('dynamic');
                }
            }
        } catch (err) {
            console.error('Failed to refresh voucher list:', err);
            addMessage('加载凭证列表失败', 'ai');
        }
    }

    // --- Rules View ---
    const bizTypeLabels = { 'sales_revenue': '销售收入', 'expense': '费用报销', 'asset_purchase': '资产采购', 'salary': '工资薪酬', 'loan': '借款/还款' };

    function renderRules(rules, ruleMgmtAction) {
        const container = document.getElementById('rulesContent');

        let html = `
            <div class="view-toolbar">
                <div class="view-toolbar-info">
                    <span class="view-count">${rules.length} 条规则</span>
                </div>
                <div class="view-toolbar-actions">
                    <button class="btn btn-primary" id="addRuleBtn"><i class="ph ph-plus"></i> 新增规则</button>
                </div>
            </div>
        `;

        rules.forEach(rule => {
            const bizLabel = bizTypeLabels[rule.business_type] || rule.business_type;
            const productLabel = rule.product_type === '*' ? '全部' : rule.product_type;
            const taxLabel = rule.tax_rate === '*' ? '全部' : rule.tax_rate;
            const docLabel = rule.document_type || 'DR';

            let linesHTML = '';
            rule.lines.forEach(line => {
                const dcClass = line.debit_credit === 'S' ? 'rule-dc-debit' : 'rule-dc-credit';
                linesHTML += `
                    <tr>
                        <td>${line.line_no}</td>
                        <td><span class="rule-dc-badge ${dcClass}">${line.debit_credit_display}</span></td>
                        <td>${line.account_code}</td>
                        <td>${line.account_name}</td>
                        <td><span class="rule-amount-field">${line.amount_field_display}</span></td>
                        <td>${line.customer_source || '—'}</td>
                        <td>${line.tax_code_rule || '—'}</td>
                        <td>${line.profit_center_source || '—'}</td>
                        <td>${line.cost_center_source || '—'}</td>
                        <td>${line.assignment_source || '—'}</td>
                        <td><span class="rule-text-template">${line.text_template || '—'}</span></td>
                    </tr>
                `;
            });

            html += `
                <div class="rule-card">
                    <div class="rule-card-header">
                        <div class="rule-card-title">
                            <span class="rule-code-badge">${rule.rule_code}</span>
                            <h3>${bizLabel}</h3>
                        </div>
                        <div class="rule-card-meta">
                            <span class="meta-tag"><i class="ph ph-package"></i> 产品: ${productLabel}</span>
                            <span class="meta-tag"><i class="ph ph-percent"></i> 税率: ${taxLabel}</span>
                            <span class="meta-tag"><i class="ph ph-file-text"></i> 凭证类型: ${docLabel}</span>
                        </div>
                        <div class="rule-card-actions">
                            <button class="icon-btn-small edit-rule-btn" data-rule-code="${rule.rule_code}" title="编辑"><i class="ph ph-pencil"></i></button>
                            <button class="icon-btn-small delete-rule-btn" data-rule-code="${rule.rule_code}" title="删除"><i class="ph ph-trash"></i></button>
                        </div>
                    </div>
                    <table class="rule-lines-table">
                        <thead><tr><th>行号</th><th>借/贷</th><th>科目代码</th><th>科目名称</th><th>金额取值</th><th>客户来源</th><th>税码规则</th><th>利润中心</th><th>成本中心</th><th>分配</th><th>摘要模板</th></tr></thead>
                        <tbody>${linesHTML}</tbody>
                    </table>
                </div>
            `;
        });

        container.innerHTML = html;

        // Bind events
        container.querySelectorAll('.edit-rule-btn').forEach(btn => {
            btn.addEventListener('click', () => openEditRuleModal(btn.dataset.ruleCode));
        });
        container.querySelectorAll('.delete-rule-btn').forEach(btn => {
            btn.addEventListener('click', () => deleteRule(btn.dataset.ruleCode));
        });
        document.getElementById('addRuleBtn')?.addEventListener('click', () => openAddRuleModal());
    }

    // --- User List View ---
    function renderUserList(users) {
        const container = document.getElementById('userListContent');

        let rowsHTML = '';
        users.forEach(u => {
            const roleLabel = u.role === 'admin' ? '<span class="role-badge role-admin">管理员</span>' : '<span class="role-badge role-user">普通用户</span>';
            const statusLabel = u.is_active ? '<span class="status-badge status-posted">启用</span>' : '<span class="status-badge status-draft">停用</span>';
            const createdAt = new Date(u.created_at).toLocaleString('zh-CN');

            rowsHTML += `
                <tr>
                    <td>${u.username}</td>
                    <td>${u.display_name}</td>
                    <td>${roleLabel}</td>
                    <td>${statusLabel}</td>
                    <td>${createdAt}</td>
                    <td class="users-actions">
                        <button class="icon-btn-small edit-user-btn" data-id="${u.id}" data-username="${u.username}" data-display="${u.display_name}" data-role="${u.role}" title="编辑"><i class="ph ph-pencil"></i></button>
                        <button class="icon-btn-small delete-user-btn" data-id="${u.id}" title="删除"><i class="ph ph-trash"></i></button>
                    </td>
                </tr>
            `;
        });

        container.innerHTML = `
            <div class="view-toolbar">
                <div class="view-toolbar-info">
                    <span class="view-count">${users.length} 个用户</span>
                </div>
                <div class="view-toolbar-actions">
                    <button class="btn btn-primary" id="addUserBtn"><i class="ph ph-plus"></i> 添加用户</button>
                </div>
            </div>
            <table class="users-table">
                <thead>
                    <tr>
                        <th>用户名</th>
                        <th>显示名称</th>
                        <th>角色</th>
                        <th>状态</th>
                        <th>创建时间</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>${rowsHTML}</tbody>
            </table>
        `;

        // Bind events
        container.querySelectorAll('.edit-user-btn').forEach(btn => {
            btn.addEventListener('click', () => openEditUserModal(btn.dataset));
        });
        container.querySelectorAll('.delete-user-btn').forEach(btn => {
            btn.addEventListener('click', () => deleteUser(btn.dataset.id));
        });
        document.getElementById('addUserBtn')?.addEventListener('click', openAddUserModal);
    }

    // ── User Management Modal ──────────────────────────────────────────────────

    function openAddUserModal() {
        let modal = document.getElementById('userModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'userModal';
            modal.className = 'modal-overlay';
            modal.style.display = 'none';
            modal.innerHTML = `
                <div class="modal-card glass-panel">
                    <div class="modal-header">
                        <h3 id="userModalTitle">添加用户</h3>
                        <button class="icon-btn" id="closeUserModal"><i class="ph ph-x"></i></button>
                    </div>
                    <form id="userForm">
                        <input type="hidden" id="editUserId">
                        <div class="form-field">
                            <label for="newUsername">用户名</label>
                            <input type="text" id="newUsername" required>
                        </div>
                        <div class="form-field">
                            <label for="newPassword">密码</label>
                            <input type="password" id="newPassword">
                        </div>
                        <div class="form-field">
                            <label for="newDisplayName">显示名称</label>
                            <input type="text" id="newDisplayName" required>
                        </div>
                        <div class="form-field">
                            <label for="newRole">角色</label>
                            <select id="newRole">
                                <option value="user">普通用户</option>
                                <option value="admin">管理员</option>
                            </select>
                        </div>
                        <div class="modal-actions">
                            <button type="button" class="btn btn-secondary" id="cancelUserModal">取消</button>
                            <button type="submit" class="btn btn-primary">保存</button>
                        </div>
                    </form>
                </div>
            `;
            document.body.appendChild(modal);

            // Bind modal events
            document.getElementById('closeUserModal').addEventListener('click', () => modal.style.display = 'none');
            document.getElementById('cancelUserModal').addEventListener('click', () => modal.style.display = 'none');
            document.getElementById('userForm').addEventListener('submit', handleUserFormSubmit);
        }

        document.getElementById('userModalTitle').textContent = '添加用户';
        document.getElementById('editUserId').value = '';
        document.getElementById('newUsername').value = '';
        document.getElementById('newUsername').disabled = false;
        document.getElementById('newPassword').value = '';
        document.getElementById('newPassword').required = true;
        document.getElementById('newPassword').placeholder = '';
        document.getElementById('newDisplayName').value = '';
        document.getElementById('newRole').value = 'user';
        modal.style.display = 'flex';
    }

    function openEditUserModal(dataset) {
        openAddUserModal(); // Create/show modal first
        document.getElementById('userModalTitle').textContent = '编辑用户';
        document.getElementById('editUserId').value = dataset.id;
        document.getElementById('newUsername').value = dataset.username;
        document.getElementById('newUsername').disabled = true;
        document.getElementById('newPassword').value = '';
        document.getElementById('newPassword').required = false;
        document.getElementById('newPassword').placeholder = '留空则不修改密码';
        document.getElementById('newDisplayName').value = dataset.display;
        document.getElementById('newRole').value = dataset.role;
    }

    async function handleUserFormSubmit(e) {
        e.preventDefault();
        const editId = document.getElementById('editUserId').value;
        const payload = {
            username: document.getElementById('newUsername').value.trim(),
            password: document.getElementById('newPassword').value,
            display_name: document.getElementById('newDisplayName').value.trim(),
            role: document.getElementById('newRole').value,
        };

        try {
            if (editId) {
                await apiFetch(`/api/users/${editId}`, { method: 'PUT', body: JSON.stringify(payload) });
            } else {
                await apiFetch('/api/users', { method: 'POST', body: JSON.stringify(payload) });
            }
            document.getElementById('userModal').style.display = 'none';
            // Refresh user list view
            const resp = await apiFetch('/api/users');
            const data = await resp.json();
            const users = (data.users || []).map(u => { u.pop?.('password_hash'); u.pop?.('password_salt'); return u; });
            renderUserList(data.users || []);
        } catch (err) {
            console.error('Failed to save user:', err);
            alert('保存用户失败');
        }
    }

    async function deleteUser(userId) {
        if (!confirm('确定要删除此用户吗？')) return;
        try {
            await apiFetch(`/api/users/${userId}`, { method: 'DELETE' });
            // Refresh user list
            const resp = await apiFetch('/api/users');
            const data = await resp.json();
            renderUserList(data.users || []);
        } catch (err) {
            console.error('Failed to delete user:', err);
            alert('删除用户失败');
        }
    }

    // ── Rule Edit View (right panel) ─────────────────────────────────────────

    function openAddRuleModal(ruleType) {
        renderRuleEditForm({ ruleType: ruleType || 'sales_revenue' });
    }

    async function openEditRuleModal(ruleCode) {
        let rules = [];
        try {
            const resp = await apiFetch('/api/rules');
            const data = await resp.json();
            rules = data.rules || [];
        } catch (err) {
            alert('加载规则失败');
            return;
        }
        const rule = rules.find(r => r.rule_code === ruleCode);
        if (!rule) { alert('规则不存在'); return; }
        renderRuleEditForm({ editRule: rule });
    }

    function renderRuleEditForm(opts = {}) {
        const container = document.getElementById('ruleEditContent');
        const editRule = opts.editRule;
        const isEdit = !!editRule;
        const bizType = editRule?.business_type || opts.ruleType || 'sales_revenue';
        const title = isEdit ? `编辑规则 · ${editRule.rule_code}` : '新增规则';

        const linesHTML = (editRule?.lines || [{}]).map((line, idx) => ruleLineRowHTML(line, idx + 1)).join('');

        container.innerHTML = `
            <form id="ruleForm">
                <input type="hidden" id="editRuleCode" value="${editRule?.rule_code || ''}">

                <div class="section">
                    <div class="section-header">
                        <h2>${title}</h2>
                        <div class="header-actions">
                            <button type="button" class="btn btn-secondary" id="cancelRuleEdit"><i class="ph ph-arrow-left"></i> 返回</button>
                            <button type="submit" class="btn btn-primary"><i class="ph ph-check"></i> 保存规则</button>
                        </div>
                    </div>
                    <div class="form-header-details">
                        <div class="form-group">
                            <label for="ruleCode">规则代码</label>
                            <input type="text" id="ruleCode" value="${editRule?.rule_code || ''}" placeholder="EXPENSE_STANDARD" required ${isEdit ? 'disabled' : ''}>
                        </div>
                        <div class="form-group">
                            <label for="ruleBusinessType">业务类型</label>
                            <select id="ruleBusinessType" class="date-input">
                                <option value="sales_revenue" ${bizType === 'sales_revenue' ? 'selected' : ''}>销售收入</option>
                                <option value="expense" ${bizType === 'expense' ? 'selected' : ''}>费用报销</option>
                                <option value="asset_purchase" ${bizType === 'asset_purchase' ? 'selected' : ''}>资产采购</option>
                                <option value="salary" ${bizType === 'salary' ? 'selected' : ''}>工资薪酬</option>
                                <option value="loan" ${bizType === 'loan' ? 'selected' : ''}>借款/还款</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label for="ruleProductType">产品类型</label>
                            <input type="text" id="ruleProductType" value="${editRule?.product_type || '*'}" placeholder="*">
                        </div>
                        <div class="form-group">
                            <label for="ruleTaxRate">税率</label>
                            <input type="text" id="ruleTaxRate" value="${editRule?.tax_rate || '*'}" placeholder="*">
                        </div>
                        <div class="form-group">
                            <label for="ruleDocType">凭证类型</label>
                            <input type="text" id="ruleDocType" value="${editRule?.document_type || 'DR'}" placeholder="DR">
                        </div>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">
                        <h2>分录行</h2>
                        <div class="header-actions">
                            <span class="badge" id="lineCount">${editRule?.lines?.length || 1} 行</span>
                            <button type="button" class="btn btn-secondary" id="addLineBtn"><i class="ph ph-plus"></i> 添加行</button>
                        </div>
                    </div>
                    <div class="voucher-table-wrapper">
                        <table class="voucher-table rule-edit-table">
                            <thead>
                                <tr>
                                    <th class="col-no">行号</th>
                                    <th class="col-dc">借/贷</th>
                                    <th class="col-acct">科目代码</th>
                                    <th class="col-name">科目名称</th>
                                    <th>金额取值</th>
                                    <th>客户来源</th>
                                    <th>税码规则</th>
                                    <th>利润中心</th>
                                    <th>成本中心</th>
                                    <th>分配</th>
                                    <th class="col-text">摘要模板</th>
                                    <th></th>
                                </tr>
                            </thead>
                            <tbody id="ruleLinesBody">${linesHTML}</tbody>
                        </table>
                    </div>
                </div>
            </form>
        `;

        // Bind events
        document.getElementById('cancelRuleEdit').addEventListener('click', () => switchView('rules'));
        document.getElementById('addLineBtn').addEventListener('click', () => {
            const tbody = document.getElementById('ruleLinesBody');
            const nextNo = tbody.querySelectorAll('tr').length + 1;
            const tr = document.createElement('tr');
            tr.innerHTML = ruleLineRowHTML({}, nextNo);
            tr.querySelector('.remove-line-btn').addEventListener('click', () => { tr.remove(); updateLineCount(); });
            tbody.appendChild(tr);
            updateLineCount();
        });
        document.querySelectorAll('#ruleLinesBody .remove-line-btn').forEach(btn => {
            btn.addEventListener('click', () => { btn.closest('tr').remove(); updateLineCount(); });
        });
        document.getElementById('ruleForm').addEventListener('submit', handleRuleFormSubmit);

        switchView('rule_edit');
    }

    function updateLineCount() {
        const count = document.querySelectorAll('#ruleLinesBody .rule-line-row').length;
        const badge = document.getElementById('lineCount');
        if (badge) badge.textContent = `${count} 行`;
    }

    function ruleLineRowHTML(data, lineNo) {
        const d = data || {};
        return `<tr class="rule-line-row">
            <td><input type="number" class="rl-line-no" value="${lineNo}" min="1" style="width:50px"></td>
            <td><select class="rl-debit-credit"><option value="S" ${d.debit_credit === 'S' ? 'selected' : ''}>借</option><option value="H" ${d.debit_credit === 'H' ? 'selected' : ''}>贷</option></select></td>
            <td><input type="text" class="rl-account-code" value="${d.account_code || ''}" placeholder="112200"></td>
            <td><input type="text" class="rl-account-name" value="${d.account_name || ''}" placeholder="应收账款"></td>
            <td><select class="rl-amount-field"><option value="total_amount" ${d.amount_field === 'total_amount' ? 'selected' : ''}>价税合计</option><option value="tax_excluded_amount" ${d.amount_field === 'tax_excluded_amount' ? 'selected' : ''}>不含税金额</option><option value="tax_amount" ${d.amount_field === 'tax_amount' ? 'selected' : ''}>税额</option></select></td>
            <td><input type="text" class="rl-customer-source" value="${d.customer_source || ''}" placeholder="customer"></td>
            <td><input type="text" class="rl-tax-code-rule" value="${d.tax_code_rule || ''}" placeholder="by_tax_rate"></td>
            <td><input type="text" class="rl-profit-center-source" value="${d.profit_center_source || ''}" placeholder="profit_center"></td>
            <td><input type="text" class="rl-cost-center-source" value="${d.cost_center_source || ''}" placeholder="cost_center"></td>
            <td><input type="text" class="rl-assignment-source" value="${d.assignment_source || ''}" placeholder="contract_no"></td>
            <td><input type="text" class="rl-text-template" value="${d.text_template || ''}" placeholder="摘要模板"></td>
            <td><button type="button" class="icon-btn-small remove-line-btn" title="删除行"><i class="ph ph-trash"></i></button></td>
        </tr>`;
    }

    function collectRuleLines() {
        const rows = document.querySelectorAll('#ruleLinesBody .rule-line-row');
        return Array.from(rows).map((row, idx) => ({
            line_no: parseInt(row.querySelector('.rl-line-no').value) || idx + 1,
            debit_credit: row.querySelector('.rl-debit-credit').value,
            account_code: row.querySelector('.rl-account-code').value.trim(),
            account_name: row.querySelector('.rl-account-name').value.trim(),
            amount_field: row.querySelector('.rl-amount-field').value,
            customer_source: row.querySelector('.rl-customer-source').value.trim(),
            tax_code_rule: row.querySelector('.rl-tax-code-rule').value.trim(),
            profit_center_source: row.querySelector('.rl-profit-center-source').value.trim(),
            cost_center_source: row.querySelector('.rl-cost-center-source').value.trim(),
            assignment_source: row.querySelector('.rl-assignment-source').value.trim(),
            text_template: row.querySelector('.rl-text-template').value.trim(),
        }));
    }

    async function handleRuleFormSubmit(e) {
        e.preventDefault();
        const editCode = document.getElementById('editRuleCode').value;
        const lines = collectRuleLines();
        if (lines.length === 0) { alert('至少需要一条分录行'); return; }

        const payload = {
            rule_code: document.getElementById('ruleCode').value.trim(),
            business_type: document.getElementById('ruleBusinessType').value,
            product_type: document.getElementById('ruleProductType').value.trim() || '*',
            tax_rate: document.getElementById('ruleTaxRate').value.trim() || '*',
            document_type: document.getElementById('ruleDocType').value.trim() || 'DR',
            lines: lines,
        };

        try {
            if (editCode) {
                await apiFetch(`/api/rules/${editCode}`, { method: 'PUT', body: JSON.stringify(payload) });
            } else {
                await apiFetch('/api/rules', { method: 'POST', body: JSON.stringify(payload) });
            }
            const resp = await apiFetch('/api/rules');
            const data = await resp.json();
            renderRules(data.rules || []);
            switchView('rules');
        } catch (err) {
            console.error('Failed to save rule:', err);
            alert('保存规则失败');
        }
    }

    async function deleteRule(ruleCode) {
        if (!confirm(`确定要删除规则「${ruleCode}」吗？此操作不可恢复。`)) return;
        try {
            await apiFetch(`/api/rules/${ruleCode}`, { method: 'DELETE' });
            const resp = await apiFetch('/api/rules');
            const data = await resp.json();
            renderRules(data.rules || []);
        } catch (err) {
            console.error('Failed to delete rule:', err);
            alert('删除规则失败');
        }
    }

    // ── Voucher Edit Form ──────────────────────────────────────────────────────

    async function loadVoucherEditForm(voucherId) {
        try {
            const resp = await fetch('/api/a2ui-action', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(authToken ? { 'Authorization': 'Bearer ' + authToken } : {}),
                },
                body: JSON.stringify({ event: 'edit_voucher', data: { voucherId } }),
            });
            const data = await resp.json();
            if (data.status === 'error') {
                addMessage(`加载凭证失败：${data.message}`, 'ai');
                return;
            }
            renderVoucherEditForm(data.voucher || data, voucherId);
        } catch (err) {
            console.error('Failed to load voucher for editing:', err);
            addMessage('加载凭证数据失败', 'ai');
        }
    }

    function renderVoucherEditForm(voucher, voucherId) {
        const container = document.getElementById('viewDynamic');
        if (!container) return;

        const rows = voucher.rows || [];
        const headerText = voucher.header_text || '';
        const docDate = voucher.document_date || '';
        const postDate = voucher.posting_date || '';

        let html = `
        <div class="a2ui-voucher-edit">
            <div class="a2ui-edit-header">
                <button class="a2ui-btn a2ui-btn-secondary" id="editCancelBtn">← 返回</button>
                <h2>编辑凭证 ${voucherId}</h2>
            </div>
            <div class="a2ui-card">
                <div class="a2ui-card-title">凭证信息</div>
                <div class="a2ui-edit-fields">
                    <div class="a2ui-field">
                        <label>凭证头文本</label>
                        <input type="text" id="editHeaderText" value="${_escHtml(headerText)}" class="a2ui-input">
                    </div>
                    <div class="a2ui-field">
                        <label>凭证日期</label>
                        <input type="date" id="editDocDate" value="${docDate}" class="a2ui-input">
                    </div>
                    <div class="a2ui-field">
                        <label>过账日期</label>
                        <input type="date" id="editPostDate" value="${postDate}" class="a2ui-input">
                    </div>
                </div>
            </div>
            <div class="a2ui-card">
                <div class="a2ui-card-title">凭证明细</div>
                <div class="a2ui-table-wrapper">
                    <table class="a2ui-table">
                        <thead>
                            <tr>
                                <th>行号</th>
                                <th>科目代码</th>
                                <th>科目名称</th>
                                <th>借/贷</th>
                                <th>借方金额</th>
                                <th>贷方金额</th>
                                <th>摘要</th>
                            </tr>
                        </thead>
                        <tbody>`;

        rows.forEach((r, i) => {
            const debit = r.debit || 0;
            const credit = r.credit || 0;
            html += `
                            <tr>
                                <td>${r.line_no || i + 1}</td>
                                <td>${_escHtml(r.account_code || '')}</td>
                                <td>${_escHtml(r.account_name || '')}</td>
                                <td>${r.debit_credit === 'S' ? '借' : '贷'}</td>
                                <td><input type="number" step="0.01" value="${debit}" class="a2ui-input a2ui-input-right" data-row="${i}" data-field="debit"></td>
                                <td><input type="number" step="0.01" value="${credit}" class="a2ui-input a2ui-input-right" data-row="${i}" data-field="credit"></td>
                                <td>${_escHtml(r.text || '')}</td>
                            </tr>`;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="a2ui-edit-actions">
                <button class="a2ui-btn a2ui-btn-primary" id="editSaveBtn">保存修改</button>
            </div>
        </div>`;

        container.innerHTML = html;
        window._a2uiRenderer = null; // Clear renderer since we're using raw HTML
        switchView('dynamic');

        // Cancel button
        document.getElementById('editCancelBtn').addEventListener('click', () => {
            // Reload voucher detail
            fetch('/api/a2ui-action', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + authToken,
                },
                body: JSON.stringify({ event: 'view_voucher_detail', data: { voucherId } }),
            }).then(r => r.json()).then(data => {
                if (data.a2ui?.messages) {
                    const renderer = new A2UIRenderer(container);
                    window._a2uiRenderer = renderer;
                    renderer.processMessages(data.a2ui.messages);
                }
            });
        });

        // Save button
        document.getElementById('editSaveBtn').addEventListener('click', async () => {
            const saveBtn = document.getElementById('editSaveBtn');
            saveBtn.disabled = true;
            saveBtn.textContent = '保存中...';

            // Collect edited values
            const editedRows = rows.map((r, i) => {
                const debitInput = container.querySelector(`input[data-row="${i}"][data-field="debit"]`);
                const creditInput = container.querySelector(`input[data-row="${i}"][data-field="credit"]`);
                return {
                    ...r,
                    debit: parseFloat(debitInput?.value || 0),
                    credit: parseFloat(creditInput?.value || 0),
                };
            });

            const totalDebit = editedRows.reduce((s, r) => s + (r.debit || 0), 0);
            const totalCredit = editedRows.reduce((s, r) => s + (r.credit || 0), 0);
            if (Math.abs(totalDebit - totalCredit) > 0.01) {
                addMessage(`借贷不平衡：借方 ${totalDebit.toFixed(2)}，贷方 ${totalCredit.toFixed(2)}，请修正。`, 'ai');
                saveBtn.disabled = false;
                saveBtn.textContent = '保存修改';
                return;
            }

            try {
                const resp = await apiFetch('/api/a2ui-action', {
                    method: 'POST',
                    body: JSON.stringify({
                        event: 'save_voucher_edit',
                        data: {
                            voucherId,
                            voucherData: {
                                ...voucher,
                                header_text: document.getElementById('editHeaderText').value,
                                document_date: document.getElementById('editDocDate').value,
                                posting_date: document.getElementById('editPostDate').value,
                                rows: editedRows,
                            },
                        },
                    }),
                });
                const result = await resp.json();
                if (result.message) addMessage(result.message, 'ai');
                if (result.status === 'error') {
                    saveBtn.disabled = false;
                    saveBtn.textContent = '保存修改';
                    return;
                }
                if (result.a2ui?.messages) {
                    const renderer = new A2UIRenderer(container);
                    window._a2uiRenderer = renderer;
                    renderer.processMessages(result.a2ui.messages);
                }
            } catch (err) {
                console.error('Save voucher edit error:', err);
                addMessage('保存失败：' + (err.message || '网络错误'), 'ai');
                saveBtn.disabled = false;
                saveBtn.textContent = '保存修改';
            }
        });
    }

    function _escHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── Attachment Upload ──────────────────────────────────────────────────────

    function openAttachmentPicker(voucherId, accept) {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = accept || '.png,.jpg,.jpeg,.pdf,.xlsx,.xls,.csv';
        input.onchange = async () => {
            const file = input.files[0];
            if (!file) return;
            addMessage(`正在上传附件「${file.name}」...`, 'ai');
            try {
                const formData = new FormData();
                formData.append('file', file);
                const resp = await fetch(`/api/vouchers/${voucherId}/attachments`, {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + authToken },
                    body: formData,
                });
                const data = await resp.json();
                if (data.error) {
                    addMessage(`上传失败：${data.error}`, 'ai');
                    return;
                }
                addMessage(`附件「${file.name}」上传成功。`, 'ai');
                // Refresh voucher detail to show new attachment
                const a2uiRenderer = window._a2uiRenderer;
                if (a2uiRenderer) {
                    const refreshResp = await fetch('/api/a2ui-action', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': 'Bearer ' + authToken,
                        },
                        body: JSON.stringify({ event: 'view_voucher_detail', data: { voucherId } }),
                    });
                    const refreshData = await refreshResp.json();
                    if (refreshData.a2ui?.messages) {
                        a2uiRenderer.processMessages(refreshData.a2ui.messages);
                    }
                }
            } catch (err) {
                console.error('Attachment upload error:', err);
                addMessage('附件上传失败：' + (err.message || '网络错误'), 'ai');
            }
        };
        input.click();
    }

    // ── Initialize ─────────────────────────────────────────────────────────────

    checkAuth();
});

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
                showApp();
            } else {
                showLogin();
            }
        } catch {
            showLogin();
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
            removeTypingIndicator();
            if (err.message && err.message.includes('登录已过期')) {
                addMessage('登录已过期，请重新登录。', 'ai');
            } else {
                addMessage('网络错误，请重试。', 'ai');
            }
            console.error(err);
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

    function resizeTextarea() {
        userInput.style.height = 'auto';
        userInput.style.height = userInput.scrollHeight + 'px';
    }

    async function processAIResponse(input) {
        const resp = await apiFetch('/api/chat', {
            method: 'POST',
            body: JSON.stringify({ message: input, session_id: sessionId }),
        });

        const data = await resp.json();
        removeTypingIndicator();
        sessionId = data.session_id;

        addMessage(data.reply, 'ai');

        // Route to the correct view based on server response
        const view = data.view;
        if (view === 'voucher' && data.voucher) {
            currentVoucherId = data.voucher.voucher_id;
            activateWorkspace(data.voucher);
            switchView('voucher');
        } else if (view === 'rules') {
            renderRules(data.rules || [], data.rule_mgmt?.action);
            switchView('rules');
            if (data.rule_mgmt) {
                if (data.rule_mgmt.action === 'create') {
                    openAddRuleModal(data.rule_mgmt.rule_type);
                }
            }
        } else if (view === 'voucher_list' && data.view_data) {
            renderVoucherList(data.view_data.vouchers, data.view_data.total, data.view_data.status_filter);
            switchView('voucher_list');
        } else if (view === 'user_list' && data.view_data) {
            renderUserList(data.view_data.users);
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

        const data = await resp.json();
        removeTypingIndicator();
        sessionId = data.session_id;

        addMessage(data.reply, 'ai');

        if (data.file) {
            showSourceData(data.file);
        }

        if (data.vouchers && data.vouchers.length > 0) {
            const lastVoucher = data.vouchers[data.vouchers.length - 1];
            currentVoucherId = lastVoucher.voucher_id;
            activateWorkspace(lastVoucher);
            switchView('voucher');

            if (data.vouchers.length > 1) {
                addMessage(`共生成 ${data.vouchers.length} 张凭证，当前显示最后一张。`, 'ai');
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
        const container = document.getElementById('voucherListContent');
        container.innerHTML = `<div class="view-loading"><i class="ph ph-spinner ph-spin"></i> 加载中...</div>`;

        try {
            const params = new URLSearchParams({ limit: '50', offset: '0' });
            if (status) params.set('status', status);
            const resp = await apiFetch(`/api/vouchers?${params}`);
            const data = await resp.json();
            renderVoucherList(data.vouchers, data.total, status);
        } catch (err) {
            container.innerHTML = `<div class="view-empty-state"><i class="ph ph-warning-circle"></i><p>加载失败</p></div>`;
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

    // ── Initialize ─────────────────────────────────────────────────────────────

    checkAuth();
});

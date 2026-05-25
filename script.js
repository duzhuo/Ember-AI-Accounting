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

    const initialState = document.getElementById('initialState');
    const workspaceContent = document.getElementById('workspaceContent');
    const sourceDataContent = document.getElementById('sourceDataContent');
    const voucherRows = document.getElementById('voucherRows');
    const totalDebitEl = document.getElementById('totalDebit');
    const totalCreditEl = document.getElementById('totalCredit');
    const createTransactionBtn = document.getElementById('createTransactionBtn');
    const sourceBadge = document.getElementById('sourceBadge');
    const toggleSourceBtn = document.getElementById('toggleSourceData');

    // ── App State ─────────────────────────────────────────────────────────────
    let isProcessing = false;
    let sessionId = null;
    let pendingFile = null;
    let currentVoucherId = null;
    let rulesLoaded = false;
    let isPosted = false;

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
            // Token expired, redirect to login
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

        // Show admin-only tabs
        const tabUsers = document.getElementById('tabUsers');
        if (currentUser.role === 'admin') {
            tabUsers.style.display = 'flex';
        } else {
            tabUsers.style.display = 'none';
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

        if (data.voucher) {
            currentVoucherId = data.voucher.voucher_id;
            activateWorkspace(data.voucher);
        }

        if (data.rules && data.rules.length > 0) {
            rulesLoaded = true;
            switchTab('rules');
            renderRules(data.rules);
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
        const pdfExts = ['pdf'];
        let iconClass = 'ph-file-xls';
        if (imageExts.includes(ext)) iconClass = 'ph-image';
        else if (pdfExts.includes(ext)) iconClass = 'ph-file-pdf';

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
        switchTab('voucher');

        if (initialState.style.display !== 'none') {
            initialState.style.display = 'none';
            workspaceContent.style.display = 'block';
        }

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

    // ── Workspace Tabs ─────────────────────────────────────────────────────────

    const tabBtns = document.querySelectorAll('.workspace-tab');
    const tabContents = document.querySelectorAll('.tab-content');

    function switchTab(tabName) {
        tabBtns.forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tabName));
        tabContents.forEach(content => {
            content.classList.toggle('active', content.id === `tabContent${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`);
        });

        if (tabName === 'rules' && !rulesLoaded) showRulesGuide();
        if (tabName === 'history') loadVoucherHistory();
        if (tabName === 'users') loadUsers();
    }

    tabBtns.forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

    // ── Voucher Rules ──────────────────────────────────────────────────────────

    function showRulesGuide() {
        const list = document.getElementById('rulesList');
        const loading = document.getElementById('rulesLoading');
        const empty = document.getElementById('rulesEmpty');
        if (loading) loading.style.display = 'none';
        list.innerHTML = '';
        list.style.display = 'none';
        if (empty) {
            empty.innerHTML = `
                <i class="ph ph-book-open"></i>
                <p>请在聊天框中询问凭证规则，例如：</p>
                <ul style="text-align: left; margin: 12px 0; padding-left: 24px;">
                    <li>「凭证规则是什么」— 查看可用的规则类型</li>
                    <li>「我想看销售收入的凭证规则」— 查看具体规则</li>
                </ul>
            `;
            empty.style.display = 'block';
        }
    }

    function renderRules(rules) {
        const list = document.getElementById('rulesList');
        const loading = document.getElementById('rulesLoading');
        const empty = document.getElementById('rulesEmpty');
        list.innerHTML = '';
        if (loading) loading.style.display = 'none';
        if (empty) empty.style.display = 'none';
        list.style.display = 'flex';

        const bizTypeLabels = { 'sales_revenue': '销售收入', 'expense': '费用报销', 'asset_purchase': '资产采购', 'salary': '工资薪酬', 'loan': '借款/还款' };

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

            const card = document.createElement('div');
            card.className = 'rule-card';
            card.innerHTML = `
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
                </div>
                <table class="rule-lines-table">
                    <thead><tr><th>行号</th><th>借/贷</th><th>科目代码</th><th>科目名称</th><th>金额取值</th><th>客户来源</th><th>税码规则</th><th>利润中心</th><th>成本中心</th><th>分配</th><th>摘要模板</th></tr></thead>
                    <tbody>${linesHTML}</tbody>
                </table>
            `;
            list.appendChild(card);
        });
    }

    // ── Voucher History ────────────────────────────────────────────────────────

    async function loadVoucherHistory() {
        const loading = document.getElementById('historyLoading');
        const empty = document.getElementById('historyEmpty');
        const list = document.getElementById('historyList');
        const statusFilter = document.getElementById('historyStatusFilter').value;

        loading.style.display = 'block';
        empty.style.display = 'none';
        list.style.display = 'none';

        try {
            const params = new URLSearchParams({ limit: '50', offset: '0' });
            if (statusFilter) params.set('status', statusFilter);

            const resp = await apiFetch(`/api/vouchers?${params}`);
            const data = await resp.json();

            loading.style.display = 'none';

            if (!data.vouchers || data.vouchers.length === 0) {
                empty.style.display = 'block';
                return;
            }

            list.style.display = 'flex';
            renderVoucherHistory(data.vouchers);
        } catch (err) {
            loading.style.display = 'none';
            empty.innerHTML = '<i class="ph ph-warning-circle"></i> 加载凭证记录失败';
            empty.style.display = 'block';
        }
    }

    function renderVoucherHistory(vouchers) {
        const list = document.getElementById('historyList');
        list.innerHTML = '';

        vouchers.forEach(v => {
            const statusBadge = v.status === 'posted'
                ? '<span class="status-badge status-posted">已过账</span>'
                : '<span class="status-badge status-draft">草稿</span>';

            const createdAt = new Date(v.created_at).toLocaleString('zh-CN');
            const postedInfo = v.posted_at
                ? `<span class="history-meta-item"><i class="ph ph-check-circle"></i> 过账时间: ${new Date(v.posted_at).toLocaleString('zh-CN')}</span>`
                : '';

            const card = document.createElement('div');
            card.className = 'history-card';
            card.innerHTML = `
                <div class="history-card-header">
                    <div class="history-card-title">
                        <span class="voucher-id-badge">${v.voucher_id}</span>
                        ${statusBadge}
                    </div>
                    <span class="history-meta"><i class="ph ph-clock"></i> ${createdAt}</span>
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
            `;
            list.appendChild(card);
        });
    }

    document.getElementById('refreshHistoryBtn').addEventListener('click', loadVoucherHistory);
    document.getElementById('historyStatusFilter').addEventListener('change', loadVoucherHistory);

    // ── User Management ────────────────────────────────────────────────────────

    async function loadUsers() {
        if (!currentUser || currentUser.role !== 'admin') return;

        try {
            const resp = await apiFetch('/api/users');
            const data = await resp.json();
            renderUsers(data.users || []);
        } catch (err) {
            console.error('Failed to load users:', err);
        }
    }

    function renderUsers(users) {
        const tbody = document.getElementById('usersTableBody');
        tbody.innerHTML = '';

        users.forEach(u => {
            const roleLabel = u.role === 'admin' ? '<span class="role-badge role-admin">管理员</span>' : '<span class="role-badge role-user">普通用户</span>';
            const statusLabel = u.is_active ? '<span class="status-badge status-posted">启用</span>' : '<span class="status-badge status-draft">停用</span>';
            const createdAt = new Date(u.created_at).toLocaleString('zh-CN');

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${u.username}</td>
                <td>${u.display_name}</td>
                <td>${roleLabel}</td>
                <td>${statusLabel}</td>
                <td>${createdAt}</td>
                <td class="users-actions">
                    <button class="icon-btn-small edit-user-btn" data-id="${u.id}" data-username="${u.username}" data-display="${u.display_name}" data-role="${u.role}" title="编辑"><i class="ph ph-pencil"></i></button>
                    <button class="icon-btn-small delete-user-btn" data-id="${u.id}" title="删除"><i class="ph ph-trash"></i></button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        // Bind edit/delete buttons
        tbody.querySelectorAll('.edit-user-btn').forEach(btn => {
            btn.addEventListener('click', () => openEditUserModal(btn.dataset));
        });
        tbody.querySelectorAll('.delete-user-btn').forEach(btn => {
            btn.addEventListener('click', () => deleteUser(btn.dataset.id));
        });
    }

    const userModal = document.getElementById('userModal');
    const userForm = document.getElementById('userForm');
    const editUserIdInput = document.getElementById('editUserId');

    document.getElementById('addUserBtn').addEventListener('click', () => {
        document.getElementById('userModalTitle').textContent = '添加用户';
        editUserIdInput.value = '';
        document.getElementById('newUsername').value = '';
        document.getElementById('newUsername').disabled = false;
        document.getElementById('newPassword').value = '';
        document.getElementById('newPassword').required = true;
        document.getElementById('newDisplayName').value = '';
        document.getElementById('newRole').value = 'user';
        userModal.style.display = 'flex';
    });

    function openEditUserModal(dataset) {
        document.getElementById('userModalTitle').textContent = '编辑用户';
        editUserIdInput.value = dataset.id;
        document.getElementById('newUsername').value = dataset.username;
        document.getElementById('newUsername').disabled = true;
        document.getElementById('newPassword').value = '';
        document.getElementById('newPassword').required = false;
        document.getElementById('newPassword').placeholder = '留空则不修改密码';
        document.getElementById('newDisplayName').value = dataset.display;
        document.getElementById('newRole').value = dataset.role;
        userModal.style.display = 'flex';
    }

    document.getElementById('closeUserModal').addEventListener('click', () => userModal.style.display = 'none');
    document.getElementById('cancelUserModal').addEventListener('click', () => userModal.style.display = 'none');

    userForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const editId = editUserIdInput.value;
        const payload = {
            username: document.getElementById('newUsername').value.trim(),
            password: document.getElementById('newPassword').value,
            display_name: document.getElementById('newDisplayName').value.trim(),
            role: document.getElementById('newRole').value,
        };

        try {
            if (editId) {
                // Update
                await apiFetch(`/api/users/${editId}`, {
                    method: 'PUT',
                    body: JSON.stringify(payload),
                });
            } else {
                // Create
                await apiFetch('/api/users', {
                    method: 'POST',
                    body: JSON.stringify(payload),
                });
            }
            userModal.style.display = 'none';
            loadUsers();
        } catch (err) {
            console.error('Failed to save user:', err);
            alert('保存用户失败');
        }
    });

    async function deleteUser(userId) {
        if (!confirm('确定要删除此用户吗？')) return;
        try {
            await apiFetch(`/api/users/${userId}`, { method: 'DELETE' });
            loadUsers();
        } catch (err) {
            console.error('Failed to delete user:', err);
            alert('删除用户失败');
        }
    }

    // ── Toggle source data section ────────────────────────────────────────────

    if (toggleSourceBtn) {
        toggleSourceBtn.addEventListener('click', () => {
            const isVisible = sourceDataContent.style.display !== 'none';
            sourceDataContent.style.display = isVisible ? 'none' : 'block';
            toggleSourceBtn.innerHTML = isVisible ? '<i class="ph ph-caret-down"></i>' : '<i class="ph ph-caret-up"></i>';
        });
    }

    // ── Initialize ─────────────────────────────────────────────────────────────

    checkAuth();
});

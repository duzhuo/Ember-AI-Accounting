/**
 * Rules views module
 */

import { apiFetch, apiGet, apiPost, apiPut, apiDelete } from '../api.js';
import { bus, EVENTS } from '../event-bus.js';
import { icon } from '../icons.js';
import { escHtml, showToast } from '../common.js';

// ── Rules List View ──────────────────────────────────────────────────────────

/**
 * Render rules list view
 * @param {Object} data - Rules data
 */
export function renderRules(data) {
    const container = document.getElementById('viewRules');
    if (!container) return;

    const { rules, rule_type, rule_mgmt } = data;

    let html = `
        <div class="view-toolbar">
            <div class="view-toolbar-info">
                <span class="view-count">${rules.length} 条规则</span>
            </div>
            <div class="view-toolbar-actions">
                <button class="btn btn-primary" id="addRuleBtn">
                    ${icon('plus', 14)} 新增规则
                </button>
            </div>
        </div>
    `;

    if (rules.length === 0) {
        html += `
            <div class="view-empty-state">
                ${icon('list', 48)}
                <p>暂无凭证规则</p>
            </div>
        `;
    } else {
        html += '<div class="rules-list">';
        rules.forEach((rule) => {
            html += renderRuleCard(rule);
        });
        html += '</div>';
    }

    container.innerHTML = html;

    // Bind events
    bindRulesEvents(container);

    // Open add modal if action is create
    if (rule_mgmt?.action === 'create') {
        openAddRuleModal(rule_mgmt.rule_type);
    }
}

function renderRuleCard(rule) {
    return `
        <div class="rule-card" data-code="${rule.rule_code}">
            <div class="rule-card-header">
                <span class="rule-code-badge">${escHtml(rule.rule_code)}</span>
                <span class="rule-type-badge">${escHtml(rule.business_type)}</span>
            </div>
            <div class="rule-card-body">
                <div class="rule-info">
                    <span>产品类型: ${escHtml(rule.product_type || '*')}</span>
                    <span>税率: ${escHtml(rule.tax_rate || '*')}</span>
                    <span>凭证类型: ${escHtml(rule.document_type || 'DR')}</span>
                </div>
                <div class="rule-lines-count">
                    ${rule.lines ? rule.lines.length : 0} 条分录规则
                </div>
            </div>
            <div class="rule-card-actions">
                <button class="icon-btn-small edit-rule-btn" data-code="${rule.rule_code}" title="编辑">
                    ${icon('edit', 14)}
                </button>
                <button class="icon-btn-small delete-rule-btn" data-code="${rule.rule_code}" title="删除">
                    ${icon('delete', 14)}
                </button>
            </div>
        </div>
    `;
}

function bindRulesEvents(container) {
    // Add rule button
    const addBtn = container.querySelector('#addRuleBtn');
    if (addBtn) {
        addBtn.addEventListener('click', () => openAddRuleModal());
    }

    // Edit buttons
    container.querySelectorAll('.edit-rule-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const ruleCode = btn.dataset.code;
            openEditRuleModal(ruleCode);
        });
    });

    // Delete buttons
    container.querySelectorAll('.delete-rule-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const ruleCode = btn.dataset.code;
            deleteRule(ruleCode);
        });
    });
}

// ── Rule Edit View ───────────────────────────────────────────────────────────

/**
 * Render rule edit form
 * @param {Object} rule - Rule data
 */
export function renderRuleEditForm(rule) {
    const container = document.getElementById('viewRuleEdit');
    if (!container) return;

    const isNew = !rule.rule_code;
    const title = isNew ? '新增规则' : '编辑规则';

    let html = `
        <div class="rule-edit-form">
            <h2>${title}</h2>

            <div class="form-section">
                <h3>基本信息</h3>
                <div class="form-grid">
                    <div class="form-field">
                        <label>规则代码</label>
                        <input type="text" id="ruleCode" value="${escHtml(rule.rule_code || '')}"
                               ${isNew ? '' : 'readonly'} placeholder="自动生成或手动输入">
                    </div>
                    <div class="form-field">
                        <label>业务类型</label>
                        <select id="ruleBusinessType">
                            <option value="sales_revenue" ${rule.business_type === 'sales_revenue' ? 'selected' : ''}>销售收入</option>
                            <option value="expense" ${rule.business_type === 'expense' ? 'selected' : ''}>费用报销</option>
                            <option value="asset_purchase" ${rule.business_type === 'asset_purchase' ? 'selected' : ''}>资产采购</option>
                            <option value="salary" ${rule.business_type === 'salary' ? 'selected' : ''}>工资薪酬</option>
                            <option value="loan" ${rule.business_type === 'loan' ? 'selected' : ''}>借款/还款</option>
                        </select>
                    </div>
                    <div class="form-field">
                        <label>产品类型</label>
                        <input type="text" id="ruleProductType" value="${escHtml(rule.product_type || '*')}" placeholder="* 表示匹配所有">
                    </div>
                    <div class="form-field">
                        <label>税率</label>
                        <input type="text" id="ruleTaxRate" value="${escHtml(rule.tax_rate || '*')}" placeholder="* 表示匹配所有">
                    </div>
                    <div class="form-field">
                        <label>凭证类型</label>
                        <input type="text" id="ruleDocumentType" value="${escHtml(rule.document_type || 'DR')}" placeholder="DR">
                    </div>
                </div>
            </div>

            <div class="form-section">
                <h3>分录规则</h3>
                <div id="ruleLinesContainer">
                    ${(rule.lines || []).map((line, index) => renderRuleLineForm(line, index)).join('')}
                </div>
                <button class="btn btn-secondary" id="addRuleLineBtn">
                    ${icon('plus', 14)} 添加分录
                </button>
            </div>

            <div class="form-actions">
                <button class="btn btn-secondary" id="cancelRuleEditBtn">取消</button>
                <button class="btn btn-primary" id="saveRuleBtn">保存</button>
            </div>
        </div>
    `;

    container.innerHTML = html;

    // Bind events
    bindRuleEditEvents(container, rule);
}

function renderRuleLineForm(line, index) {
    return `
        <div class="rule-line-form" data-index="${index}">
            <div class="rule-line-header">
                <span>分录 ${index + 1}</span>
                <button class="icon-btn-small delete-rule-line-btn" data-index="${index}">
                    ${icon('delete', 14)}
                </button>
            </div>
            <div class="form-grid">
                <div class="form-field">
                    <label>借贷方向</label>
                    <select class="rule-line-dc">
                        <option value="S" ${line.debit_credit === 'S' ? 'selected' : ''}>借方</option>
                        <option value="H" ${line.debit_credit === 'H' ? 'selected' : ''}>贷方</option>
                    </select>
                </div>
                <div class="form-field">
                    <label>科目代码</label>
                    <input type="text" class="rule-line-account-code" value="${escHtml(line.account_code || '')}">
                </div>
                <div class="form-field">
                    <label>科目名称</label>
                    <input type="text" class="rule-line-account-name" value="${escHtml(line.account_name || '')}">
                </div>
                <div class="form-field">
                    <label>金额字段</label>
                    <input type="text" class="rule-line-amount-field" value="${escHtml(line.amount_field || '')}" placeholder="tax_excluded_amount">
                </div>
            </div>
        </div>
    `;
}

function bindRuleEditEvents(container, rule) {
    // Cancel button
    const cancelBtn = container.querySelector('#cancelRuleEditBtn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            bus.emit(EVENTS.VIEW_SWITCH, { view: 'rules' });
        });
    }

    // Save button
    const saveBtn = container.querySelector('#saveRuleBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', () => saveRule(rule));
    }

    // Add line button
    const addLineBtn = container.querySelector('#addRuleLineBtn');
    if (addLineBtn) {
        addLineBtn.addEventListener('click', () => addRuleLine(container));
    }

    // Delete line buttons
    container.querySelectorAll('.delete-rule-line-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const index = parseInt(btn.dataset.index);
            deleteRuleLine(container, index);
        });
    });
}

// ── Rule CRUD Operations ─────────────────────────────────────────────────────

async function saveRule(originalRule) {
    const isNew = !originalRule.rule_code;

    const ruleData = {
        rule_code: document.getElementById('ruleCode').value.trim(),
        business_type: document.getElementById('ruleBusinessType').value,
        product_type: document.getElementById('ruleProductType').value.trim() || '*',
        tax_rate: document.getElementById('ruleTaxRate').value.trim() || '*',
        document_type: document.getElementById('ruleDocumentType').value.trim() || 'DR',
        lines: collectRuleLines(),
    };

    if (!ruleData.rule_code) {
        showToast('请输入规则代码', 'error');
        return;
    }

    try {
        if (isNew) {
            await apiPost('/api/rules', ruleData);
            showToast('规则创建成功', 'success');
        } else {
            await apiPut(`/api/rules/${originalRule.rule_code}`, ruleData);
            showToast('规则更新成功', 'success');
        }

        // Refresh rules list
        bus.emit(EVENTS.VIEW_SWITCH, { view: 'rules', refresh: true });
    } catch (err) {
        showToast('保存失败: ' + err.message, 'error');
    }
}

async function deleteRule(ruleCode) {
    if (!confirm(`确定要删除规则 ${ruleCode} 吗？`)) return;

    try {
        await apiDelete(`/api/rules/${ruleCode}`);
        showToast('规则已删除', 'success');

        // Refresh rules list
        bus.emit(EVENTS.VIEW_SWITCH, { view: 'rules', refresh: true });
    } catch (err) {
        showToast('删除失败: ' + err.message, 'error');
    }
}

function collectRuleLines() {
    const lines = [];
    document.querySelectorAll('.rule-line-form').forEach((form) => {
        lines.push({
            debit_credit: form.querySelector('.rule-line-dc').value,
            account_code: form.querySelector('.rule-line-account-code').value.trim(),
            account_name: form.querySelector('.rule-line-account-name').value.trim(),
            amount_field: form.querySelector('.rule-line-amount-field').value.trim(),
        });
    });
    return lines;
}

function addRuleLine(container) {
    const linesContainer = container.querySelector('#ruleLinesContainer');
    const index = linesContainer.children.length;
    const lineHtml = renderRuleLineForm({}, index);

    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = lineHtml;
    linesContainer.appendChild(tempDiv.firstElementChild);
}

function deleteRuleLine(container, index) {
    const linesContainer = container.querySelector('#ruleLinesContainer');
    const lineForm = linesContainer.querySelector(`[data-index="${index}"]`);
    if (lineForm) {
        lineForm.remove();
    }
}

// ── Modal Functions ──────────────────────────────────────────────────────────

function openAddRuleModal(ruleType) {
    renderRuleEditForm({
        rule_type: ruleType,
        lines: [],
    });
}

function openEditRuleModal(ruleCode) {
    // Load rule data and open edit form
    apiGet(`/api/rules/${ruleCode}`)
        .then((data) => {
            renderRuleEditForm(data.rule);
        })
        .catch((err) => {
            showToast('加载规则失败: ' + err.message, 'error');
        });
}

/**
 * Voucher views module
 */

import { apiFetch, apiGet } from '../api.js';
import { appStore } from '../state.js';
import { bus, EVENTS } from '../event-bus.js';
import { icon } from '../icons.js';
import { escHtml, formatTime, formatAmount, showToast, STATUS_MAP } from '../common.js';

// ── Voucher List View ────────────────────────────────────────────────────────

/**
 * Render voucher list view
 * @param {Object} data - Voucher list data
 */
export function renderVoucherList(data) {
    const container = document.getElementById('viewVoucherList');
    if (!container) return;

    const { vouchers, total, status_filter } = data;

    let html = `
        <div class="view-toolbar">
            <div class="view-toolbar-info">
                <span class="view-count">${total} 条记录</span>
            </div>
            <div class="view-toolbar-actions">
                <button class="btn btn-sm btn-secondary" id="refreshVoucherListBtn">
                    ${icon('refresh', 14)} 刷新
                </button>
            </div>
        </div>
    `;

    if (vouchers.length === 0) {
        html += `
            <div class="view-empty-state">
                ${icon('document', 48)}
                <p>暂无凭证记录</p>
            </div>
        `;
    } else {
        html += '<div class="voucher-list">';
        vouchers.forEach((v) => {
            const status = STATUS_MAP[v.status] || STATUS_MAP.draft;
            const createdAt = formatTime(v.created_at);

            html += `
                <div class="voucher-card" data-id="${v.voucher_id}">
                    <div class="voucher-card-header">
                        <span class="voucher-id-badge">${escHtml(v.voucher_id)}</span>
                        <span class="status-badge ${status.class}">${status.label}</span>
                    </div>
                    <div class="voucher-card-body">
                        <div class="voucher-card-info">
                            <span>${icon('document', 14)} ${escHtml(v.header_text || '-')}</span>
                            <span>${icon('clock', 14)} ${createdAt}</span>
                        </div>
                        <div class="voucher-card-meta">
                            <span>${escHtml(v.company_code || '-')}</span>
                            <span>${escHtml(v.document_type || '-')}</span>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
    }

    container.innerHTML = html;

    // Bind events
    bindVoucherListEvents(container);
}

function bindVoucherListEvents(container) {
    // Refresh button
    const refreshBtn = container.querySelector('#refreshVoucherListBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            bus.emit(EVENTS.VIEW_SWITCH, { view: 'voucher_list', refresh: true });
        });
    }

    // Voucher cards
    container.querySelectorAll('.voucher-card').forEach((card) => {
        card.addEventListener('click', () => {
            const voucherId = card.dataset.id;
            if (voucherId) {
                loadVoucherDetail(voucherId);
            }
        });
    });
}

// ── Voucher Detail View ──────────────────────────────────────────────────────

/**
 * Load and display voucher detail
 * @param {string} voucherId - Voucher ID
 */
export async function loadVoucherDetail(voucherId) {
    try {
        const data = await apiGet(`/api/vouchers/${voucherId}`);
        const voucher = data.voucher;

        if (voucher) {
            appStore.set('currentVoucherId', voucherId);
            renderVoucherDetail(voucher);
            bus.emit(EVENTS.VIEW_SWITCH, { view: 'voucher' });
        }
    } catch (err) {
        showToast('加载凭证失败: ' + err.message, 'error');
    }
}

/**
 * Render voucher detail view
 * @param {Object} voucher - Voucher data
 */
export function renderVoucherDetail(voucher) {
    const container = document.getElementById('viewVoucher');
    if (!container) return;

    const status = STATUS_MAP[voucher.status] || STATUS_MAP.draft;
    const lines = voucher.rows || [];

    const totalDebit = lines.reduce((sum, row) => sum + (parseFloat(row.debit) || 0), 0);
    const totalCredit = lines.reduce((sum, row) => sum + (parseFloat(row.credit) || 0), 0);

    let html = `
        <div class="voucher-detail">
            <div class="voucher-detail-header">
                <div class="voucher-detail-title">
                    <span class="voucher-id-badge">${escHtml(voucher.voucher_id)}</span>
                    <span class="status-badge ${status.class}">${status.label}</span>
                </div>
                <div class="voucher-detail-actions">
                    ${voucher.status === 'draft' ? `
                        <button class="btn btn-primary" id="submitVoucherBtn">
                            提交审批
                        </button>
                        <button class="btn btn-secondary" id="editVoucherBtn">
                            编辑
                        </button>
                    ` : ''}
                    ${voucher.status === 'posted' ? `
                        <button class="btn btn-secondary" id="exportPdfBtn">
                            ${icon('download', 14)} 导出 PDF
                        </button>
                    ` : ''}
                </div>
            </div>

            <div class="voucher-detail-info">
                <div class="info-row">
                    <span class="info-label">摘要</span>
                    <span class="info-value">${escHtml(voucher.header_text || '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">凭证日期</span>
                    <span class="info-value">${escHtml(voucher.document_date || '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">过账日期</span>
                    <span class="info-value">${escHtml(voucher.posting_date || '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">公司代码</span>
                    <span class="info-value">${escHtml(voucher.company_code || '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">凭证类型</span>
                    <span class="info-value">${escHtml(voucher.document_type || '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">参考号</span>
                    <span class="info-value">${escHtml(voucher.reference || '-')}</span>
                </div>
            </div>

            <div class="voucher-detail-lines">
                <h3>会计分录</h3>
                <table class="voucher-lines-table">
                    <thead>
                        <tr>
                            <th>科目</th>
                            <th>摘要</th>
                            <th>借方</th>
                            <th>贷方</th>
                        </tr>
                    </thead>
                    <tbody>
    `;

    lines.forEach((line) => {
        const isDebit = line.dc === 'S' || line.debit > 0;
        const amount = isDebit ? line.debit : line.credit;

        html += `
            <tr>
                <td>${escHtml(line.account_code || '')} ${escHtml(line.account_name || '')}</td>
                <td>${escHtml(line.text || line.assignment || '')}</td>
                <td class="amount-debit">${isDebit ? formatAmount(amount) : ''}</td>
                <td class="amount-credit">${!isDebit ? formatAmount(amount) : ''}</td>
            </tr>
        `;
    });

    html += `
                    </tbody>
                    <tfoot>
                        <tr>
                            <td colspan="2"><strong>合计</strong></td>
                            <td class="amount-debit"><strong>${formatAmount(totalDebit)}</strong></td>
                            <td class="amount-credit"><strong>${formatAmount(totalCredit)}</strong></td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        </div>
    `;

    container.innerHTML = html;

    // Bind events
    bindVoucherDetailEvents(voucher);
}

function bindVoucherDetailEvents(voucher) {
    // Submit button
    const submitBtn = document.getElementById('submitVoucherBtn');
    if (submitBtn) {
        submitBtn.addEventListener('click', () => {
            openApprovalModal(voucher.voucher_id);
        });
    }

    // Edit button
    const editBtn = document.getElementById('editVoucherBtn');
    if (editBtn) {
        editBtn.addEventListener('click', () => {
            bus.emit(EVENTS.VOUCHER_LOAD, { voucherId: voucher.voucher_id, mode: 'edit' });
        });
    }

    // Export PDF button
    const exportBtn = document.getElementById('exportPdfBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', async () => {
            showToast('正在生成 PDF...', 'info');
            try {
                const resp = await apiFetch(`/api/vouchers/${voucher.voucher_id}/pdf`);
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `voucher_${voucher.voucher_id}.pdf`;
                a.click();
                URL.revokeObjectURL(url);
                showToast('PDF 已下载', 'success');
            } catch (err) {
                showToast('PDF 下载失败: ' + err.message, 'error');
            }
        });
    }
}

// ── Approval Modal ───────────────────────────────────────────────────────────

function openApprovalModal(voucherId) {
    // Implementation for approval modal
    console.log('Open approval modal for:', voucherId);
}

// ── Export Functions ──────────────────────────────────────────────────────────

export { loadVoucherDetail as loadVoucher };

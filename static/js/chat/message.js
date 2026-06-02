/**
 * Message rendering utilities
 */

import { escHtml } from '../common.js';

// ── Content Formatting ───────────────────────────────────────────────────────

/**
 * Format message content with basic markdown-like syntax
 * @param {string} content - Raw content
 * @returns {string} Formatted HTML
 */
export function formatContent(content) {
    if (!content) return '';
    if (typeof content !== 'string') {
        content = String(content);
    }

    let html = escHtml(content);

    // Bold: **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    // Italic: *text*
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

    // Code: `text`
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');

    // Links: [text](url)
    html = html.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank">$1</a>');

    // Line breaks
    html = html.replace(/\n/g, '<br>');

    // Lists: - item
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

    return html;
}

/**
 * Format code block
 * @param {string} code - Code content
 * @param {string} language - Language hint
 * @returns {string} Formatted code block
 */
export function formatCodeBlock(code, language = '') {
    return `<pre><code class="language-${language}">${escHtml(code)}</code></pre>`;
}

/**
 * Format table
 * @param {Array} headers - Table headers
 * @param {Array} rows - Table rows
 * @returns {string} Formatted table HTML
 */
export function formatTable(headers, rows) {
    let html = '<table class="message-table">';

    // Headers
    if (headers && headers.length > 0) {
        html += '<thead><tr>';
        headers.forEach((header) => {
            html += `<th>${escHtml(header)}</th>`;
        });
        html += '</tr></thead>';
    }

    // Rows
    if (rows && rows.length > 0) {
        html += '<tbody>';
        rows.forEach((row) => {
            html += '<tr>';
            row.forEach((cell) => {
                html += `<td>${escHtml(String(cell))}</td>`;
            });
            html += '</tr>';
        });
        html += '</tbody>';
    }

    html += '</table>';
    return html;
}

/**
 * Format key-value pairs
 * @param {Object} data - Key-value data
 * @returns {string} Formatted HTML
 */
export function formatKeyValue(data) {
    if (!data || typeof data !== 'object') return '';

    let html = '<div class="message-kv">';
    Object.entries(data).forEach(([key, value]) => {
        html += `<div class="kv-row">
            <span class="kv-label">${escHtml(key)}</span>
            <span class="kv-value">${escHtml(String(value))}</span>
        </div>`;
    });
    html += '</div>';

    return html;
}

/**
 * Format error message
 * @param {string} message - Error message
 * @returns {string} Formatted error HTML
 */
export function formatError(message) {
    return `<div class="message-error">${escHtml(message)}</div>`;
}

/**
 * Format success message
 * @param {string} message - Success message
 * @returns {string} Formatted success HTML
 */
export function formatSuccess(message) {
    return `<div class="message-success">${escHtml(message)}</div>`;
}

/**
 * Format warning message
 * @param {string} message - Warning message
 * @returns {string} Formatted warning HTML
 */
export function formatWarning(message) {
    return `<div class="message-warning">${escHtml(message)}</div>`;
}

// ── Message Templates ────────────────────────────────────────────────────────

/**
 * Create a voucher summary message
 * @param {Object} voucher - Voucher data
 * @returns {string} Formatted HTML
 */
export function formatVoucherSummary(voucher) {
    if (!voucher) return '';

    const lines = voucher.rows || [];
    const totalDebit = lines.reduce((sum, row) => sum + (parseFloat(row.debit) || 0), 0);
    const totalCredit = lines.reduce((sum, row) => sum + (parseFloat(row.credit) || 0), 0);

    let html = `
        <div class="voucher-summary">
            <div class="voucher-header">
                <span class="voucher-id">${escHtml(voucher.voucher_id)}</span>
                <span class="voucher-status badge-${voucher.status}">${escHtml(voucher.status)}</span>
            </div>
            <div class="voucher-info">
                <div><strong>摘要:</strong> ${escHtml(voucher.header_text || '-')}</div>
                <div><strong>日期:</strong> ${escHtml(voucher.document_date || '-')}</div>
                <div><strong>公司:</strong> ${escHtml(voucher.company_code || '-')}</div>
            </div>
            <div class="voucher-amounts">
                <span>借方合计: ¥${totalDebit.toFixed(2)}</span>
                <span>贷方合计: ¥${totalCredit.toFixed(2)}</span>
            </div>
        </div>
    `;

    return html;
}

/**
 * Create a user list message
 * @param {Array} users - User list
 * @returns {string} Formatted HTML
 */
export function formatUserList(users) {
    if (!users || users.length === 0) return '<p>暂无用户</p>';

    let html = '<div class="user-list">';
    users.forEach((user) => {
        html += `
            <div class="user-item">
                <span class="user-name">${escHtml(user.display_name || user.username)}</span>
                <span class="user-role badge-${user.role}">${escHtml(user.role)}</span>
            </div>
        `;
    });
    html += '</div>';

    return html;
}

/**
 * Create a rule list message
 * @param {Array} rules - Rule list
 * @returns {string} Formatted HTML
 */
export function formatRuleList(rules) {
    if (!rules || rules.length === 0) return '<p>暂无规则</p>';

    let html = '<div class="rule-list">';
    rules.forEach((rule) => {
        html += `
            <div class="rule-item">
                <div class="rule-code">${escHtml(rule.rule_code)}</div>
                <div class="rule-type">${escHtml(rule.business_type)}</div>
            </div>
        `;
    });
    html += '</div>';

    return html;
}

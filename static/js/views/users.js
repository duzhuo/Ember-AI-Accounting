/**
 * User management views module
 */

import { apiFetch, apiGet, apiPost, apiPut, apiDelete } from '../api.js';
import { bus, EVENTS } from '../event-bus.js';
import { icon } from '../icons.js';
import { escHtml, showToast, ROLE_MAP } from '../common.js';

// ── User List View ───────────────────────────────────────────────────────────

/**
 * Render user list view
 * @param {Object} data - User list data
 */
export function renderUserList(data) {
    const container = document.getElementById('viewUserList');
    if (!container) return;

    const { users } = data;

    let html = `
        <div class="view-toolbar">
            <div class="view-toolbar-info">
                <span class="view-count">${users.length} 个用户</span>
            </div>
            <div class="view-toolbar-actions">
                <button class="btn btn-primary" id="addUserBtn">
                    ${icon('plus', 14)} 添加用户
                </button>
            </div>
        </div>
    `;

    if (users.length === 0) {
        html += `
            <div class="view-empty-state">
                ${icon('users', 48)}
                <p>暂无用户</p>
            </div>
        `;
    } else {
        html += `
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
                <tbody>
        `;

        users.forEach((user) => {
            const role = ROLE_MAP[user.role] || ROLE_MAP.user;
            const statusClass = user.is_active ? 'status-posted' : 'status-draft';
            const statusLabel = user.is_active ? '启用' : '停用';
            const createdAt = new Date(user.created_at).toLocaleString('zh-CN');

            html += `
                <tr>
                    <td>${escHtml(user.username)}</td>
                    <td>${escHtml(user.display_name)}</td>
                    <td><span class="role-badge ${role.class}">${role.label}</span></td>
                    <td><span class="status-badge ${statusClass}">${statusLabel}</span></td>
                    <td>${createdAt}</td>
                    <td class="users-actions">
                        <button class="icon-btn-small edit-user-btn" data-id="${user.id}" title="编辑">
                            ${icon('edit', 14)}
                        </button>
                        <button class="icon-btn-small reset-password-btn" data-id="${user.id}" data-username="${escHtml(user.username)}" title="重置密码">
                            ${icon('key', 14)}
                        </button>
                        <button class="icon-btn-small delete-user-btn" data-id="${user.id}" data-username="${escHtml(user.username)}" title="删除">
                            ${icon('delete', 14)}
                        </button>
                    </td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;
    }

    container.innerHTML = html;

    // Bind events
    bindUserListEvents(container);
}

function bindUserListEvents(container) {
    // Add user button
    const addBtn = container.querySelector('#addUserBtn');
    if (addBtn) {
        addBtn.addEventListener('click', openAddUserModal);
    }

    // Edit buttons
    container.querySelectorAll('.edit-user-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const userId = btn.dataset.id;
            openEditUserModal(userId);
        });
    });

    // Reset password buttons
    container.querySelectorAll('.reset-password-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const userId = btn.dataset.id;
            const username = btn.dataset.username;
            resetUserPassword(userId, username);
        });
    });

    // Delete buttons
    container.querySelectorAll('.delete-user-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const userId = btn.dataset.id;
            const username = btn.dataset.username;
            deleteUser(userId, username);
        });
    });
}

// ── User Modal ───────────────────────────────────────────────────────────────

function openAddUserModal() {
    renderUserModal({
        isNew: true,
        user: { role: 'user' },
    });
}

async function openEditUserModal(userId) {
    try {
        const data = await apiGet('/api/users');
        const user = data.users.find((u) => u.id === userId);

        if (user) {
            renderUserModal({
                isNew: false,
                user,
            });
        } else {
            showToast('用户不存在', 'error');
        }
    } catch (err) {
        showToast('加载用户信息失败', 'err');
    }
}

function renderUserModal({ isNew, user }) {
    // Remove existing modal
    const existingModal = document.getElementById('userModal');
    if (existingModal) existingModal.remove();

    const modal = document.createElement('div');
    modal.id = 'userModal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-card glass-panel">
            <div class="modal-header">
                <h3>${isNew ? '添加用户' : '编辑用户'}</h3>
                <button class="icon-btn" id="closeUserModal">
                    ${icon('close', 18)}
                </button>
            </div>
            <form id="userForm">
                <input type="hidden" id="editUserId" value="${user.id || ''}">
                <div class="form-field">
                    <label for="newUsername">用户名</label>
                    <input type="text" id="newUsername" value="${escHtml(user.username || '')}"
                           ${isNew ? '' : 'disabled'} required>
                </div>
                <div class="form-field">
                    <label for="newPassword">密码</label>
                    <input type="password" id="newPassword"
                           ${isNew ? 'required' : ''} placeholder="${isNew ? '至少6位' : '留空则不修改'}">
                </div>
                <div class="form-field">
                    <label for="newDisplayName">显示名称</label>
                    <input type="text" id="newDisplayName" value="${escHtml(user.display_name || '')}" required>
                </div>
                <div class="form-field">
                    <label for="newRole">角色</label>
                    <select id="newRole">
                        <option value="user" ${user.role === 'user' ? 'selected' : ''}>普通用户</option>
                        <option value="reviewer" ${user.role === 'reviewer' ? 'selected' : ''}>复核人</option>
                        <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>管理员</option>
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
    modal.style.display = 'flex';

    // Bind events
    bindUserModalEvents(modal, isNew);
}

function bindUserModalEvents(modal, isNew) {
    // Close button
    const closeBtn = modal.querySelector('#closeUserModal');
    closeBtn.addEventListener('click', () => modal.remove());

    // Cancel button
    const cancelBtn = modal.querySelector('#cancelUserModal');
    cancelBtn.addEventListener('click', () => modal.remove());

    // Click outside to close
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });

    // Form submit
    const form = modal.querySelector('#userForm');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveUser(modal, isNew);
    });
}

async function saveUser(modal, isNew) {
    const editId = modal.querySelector('#editUserId').value;
    const payload = {
        username: modal.querySelector('#newUsername').value.trim(),
        password: modal.querySelector('#newPassword').value,
        display_name: modal.querySelector('#newDisplayName').value.trim(),
        role: modal.querySelector('#newRole').value,
    };

    if (!isNew) {
        delete payload.username;
    }

    try {
        if (isNew) {
            await apiPost('/api/users', payload);
            showToast('用户创建成功', 'success');
        } else {
            await apiPut(`/api/users/${editId}`, payload);
            showToast('用户更新成功', 'success');
        }

        modal.remove();

        // Refresh user list
        bus.emit(EVENTS.VIEW_SWITCH, { view: 'user_list', refresh: true });
    } catch (err) {
        showToast('保存失败: ' + err.message, 'error');
    }
}

// ── User Actions ─────────────────────────────────────────────────────────────

async function resetUserPassword(userId, username) {
    if (!confirm(`确定要重置用户「${username}」的密码吗？`)) return;

    try {
        const data = await apiPost(`/api/users/${userId}/reset-password`);

        if (data.new_password) {
            showResetPasswordResult(username, data.new_password);
        }
    } catch (err) {
        showToast('重置密码失败: ' + err.message, 'error');
    }
}

function showResetPasswordResult(username, password) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-card" style="max-width:400px;">
            <div class="modal-header">
                <h3>密码重置成功</h3>
                <button class="icon-btn close-modal-btn">
                    ${icon('close', 18)}
                </button>
            </div>
            <div style="padding:16px 24px;">
                <p style="margin-bottom:12px;">已重置用户 <strong>${escHtml(username)}</strong> 的密码：</p>
                <div style="background:var(--bg-color);padding:12px;border-radius:8px;font-family:monospace;font-size:1.1rem;text-align:center;user-select:all;" id="resetPasswordDisplay">
                    ${escHtml(password)}
                </div>
                <p style="margin-top:12px;font-size:0.85rem;color:var(--text-secondary);">请将此密码告知用户，用户登录后需修改密码。</p>
            </div>
            <div class="modal-actions">
                <button class="btn btn-primary" id="copyResetPasswordBtn">复制密码</button>
                <button class="btn btn-secondary close-modal-btn">关闭</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    modal.style.display = 'flex';

    // Close buttons
    modal.querySelectorAll('.close-modal-btn').forEach((btn) => {
        btn.addEventListener('click', () => modal.remove());
    });

    // Copy button
    const copyBtn = modal.querySelector('#copyResetPasswordBtn');
    copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(password).then(() => {
            copyBtn.textContent = '已复制';
            setTimeout(() => {
                copyBtn.textContent = '复制密码';
            }, 2000);
        });
    });
}

async function deleteUser(userId, username) {
    if (!confirm(`确定要删除用户「${username}」吗？此操作不可撤销。`)) return;

    try {
        await apiDelete(`/api/users/${userId}`);
        showToast('用户已删除', 'success');

        // Refresh user list
        bus.emit(EVENTS.VIEW_SWITCH, { view: 'user_list', refresh: true });
    } catch (err) {
        showToast('删除失败: ' + err.message, 'error');
    }
}

# Ember AI Accounting

AI 原生智能会计凭证系统。通过自然语言对话、发票识别、Excel 上传自动生成会计凭证。

# 常用命令

- 启动服务: `python server.py` (端口 8000)
- 安装依赖: `pip install -r requirements.txt`
- 数据库位置: `data/ember.db` (SQLite)
- 查看日志: `tail -f data/logs/ember.log`

# 代码规范

## Python
- 使用 4 空格缩进
- 函数和变量使用 snake_case
- 类名使用 PascalCase
- 异步函数使用 async/await
- 类型提示使用 `str | None` 而非 `Optional[str]`

## JavaScript
- 使用 4 空格缩进
- 函数使用 camelCase
- 常量使用 UPPER_SNAKE_CASE
- DOM 操作使用原生 API（无框架）

## CSS
- 使用 CSS 变量（定义在 `:root`）
- 类名使用 kebab-case
- 响应式使用 `@media`

# 项目架构

## 后端 (Python/FastAPI)
- `server.py` — FastAPI 入口，路由注册
- `database.py` — SQLite 操作（用户、凭证、规则、通知、审计）
- `routes/` — API 路由（每个文件一个 APIRouter）
- `agents/` — AgentScope Agent（意图识别、凭证生成、OCR）
- `helpers/` — 工具函数（认证、SSE、A2UI、导出）

## 前端
- `index.html` + `script.js` — 桌面端（双栏布局：侧边栏 + 工作区）
- `mobile.html` + `mobile.js` — 移动端（单页应用）
- A2UI 协议 — 服务端返回组件声明，前端动态渲染

## 数据库表
- `users` — 用户（角色：admin/reviewer/user）
- `voucher_records` — 凭证（状态：draft/pending_approval/posted/reversed）
- `approval_records` — 审批记录
- `notifications` — 通知
- `chat_messages` — 聊天记录
- `audit_logs` — 审计日志

# 角色权限

| 角色 | 用户管理 | 凭证操作 | 凭证审核 | 查看规则 |
|------|----------|----------|----------|----------|
| admin | ✓ | ✗ | ✗ | ✗ |
| reviewer | ✗ | ✗ | ✓ | ✓ |
| user | ✗ | ✓ | ✗ | ✓ |

# 已知陷阱

## 密码更新 bug
`update_user` 函数中，密码更新逻辑在字段过滤之后。如果只更新密码不更新其他字段，需要确保密码处理在空字段检查之前。

## A2UI vs 传统渲染
用户列表使用 A2UI 渲染时，DataTable 不支持行内按钮。需要使用 `actionColumns` 配置或切换到传统渲染。

## Phosphor Icons 已移除
前端不再使用 Phosphor Icons CDN，所有图标使用内联 SVG。

## 会话标题生成
会话标题由 LLM 异步生成，首次查询时可能为空。

# Git 工作流

- 主分支: `master`
- 提交信息格式: `类型: 简短描述`
  - feat: 新功能
  - fix: 修复
  - refactor: 重构
  - docs: 文档
- **推送前必须更新 README.md**

# 环境变量

```env
PMDE_BASE_URL=https://your-llm-endpoint/v1
PMDE_API_KEY=your-api-key
PMDE_MODEL_NAME=your-model
PMDE_VISION_MODEL_NAME=your-vision-model
CORS_ORIGINS=http://localhost:8000
```

# 默认账号

- 管理员: `admin` / `admin123`（首次登录需修改密码）
- 密码格式: bcrypt 哈希存储

# API 端点

- `POST /api/auth/login` — 登录
- `POST /api/chat` — 聊天（SSE 流式）
- `POST /api/upload` — 文件上传（SSE 流式）
- `GET/PUT /api/vouchers` — 凭证 CRUD
- `POST /api/vouchers/{id}/submit` — 提交审批
- `POST /api/vouchers/{id}/approve` — 审批通过
- `POST /api/vouchers/{id}/reject` — 审批驳回
- `GET /api/notifications` — 通知列表
- `GET /api/rules` — 凭证规则
- `GET /api/users` — 用户列表（管理员）

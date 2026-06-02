# Ember AI Accounting

AI 原生智能会计凭证系统。自然语言对话、发票识别、Excel 上传自动生成会计凭证。

# 常用命令

- 启动服务: `.venv/bin/python server.py` (端口 8000)
- 安装依赖: `pip install -r requirements.txt`
- 运行测试: `.venv/bin/python -m pytest tests/ -v`
- 数据库: `data/ember.db` (SQLite)
- 日志: `tail -f data/logs/ember.log`

# 代码规范

## Python
- 4 空格缩进，snake_case 函数/变量，PascalCase 类名
- 异步用 async/await，类型提示用 `str | None` 而非 `Optional[str]`
- FastAPI 路由每个文件一个 APIRouter，放在 `routes/`

## JavaScript
- 4 空格缩进，camelCase 函数，UPPER_SNAKE_CASE 常量
- 原生 DOM 操作，无框架
- A2UI 协议: 服务端返回组件声明，前端动态渲染

## CSS
- CSS 变量定义在 `:root`，kebab-case 类名，`@media` 响应式

# 架构要点

- 后端: FastAPI + SQLite + AgentScope Agent
- 前端: `index.html` + `script.js` (桌面) / `mobile.html` + `mobile.js` (移动)
- 前端模块: `static/js/` 下按功能拆分 (common, api, auth, state, event-bus, views, chat, a2ui, components)
- 数据库表: users, voucher_records, approval_records, notifications, chat_messages, audit_logs

# 角色权限

| 角色 | 用户管理 | 凭证操作 | 凭证审核 | 查看规则 |
|------|----------|----------|----------|----------|
| admin | ✓ | ✗ | ✗ | ✗ |
| reviewer | ✗ | ✗ | ✓ | ✓ |
| user | ✗ | ✓ | ✗ | ✓ |

# 已知陷阱

- `update_user` 密码更新: 密码处理必须在空字段检查之前，否则只更新密码会失败
- A2UI DataTable 不支持行内按钮，需用 `actionColumns` 或传统渲染
- 前端图标全部内联 SVG，不用 CDN
- 会话标题由 LLM 异步生成，首次查询可能为空

# Git 工作流

- 主分支: `master`
- 提交格式: `类型: 简短描述` (feat/fix/refactor/docs)
- **推送前必须更新 README.md**

# 环境变量

```env
PMDE_BASE_URL=https://your-llm-endpoint/v1
PMDE_API_KEY=your-api-key
PMDE_MODEL_NAME=your-model
PMDE_VISION_MODEL_NAME=your-vision-model
CORS_ORIGINS=http://localhost:8000
```

# 测试

- E2E 测试: `npx playwright test` (Playwright, 需要服务运行)
- Agent 评测: `.venv/bin/python -m pytest tests/evals/ -v`
- 写测试后必须运行验证，修复失败的测试

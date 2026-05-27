# Ember AI Accounting

AI 原生智能会计凭证系统。通过自然语言对话、发票图片识别、Excel 上传，自动生成符合中国会计准则的会计凭证，支持 SAP 格式导出。

## 功能概览

### 自然语言对话
- 6 种意图识别：业务描述、规则查询、规则管理、凭证查询、用户管理、闲聊
- 流式 SSE 响应，实时输出
- 多轮对话上下文（最多 200 条历史）
- 支持追问补充信息

### 凭证生成
- LLM 驱动的凭证生成，自动匹配会计规则
- 支持业务类型：销售收入（sales_revenue）、费用报销（expense）
- 自动借贷平衡校验
- 置信度评分 + 警告提示
- 凭证生命周期：草稿 → 过账

### 文件上传与 OCR
- Excel 批量导入（.xlsx / .xls / .csv）
- 图片发票识别（多模态 LLM OCR）
- PDF 识别（PyMuPDF 转图片后 OCR）
- 自动去重

### 凭证规则引擎
- 可配置的规则模板，存储于数据库
- 按业务类型、产品类型、税率匹配
- 内置销售收入和费用报销默认规则
- 管理员可通过 API 或对话增删改规则
- 税码映射：13%→X1, 6%→X6, 0%→X0

### 凭证管理
- 凭证列表（状态筛选：全部/草稿/已过账）
- 凭证详情查看（含分录行）
- 草稿凭证在线编辑
- 凭证过账确认（含审计日志）
- 附件管理：上传、查看、删除

### SAP 导出
- SAP 标准 CSV 格式（BUKRS, BLART, BLDAT, BUDAT 等字段）
- 文件上传时自动导出
- 过账凭证追加到 `data/output/posted_vouchers.csv`

### 审计系统
- 全操作审计日志：登录、登出、凭证生成、过账、规则管理、用户管理
- 聊天记录持久化
- 管理员可查看审计日志

### A2UI 协议（声明式 UI）
- 自研 Agent-to-UI 协议 v0.9
- 支持组件：Text, Card, DataTable, KeyValue, Button, Row, FilterTabs, Badge, Modal 等
- 前端根据服务端返回的组件声明动态渲染界面

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.13, FastAPI, Uvicorn |
| Agent 框架 | AgentScope 2.0.0 |
| 数据库 | SQLite (aiosqlite), WAL 模式 |
| Excel 处理 | openpyxl |
| PDF 处理 | PyMuPDF |
| 前端 | 原生 HTML/CSS/JS, Phosphor Icons |
| LLM | 可配置（默认 mimo-v2.5-pro） |

## 项目结构

```
server.py              # FastAPI 主应用，所有 API 端点，SSE 流式响应，A2UI 协议
database.py            # SQLite 数据库操作（用户、凭证、规则、审计、附件）
prompts.py             # LLM 系统提示词（意图识别、OCR、凭证生成）
voucher_rules.py       # 规则引擎：规则加载、默认规则定义、凭证构建
voucher_models.py      # 数据模型：SalesTransaction, ExpenseTransaction, Voucher
excel_loader.py        # Excel 文件解析
sap_exporter.py        # SAP CSV 导出
script.js              # 前端 JS：认证、聊天、文件上传、A2UI 渲染器
index.html             # 前端页面
style.css              # 样式表
agents/
  intent_agent.py      # 意图分类 Agent
  voucher_agent.py     # 凭证生成 Agent
  ocr_agent.py         # OCR Agent
  model_factory.py     # LLM 模型工厂
  middleware.py         # Agent 中间件：日志、计时、追踪
data/
  ember.db             # SQLite 数据库
  uploads/             # 上传文件
  output/              # 导出的 SAP CSV
  sessions/            # 会话上下文
```

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，配置 LLM API：

```env
PMDE_BASE_URL=https://your-llm-endpoint/v1
PMDE_API_KEY=your-api-key
PMDE_MODEL_NAME=your-model
PMDE_VISION_MODEL_NAME=your-vision-model
```

### 3. 启动

```bash
python server.py
```

访问 `http://localhost:8000`，默认管理员账号：`admin` / `admin123`

### 启动时自动完成

- 数据库初始化
- 默认管理员创建
- 默认凭证规则种子数据写入
- AgentScope Agent 初始化

## API 接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 当前用户信息 |

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 发送消息（SSE 流式响应） |
| POST | `/api/upload` | 上传文件（SSE 流式响应） |

### 凭证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/vouchers` | 凭证列表 |
| GET | `/api/vouchers/{id}` | 凭证详情 |
| PUT | `/api/vouchers/{id}` | 更新草稿凭证 |
| POST | `/api/confirm` | 过账凭证 |

### 规则

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/rules` | 规则列表 |
| POST | `/api/rules` | 新增规则（管理员） |
| PUT | `/api/rules/{code}` | 修改规则（管理员） |
| DELETE | `/api/rules/{code}` | 删除规则（管理员） |

### 用户管理（管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 创建用户 |
| PUT | `/api/users/{id}` | 修改用户 |
| DELETE | `/api/users/{id}` | 删除用户 |

### A2UI

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/a2ui-action` | 处理 UI 组件事件 |

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `users` | 用户账号 |
| `sessions` | 会话令牌 |
| `voucher_records` | 凭证记录 |
| `chat_messages` | 聊天记录 |
| `audit_logs` | 审计日志 |
| `voucher_rules` | 凭证规则定义 |
| `voucher_rule_lines` | 规则分录行模板 |
| `voucher_attachments` | 凭证附件 |

## 使用示例

### 自然语言生成凭证

```
用户：卖软件给华为，不含税10万元，13%税率
助手：已为您生成凭证草稿（置信度 0.95）
      [凭证详情：借 应收账款 113,000 / 贷 主营业务收入 100,000 / 贷 应交税费 13,000]
```

### 图片识别

上传发票图片，系统自动识别金额、税率、客户信息，生成凭证。

### 查询规则

```
用户：查看凭证规则
助手：以下是「全部」类型的凭证规则，共 2 条：
      [规则列表，点击查看分录行详情]
```

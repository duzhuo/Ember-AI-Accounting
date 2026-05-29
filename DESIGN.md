# Ember AI Accounting — 系统设计文档

> 生成日期：2026-05-29

---

## 一、项目概览

**项目名称：** Ember AI Accounting  
**版本：** 2.0（当前）  
**定位：** AI 智能记账助手，为配合 SAP 财务系统设计，让普通员工通过自然语言对话完成记账，最终生成符合 SAP 格式的数据。

---

## 二、当前技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.110+，Uvicorn 0.29+ |
| Agent 框架 | AgentScope 2.0.0 |
| 数据库 | SQLite + aiosqlite（WAL 模式） |
| LLM | DeepSeek API（默认 deepseek-v4-pro） |
| Excel 处理 | openpyxl 3.1.5 |
| PDF 处理 | PyMuPDF 1.24（OCR 转换） |
| PDF 导出 | WeasyPrint + Jinja2 |
| 认证 | bcrypt 密码哈希 + Session（6小时超时） |
| 前端 | 原生 HTML/CSS/JavaScript，Phosphor Icons |
| 协议 | A2UI v0.9（自定义 Agent-to-UI 声明式协议） |
| 流式传输 | Server-Sent Events（SSE） |

---

## 三、项目结构

```
ember-ai-accounting/
├── server.py                    # FastAPI 主入口
├── database.py                  # SQLite 层（用户、凭证、审计日志）
├── prompts.py                   # LLM 提示词加载器
├── voucher_models.py            # 数据模型
├── voucher_rules.py             # 凭证生成规则引擎
├── excel_loader.py              # Excel 解析（灵活表头）
├── sap_exporter.py              # SAP CSV 导出
├── index.html                   # 前端入口
├── script.js                    # 前端逻辑（1945行）
├── style.css                    # 样式表（1945行）
│
├── agents/
│   ├── intent_agent.py          # 意图分类 + 数据提取
│   ├── voucher_agent.py         # 凭证生成
│   ├── ocr_agent.py             # 图片/PDF → 交易提取
│   ├── model_factory.py         # DeepSeek LLM 工厂
│   ├── agent_config.py          # Agent 身份配置
│   └── middleware.py            # 日志、计时、追踪中间件
│
├── helpers/
│   ├── a2ui.py                  # A2UI 协议转换
│   ├── auth.py                  # Session 管理与认证
│   ├── csv_export.py            # SAP CSV 追加导出
│   ├── pdf_export.py            # PDF 生成
│   ├── sse.py                   # SSE 格式化工具
│   └── voucher.py               # 凭证格式化助手
│
├── routes/
│   ├── auth.py                  # /api/auth/*, /api/users/*
│   ├── chat.py                  # /api/chat（SSE 流式）
│   ├── upload.py                # /api/upload（文件处理）
│   ├── vouchers.py              # /api/vouchers/*（CRUD）
│   ├── rules.py                 # /api/rules/*（规则管理）
│   ├── audit.py                 # /api/audit-logs, /api/chat-history
│   ├── attachments.py           # /api/attachments/*
│   ├── confirm.py               # /api/confirm（凭证过账）
│   └── a2ui_action.py           # /api/a2ui-action（UI 事件）
│
├── prompts/
│   ├── intent_recognition.md    # 意图分类规则
│   ├── image_recognition.md     # 发票 OCR 提取
│   └── voucher_generation.md    # 凭证生成指令
│
└── templates/
    └── voucher_pdf.html         # PDF 凭证模板
```

---

## 四、数据库结构（当前）

| 表名 | 用途 |
|-----|------|
| `users` | 用户账户（id, username, password_hash, role） |
| `sessions` | Session 令牌（6小时过期） |
| `voucher_records` | 凭证草稿/已过账/已冲销（含审计链） |
| `voucher_rules` | 规则定义（rule_code, business_type, tax_rate） |
| `voucher_rule_lines` | 规则行项目（科目、借贷方向、税码） |
| `chat_messages` | 对话历史 |
| `audit_logs` | 操作审计日志 |
| `voucher_attachments` | 凭证附件 |

---

## 五、API 端点

| 路径 | 方法 | 说明 |
|-----|------|------|
| `/api/auth/login` | POST | 登录（含频率限制） |
| `/api/auth/logout` | POST | 登出 |
| `/api/auth/me` | GET | 当前用户信息 |
| `/api/auth/password` | PUT | 修改密码 |
| `/api/chat` | POST | SSE 流式对话 |
| `/api/upload` | POST | 文件上传（SSE 进度） |
| `/api/vouchers` | GET | 凭证列表（含过滤） |
| `/api/vouchers/{id}` | GET/PUT | 凭证详情/编辑草稿 |
| `/api/vouchers/{id}/reverse` | POST | 冲销凭证 |
| `/api/vouchers/{id}/pdf` | GET | 导出 PDF |
| `/api/confirm` | POST | 单条过账 |
| `/api/confirm/batch` | POST | 批量过账 |
| `/api/rules` | GET/POST/PUT/DELETE | 规则管理（仅管理员） |
| `/api/users` | GET/POST/PUT/DELETE | 用户管理（仅管理员） |
| `/api/audit-logs` | GET | 审计日志（仅管理员） |
| `/api/chat-history` | GET | 对话记录（仅管理员） |
| `/api/vouchers/{id}/attachments` | GET/POST/DELETE | 附件管理 |
| `/api/a2ui-action` | POST | UI 组件事件处理 |

---

## 六、当前功能说明

### 6.1 自然语言录凭证
用户用中文描述业务，系统自动生成符合中国会计准则的分录：
- 销售收入：应收账款 / 主营业务收入 / 应交增值税
- 费用报销：管理费用 / 应交增值税进项 / 应付账款

### 6.2 发票图片识别
- 支持格式：.png .jpg .jpeg .gif .webp .bmp .pdf
- OCR 提取：金额、税率、供应商、日期
- PDF 通过 PyMuPDF 转图片后识别

### 6.3 Excel 批量导入
- 灵活表头映射（中英文别名）
- 批量生成多张凭证
- 自动去重

### 6.4 凭证生命周期
```
草稿(draft) → 已过账(posted) → 已冲销(reversed)
```

### 6.5 SAP 集成（当前）
- 导出标准 SAP CSV 格式
- 字段：BUKRS, BLART, BLDAT, BUDAT, XBLNR, HKONT 等
- 过账后自动追加到 `data/output/posted_vouchers.csv`
- 导入方式：LSMW / FB01 手动导入

### 6.6 税码映射
| 税率 | SAP 税码 |
|-----|---------|
| 13% | X1 |
| 6% | X6 |
| 0% | X0 |

---

## 七、代码审查发现的问题

### 关键问题（Critical）

| # | 文件 | 行号 | 问题 | 修复方案 |
|---|------|------|------|---------|
| 1 | `helpers/auth.py` | 108-134 | 路径穿越漏洞：`session_id` 未验证，可写入任意文件 | 用正则校验 session_id 格式，限制路径在 SESSION_DIR 内 |
| 2 | `routes/chat.py` | 410-413 | 明文密码存入 `chat_messages` 表 | 保存到数据库时替换为脱敏文本 |
| 3 | `routes/vouchers.py` | 66-96 | PUT 编辑凭证缺少所有权校验，任意用户可编辑他人凭证 | 加 `user_id` 比对检查 |

### 高危问题（High）

| # | 文件 | 行号 | 问题 | 修复方案 |
|---|------|------|------|---------|
| 4 | `routes/attachments.py` | 31-62 | 上传附件无所有权检查 | 查凭证归属后再允许上传 |
| 5 | `routes/confirm.py` | 69-92 | 批量过账无所有权检查 | 加 user_id 过滤 |
| 6 | `routes/confirm.py` | 19-65 | 单条过账无所有权检查 | 同上 |
| 7 | `routes/attachments.py` | 65-75 | 删除附件无权限验证 | 增加附件归属查询 |
| 8 | `database.py` | 518-531 | `mark_voucher_posted` 无 `status='draft'` 守卫，已冲销凭证可重新过账 | WHERE 加 `AND status = 'draft'` |
| 9 | `routes/vouchers.py` | 116-121 | 冲销操作两次 DB 调用无事务，崩溃产生幽灵凭证 | 包装为单一原子事务 |
| 10 | `agents/intent_agent.py` | 173 | 空 `transaction_id` 导致所有凭证碰撞到 `VR-` 主键，静默丢数据 | 空时生成 UUID 兜底 |

### 中危问题（Medium）

| # | 文件 | 行号 | 问题 | 修复方案 |
|---|------|------|------|---------|
| 11 | `database.py` | 638-697 | `amount_min`/`amount_max` 参数被接受但从不执行过滤 | 实现过滤逻辑或删除死参数 |
| 12 | `helpers/auth.py` | 154 | Session 过期用 `utcnow()`，其他地方用 `now()`，UTC+8 下 session 永不过期 | 统一使用 `datetime.now()` |
| 13 | `routes/auth.py` | 28-49 | 登录频率限制存于内存，重启即清零 | 迁移到 Redis 或 SQLite |
| 14 | `routes/upload.py` | 82-85 | 文件上传无大小/类型验证，可耗尽磁盘 | 限制 20MB，校验扩展名 |
| 15 | `agents/ocr_agent.py` | 209-218 | PDF 无页数上限，500页PDF耗尽内存 | 限制最多处理 10 页 |
| 16 | `routes/auth.py` | 178-191 | `**payload` 直接展开传入 DB，密码明文写入审计日志 | 显式白名单提取字段 |
| 17 | `server.py` | 121 | 生产环境 `reload=True`，攻击者写文件可触发重启 | 通过环境变量控制 reload |
| 18 | `database.py` | 661-666 | 关键词搜索对整个 JSON blob 做 LIKE，全表扫描性能差 | 只搜索反范化列 |
| 19 | `database.py` | 935-938 | `create_rule` 关闭连接后再开一个查询，多余往返 | 复用同一连接 |
| 20 | `routes/upload.py` | 85 | 同步 I/O 阻塞 async 事件循环 | 改用 `asyncio.to_thread()` |
| 21 | `database.py` | 430-438 | 删除用户不级联清理 sessions/凭证，产生孤立数据 | 改为软删除或显式级联 |

### 低危问题（Low）

| # | 文件 | 行号 | 问题 | 修复方案 |
|---|------|------|------|---------|
| 22 | `server.py` | 50-55 | CORS `allow_credentials` 未设置 | 按需显式设置 |
| 23 | `helpers/sse.py` | 11-25 | 手工解析流式 JSON，遗漏 `\t` `\uXXXX` 等转义 | 改用增量 JSON 解析器 |
| 24 | `helpers/auth.py` | 116-124 | Session 加载失败静默吞异常，用户会话丢失无日志 | 记录 WARNING 日志 |

---

## 八、对 SAP 系统的辅助价值

### 解决的痛点
SAP 录凭证需要专业财务人员手动操作，流程繁琐。本系统让普通员工通过自然语言完成记账，财务人员只做审核。

### 典型流程对比

| 场景 | 传统方式 | 使用本系统 |
|-----|---------|----------|
| 单张发票报销 | 财务手录 5-10 分钟 | 拍照上传，30秒 |
| 月末批量销售凭证 | Excel 整理 + 逐条录入 SAP | 上传 Excel，自动生成 |
| 审计追踪 | 纸质台账 | 全程数字化审计日志 |

### 当前局限
1. 不直接连接 SAP，是"前置处理层"，仍需手动导入 CSV
2. 科目规则需要初始配置
3. 仅支持销售收入和费用报销两种业务类型
4. AI 识别复杂发票需人工复核

---

## 九、重新设计方案

### 9.1 两版本路线

**V1（CSV增强版）：** 在现有代码基础上修复问题、增加审批流、扩展业务类型  
**V2（BAPI直连版）：** 直连 SAP 过账，主数据双向同步

---

### 9.2 V1 架构

```
┌─────────────────────────────────────────┐
│              前端层                      │
│  PC Web (Vue3)  +  移动端H5(拍照上传)    │
└─────────────────────────────────────────┘
              ↕ REST + SSE
┌─────────────────────────────────────────┐
│              API 网关层                  │
│  FastAPI  •  认证中间件  •  审计日志      │
└─────────────────────────────────────────┘
              ↕
┌─────────────────────────────────────────┐
│              业务服务层                  │
│  审批流引擎  •  规则引擎  •  导出服务     │
└─────────────────────────────────────────┘
              ↕
┌─────────────────────────────────────────┐
│              AI Agent 层                │
│  IntentAgent → ValidationAgent →        │
│  VoucherAgent → ReviewAgent             │
└─────────────────────────────────────────┘
              ↕
┌─────────────────────────────────────────┐
│              数据层                      │
│  PostgreSQL  •  Redis(Session/限流)      │
└─────────────────────────────────────────┘
```

### 9.3 V1 核心改动

#### 安全层
- session_id 路径校验
- 密码不写入聊天记录
- 所有凭证操作加所有权检查
- 文件上传大小/类型限制（20MB上限）
- 登录频率限制迁移到 Redis
- mark_voucher_posted 加 status guard
- 冲销操作改为原子事务

#### 数据库升级：SQLite → PostgreSQL

```sql
-- 凭证头（拆出 JSON blob）
voucher_headers (
  id, company_code, doc_type, doc_date,
  posting_date, reference, header_text,
  status, created_by, approved_by,
  sap_doc_number   -- V2 填充
)

-- 凭证行
voucher_lines (
  id, voucher_id, line_no,
  account, debit_credit, amount,
  tax_code, cost_center, text
)

-- 审批记录
approval_records (
  id, voucher_id, step, action,
  operator, comment, operated_at
)
```

#### 审批流引擎

```
提交草稿
   ↓
系统自动校验（借贷平衡、科目合法性）
   ↓
金额 < 1万    → 财务初审 → 生成CSV
金额 1万~10万 → 财务初审 → 财务主管审批 → 生成CSV
金额 ≥ 10万   → 财务初审 → 主管审批 → CFO审批 → 生成CSV
```

审批阈值后台可配置。

#### AI Agent 层重构（新增两个Agent）

```
IntentAgent     （已有）意图分类
ExtractionAgent （已有）结构化提取
ValidationAgent （新增）金额异常/税率校验/重复检测
VoucherAgent    （已有）生成分录
ReviewAgent     （新增）借贷平衡/科目方向/置信度评分
```

#### 扩展业务类型

| 类型 | V1 | V2 |
|-----|----|----|
| 销售收入 | ✅ 已有 | ✅ |
| 费用报销 | ✅ 已有 | ✅ |
| 银行付款 | ✅ 新增 | ✅ |
| 固定资产采购 | ✅ 新增 | ✅ |
| 工资计提 | 🔲 | ✅ |
| 期末结转 | 🔲 | ✅ |

#### 移动端 H5
- 响应式 H5 页面，无需原生 App
- 调用相机拍照上传发票
- 预览草稿后提交审批

---

### 9.4 V2 架构新增：SAP 集成层

```
┌──────────────────────────────────┐
│         SAP 集成服务              │
│                                  │
│  SAPConnector                    │
│  ├── post_document()             │  ← BAPI_ACC_DOCUMENT_POST
│  ├── check_document()            │  ← BAPI_ACC_DOCUMENT_CHECK
│  ├── sync_chart_of_accounts()    │  ← 同步科目表 SKA1
│  ├── sync_vendor_master()        │  ← 同步供应商 LFA1
│  └── sync_customer_master()      │  ← 同步客户 KNA1
└──────────────────────────────────┘
```

#### 主数据同步（每日凌晨2点）

```
SAP → 本地缓存
SKA1 科目表       → account_master
KNA1 客户主数据   → customer_master
LFA1 供应商主数据 → vendor_master
CSKS 成本中心     → cost_center_master
```

#### 过账回写流程

```
审批通过
   ↓
BAPI_ACC_DOCUMENT_CHECK（模拟校验）
   ↓
失败 → 返回 SAP 错误信息
   ↓
通过 → BAPI_ACC_DOCUMENT_POST
   ↓
获取 SAP 凭证号（如 1800000123）
   ↓
回写 voucher_headers.sap_doc_number
   ↓
状态变更为 sap_posted
```

---

### 9.5 技术选型对比

| 组件 | 当前 | V1 | V2 |
|-----|------|----|----|
| 数据库 | SQLite | PostgreSQL | PostgreSQL |
| Session/限流 | 内存/文件 | Redis | Redis |
| 前端 | 原生 JS | Vue3 | Vue3 |
| Agent 框架 | AgentScope | AgentScope | AgentScope |
| SAP 集成 | CSV 导出 | CSV 导出（优化） | pyrfc + BAPI |
| 主数据 | 硬编码规则 | 可配置规则 | SAP 同步 |
| 部署 | 单进程 | Docker Compose | Docker Compose |

---

### 9.6 实施路线图

```
第1个月  安全修复 + PostgreSQL 迁移 + 审批流
第2个月  新业务类型 + ValidationAgent + ReviewAgent
第3个月  移动端 H5 + UI 重构(Vue3) + 性能优化
─────────────── V1 交付 ───────────────
第4个月  SAP RFC 连接层 + BAPI 测试环境搭建
第5个月  主数据同步 + 过账回写 + 错误处理
第6个月  压测 + 安全审计 + 生产部署
─────────────── V2 交付 ───────────────
```

---

## 十、核心价值重申

这个系统最大的价值不在于替代财务人员，而在于**让非财务人员也能正确提交财务数据**：

- 业务员、采购、销售自助提交
- 财务人员只做审核和例外处理
- 财务回归真正的管控角色
- 录入工作从财务部门分散到全公司

---

*文档由 Claude Code 根据代码审查和设计讨论自动生成*

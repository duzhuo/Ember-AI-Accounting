"""A2UI Protocol v0.9 helpers — voucher, rules, users conversion to declarative UI."""

# ── Constants ────────────────────────────────────────────────────────────────

BIZ_TYPE_LABELS = {
    "sales_revenue": "销售收入",
    "expense": "费用报销",
    "asset_purchase": "资产采购",
    "salary": "工资薪酬",
    "loan": "借款/还款",
}


# ── A2UI Protocol Helpers ────────────────────────────────────────────────────


def _build_a2ui_messages(surface_id: str, components: list, data: dict | None = None) -> list:
    """Build A2UI v0.9 protocol messages: createSurface + updateComponents + optional updateDataModel."""
    msgs = [{"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": "ember"}}]
    msgs.append({"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}})
    if data:
        msgs.append({"version": "v0.9", "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data}})
    return msgs


def _voucher_to_a2ui(voucher_front: dict, voucher_id: str, show_actions: bool = True, attachments: list | None = None) -> dict:
    """Convert voucher frontend dict to A2UI messages."""
    rows = voucher_front.get("rows", [])
    header_pairs = [
        {"label": "凭证号", "value": voucher_front.get("voucher_id", "—")},
        {"label": "公司代码", "value": voucher_front.get("company_code", "—")},
        {"label": "凭证类型", "value": voucher_front.get("document_type", "—")},
        {"label": "凭证日期", "value": voucher_front.get("document_date", "—")},
        {"label": "过账日期", "value": voucher_front.get("posting_date", "—")},
        {"label": "参考", "value": voucher_front.get("reference", "—")},
        {"label": "凭证头文本", "value": voucher_front.get("header_text", "—")},
        {"label": "置信度", "value": voucher_front.get("confidence", "—")},
    ]

    table_columns = [
        {"key": "line_no", "label": "行号"},
        {"key": "account_code", "label": "科目代码"},
        {"key": "account_name", "label": "科目名称"},
        {"key": "debit_credit", "label": "借/贷"},
        {"key": "debit", "label": "借方金额", "align": "right"},
        {"key": "credit", "label": "贷方金额", "align": "right"},
        {"key": "currency", "label": "币种"},
        {"key": "text", "label": "摘要"},
    ]
    table_rows = []
    for r in rows:
        table_rows.append({
            "line_no": str(r.get("line_no", "")),
            "account_code": r.get("account_code", ""),
            "account_name": r.get("account_name", ""),
            "debit_credit": "借" if r.get("debit_credit") == "S" else "贷",
            "debit": f"{r.get('debit', 0):,.2f}" if r.get("debit") else "",
            "credit": f"{r.get('credit', 0):,.2f}" if r.get("credit") else "",
            "currency": r.get("currency", "CNY"),
            "text": r.get("text", ""),
        })

    total_debit = sum(r.get("debit", 0) for r in rows)
    total_credit = sum(r.get("credit", 0) for r in rows)

    warnings = voucher_front.get("warnings", [])
    warning_components = []
    if warnings:
        warning_components.append({
            "id": "warnings", "component": "Text",
            "text": "⚠️ " + "；".join(warnings), "variant": "caption",
        })

    status = voucher_front.get("status", "draft")
    is_posted = status == "posted"

    components = [
        {"id": "back-btn", "component": "Button", "child": "back-text",
         "variant": "secondary", "action": {"event": {"name": "back_to_voucher_list"}}},
        {"id": "back-text", "component": "Text", "text": "← 返回列表"},
        {"id": "title", "component": "Text", "text": f"凭证 {voucher_id}", "variant": "h2"},
        {"id": "info-card", "component": "Card", "title": "凭证信息", "children": ["kv-info"]},
        {"id": "kv-info", "component": "KeyValue", "pairs": header_pairs},
        *warning_components,
        {"id": "rows-card", "component": "Card", "title": "凭证明细", "children": ["rows-table"]},
        {"id": "rows-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows,
         "footer": {"label": "合计", "values": ["", "", "", "",
                      f"{total_debit:,.2f}", f"{total_credit:,.2f}", "", ""]}},
    ]
    # Attachments section
    att_list = attachments or []
    att_table_rows = []
    for att in att_list:
        size_kb = round(att.get("file_size", 0) / 1024, 1)
        att_table_rows.append({
            "id": att.get("id", ""),
            "filename": att.get("filename", ""),
            "size": f"{size_kb} KB",
            "uploaded_by_name": att.get("uploaded_by_name", ""),
            "created_at": att.get("created_at", ""),
        })

    att_columns = [
        {"key": "filename", "label": "文件名"},
        {"key": "size", "label": "大小"},
        {"key": "uploaded_by_name", "label": "上传人"},
        {"key": "created_at", "label": "上传时间"},
    ]

    components.append(
        {"id": "attach-card", "component": "Card", "title": f"附件（{len(att_list)} 份）", "children": ["attach-table", "attach-btn-row"]},
    )
    components.append(
        {"id": "attach-table", "component": "DataTable",
         "columns": att_columns, "rows": att_table_rows},
    )
    components.append(
        {"id": "attach-btn-row", "component": "Row", "children": ["upload-attach-btn"]},
    )
    components.append(
        {"id": "upload-attach-btn", "component": "Button", "child": "upload-attach-text",
         "variant": "secondary",
         "action": {"event": {"name": "upload_attachment", "data": {"voucherId": voucher_id}}}},
    )
    components.append(
        {"id": "upload-attach-text", "component": "Text", "text": "上传附件"},
    )

    if show_actions:
        components.extend([
            {"id": "actions-row", "component": "Row", "children": ["confirm-btn", "edit-btn"]},
            {"id": "confirm-btn", "component": "Button", "child": "confirm-text",
             "variant": "primary", "disabled": is_posted,
             "action": {"event": {"name": "confirm_voucher", "data": {"voucherId": voucher_id}}}},
            {"id": "confirm-text", "component": "Text", "text": "已过账" if is_posted else "确认并记账"},
            {"id": "edit-btn", "component": "Button", "child": "edit-text",
             "variant": "secondary", "disabled": is_posted,
             "action": {"event": {"name": "edit_voucher", "data": {"voucherId": voucher_id}}}},
            {"id": "edit-text", "component": "Text", "text": "编辑凭证"},
        ])
    return _build_a2ui_messages("voucher-detail", components)


def _voucher_list_to_a2ui(records: list, total: int, status_filter: str | None) -> dict:
    """Convert voucher records list to A2UI messages."""
    status_label = {"draft": "草稿", "posted": "已过账"}.get(status_filter, "全部")

    tabs = [
        {"key": "", "label": "全部"},
        {"key": "draft", "label": "草稿"},
        {"key": "posted", "label": "已过账"},
    ]

    table_columns = [
        {"key": "voucher_id", "label": "凭证号"},
        {"key": "document_type", "label": "类型"},
        {"key": "document_date", "label": "日期"},
        {"key": "header_text", "label": "摘要"},
        {"key": "status", "label": "状态"},
        {"key": "created_at", "label": "创建时间"},
    ]
    table_rows = []
    for rec in records:
        status_text = "已过账" if rec.get("status") == "posted" else "草稿"
        table_rows.append({
            "voucher_id": rec.get("voucher_id", ""),
            "document_type": rec.get("document_type", ""),
            "document_date": rec.get("document_date", ""),
            "header_text": rec.get("header_text", ""),
            "status": status_text,
            "created_at": rec.get("created_at", ""),
        })

    components = [
        {"id": "title", "component": "Text", "text": f"凭证列表 — {status_label}（共 {total} 条）", "variant": "h2"},
        {"id": "filter-tabs", "component": "FilterTabs",
         "tabs": tabs, "active": status_filter or "",
         "action": {"event": {"name": "filter_vouchers"}}},
        {"id": "voucher-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows,
         "rowAction": {"event": {"name": "view_voucher_detail", "data": {"voucherId": "{voucher_id}"}}}},
    ]
    return _build_a2ui_messages("voucher-list", components)


def _rules_to_a2ui(rules_list: list, rule_type: str | None, rule_mgmt: dict | None = None) -> dict:
    """Convert rules list to A2UI messages."""
    biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type) if rule_type else "全部"

    table_columns = [
        {"key": "rule_code", "label": "规则编码"},
        {"key": "business_type", "label": "业务类型"},
        {"key": "product_type", "label": "产品类型"},
        {"key": "tax_rate", "label": "税率"},
        {"key": "document_type", "label": "凭证类型"},
        {"key": "line_count", "label": "分录行数"},
    ]
    table_rows = []
    for rule in rules_list:
        table_rows.append({
            "rule_code": rule.get("rule_code", ""),
            "business_type": rule.get("business_type", ""),
            "product_type": rule.get("product_type", ""),
            "tax_rate": str(rule.get("tax_rate", "")),
            "document_type": rule.get("document_type", ""),
            "line_count": str(len(rule.get("lines", []))),
        })

    action_buttons = []
    if rule_mgmt and rule_mgmt.get("action") == "create":
        action_buttons.append({
            "id": "add-rule-btn", "component": "Button", "child": "add-rule-text",
            "variant": "primary",
            "action": {"event": {"name": "create_rule", "data": {"ruleType": rule_type}}}},
        )
        action_buttons.append({"id": "add-rule-text", "component": "Text", "text": "新增规则"})

    components = [
        {"id": "title", "component": "Text", "text": f"凭证规则 — {biz_label}（共 {len(rules_list)} 条）", "variant": "h2"},
        {"id": "rules-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows,
         "rowAction": {"event": {"name": "view_rule_detail", "data": {"ruleCode": "{rule_code}"}}}},
        *action_buttons,
    ]
    return _build_a2ui_messages("rules", components)


def _rule_detail_to_a2ui(rule: dict) -> dict:
    """Convert a single rule with lines to A2UI detail view."""
    biz_label = BIZ_TYPE_LABELS.get(rule.get("business_type"), rule.get("business_type", ""))
    components = [
        {"id": "detail-title", "component": "Text",
         "text": f"规则详情 — {rule.get('rule_code', '')}", "variant": "h2"},
        {"id": "detail-info", "component": "Card", "children": ["info-biz", "info-prod", "info-tax", "info-doc"],
         "title": "基本信息"},
        {"id": "info-biz", "component": "Text", "text": f"业务类型：{biz_label}"},
        {"id": "info-prod", "component": "Text", "text": f"产品类型：{rule.get('product_type', '-')}"},
        {"id": "info-tax", "component": "Text", "text": f"税率：{rule.get('tax_rate', '-')}"},
        {"id": "info-doc", "component": "Text", "text": f"凭证类型：{rule.get('document_type', '-')}"},
        {"id": "lines-title", "component": "Text", "text": "分录行", "variant": "h3"},
    ]

    lines = rule.get("lines", [])
    if lines:
        line_columns = [
            {"key": "line_no", "label": "行号"},
            {"key": "debit_credit", "label": "借贷"},
            {"key": "account_code", "label": "科目编码"},
            {"key": "account_name", "label": "科目名称"},
            {"key": "amount_field", "label": "金额字段"},
            {"key": "tax_code_rule", "label": "税码规则"},
        ]
        line_rows = []
        for line in lines:
            line_rows.append({
                "line_no": str(line.get("line_no", "")),
                "debit_credit": "借" if line.get("debit_credit") == "S" else "贷",
                "account_code": line.get("account_code", ""),
                "account_name": line.get("account_name", ""),
                "amount_field": line.get("amount_field", ""),
                "tax_code_rule": line.get("tax_code_rule", ""),
            })
        components.append({
            "id": "lines-table", "component": "DataTable",
            "columns": line_columns, "rows": line_rows,
        })
    else:
        components.append({"id": "no-lines", "component": "Text", "text": "暂无分录行"})

    components.append({
        "id": "back-btn", "component": "Button", "child": "back-btn-text",
        "variant": "secondary",
        "action": {"event": {"name": "back_to_rules", "data": {}}},
    })
    components.append({"id": "back-btn-text", "component": "Text", "text": "返回规则列表"})

    return _build_a2ui_messages("rule_detail", components)


def _users_to_a2ui(users: list) -> dict:
    """Convert users list to A2UI messages."""
    table_columns = [
        {"key": "username", "label": "用户名"},
        {"key": "display_name", "label": "显示名称"},
        {"key": "role", "label": "角色"},
        {"key": "created_at", "label": "创建时间"},
    ]
    table_rows = []
    for u in users:
        table_rows.append({
            "username": u.get("username", ""),
            "display_name": u.get("display_name", ""),
            "role": "管理员" if u.get("role") == "admin" else "普通用户",
            "created_at": u.get("created_at", ""),
        })

    components = [
        {"id": "title", "component": "Text", "text": f"用户管理（共 {len(users)} 人）", "variant": "h2"},
        {"id": "users-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows},
    ]
    return _build_a2ui_messages("users", components)

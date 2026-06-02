"""A2UI Protocol v0.9 helpers — voucher, rules, users conversion to declarative UI."""

# ── Constants ────────────────────────────────────────────────────────────────

BIZ_TYPE_LABELS = {
    "sales_revenue": "销售收入",
    "expense": "费用报销",
    "asset_purchase": "资产采购",
    "salary": "工资薪酬",
    "loan": "借款/还款",
}


# ── Reusable A2UI component builders ─────────────────────────────────────────


def _build_a2ui_messages(surface_id: str, components: list, data: dict | None = None) -> list:
    """Build A2UI v0.9 protocol messages: createSurface + updateComponents + optional updateDataModel."""
    msgs = [{"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": "ember"}}]
    msgs.append({"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}})
    if data:
        msgs.append({"version": "v0.9", "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data}})
    return msgs


def _a2ui_datatable(
    table_id: str,
    columns: list[dict],
    rows: list[dict],
    **kwargs: object,
) -> dict:
    """Build a DataTable component dict.

    Args:
        table_id: Component ID.
        columns: Column definitions.
        rows: Row data.
        **kwargs: Extra attributes (selectable, rowAction, footer, actionColumns, etc.).
    """
    component: dict = {
        "id": table_id,
        "component": "DataTable",
        "columns": columns,
        "rows": rows,
    }
    component.update(kwargs)
    return component


def _a2ui_button(
    btn_id: str,
    text: str,
    variant: str = "secondary",
    action: dict | None = None,
    disabled: bool = False,
) -> list[dict]:
    """Build a Button + Text child pair.

    Returns a list of two component dicts: the Button and its Text child.
    """
    text_id = f"{btn_id}-text"
    btn: dict = {
        "id": btn_id,
        "component": "Button",
        "child": text_id,
        "variant": variant,
    }
    if action:
        btn["action"] = action
    if disabled:
        btn["disabled"] = True
    return [btn, {"id": text_id, "component": "Text", "text": text}]


def _a2ui_card(card_id: str, title: str, children: list[str], **kwargs: object) -> dict:
    """Build a Card component dict."""
    component: dict = {
        "id": card_id,
        "component": "Card",
        "title": title,
        "children": children,
    }
    component.update(kwargs)
    return component


def _a2ui_pagination(
    offset: int,
    limit: int,
    total: int,
    event_name: str,
    extra_data: dict | None = None,
) -> list[dict]:
    """Build pagination components (prev-btn, page-info, next-btn wrapped in a Row).

    Returns a list of component dicts, or empty list if pagination is not needed.
    """
    if total <= limit:
        return []

    has_prev = offset > 0
    has_next = offset + limit < total
    page_num = offset // limit + 1
    total_pages = max(1, (total + limit - 1) // limit)
    pagination_data = extra_data or {}

    return [
        {"id": "pagination-row", "component": "Row", "children": ["prev-btn", "page-info", "next-btn"]},
        *_a2ui_button("prev-btn", "上一页", "secondary",
                       action={"event": {"name": event_name, "data": {**pagination_data, "limit": limit, "offset": max(0, offset - limit)}}},
                       disabled=not has_prev),
        {"id": "page-info", "component": "Text", "text": f"第 {page_num}/{total_pages} 页", "variant": "caption"},
        *_a2ui_button("next-btn", "下一页", "secondary",
                       action={"event": {"name": event_name, "data": {**pagination_data, "limit": limit, "offset": offset + limit}}},
                       disabled=not has_next),
    ]


# ── A2UI Protocol Converters ─────────────────────────────────────────────────


def _voucher_to_a2ui(voucher_front: dict, voucher_id: str, show_actions: bool = True, attachments: list | None = None) -> list:
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
    is_reversed = status == "reversed"

    status_map = {"posted": "已过账", "reversed": "已冲销"}
    status_badge = status_map.get(status, "草稿")

    components = [
        *_a2ui_button("back-btn", "← 返回列表", "secondary",
                       action={"event": {"name": "back_to_voucher_list"}}),
        {"id": "title", "component": "Text", "text": f"凭证 {voucher_id}  [{status_badge}]", "variant": "h2"},
        _a2ui_card("info-card", "凭证信息", ["kv-info"]),
        {"id": "kv-info", "component": "KeyValue", "pairs": header_pairs},
        *warning_components,
        _a2ui_card("rows-card", "凭证明细", ["rows-table"]),
        _a2ui_datatable("rows-table", table_columns, table_rows,
                         footer={"label": "合计", "values": ["", "", "", "",
                                 f"{total_debit:,.2f}", f"{total_credit:,.2f}", "", ""]}),
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

    components.append(_a2ui_card("attach-card", f"附件（{len(att_list)} 份）", ["attach-table", "attach-btn-row"]))
    components.append(_a2ui_datatable("attach-table", att_columns, att_table_rows))
    components.append({"id": "attach-btn-row", "component": "Row", "children": ["upload-attach-btn"]})
    components.extend(_a2ui_button("upload-attach-btn", "上传附件", "secondary",
                                    action={"event": {"name": "upload_attachment", "data": {"voucherId": voucher_id}}}))

    if show_actions:
        confirm_text = "已过账" if is_posted else ("已冲销" if is_reversed else "确认并记账")
        components.append({"id": "actions-row", "component": "Row", "children": ["confirm-btn", "edit-btn", "reverse-btn", "pdf-btn"]})
        components.extend(_a2ui_button("confirm-btn", confirm_text, "primary",
                                        action={"event": {"name": "confirm_voucher", "data": {"voucherId": voucher_id}}},
                                        disabled=is_posted or is_reversed))
        components.extend(_a2ui_button("edit-btn", "编辑凭证", "secondary",
                                        action={"event": {"name": "edit_voucher", "data": {"voucherId": voucher_id}}},
                                        disabled=is_posted or is_reversed))
        components.extend(_a2ui_button("reverse-btn", "冲销凭证", "danger",
                                        action={"event": {"name": "reverse_voucher", "data": {"voucherId": voucher_id}}},
                                        disabled=not is_posted))
        components.extend(_a2ui_button("pdf-btn", "导出 PDF", "secondary",
                                        action={"event": {"name": "export_voucher_pdf", "data": {"voucherId": voucher_id}}}))

    return _build_a2ui_messages("voucher-detail", components)


def _voucher_list_to_a2ui(records: list, total: int, status_filter: str | None, keyword: str | None = None, limit: int = 50, offset: int = 0) -> list:
    """Convert voucher records list to A2UI messages with pagination."""
    status_label = {"draft": "草稿", "posted": "已过账", "reversed": "已冲销"}.get(status_filter, "全部")

    tabs = [
        {"key": "", "label": "全部"},
        {"key": "draft", "label": "草稿"},
        {"key": "posted", "label": "已过账"},
        {"key": "reversed", "label": "已冲销"},
    ]

    table_columns = [
        {"key": "select", "label": "☐", "width": "36px"},
        {"key": "voucher_id", "label": "凭证号"},
        {"key": "document_type", "label": "类型"},
        {"key": "document_date", "label": "日期"},
        {"key": "header_text", "label": "摘要"},
        {"key": "status", "label": "状态"},
        {"key": "created_at", "label": "创建时间"},
    ]
    table_rows = []
    for rec in records:
        st = rec.get("status", "draft")
        status_text = {"posted": "已过账", "reversed": "已冲销"}.get(st, "草稿")
        table_rows.append({
            "select": "☐",
            "voucher_id": rec.get("voucher_id", ""),
            "document_type": rec.get("document_type", ""),
            "document_date": rec.get("document_date", ""),
            "header_text": rec.get("header_text", ""),
            "status": status_text,
            "created_at": rec.get("created_at", ""),
        })

    components = [
        {"id": "title", "component": "Text", "text": f"凭证列表 — {status_label}（共 {total} 条）", "variant": "h2"},
        {"id": "search-row", "component": "Row", "children": ["search-input", "search-btn", "batch-post-btn"]},
        {"id": "search-input", "component": "SearchInput", "placeholder": "搜索凭证（摘要、凭证号、客户）",
         "value": keyword or "", "action": {"event": {"name": "search_vouchers"}}},
        *_a2ui_button("search-btn", "搜索", "secondary",
                       action={"event": {"name": "search_vouchers"}}),
        *_a2ui_button("batch-post-btn", "批量过账", "primary",
                       action={"event": {"name": "batch_post_vouchers"}},
                       disabled=True),
        {"id": "filter-tabs", "component": "FilterTabs",
         "tabs": tabs, "active": status_filter or "",
         "action": {"event": {"name": "filter_vouchers"}}},
        _a2ui_datatable("voucher-table", table_columns, table_rows, selectable=True,
                         rowAction={"event": {"name": "view_voucher_detail", "data": {"voucherId": "{voucher_id}"}}}),
    ]

    # Pagination
    pagination_event = "filter_vouchers" if not keyword else "search_vouchers"
    pagination_data = {"status": status_filter or "", "keyword": keyword or ""}
    components.extend(_a2ui_pagination(offset, limit, total, pagination_event, pagination_data))

    return _build_a2ui_messages("voucher-list", components)


def _rules_to_a2ui(rules_list: list, rule_type: str | None, rule_mgmt: dict | None = None) -> list:
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

    action_buttons: list[dict] = []
    if rule_mgmt and rule_mgmt.get("action") == "create":
        action_buttons.extend(_a2ui_button("add-rule-btn", "新增规则", "primary",
                                            action={"event": {"name": "create_rule", "data": {"ruleType": rule_type}}}))

    components = [
        {"id": "title", "component": "Text", "text": f"凭证规则 — {biz_label}（共 {len(rules_list)} 条）", "variant": "h2"},
        _a2ui_datatable("rules-table", table_columns, table_rows,
                         rowAction={"event": {"name": "view_rule_detail", "data": {"ruleCode": "{rule_code}"}}}),
        *action_buttons,
    ]
    return _build_a2ui_messages("rules", components)


def _rule_detail_to_a2ui(rule: dict) -> list:
    """Convert a single rule with lines to A2UI detail view."""
    biz_label = BIZ_TYPE_LABELS.get(rule.get("business_type"), rule.get("business_type", ""))
    components = [
        {"id": "detail-title", "component": "Text",
         "text": f"规则详情 — {rule.get('rule_code', '')}", "variant": "h2"},
        _a2ui_card("detail-info", "基本信息", ["info-biz", "info-prod", "info-tax", "info-doc"]),
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
        components.append(_a2ui_datatable("lines-table", line_columns, line_rows))
    else:
        components.append({"id": "no-lines", "component": "Text", "text": "暂无分录行"})

    components.extend(_a2ui_button("back-btn", "返回规则列表", "secondary",
                                    action={"event": {"name": "back_to_rules", "data": {}}}))

    return _build_a2ui_messages("rule_detail", components)


def _users_to_a2ui(users: list) -> list:
    """Convert users list to A2UI messages."""
    table_columns = [
        {"key": "username", "label": "用户名"},
        {"key": "display_name", "label": "显示名称"},
        {"key": "role", "label": "角色"},
        {"key": "created_at", "label": "创建时间"},
        {"key": "actions", "label": "操作", "align": "center", "width": "120px"},
    ]
    table_rows = []
    role_labels = {"admin": "管理员", "reviewer": "复核人", "user": "普通用户"}
    for u in users:
        table_rows.append({
            "user_id": u.get("id", ""),
            "username": u.get("username", ""),
            "display_name": u.get("display_name", ""),
            "role": role_labels.get(u.get("role"), "普通用户"),
            "created_at": u.get("created_at", ""),
            "actions": "",  # Will be rendered by actionColumns
        })

    action_columns = {
        "actions": [
            {
                "label": "编辑",
                "icon": '<svg width="16" height="16" viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16"><path d="M180 40l36 36-128 128H52v-36L180 40z" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                "tooltip": "编辑用户",
                "action": {"event": {"name": "user_edit", "data": {"user_id": "{user_id}", "username": "{username}"}}}
            },
            {
                "label": "重置密码",
                "icon": '<svg width="16" height="16" viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16"><rect x="48" y="120" width="160" height="112" rx="8"/><path d="M88 120V80a40 40 0 0 1 80 0v40" stroke-linecap="round"/><circle cx="128" cy="176" r="16"/></svg>',
                "tooltip": "重置密码",
                "action": {"event": {"name": "user_reset_password", "data": {"user_id": "{user_id}", "username": "{username}"}}}
            },
            {
                "label": "删除",
                "icon": '<svg width="16" height="16" viewBox="0 0 256 256" fill="none" stroke="currentColor" stroke-width="16"><path d="M48 72h160M104 72V48h48v24M56 72l16 136h112l16-136" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                "tooltip": "删除用户",
                "action": {"event": {"name": "user_delete", "data": {"user_id": "{user_id}", "username": "{username}"}}}
            },
        ]
    }

    components = [
        {"id": "title", "component": "Text", "text": f"用户管理（共 {len(users)} 人）", "variant": "h2"},
        _a2ui_datatable("users-table", table_columns, table_rows, actionColumns=action_columns),
    ]
    return _build_a2ui_messages("users", components)

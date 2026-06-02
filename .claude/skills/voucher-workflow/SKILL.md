---
name: voucher-workflow
description: Guide through voucher creation, submission, and approval workflow
---
# Voucher Workflow Guide

## Voucher States
- `draft` → `pending_approval` → `posted`
- Can be `reversed` from `posted`

## Creating a Voucher
1. Voucher is created via chat (natural language) or file upload
2. Agent generates voucher entries with debit/credit amounts
3. Voucher saved as `draft` status

## Submitting for Approval
- `POST /api/vouchers/{id}/submit`
- Changes status from `draft` to `pending_approval`
- Creates notification for reviewers

## Approval/Rejection
- `POST /api/vouchers/{id}/approve` — reviewer approves
- `POST /api/vouchers/{id}/reject` — reviewer rejects with reason
- Only users with `reviewer` role can approve

## Reversal
- `POST /api/vouchers/{id}/reverse`
- Creates a reversing entry, original marked as `reversed`

## Key Files
- `routes/vouchers.py` — voucher API routes
- `database.py` — voucher CRUD operations
- `agents/` — AI agents for voucher generation
- `voucher_models.py` — voucher data models
- `voucher_rules.py` — accounting rules

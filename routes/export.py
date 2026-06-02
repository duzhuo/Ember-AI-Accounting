"""CSV export routes for SAP voucher data."""

import csv
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from database import get_voucher_record
from helpers.auth import _require_auth
from helpers.csv_export import POSTED_CSV
from sap_exporter import SAP_COLUMNS, record_to_sap_rows

router = APIRouter()


@router.get("/api/export/csv/all")
async def export_all_posted_csv(request: Request):
    """Download the full posted_vouchers.csv accumulated on the server."""
    await _require_auth(request)

    if not POSTED_CSV.exists() or POSTED_CSV.stat().st_size == 0:
        return JSONResponse({"error": "暂无已过账凭证数据"}, status_code=404)

    return FileResponse(
        path=str(POSTED_CSV),
        media_type="text/csv",
        filename="posted_vouchers.csv",
        headers={"Content-Disposition": 'attachment; filename="posted_vouchers.csv"'},
    )


@router.get("/api/export/csv")
async def export_vouchers_csv(request: Request):
    """Export specific vouchers as SAP CSV. Draft vouchers are included."""
    await _require_auth(request)

    ids_param = request.query_params.get("ids", "")
    id_list = [v.strip() for v in ids_param.split(",") if v.strip()]
    if not id_list:
        return JSONResponse({"error": "ids 参数必填"}, status_code=400)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
    )
    writer = csv.DictWriter(tmp, fieldnames=SAP_COLUMNS)
    writer.writeheader()

    found = 0
    for voucher_id in id_list:
        record = await get_voucher_record(voucher_id)
        if not record:
            continue
        found += 1
        writer.writerows(record_to_sap_rows(record))

    tmp.close()

    if found == 0:
        return JSONResponse({"error": "未找到匹配的凭证"}, status_code=404)

    return FileResponse(
        path=tmp.name,
        media_type="text/csv",
        filename="sap_export.csv",
        headers={"Content-Disposition": 'attachment; filename="sap_export.csv"'},
    )

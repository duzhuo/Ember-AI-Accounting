"""FastAPI backend for the AI Accounting Voucher web app.

Run:
    source .venv/bin/activate
    python server.py
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agentscope.workspace import LocalWorkspace
from agents.intent_agent import IntentAgent
from agents.voucher_agent import VoucherAgent
from agents.ocr_agent import OcrAgent

from database import init_db, migrate_rules_from_excel, seed_default_rules

from routes import auth, chat, upload, vouchers, rules, audit, attachments, confirm, a2ui_action

# ── Logging ──────────────────────────────────────────────────────────────────

_log_dir = Path(__file__).parent / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_dir / "ember.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── App setup ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent

app = FastAPI(title="Ember AI Accounting", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ─────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(upload.router)
app.include_router(vouchers.router)
app.include_router(rules.router)
app.include_router(audit.router)
app.include_router(attachments.router)
app.include_router(confirm.router)
app.include_router(a2ui_action.router)

# ── Startup / Shutdown ──────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("Database initialized")
    migrated = await migrate_rules_from_excel()
    if migrated:
        logger.info("Migrated %d rules from Excel to database", migrated)
    seeded = await seed_default_rules()
    if seeded:
        logger.info("Seeded %d default rules", seeded)

    workspace = LocalWorkspace(workdir=str(PROJECT_ROOT / "data" / "workspace"))
    await workspace.initialize()
    app.state.workspace = workspace
    logger.info("Workspace initialized: %s", workspace.workdir)

    app.state.intent_agent = IntentAgent("intent_agent", offloader=workspace)
    app.state.voucher_agent = VoucherAgent("voucher_agent", offloader=workspace)
    app.state.ocr_agent = OcrAgent("ocr_agent")
    logger.info("Agents initialized")


@app.on_event("shutdown")
async def shutdown():
    workspace = getattr(app.state, "workspace", None)
    if workspace:
        await workspace.close()
        logger.info("Workspace closed")


# ── Serve static frontend ────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(PROJECT_ROOT), html=True), name="static")


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

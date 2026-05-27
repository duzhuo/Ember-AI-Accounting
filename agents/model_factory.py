"""Centralized model creation for all AgentScope agents."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from agentscope.credential import DeepSeekCredential
from agentscope.formatter import OpenAIChatFormatter
from agentscope.model import DeepSeekChatModel

PMDE_BASE_URL = os.environ.get("PMDE_BASE_URL", "")
PMDE_API_KEY = os.environ.get("PMDE_API_KEY", "")
PMDE_MODEL_NAME = os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro")
PMDE_VISION_MODEL_NAME = os.environ.get("PMDE_VISION_MODEL_NAME", "deepseek-v4-pro")


def create_chat_model(vision: bool = False) -> DeepSeekChatModel:
    """Create a DeepSeekChatModel for the configured endpoint."""
    kwargs = dict(
        credential=DeepSeekCredential(api_key=PMDE_API_KEY, base_url=PMDE_BASE_URL),
        model=PMDE_VISION_MODEL_NAME if vision else PMDE_MODEL_NAME,
        stream=True,
        parameters=DeepSeekChatModel.Parameters(temperature=0.1),
    )
    if vision:
        kwargs["formatter"] = OpenAIChatFormatter(input_types=["text/plain", "image/png", "image/jpeg"])
    return DeepSeekChatModel(**kwargs)

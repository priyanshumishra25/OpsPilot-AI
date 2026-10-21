"""ASGI entrypoint: `uvicorn opspilot.main:app`."""

from opspilot.api.app import create_app

app = create_app()

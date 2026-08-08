from fastapi.testclient import TestClient
import json
import logging
from io import StringIO


def test_imports_and_construction():
    from transcription.main import app as trans_app
    from orchestrator.main import app as orch_app
    from bot_df.main import app as bot_app

    assert trans_app is not None
    assert orch_app is not None
    assert bot_app is not None


def test_health_endpoints():
    from transcription.main import app as trans_app
    from orchestrator.main import app as orch_app
    from bot_df.main import app as bot_app

    # Transcription
    client = TestClient(trans_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Orchestrator
    client = TestClient(orch_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Bot DF
    client = TestClient(bot_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_correlation_id_middleware():
    from orchestrator.main import app

    client = TestClient(app)

    # Case 1: Custom correlation ID passed
    custom_id = "test-custom-uuid-1234"
    response = client.get("/health", headers={"X-Correlation-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_id

    # Case 2: Generated correlation ID
    response = client.get("/health")
    assert response.status_code == 200
    generated_id = response.headers.get("X-Correlation-ID")
    assert generated_id is not None
    assert len(generated_id) > 0


def test_json_formatter_and_correlation_logging():
    from observability.logging import correlation_id_var

    # Setup logger and capture output
    log_capture = StringIO()
    logger = logging.getLogger("test_logger")
    logger.setLevel(logging.INFO)

    # Import Formatter
    from observability.logging import JSONFormatter

    handler = logging.StreamHandler(log_capture)
    handler.setFormatter(JSONFormatter())
    logger.handlers = [handler]

    # Log without correlation id
    logger.info("Hello world")
    log_output = log_capture.getvalue()
    log_data = json.loads(log_output.splitlines()[-1])
    assert log_data["message"] == "Hello world"
    assert "correlation_id" not in log_data

    # Log with correlation id
    token = correlation_id_var.set("correlation-123")
    try:
        logger.info("Log with correlation")
    finally:
        correlation_id_var.reset(token)

    log_output = log_capture.getvalue()
    log_data = json.loads(log_output.splitlines()[-1])
    assert log_data["message"] == "Log with correlation"
    assert log_data["correlation_id"] == "correlation-123"


def test_sqlalchemy_metadata_and_models():
    from db.models import Base, Organization, Bot, Instance, User

    # Check tables are in metadata
    assert "organizations" in Base.metadata.tables
    assert "bots" in Base.metadata.tables
    assert "instances" in Base.metadata.tables
    assert "users" in Base.metadata.tables

    # Confirm relationship columns
    assert Organization.id.name == "id"
    assert Bot.organization_id.name == "organization_id"
    assert Instance.bot_id.name == "bot_id"
    assert User.phone_number.name == "phone_number"

from fastapi import FastAPI, Request, HTTPException, Header, Depends
from contextlib import asynccontextmanager
import logging
import os
import hmac

from observability.logging import setup_logging
from observability.middleware import CorrelationIdMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(title="Bot DF", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)


# Token auth dependency
def verify_bearer_token(authorization: str = Header(None)):
    token = os.environ.get("ORCHESTRATOR_TO_BOT_TOKEN", "placeholder_bearer_token")
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("Bot DF: Missing or malformed authorization header.")
        raise HTTPException(status_code=401, detail="Unauthorized")

    auth_token = authorization.split("Bearer ")[1]
    if not hmac.compare_digest(auth_token.encode("utf-8"), token.encode("utf-8")):
        logger.warning("Bot DF: Invalid authorization token.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    return auth_token


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "bot-df"}


@app.post("/events")
async def receive_event(request: Request, token: str = Depends(verify_bearer_token)):
    payload = await request.json()
    logger.info(
        f"Bot DF received event: {payload.get('external_message_id')} | status=ROUTED"
    )
    return {"status": "accepted"}

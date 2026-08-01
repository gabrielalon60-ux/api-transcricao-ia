from fastapi import FastAPI, Request, Depends, HTTPException
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
import sqlalchemy as sa
import logging
import json
import uuid
import re
from datetime import datetime, timezone
import hmac
import hashlib

from observability.logging import setup_logging
from observability.middleware import CorrelationIdMiddleware
from observability.logging import correlation_id_var

from db.session import get_db
from db.models import Event, Instance, Organization, User
from security.hash import verify_secret, hash_pii
from orchestrator.config import get_settings
from orchestrator.wuzapi import WuzapiClient, WuzapiError
from orchestrator.rate_limit import check_and_record_registration

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(title="Orchestrator", lifespan=lifespan)
app.add_middleware(CorrelationIdMiddleware)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "orchestrator"}


def verify_webhook_signature(
    body_bytes: bytes, signature: str | None, secret: str
) -> bool:
    if not signature or not secret:
        return False
    computed = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def normalize_phone_number(phone: str) -> str:
    # Remove JID suffixes
    phone_clean = phone.split("@")[0]
    # Remove non-digits
    digits = re.sub(r"\D", "", phone_clean)
    digits = digits.lstrip("0")
    # Add Brazil DDI 55 if length represents a local number (10 or 11 digits)
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    return digits


def mask_phone_number(phone: str) -> str:
    if len(phone) <= 8:
        return "****"
    return phone[:4] + "****" + phone[-4:]


@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()

    # 1. Read raw body and authenticate signature
    body_bytes = await request.body()
    signature = request.headers.get("x-hmac-signature")

    if not verify_webhook_signature(
        body_bytes, signature, settings.wuzapi_webhook_secret
    ):
        logger.warning("Webhook signature verification failed.")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse payload
    content_type = request.headers.get("content-type", "")
    try:
        if "form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            json_data = form.get("jsonData")
            payload = json.loads(json_data) if isinstance(json_data, str) else {}
        else:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception as exc:
        logger.warning(f"Malformed webhook payload: {exc}")
        raise HTTPException(status_code=400, detail="Malformed payload")

    # Derive Ingress Identity
    provider = payload.get("provider") or "WUZAPI"

    # Extract external IDs
    external_instance_id = None
    external_message_id = None
    sender_phone_raw = None
    message_type = None
    text_content = None

    # Handle WUZAPI and Evolution API formats
    is_evolution = "base64" in payload and isinstance(payload.get("event"), dict)
    if is_evolution:
        info = payload["event"].get("Info", {})
        external_instance_id = payload.get("instanceId")
        external_message_id = info.get("Id")
        sender_phone_raw = info.get("SenderAlt") or info.get("Sender") or ""
        msg_type = info.get("MediaType") or "text"
        message_type = (
            "image"
            if msg_type == "image"
            else ("pdf" if msg_type == "document" else "text")
        )
        text_content = payload.get("text")
    else:
        # Standard WUZAPI format
        external_instance_id = payload.get("instanceId")
        data = payload.get("data") or {}
        message = data.get("message") or {}
        if not message and "messages" in data:
            message = data["messages"][0] if data["messages"] else {}

        if not external_instance_id:
            external_instance_id = data.get("instanceId") or payload.get(
                "instance", {}
            ).get("external_id")

        key = message.get("key", {})
        external_message_id = key.get("id")
        sender_phone_raw = key.get("remoteJid") or payload.get("sender") or ""

        # Determine message type
        if "imageMessage" in message:
            message_type = "image"
        elif "documentMessage" in message:
            message_type = "pdf"
        else:
            message_type = "text"
            text_content = message.get("conversation") or message.get(
                "extendedTextMessage", {}
            ).get("text")

    if not provider or not external_instance_id or not external_message_id:
        logger.warning(
            "Webhook payload lacks minimum source fields to compute idempotency key."
        )
        raise HTTPException(status_code=400, detail="Missing required source fields")

    # Set correlation ID
    correlation_id = correlation_id_var.get() or str(uuid.uuid4())
    correlation_id_var.set(correlation_id)

    # 2. Transaction A (Atomic Event Insertion / Idempotency Check)
    try:
        event = Event(
            correlation_id=correlation_id,
            provider=provider,
            external_instance_id=external_instance_id,
            external_message_id=external_message_id,
            message_type=message_type,
            status="RECEIVED",
            duplicate_count=0,
        )
        db.add(event)
        db.commit()
    except sa.exc.IntegrityError:
        db.rollback()
        # Atomic duplicate update directly in SQL
        db.execute(
            sa.text(
                "UPDATE events "
                "SET duplicate_count = duplicate_count + 1, "
                "    last_duplicate_at = CURRENT_TIMESTAMP "
                "WHERE provider = :provider "
                "  AND external_instance_id = :ext_inst_id "
                "  AND external_message_id = :ext_msg_id"
            ),
            {
                "provider": provider,
                "ext_inst_id": external_instance_id,
                "ext_msg_id": external_message_id,
            },
        )
        db.commit()
        logger.info(
            f"Duplicate webhook received for {external_message_id}. Idempotently ignored."
        )
        return {"status": "ok", "detail": "idempotent duplicate"}

    # Refresh event to get the ID
    db.refresh(event)

    # 3. Non-Transactional Parsing / Identity Resolution
    # Resolve internal Instance
    instance = (
        db.query(Instance)
        .filter_by(external_instance_id=external_instance_id, provider=provider)
        .first()
    )
    if not instance:
        # Update Event status to INSTANCE_NOT_FOUND
        event.status = "INSTANCE_NOT_FOUND"
        db.commit()

        # TBD-WUZAPI-UNKNOWN-INSTANCE-OUTBOUND (Condicional)
        # We only attempt to send if we have a valid sender phone and can construct a client
        should_send_unknown = False
        if sender_phone_raw and settings.wuzapi_base_url and settings.wuzapi_token:
            should_send_unknown = True

        if should_send_unknown:
            try:
                wuzapi = WuzapiClient()
                phone_norm = normalize_phone_number(sender_phone_raw)
                await wuzapi.send_text_message(
                    phone_norm,
                    "Não foi possível processar sua solicitação neste momento.\nTente novamente mais tarde.",
                )
            except Exception as e:
                logger.error(f"Failed to send unknown-instance WhatsApp response: {e}")

        logger.warning(f"Instance not found for external_id: {external_instance_id}")
        return {"status": "ok", "detail": "instance_not_found"}

    # Update event routing fields
    event.instance_id = instance.id
    event.organization_id = instance.organization_id
    db.commit()

    # Normalize phone
    phone_norm = normalize_phone_number(sender_phone_raw)
    phone_masked = mask_phone_number(phone_norm)
    phone_correlation = hash_pii(phone_norm, settings.log_pii_hash_key)

    logger.info(
        f"Processing message | correlation_hash={phone_correlation[:10]} | masked={phone_masked}"
    )

    # Resolve User globally
    user = db.query(User).filter_by(phone_number=phone_norm).first()

    # 4. Inbound Decision Paths
    if user:
        event.user_id = user.id
        db.commit()

        # Check organization mismatch
        if user.organization_id != instance.organization_id:
            event.status = "USER_ORGANIZATION_MISMATCH"
            db.commit()

            # Send conflict message
            try:
                wuzapi = WuzapiClient()
                await wuzapi.send_text_message(
                    phone_norm,
                    "❌ Este número já está vinculado a outra organização.\n\nEntre em contato com o responsável pelo sistema para solicitar a regularização.",
                )
            except WuzapiError as e:
                event.error_code = "WUZAPI_SEND_FAILED"
                event.error_message_sanitized = str(e)
                db.commit()
            return {"status": "ok", "detail": "organization_mismatch"}

        # Check user status
        if user.status in ("INACTIVE", "SUSPENDED"):
            event.status = "USER_INACTIVE"
            db.commit()

            try:
                wuzapi = WuzapiClient()
                await wuzapi.send_text_message(
                    phone_norm,
                    "⚠️ O acesso deste número está desativado.\n\nEntre em contato com o responsável pelo sistema.",
                )
            except WuzapiError as e:
                event.error_code = "WUZAPI_SEND_FAILED"
                event.error_message_sanitized = str(e)
                db.commit()
            return {"status": "ok", "detail": "user_inactive"}

        # Check Active User commands
        if (
            message_type == "text"
            and text_content
            and text_content.strip().startswith("/cadastro")
        ):
            event.status = "REGISTRATION_ALREADY_ACTIVE"
            db.commit()

            try:
                wuzapi = WuzapiClient()
                await wuzapi.send_text_message(
                    phone_norm,
                    "✅ Este número já está cadastrado.\n\nVocê já pode enviar seus comprovantes.",
                )
            except WuzapiError as e:
                event.error_code = "WUZAPI_SEND_FAILED"
                event.error_message_sanitized = str(e)
                db.commit()
            return {"status": "ok", "detail": "already_active"}

        # Authorized routing path
        await route_active_user_event(db, event, settings, payload)
        return {"status": "ok", "detail": "routed"}

    else:
        # User does not exist
        # If normal document/media, reply unauthorized instructions
        is_registration = (
            message_type == "text"
            and text_content
            and text_content.strip().startswith("/cadastro")
        )

        if not is_registration:
            event.status = "UNAUTHORIZED_USER"
            db.commit()

            try:
                wuzapi = WuzapiClient()
                await wuzapi.send_text_message(
                    phone_norm,
                    "Este número ainda não está cadastrado.\n\nPara habilitar o acesso, envie:\n/cadastro SUA_SENHA",
                )
            except WuzapiError as e:
                event.error_code = "WUZAPI_SEND_FAILED"
                event.error_message_sanitized = str(e)
                db.commit()
            return {"status": "ok", "detail": "unauthorized_user"}

        # Registration flow /cadastro <senha>
        content_str = text_content or ""
        parts = content_str.strip().split(maxsplit=1)
        submitted_secret = parts[1] if len(parts) > 1 else ""

        # Define organization secret validation function
        org = db.query(Organization).filter_by(id=instance.organization_id).first()
        if not org:
            logger.error(f"Organization {instance.organization_id} not found.")
            raise HTTPException(status_code=500, detail="Organization misconfiguration")

        secret_hash = org.registration_secret_hash
        if not secret_hash:
            logger.error(
                f"Organization {instance.organization_id} has no registration secret hash."
            )
            raise HTTPException(status_code=500, detail="Organization misconfiguration")

        def secret_validator(raw_secret: str) -> bool:
            return verify_secret(
                instance.organization_id + ":" + raw_secret,
                secret_hash,
                settings.registration_secret_pepper,
            )

        # 5. Transação B (Cadastro e Rate Limit)
        success, user_msg, err_code = check_and_record_registration(
            db=db,
            organization_id=instance.organization_id,
            phone_number=phone_norm,
            instance_id=instance.id,
            correlation_id=correlation_id,
            secret_validator_fn=secret_validator,
            submitted_secret=submitted_secret,
            pepper=settings.registration_secret_pepper,
        )

        if success:
            # Create user
            try:
                new_user = User(
                    organization_id=instance.organization_id,
                    phone_number=phone_norm,
                    name=None,
                    status="ACTIVE",
                    registered_at=datetime.now(timezone.utc),
                )
                db.add(new_user)
                db.flush()
                event.user_id = new_user.id
                event.status = "REGISTRATION_SUCCEEDED"
                db.commit()
            except sa.exc.IntegrityError:
                # Concurrent race condition (another transaction registered same user)
                db.rollback()
                logger.info(
                    f"Conflict: user registration race resolved idempotently for {phone_correlation[:10]}."
                )
                # Find the user created by the winner
                winning_user = db.query(User).filter_by(phone_number=phone_norm).first()
                if winning_user:
                    event.user_id = winning_user.id
                event.status = "REGISTRATION_SUCCEEDED"
                db.commit()
        else:
            # Failed attempt (blocked or invalid secret)
            event.status = (
                "REGISTRATION_BLOCKED"
                if err_code == "REGISTRATION_RATE_LIMITED"
                else "REGISTRATION_FAILED"
            )
            if err_code:
                event.error_code = err_code
            db.commit()

        # 6. External side effect (send WUZAPI response outside transaction)
        try:
            wuzapi = WuzapiClient()
            await wuzapi.send_text_message(phone_norm, user_msg)
        except WuzapiError as e:
            # Record outbound failure without rollback
            event.error_code = "WUZAPI_SEND_FAILED"
            event.error_message_sanitized = str(e)
            db.commit()

        return {"status": "ok", "detail": event.status}


async def route_active_user_event(
    db: Session, event: Event, settings, payload: dict
) -> None:
    # 1. Update Event to ROUTING and commit
    event.status = "ROUTING"
    db.commit()

    # 2. Call Bot DF outside transaction
    import httpx

    url = f"{settings.bot_df_url.rstrip('/')}/events"
    headers = {
        "Authorization": f"Bearer {settings.orchestrator_to_bot_token}",
        "Content-Type": "application/json",
    }

    # We forward the normalized event (or simplified request)
    normalized_payload = {
        "correlation_id": event.correlation_id,
        "provider": event.provider,
        "external_instance_id": event.external_instance_id,
        "external_message_id": event.external_message_id,
        "organization_id": event.organization_id,
        "instance_id": event.instance_id,
        "user_id": event.user_id,
        "message_type": event.message_type,
        "received_at": event.received_at.isoformat() if event.received_at else None,
    }

    success = False
    err_msg = None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, headers=headers, json=normalized_payload)
            if response.status_code == 200:
                success = True
            else:
                err_msg = f"HTTP {response.status_code}: {response.text[:200]}"
    except Exception as e:
        err_msg = str(e)

    # 3. Transaction C: Update status based on result
    if success:
        event.status = "ROUTED"
        event.routed_at = datetime.now(timezone.utc)
    else:
        event.status = "FAILED"
        event.failed_at = datetime.now(timezone.utc)
        event.error_code = "BOT_ROUTING_FAILED"
        event.error_message_sanitized = err_msg
    db.commit()

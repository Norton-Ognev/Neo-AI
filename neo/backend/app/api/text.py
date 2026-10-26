import logging

from fastapi import APIRouter, HTTPException

from ..schemas.assistant import TextRequest, TextResponse
from ..services.request_cancellation import (
    RequestCancelled,
    cancel_request,
    register_request,
    unregister_request,
)
from ..services.assistant_service import AssistantUnavailableError, process_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["assistant"])


def _publish_wake_result(request: TextRequest, response: TextResponse) -> None:
    if request.session_id != "wake-word":
        return
    try:
        from neo.desktop.audio_bridge import publish_wake_response

        publish_wake_response(request.normalized_text(), response.model_dump())
    except Exception:
        logger.debug("Could not publish background voice response to desktop UI", exc_info=True)


@router.post("/cancel")
def cancel_assistant_request(payload: dict) -> dict[str, bool]:
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise HTTPException(status_code=422, detail="Не указан запрос для отмены.")
    return {"cancelled": cancel_request(request_id)}


@router.post("/text", response_model=TextResponse)
def handle_text(request: TextRequest) -> TextResponse:
    if not request.normalized_text():
        raise HTTPException(status_code=422, detail="Текст команды не должен быть пустым")
    cancellation_event = register_request(request.request_id)
    try:
        response = process_text(request, cancellation_event)
        if cancellation_event.is_set():
            raise RequestCancelled
        if request.speak and response.action != "cancel":
            try:
                from neo.voice.tts import speak_text

                speak_text(response.response_text)
                response.speech_queued = True
            except Exception:
                # Speech is an optional output channel; keep the text response usable.
                logger.exception("Could not queue response for speech")
        _publish_wake_result(request, response)
        return response
    except RequestCancelled:
        response = TextResponse(action="cancel", response_text="Обработка отменена.")
        _publish_wake_result(request, response)
        return response
    except AssistantUnavailableError as exc:
        logger.warning("Ollama unavailable; returning an AI service error to the client")
        raise HTTPException(
            status_code=503,
            detail="Не удалось обратиться к AI. Проверьте, что Ollama запущена и модель загружена.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to process text command")
        raise HTTPException(
            status_code=500,
            detail="Не удалось обработать команду на backend.",
        ) from exc
    finally:
        unregister_request(request.request_id, cancellation_event)

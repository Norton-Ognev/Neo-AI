import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from neo.backend.app.schemas.assistant import TextRequest
from neo.backend.app.services.assistant_service import (
    AssistantUnavailableError,
    process_text,
)
from neo.backend.app.services.request_cancellation import (
    RequestCancelled,
    register_request,
    raise_if_cancelled,
    unregister_request,
)
from neo.voice.tts import get_status, speak_text, stop_speech

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.get("/test-ui", include_in_schema=False)
def voice_test_ui() -> FileResponse:
    """Serve a tiny same-origin page for testing the microphone in a browser."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "voice_test.html"))


@router.get("/status")
def voice_status() -> dict:
    return get_status()


@router.post("/stop")
def stop_voice_speech() -> dict[str, bool]:
    stop_speech()
    return {"stopped": True}


@router.post("/test")
def test_voice() -> dict[str, str | bool]:
    try:
        speak_text("Привет! Я Нео. Проверяю, что голосовое воспроизведение работает.")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Не удалось запустить озвучивание") from exc
    return {"queued": True, "message": "Тестовая фраза поставлена в очередь."}


@router.post("/command")
def voice_command(
    audio: UploadFile = File(..., description="Аудиозапись команды с микрофона"),
    session_id: str = Form(default="default", min_length=1, max_length=100),
    request_id: str | None = Form(default=None, max_length=100),
    speak: bool = Form(default=True),
) -> dict:
    """Recognize uploaded speech and pass its transcript to the assistant."""
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac"}:
        suffix = ".webm"

    cancellation_event = register_request(request_id)
    data = audio.file.read(_MAX_AUDIO_BYTES + 1)
    if not data:
        unregister_request(request_id, cancellation_event)
        raise HTTPException(status_code=422, detail="Аудиозапись пустая.")
    if len(data) > _MAX_AUDIO_BYTES:
        unregister_request(request_id, cancellation_event)
        raise HTTPException(status_code=413, detail="Аудиозапись превышает лимит 25 МБ.")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(data)

        from neo.voice.stt import normalize_transcript, transcribe_audio

        raise_if_cancelled(cancellation_event)
        transcript = transcribe_audio(temp_path)
        raise_if_cancelled(cancellation_event)
        if not transcript:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Не удалось распознать речь. Проверьте, что браузер использует нужный "
                    "микрофон и индикатор уровня входа реагирует на голос; затем повторите запись."
                ),
            )
        normalized_transcript = normalize_transcript(transcript)

        response = process_text(TextRequest(
            text=normalized_transcript,
            session_id=session_id,
            request_id=request_id,
            speak=speak,
        ), cancellation_event)
        raise_if_cancelled(cancellation_event)
        if speak and response.action != "cancel":
            try:
                speak_text(response.response_text)
                response.speech_queued = True
            except Exception:
                logger.exception("Не удалось поставить голосовой ответ в очередь")
        return {
            "transcription": transcript,
            "normalized_transcription": normalized_transcript,
            **response.model_dump(),
        }
    except RequestCancelled:
        return {"action": "cancel", "response_text": "Обработка отменена.", "speech_queued": False}
    except AssistantUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Речь распознана, но backend не смог обработать команду через AI. "
                "Смотрите traceback в терминале, где запущен backend."
            ),
        ) from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        unregister_request(request_id, cancellation_event)

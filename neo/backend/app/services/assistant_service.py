from __future__ import annotations

from datetime import datetime, time, timedelta
import calendar
import logging
import re
from ollama import ResponseError

from ..schemas.assistant import TextRequest, TextResponse
from ..schemas.event import EventCreate
from .event_service import EventService

logger = logging.getLogger(__name__)
_PERIOD_NUMBER_WORDS = {
    "одна": 1, "один": 1, "одну": 1, "одной": 1,
    "две": 2, "два": 2, "двух": 2, "пару": 2,
    "три": 3, "трех": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10, "несколько": 3,
}


def _period_count(text: str, unit: str) -> int:
    match = re.search(
        rf"\b(\d{{1,2}}|{'|'.join(_PERIOD_NUMBER_WORDS)})\s+{unit}\w*\b",
        text,
    )
    if not match:
        return 1
    value = match.group(1)
    count = int(value) if value.isdigit() else _PERIOD_NUMBER_WORDS[value]
    return max(1, min(count, 365))


def _add_months(value, months: int):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _date_only_reply(text: str, reference: datetime) -> datetime | None:
    """Разбирает уточнение даты, например «мне нужно 31 число», в контексте события."""
    folded = text.casefold().replace("ё", "е")
    if re.search(r"\b\d{1,2}(?:[:.]\d{2})\b|\b\d{1,2}\s*час(?:а|ов)?\b|\b(?:утра|дня|вечера|ночи)\b", folded):
        return None

    month_numbers = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    explicit_month = re.search(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|"
        r"августа|сентября|октября|ноября|декабря)(?:\s+(\d{4}))?\b",
        folded,
    )
    today = datetime.now().astimezone().date()
    if explicit_month:
        day = int(explicit_month.group(1))
        month = month_numbers[explicit_month.group(2)]
        year = int(explicit_month.group(3) or reference.year)
        try:
            target = datetime(year, month, day, tzinfo=reference.tzinfo)
        except ValueError:
            return None
        if not explicit_month.group(3) and target.date() < today:
            try:
                target = target.replace(year=target.year + 1)
            except ValueError:
                return None
        return target

    day_match = re.search(r"\b(\d{1,2})(?:-?(?:го|е))?\s+числ\w*\b", folded)
    if not day_match:
        day_match = re.search(r"\b(\d{1,2})-(?:го|е)\b", folded)
    if not day_match:
        return None

    day = int(day_match.group(1))
    month_cursor = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(12):
        if day <= calendar.monthrange(month_cursor.year, month_cursor.month)[1]:
            target = month_cursor.replace(day=day)
            if target.date() >= today:
                return target
        month_cursor = _add_months(month_cursor, 1)
    return None


_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _spoken_datetime(value: str | None) -> str:
    if not value:
        return "время не указано"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    now = datetime.now().astimezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    else:
        dt = dt.astimezone(now.tzinfo)
    return f"{dt.day} {_MONTHS_GENITIVE[dt.month - 1]} в {dt:%H:%M}"


class AssistantUnavailableError(RuntimeError):
    """Возникает, когда оркестратор не может достучаться до локальной модели или загрузить её."""


def process_text(request: TextRequest, cancellation_event=None) -> TextResponse:
    """Классифицирует текстовую команду, выполняет соответствующее действие в базе данных и формирует ответ."""
    from .request_cancellation import raise_if_cancelled

    raise_if_cancelled(cancellation_event)
    text = request.normalized_text()
    from neo.ai import context_manager
    stop_phrase = re.sub(r"[^a-zа-яё]+", " ", text.casefold()).strip()
    if stop_phrase in {
        "нео стоп", "стоп нео", "neo stop", "stop neo",
        "нео хватит", "хватит нео", "нео отмена", "отмена нео",
        "нео отмени", "нео остановись", "нео прекрати",
        "отмена диалога", "остановить диалог",
    }:
        for session in {request.session_id, "default", "wake-word"}:
            context_manager.clear_context(session)
            context_manager.clear_pending_conflict(session)
        from neo.voice.tts import stop_speech

        stop_speech()
        return TextResponse(action="cancel", response_text="Диалог остановлен.")

    pending = context_manager.get_pending_conflict(request.session_id)
    if pending:
        folded = text.casefold().replace("ё", "е")
        previous_time = datetime.fromisoformat(pending["intent"].datetime_iso.replace("Z", "+00:00"))
        replacement_date = _date_only_reply(text, previous_time)
        if replacement_date is not None:
            replacement = previous_time.replace(
                year=replacement_date.year,
                month=replacement_date.month,
                day=replacement_date.day,
            )
            updated_intent = pending["intent"].model_copy(update={"datetime_iso": replacement.isoformat()})
            conflicts = EventService.find_conflicts(replacement)
            if conflicts:
                context_manager.save_pending_conflict(
                    request.session_id, updated_intent, conflicts,
                    event_type=pending["event_type"],
                )
                context_manager.save_intent(updated_intent, request.session_id)
                return _conflict_response(updated_intent, conflicts)
            context_manager.clear_pending_conflict(request.session_id)
            context_manager.clear_context(request.session_id)
            return _save_intent_event(updated_intent, pending["event_type"])

        moves = bool(re.search(r"\b(нет|перенес|перенести|другое время|не надо)\b", folded))
        if moves:
            context_manager.save_pending_conflict(
                request.session_id, pending["intent"], pending["conflicts"],
                awaiting_time=True, event_type=pending["event_type"],
            )
            pending = context_manager.get_pending_conflict(request.session_id)
            includes_clock = bool(re.search(
                r"\b\d{1,2}(?:[:.]\d{2})\b|\b\d{1,2}\s*(?:час(?:а|ов)?|утра|дня|вечера|ночи)\b",
                folded,
            ))
            if not includes_clock:
                return TextResponse(
                    action="conflict",
                    response_text="Хорошо, перенесём. На какую дату и время поставить событие?",
                )
        if pending["awaiting_time"]:
            try:
                intent = _replacement_time_intent(text, pending, request.session_id)
            except (ConnectionError, ResponseError) as exc:
                logger.warning("Ollama failed while processing a replacement time: %s", exc)
                raise AssistantUnavailableError("Ollama request failed") from exc
            except Exception:
                logger.exception("Assistant orchestration failed while processing a replacement time")
                raise
            if intent.datetime_iso:
                intent.action = pending["intent"].action
                intent.content = pending["intent"].content
                conflicts = EventService.find_conflicts(datetime.fromisoformat(intent.datetime_iso.replace("Z", "+00:00")))
                if conflicts:
                    context_manager.save_pending_conflict(request.session_id, intent, conflicts, event_type=pending["event_type"])
                    context_manager.save_intent(intent, request.session_id)
                    return _conflict_response(intent, conflicts)
                context_manager.clear_pending_conflict(request.session_id)
                return _save_intent_event(intent, pending["event_type"])
            return TextResponse(action="clarify", response_text="Не расслышал дату и время. Например: «завтра в 18:00».")
        accepts = bool(
            re.search(
                r"\b(?:да|ага|конечно|давай|записывай|запиши|оставь|оставить|"
                r"оставим|оставляем)\b",
                folded,
            )
            or re.search(r"\b(?:на|в)\s+(?:то|это)\s+же\s+время\b", folded)
            or re.search(r"\bкак\s+есть\b", folded)
        )
        if accepts:
            raise_if_cancelled(cancellation_event)
            context_manager.clear_pending_conflict(request.session_id)
            context_manager.clear_context(request.session_id)
            return _save_intent_event(pending["intent"], pending["event_type"])
        context_manager.clear_pending_conflict(request.session_id)
    try:
        intent = _get_intent(text, request.session_id, cancellation_event)
    except (ConnectionError, ResponseError) as exc:
        logger.warning("Ollama failed while processing a request: %s", exc)
        raise AssistantUnavailableError("Ollama request failed") from exc
    except Exception:
        logger.exception("Assistant orchestration failed while processing a request")
        raise

    raise_if_cancelled(cancellation_event)

    if intent.action == "clarify":
        return TextResponse(
            action="clarify",
            response_text=intent.clarify_question
            or "Уточните, пожалуйста, что нужно сделать и на какое время.",
        )

    if intent.action in {"create_task", "create_meeting"}:
        if not intent.content or not intent.datetime_iso:
            return TextResponse(
                action="clarify",
                response_text="Уточните, пожалуйста, содержание события и время.",
            )

        event_type = "meeting" if intent.action == "create_meeting" else "task"
        if event_type == "task" and re.search(r"\bнапомн", text.casefold()):
            event_type = "reminder"
        when = datetime.fromisoformat(intent.datetime_iso.replace("Z", "+00:00"))
        conflicts = EventService.find_conflicts(when)
        if conflicts:
            raise_if_cancelled(cancellation_event)
            context_manager.save_pending_conflict(request.session_id, intent, conflicts, event_type=event_type)
            context_manager.save_intent(intent, request.session_id)
            return _conflict_response(intent, conflicts)
        raise_if_cancelled(cancellation_event)
        return _save_intent_event(intent, event_type)

    if intent.action == "list_events":
        now = datetime.now().astimezone()
        if intent.datetime_iso:
            requested_date = datetime.fromisoformat(
                intent.datetime_iso.replace("Z", "+00:00")
            )
        else:
            requested_date = now
        if requested_date.tzinfo is None:
            requested_date = requested_date.replace(tzinfo=now.tzinfo)
        start = datetime.combine(requested_date.date(), time.min, tzinfo=requested_date.tzinfo)
        normalized_text = text.casefold().replace("ё", "е")
        if re.search(r"\b(?:год\w*|лет)\b", normalized_text):
            period_days = 365
        elif re.search(r"\bмесяц\w*\b", normalized_text):
            period_days = None
            end_date = _add_months(start.date(), _period_count(normalized_text, "месяц"))
            end = datetime.combine(end_date, time.min, tzinfo=start.tzinfo)
        elif re.search(r"\bнедел\w*\b", normalized_text):
            period_days = 7 * _period_count(normalized_text, "недел")
        else:
            period_days = 1
        if period_days is not None:
            end = start + timedelta(days=period_days)
        events = EventService.list_events(start=start, end=end)
        if not events:
            return TextResponse(
                action="list_events",
                response_text="На этот период событий нет.",
            )
        event_lines = [
            f"• {_spoken_datetime(event['datetime_iso'])} — {event['content']}"
            for event in events
        ]
        return TextResponse(
            action="list_events",
            response_text="Вот события на этот период:\n" + "\n".join(event_lines),
            events=events,
        )

    if intent.action == "search_events":
        query = intent.search_query or text
        search_start = (
            datetime.fromisoformat(intent.datetime_iso.replace("Z", "+00:00"))
            if intent.datetime_iso
            else None
        )
        events = EventService.search(query, limit=None, start=search_start)
        if not events:
            return TextResponse(
                action="search_events",
                response_text=f"Не нашёл записей по запросу «{query}».",
            )
        event_lines = [
            f"• {event['content']} ({_spoken_datetime(event['datetime_iso'])})"
            for event in events
        ]
        return TextResponse(
            action="search_events",
            response_text=f"Нашёл записей: {len(events)}.\n" + "\n".join(event_lines),
            events=events,
        )

    logger.warning("Unsupported assistant action: %s", intent.action)
    return TextResponse(action="clarify", response_text="Не удалось выполнить команду.")


def _get_intent(text: str, session_id: str, cancellation_event=None):
    from neo.ai.orchestrator import process
    return process(text, session_id=session_id, cancellation_event=cancellation_event)


def _replacement_time_intent(text: str, pending: dict, session_id: str):
    """Разбирает новую дату/время для события, которое переносится."""
    from neo.ai.orchestrator import parse_explicit_create_command

    prior = pending["intent"]
    prefix = "напомни" if pending["event_type"] == "reminder" else "запланируй встречу"
    candidate = parse_explicit_create_command(
        f"{prefix} {prior.content} {text}", datetime.now().astimezone()
    )
    if candidate and candidate.datetime_iso:
        candidate.action = prior.action
        candidate.content = prior.content
        return candidate

    # Ответ, содержащий только время, сохраняет исходную календарную дату.
    time_match = re.search(
        r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?\s*(?:час(?:а|ов)?\s*"
        r"(?:утра|дня|вечера|ночи)?)?\b",
        text.casefold(),
    )
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2) or 0)
        if hour <= 23 and minute <= 59:
            old = datetime.fromisoformat(prior.datetime_iso.replace("Z", "+00:00"))
            replacement = old.replace(hour=hour, minute=minute, second=0, microsecond=0)
            from neo.ai.schemas import Intent
            return Intent(action=prior.action, content=prior.content, datetime_iso=replacement.isoformat())

    return _get_intent(text, session_id)


def _conflict_response(intent, conflicts: list[dict]) -> TextResponse:
    first = conflicts[0]
    if intent.action == "create_meeting":
        title = re.sub(r"^(?:встреч\w*|созвон\w*|переговор\w*)\s*", "", intent.content or "", flags=re.IGNORECASE).strip()
        new_event = f"встречу {title}" if title.startswith(("с ", "по ", "о ", "об ")) else (
            f"встречу «{intent.content}»" if title else "ещё одну встречу"
        )
    elif intent.action == "create_task":
        new_event = f"задачу «{intent.content}»"
    else:
        new_event = f"напоминание «{intent.content}»"
    return TextResponse(
        action="conflict",
        response_text=(
            f"У вас на {_spoken_datetime(first['datetime_iso'])} уже есть запись "
            f"«{first['content']}». Хотите записать {new_event} на это же время "
            "или подобрать для неё другое время?"
        ),
        events=conflicts,
    )


def _save_intent_event(intent, event_type: str) -> TextResponse:
    event = EventService.create(EventCreate(
        event_type=event_type,
        content=intent.content,
        datetime_iso=datetime.fromisoformat(intent.datetime_iso.replace("Z", "+00:00")),
    ))
    label, verb = {
        "task": ("задача", "сохранена"),
        "meeting": ("встреча", "сохранена"),
        "reminder": ("напоминание", "сохранено"),
    }[event_type]
    description = event["content"]
    if event_type == "meeting":
        description = re.sub(r"^(?:встреча|созвон|переговоры)\s+", "", description, flags=re.IGNORECASE)
        description = description or event["content"]
    return TextResponse(
        action=intent.action,
        response_text=f"Готово, {label} «{description}» {verb} на {_spoken_datetime(event['datetime_iso'])}.",
        event=event,
    )
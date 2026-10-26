# ai/datetime_utils.py
from datetime import datetime, timedelta
from .schemas import Intent

def postprocess_datetime(intent: Intent, now: datetime) -> Intent:
    """
    Валидирует и страхует даты, возвращенные моделью.
    Если дата парсится нормально, но она в прошлом — сдвигает на будущее.
    """
    if intent.action not in ["create_task", "create_meeting", "list_events"]:
        return intent

    if not intent.datetime_iso:
        # Если для создания события модель не вернула дату — это интент уточнения
        if intent.action in ["create_task", "create_meeting"]:
            intent.action = "clarify"
            intent.clarify_question = intent.clarify_question or "На какое время запланировать?"
        return intent

    try:
        # Пытаемся распарсить то, что вернула модель
        dt = datetime.fromisoformat(intent.datetime_iso.replace("Z", "+00:00"))
    except ValueError:
        # Если модель вернула невалидную строку вместо ISO — уходим в clarify
        intent.action = "clarify"
        intent.clarify_question = "Не удалось распознать дату и время. Повторите, пожалуйста."
        intent.datetime_iso = None
        return intent

    # Model output can omit a UTC offset; interpret that as local time.
    if dt.tzinfo is None and now.tzinfo is not None:
        dt = dt.replace(tzinfo=now.tzinfo)

    # Страховка: если дата/время ошибочно улетели в прошлое (например, часы меньше текущих),
    # а пользователь очевидно создает задачу на будущее — двигаем вперед
    if intent.action in ["create_task", "create_meeting"] and dt < now:
        # Если дата совпадает с сегодняшней, но время упущено — переносим на завтра
        if dt.date() == now.date():
            dt = dt + timedelta(days=1)
        # Если это просто прошедший день недели — сдвигаем на неделю вперед
        elif dt < now:
            dt = dt + timedelta(days=7)

    intent.datetime_iso = dt.isoformat()
    return intent

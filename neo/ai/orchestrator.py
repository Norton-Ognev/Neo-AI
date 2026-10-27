from __future__ import annotations
import os
import re
import json
import calendar
from datetime import datetime, timedelta, time as datetime_time
from ollama import chat
from .schemas import Intent
from .datetime_utils import postprocess_datetime
from . import context_manager

_HOUR_WORDS = {
    "один": 1, "одна": 1, "одно": 1, "две": 2, "два": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12,
}
_MONTH_NUMBERS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_WORDS_PATTERN = "|".join(_MONTH_NUMBERS)

def _clean_created_title(title: str | None) -> str | None:
    if not title:
        return title
    title = re.sub(
        r"^\s*(?:(?:поставь|поставить|запланируй|запланировать|создай|создать|"
        r"добавь|добавить|запиши|записать)\s+)?(?:мне\s+)?задач\w*\s+(?:на\s+)?",
        "",
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"^\s*(?:(?:к|для|по)\s+)?мероприят(?:ие|ия|ии|ию|ием|иях)\s*"
        r"(?:(?:на|по\s+поводу|о|об)\s*)?",
        "",
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"[,;:]+", " ", title)
    for _ in range(2):
        title = re.sub(r"^\s*[,;:—-]+\s*", "", title)
        title = re.sub(
            r"^\s*(?:о\s+том,?\s+что|что|о|об|про)\s+",
            "",
            title,
            count=1,
            flags=re.IGNORECASE,
        )
    title = re.sub(
        r"\bсоревнован\w*\s+(?=турнир\b)",
        "",
        title,
        count=1,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"^собрания(?=\s+совета\b)", "собрание", title, count=1, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.!?—-\t")
    return title or None

def _normalize_hour_words(text: str) -> str:
    for word, number in _HOUR_WORDS.items():
        text = re.sub(
            rf"\b((?:в|на|к)\s+){word}\b",
            lambda match, n=number: f"{match.group(1)}{n}",
            text,
        )
        text = re.sub(rf"\b{word}(?=\s+час(?:а|ов)?\b)", str(number), text)

    for word, number in _HOUR_WORDS.items():
        text = re.sub(
            rf"\b{word}(?=\s+(?:час(?:а|ов)?\s+)?(?:утра|утром|вечера|вечером|ночи|ночью)\b)",
            str(number),
            text,
        )

    def normalize_compact_clock(match: re.Match[str]) -> str:
        digits = match.group("clock")
        split_at = len(digits) - 2
        hour, minute = int(digits[:split_at]), int(digits[split_at:])
        if hour > 23 or minute > 59:
            return match.group(0)
        return f"{match.group('prefix')}{hour}:{minute:02d}"

    text = re.sub(
        r"\b(?P<prefix>(?:в|на|к)\s+)(?P<clock>\d{3,4})\b"
        r"(?!\s*(?:год\w*|числ\w*|дн\w*|месяц\w*))",
        normalize_compact_clock,
        text,
        flags=re.IGNORECASE,
    )
    return text

def build_system_prompt(now: datetime) -> str:
    """Загружает шаблон промпта и подставляет текущий контекст времени."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(current_dir, "prompts", "system_prompt.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_template = f.read()

    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    day_name = days[now.weekday()]
    now_iso = now.isoformat()
    context_str = f"{now_iso} ({day_name}, текущий год {now.year})"
    return prompt_template.replace("{{ now_iso }}", context_str)

def parse_short_time(
    text: str, now: datetime, context_datetime_iso: str | None = None
) -> str | None:
    """
    Эвристический парсер коротких фраз вроде 'в 15:00', 'в 17 часов', 'в 9 утра'.
    Позволяет обойти капризы LLM на коротких ответах.
    """
    text_clean = _normalize_hour_words(text.casefold().replace("ё", "е").strip())

    # Ищем шаблоны времени: '15:00', '15-00', '15.00' или просто цифры 'в 15'
    match = re.search(r'(?:в\s+)?(\d{1,2})[\s:.-]*(\d{2})?', text_clean)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2)) if match.group(2) else 0

        if hours < 12 and re.search(r"\b(?:вечера|вечером|дня|днем)\b", text_clean):
            hours += 12
        elif hours == 12 and re.search(r"\b(?:утра|ночью)\b", text_clean):
            hours = 0

        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            if context_datetime_iso:
                target_dt = datetime.fromisoformat(context_datetime_iso.replace("Z", "+00:00"))
            else:
                # Если в контексте не сохранилась дата, сохраняем старое значение по умолчанию.
                target_dt = now + timedelta(days=1)
            if "сегодня" in text_clean:
                target_dt = now
            return datetime.combine(
                target_dt.date(),
                datetime_time(hours, minutes),
                tzinfo=target_dt.tzinfo or now.tzinfo,
            ).isoformat()
    return None

def has_explicit_clock_time(text: str) -> bool:
    """Возвращайте true только, если пользователь действительно предоставил пригодное время суток."""
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    if re.search(r"\b(?:полдень|полночь)\b", normalized):
        return True
    for match in re.finditer(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)", normalized):
        if int(match.group(1)) <= 23 and int(match.group(2)) <= 59:
            return True
    for match in re.finditer(r"\b(в|на|к)\s*(\d{1,2})\b", normalized):
        prefix, hour_text = match.group(1), match.group(2)
        hour = int(hour_text)
        following = normalized[match.end():]
        if hour > 23 or re.match(
            r"\s*(?:[-–]\s*(?:е|го)|числ\w*|январ\w*|феврал\w*|март\w*|"
            r"апрел\w*|ма\w*|июн\w*|июл\w*|август\w*|сентябр\w*|"
            r"октябр\w*|ноябр\w*|декабр\w*|недел\w*"
            + (r"|дн\w*" if prefix == "на" else "")
            + r")",
            following,
        ):
            continue
        return True
    if re.search(
        r"\b(?:через\s+\d+\s+час|в\s+(?:ноль|один|одна|одно|два|две|три|"
        r"четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|"
        r"двенадцать|тринадцать|четырнадцать|пятнадцать|шестнадцать|"
        r"семнадцать|восемнадцать|девятнадцать|двадцать)(?:\s+час(?:а|ов)?)?\b)",
        normalized,
    ):
        return True
    if re.search(r"\b\d{1,2}\s+час(?:а|ов)?\b", normalized):
        return True
    # Короткий ответ без предлога: «10 вечера», «6 утра», «10 часов вечера».
    for match in re.finditer(
        r"(?<![\d:.])(\d{1,2})(?:\s+час(?:а|ов)?)?\s+(утра|утром|вечера|вечером|ночи|ночью|дня|днем)\b",
        normalized,
    ):
        if not 1 <= int(match.group(1)) <= 12:
            continue
        before = normalized[:match.start()].rstrip()
        if match.group(2) in {"дня", "днем"} and re.search(r"(?:через|на|за|еще|уже|только)$", before):
            continue  # «через 2 дня», «на 2 дня» — это срок, а не время суток
        return True
    return False

def ambiguous_clock_hour(text: str) -> int | None:
    # Ищет значение в диапазоне от 1 до 12 часов, не сопровождающееся уточнением времени суток (утро/день/вечер/ночь).
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    if re.search(r"\b(?:утра|утром|дня|днем|вечера|вечером|ночи|ночью)\b", normalized):
        return None
    if re.search(r"(?<!\d)\d{1,2}[:.]\d{2}(?!\d)", normalized):
        return None

    number_words = {
        "один": 1, "одна": 1, "одно": 1, "две": 2, "два": 2,
        "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
        "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
        "двенадцать": 12,
    }
    match = re.search(r"\b(?:в|на|к)\s*(\d{1,2})\b", normalized)
    if match:
        hour = int(match.group(1))
        following = normalized[match.end():]
        if re.match(
            r"\s*(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма\w*|июн\w*|"
            r"июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|"
            r"дн\w*|недел\w*)",
            following,
        ):
            return None
        if 1 <= hour <= 12:
            return hour

    word_pattern = "|".join(sorted(number_words, key=len, reverse=True))
    word_match = re.search(rf"\b(?:в|на|к)\s+({word_pattern})\b", normalized)
    if word_match:
        return number_words[word_match.group(1)]
    bare_match = re.search(r"\b(\d{1,2})\s+час(?:а|ов)?\b", normalized)
    if bare_match and 1 <= int(bare_match.group(1)) <= 12:
        return int(bare_match.group(1))
    return None

def explicit_date_was_given(text: str) -> bool:
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    return bool(re.search(
        r"\b(?:сегодня|завтра|послезавтра|через\s+\w+\s+дн\w*|"
        r"следующ\w*\s+недел\w*|\d{1,2}(?:-?(?:го|е))?\s+(?:января|февраля|марта|апреля|мая|"
        r"июня|июля|августа|сентября|октября|ноября|декабря)|"
        r"\d{1,2}(?:-?(?:го|е))?\s+числ\w*)\b",
        normalized,
    ))

def _date_for_day_of_month(day: int, now: datetime) -> datetime.date | None:
    if not 1 <= day <= 31:
        return None
    year, month = now.year, now.month
    for _ in range(12):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = datetime(year, month, day).date()
            if candidate >= now.date():
                return candidate
        month += 1
        if month == 13:
            year += 1
            month = 1
    return None

def meridiem_from_reply(text: str) -> str | None:
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    if re.search(r"\b(?:утра|утром|утро|утренн\w*|до\s+обеда|спозаранку)\b", normalized):
        return "morning"
    if re.search(
        r"\b(?:вечера|вечером|вечер|вечерн\w*|"
        r"после\s+обеда|после\s+полудня)\b",
        normalized,
    ):
        return "afternoon"
    if re.search(r"\b(?:дня|днем|дневн\w*|пополудни|в\s+обед)\b", normalized):
        return "afternoon"
    if re.search(r"\b(?:ночи|ночью|ночь|ночн\w*|за\s+полночь)\b", normalized):
        return "night"
    return None

def resolve_ambiguous_hour(hour: int, meridiem: str) -> int:
    if meridiem == "morning":
        return 0 if hour == 12 else hour
    if meridiem == "afternoon":
        return 12 if hour == 12 else (hour + 12 if hour < 12 else hour)
    return 0 if hour == 12 else hour

def parse_explicit_reminder_command(text: str, now: datetime) -> Intent | None:
    """Отделяет текст напоминания от запланированных даты и времени."""
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    reminder_verb = re.search(r"\b(?:напомин\w*|напомн\w*)\b", normalized)
    if not reminder_verb:
        return None

    day_pattern = (
        r"после\s+завтра|послезавтра|послезавтрашн\w*|"
        r"завтра|завтрашн\w*|сегодня"
    )
    day_matches = list(re.finditer(rf"\b({day_pattern})\b", normalized))
    numbered_date_matches = list(re.finditer(
        r"\b(\d{1,2})(?:-?(?:го|е))?\s+числ\w*\b", normalized
    ))
    named_date_matches = list(re.finditer(
        rf"\b(\d{{1,2}})(?:-?(?:го|е))?\s+({_MONTH_WORDS_PATTERN})(?:\s+(\d{{4}}))?\b",
        normalized,
    ))
    quoted_body_match = re.search(
        r"«([^»]+)»|“([^”]+)”|\"([^\"]+)\"|'([^']+)'", text
    )
    quoted_body = next(
        (group for group in quoted_body_match.groups() if group is not None), None
    ) if quoted_body_match else None
    body_intro = re.search(
        r"\b(о\s+том,?\s+что|что|о|об|от|про)\b",
        normalized[reminder_verb.end():],
    )
    body_intro_start = (
        reminder_verb.end() + body_intro.start() if body_intro else len(normalized)
    )
    if quoted_body_match:
        quote_start = quoted_body_match.start()
        quote_end = quoted_body_match.end()
        schedule_day = next((match for match in reversed(day_matches)
                             if not quote_start <= match.start() < quote_end), None)
        numbered_date = next((match for match in reversed(numbered_date_matches)
                              if not quote_start <= match.start() < quote_end), None)
        named_date = next((match for match in reversed(named_date_matches)
                           if not quote_start <= match.start() < quote_end), None)
    else:
        schedule_day = next(
            (match for match in day_matches
             if match.start() < reminder_verb.start()
             or reminder_verb.end() <= match.start() < body_intro_start),
            None,
        )
        numbered_date = next(
            (match for match in numbered_date_matches
             if match.start() < reminder_verb.start()
             or reminder_verb.end() <= match.start() < body_intro_start),
            None,
        )
        named_date = next(
            (match for match in named_date_matches
             if match.start() < reminder_verb.start()
             or reminder_verb.end() <= match.start() < body_intro_start),
            None,
        )
        if schedule_day is None and body_intro and body_intro.group(1) not in {"что", "о том, что"}:
            schedule_day = day_matches[0] if day_matches else None
        # Система автоматического распознавания речи (ASR) часто вставляет лишнюю фразу «о том, что» перед указанием дня.
        # Если других дат для напоминания не обнаружено,
        # следует принять явно указанную календарную дату (где бы она ни встретилась в высказывании) и удалить её из текста напоминания.
        if schedule_day is None:
            numbered_date = numbered_date or (numbered_date_matches[0] if numbered_date_matches else None)
            named_date = named_date or (named_date_matches[0] if named_date_matches else None)

    target_date = None
    if named_date:
        day = int(named_date.group(1))
        month = _MONTH_NUMBERS[named_date.group(2)]
        specified_year = named_date.group(3)
        year = int(specified_year) if specified_year else now.year
        try:
            target_date = datetime(year, month, day).date()
            if not specified_year and target_date < now.date():
                target_date = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    elif numbered_date:
        target_date = _date_for_day_of_month(int(numbered_date.group(1)), now)
        if target_date is None:
            return None
    elif schedule_day:
        day_phrase = schedule_day.group(1)
        if day_phrase == "сегодня":
            offset = 0
        elif day_phrase.startswith("после") or day_phrase.startswith("послезавтра"):
            offset = 2
        else:
            offset = 1
        target_date = now.date() + timedelta(days=offset)

    time_match = re.search(
        r"\b(?:в|на|к)\s*(\d{1,2})(?:[:.](\d{2}))?"
        r"(?:\s*час(?:а|ов)?)?(?:\s+(утра|дня|вечера|ночи))?\b|"
        r"\b(\d{1,2})\s+час(?:а|ов)?(?:\s+(утра|дня|вечера|ночи))?\b",
        normalized,
    )
    hours = minutes = None
    if time_match:
        hours = int(time_match.group(1) or time_match.group(4))
        minutes = int(time_match.group(2) or 0)
        meridiem = time_match.group(3) or time_match.group(5)
        if meridiem == "дня" and time_match.group(0).startswith("на"):
            meridiem = None  # «на 2 дня» — срок, а не время суток
        if meridiem in {"вечера", "дня"} and hours < 12:
            hours += 12
        elif meridiem in {"утра", "ночи"} and hours == 12:
            hours = 0
        if hours > 23 or minutes > 59:
            return None

    body = quoted_body if quoted_body is not None else text[reminder_verb.end():]
    if quoted_body is None:
        selected_date_match = named_date or numbered_date
        if selected_date_match and selected_date_match.start() >= reminder_verb.end():
            body_start = selected_date_match.start() - reminder_verb.end()
            body_end = selected_date_match.end() - reminder_verb.end()
            raw_body = text[reminder_verb.end():]
            body = raw_body[:body_start] + " " + raw_body[body_end:]
    body = re.sub(r"^\s*(?:мне|пожалуйста|[,!:—-])+\s*", "", body, flags=re.IGNORECASE)
    body = re.sub(
        r"^\s*(?:о\s+том,?\s+что|что|о|об|от|про)\s*[,!:—-]*\s*",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        rf"\b(?:{day_pattern})\b",
        " ",
        body,
        count=1 if schedule_day and schedule_day.start() >= reminder_verb.start() else 0,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"^\s*(?:о\s+том,?\s+что|что|о|об|от|про)\s*[,!:—-]*\s*",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    )
    body = re.sub(
        r"\b(?:в|на|к)\s*\d{1,2}(?:[:.]\d{2})?"
        r"(?:\s*час(?:а|ов)?)?(?:\s+(?:утра|дня|вечера|ночи))?\b|"
        r"\b\d{1,2}\s+час(?:а|ов)?(?:\s+(?:утра|дня|вечера|ночи))?\b",
        " ",
        body,
        flags=re.IGNORECASE,
    )
    # Фрагменты, относящиеся к расписанию, могут предшествовать маркеру контента (например, «в 15:30 о том, что...»).
    # Удаляет маркер и сопутствующие ему слова только после того, как будут убраны токены, обозначающие дату или время.
    body = re.sub(r"\b(?:у меня|мне)\s+(?:будет|есть)\b", " ", body, flags=re.IGNORECASE)
    for _ in range(3):
        body = re.sub(
            r"^\s*(?:мне|меня|пожалуйста|[,!:—-])+\s*",
            "",
            body,
            count=1,
            flags=re.IGNORECASE,
        )
        body = re.sub(
            r"^\s*(?:о\s+том,?\s+что|что|о|об|от|про)\s*[,!:—-]*\s*",
            "",
            body,
            count=1,
            flags=re.IGNORECASE,
        )
        body = re.sub(
            r"^\s*(?:(?:мне|меня)\s+)?(?:нужно|надо)\s+(?:будет\s+)?",
            "",
            body,
            count=1,
            flags=re.IGNORECASE,
        )
    body = re.sub(r"\bпосле\s+завтра\b", "послезавтра", body, flags=re.IGNORECASE)
    body = re.sub(
        r"\bзавтрашн(?:ей|юю|яя|ее)\s+встреч(?:и|у|е|а)\b",
        "встреча",
        body,
        flags=re.IGNORECASE,
    )
    body = re.sub(r"\b(?:встречи|встрече|встречу)\b", "встреча", body, flags=re.IGNORECASE)
    body = re.sub(r"\s+", " ", body).strip(" ,.!?—-\t")
    if not body:
        return None

    datetime_iso = None
    if target_date is not None or hours is not None:
        scheduled_date = target_date or now.date()
        scheduled_time = datetime_time(hours or 0, minutes or 0)
        datetime_iso = datetime.combine(
            scheduled_date, scheduled_time, tzinfo=now.tzinfo
        ).isoformat()

    if target_date is None:
        question = f"На какую дату поставить напоминание о том, что {body}?"
        if hours is not None:
            question = f"На какую дату поставить напоминание о том, что {body}? Время — {hours:02d}:{minutes:02d}."
    elif hours is None:
        date_word = (
            "сегодня" if target_date == now.date()
            else "завтра" if target_date == now.date() + timedelta(days=1)
            else "послезавтра"
        )
        question = f"Во сколько {date_word} поставить напоминание?"
    else:
        return Intent(action="create_task", content=body, datetime_iso=datetime_iso)

    return Intent(
        action="clarify",
        content=body,
        datetime_iso=datetime_iso,
        clarify_question=question,
    )

def looks_like_time_only_reply(text: str) -> bool:
    # Выявляет ответ, содержащий указание времени и слова-заполнители, но не включающий описание события.
    normalized = text.casefold().replace("ё", "е")
    if not has_explicit_clock_time(normalized):
        return False

    remainder = re.sub(r"\b\d{1,2}(?:[:.,]\d{2})?\b", " ", normalized)
    remainder = re.sub(
        r"\b(?:запланирую|запланируй|запланировать|запиши|записать|поставлю|"
        r"поставь|назначу|назначить|назначь|пожалуйста|час|часа|часов|"
        r"утра|утром|дня|днем|вечера|вечером|ночи|примерно|около|где-то|"
        r"ровно|в|на|к|и|мне)\b",
        " ",
        remainder,
    )
    remainder = re.sub(
        r"\b(?:ноль|один|одна|одно|два|две|три|четыре|пять|шесть|семь|"
        r"восемь|девять|десять|одиннадцать|двенадцать|тринадцать|"
        r"четырнадцать|пятнадцать|шестнадцать|семнадцать|восемнадцать|"
        r"девятнадцать|двадцать)\b",
        " ",
        remainder,
    )
    remainder = re.sub(r"[\W_]+", " ", remainder, flags=re.UNICODE).strip()
    return not remainder

def parse_explicit_create_command(text: str, now: datetime) -> Intent | None:
    """Безопасно восстанавливает явную команду создания, когда модель запрашивает её без необходимости."""
    text = re.sub(
        r"^\s*на\s+какую\s+дату\s+и\s+время\s+записать\s+.+?\b(надо|нужно|хочу)\b",
        r"\1",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:со\s+звон|с\s+озвон|с\s+азвон)\b", "созвон", text, flags=re.IGNORECASE)
    normalized = _normalize_hour_words(text.casefold().replace("ё", "е"))
    relative_day_pattern = (
        r"(?:послезавтра|завтра|сегодня|"
        r"(?:(?:на|в)\s+)?следующ(?:ий|его|им)\s+д(?:е|ё)н[ьяе]?|"
        r"(?:на|в)\s+другой\s+день|"
        r"через\s+(?:сутки|день|один\s+день|одни\s+сутки|пару\s+дней|"
        r"два\s+дня|двое\s+суток|три\s+дня|четыре\s+дня|\d+\s+дн(?:я|ей)|"
        r"на\s+день\s+позже|дн(?:е|ё)м\s+позже)|"
        rf"\d{{1,2}}(?:-?(?:го|е))?\s+(?:{_MONTH_WORDS_PATTERN})(?:\s+\d{{4}})?|"
        r"\d{1,2}(?:-?(?:го|е))?\s+числ\w*)"
    )
    day_matches = list(re.finditer(
        rf"\b({relative_day_pattern})\b",
        normalized,
    ))
    # Если говорящий исправляет сам себя («в 15... нет, на 16»), последнее
    # явно указанное время считается намеренным.
    time_matches = list(re.finditer(
        r"\b(?:(?:в|на)\s*(\d{1,2})(?:[:.](\d{2}))?"
        r"(?:\s*час(?:а|ов)?(?:\s+(?:утра|дня|вечера|ночи))?)?"
        r"|(\d{1,2})\s+час(?:а|ов)?"
        r"(?:\s+(?:утра|дня|вечера|ночи))?)\b",
        normalized,
    ))
    time_matches = [
        match for match in time_matches
        if not re.match(
            r"\s*(?:[-–]\s*(?:е|го)\s*)?(?:числ\w*|месяц\w*|дн\w*|январ\w*|феврал\w*|март\w*|"
            r"апрел\w*|ма\w*|июн\w*|июл\w*|август\w*|сентябр\w*|"
            r"октябр\w*|ноябр\w*|декабр\w*)",
            normalized[match.end():],
        )
    ]
    # Несколько вариантов — это не автоматические исправления. Пусть модель уточняет,
    # если говорящий не указал, какая дата/время заменяет другую.
    has_correction = bool(re.search(
        r"\b(?:нет|лучше|давай|перенеси|вместо|точнее|поправка|"
        r"а\s+не|исправ(?:ь|ляю|ление))\b|\bс\s+\d{1,2}\s+на\s+\d{1,2}\b",
        normalized,
    ))
    if (len(time_matches) > 1 or len(day_matches) > 1) and not has_correction:
        return None

    time_match = time_matches[-1] if time_matches else None
    invalid_clock = None
    if time_match:
        hours = int(time_match.group(1) or time_match.group(3))
        minutes = int(time_match.group(2) or 0)
        if hours < 12 and re.search(r"\b(?:вечера|вечером|дня|днем)\b", normalized):
            hours += 12
        elif hours == 12 and re.search(r"\b(?:утра|ночью)\b", normalized):
            hours = 0
        if hours > 23 or minutes > 59:
            invalid_clock = f"{hours:02d}:{minutes:02d}"
            time_match = None

    if re.search(r"\b(напомни|напомнить|напоминание|задач\w*)\b", normalized):
        action = "create_task"
        command_prefix = re.compile(
            r"^\s*(?:напомни(?:ть)?(?:\s+мне)?|создай\s+(?:мне\s+)?задачу|"
            r"добавь\s+задачу|постав(?:ь|ить)\s+задачу|"
            r"запланируй(?:\s+мне)?\s+задачу|создать\s+задачу|"
            r"напоминание(?:\s+мне)?|задач\w*)\s*[,!:—-]*\s*",
            re.IGNORECASE,
        )
    elif re.search(r"\b(встреч\w*|мероприяти\w*|созвон\w*|переговор\w*|прогулк\w*|"
                   r"обед\w*|ужин\w*|завтрак\w*|свидан\w*)\b", normalized):
        action = "create_meeting"
        command_prefix = re.compile(
            r"^\s*(?:(?:надо|нужно|хочу)\s+)?"
            r"(?:(?:на\s+)?(?:встреч\w*|мероприяти\w*|созвон\w*|переговор\w*)\s+)?"
            r"(?:сделай|сделать|постав(?:ь|ить)|добавь|создай|запланируй|запиши|записать|назначь)?\s*"
            r"(?:мне\s+)?(?:встреч\w*|мероприяти\w*|созвон\w*|переговор\w*)?\s*[,!:—-]*\s*",
            re.IGNORECASE,
        )
    else:
        return None

    # Сначала убираем приветствие/обращение (оно может заканчивать предложение),
    # затем используем первоначальный запрос для названия события. Последующие
    # предложения часто содержат только исправления и могут содержать шум ASR.
    content_source = re.sub(
        r"^\s*(?:привет\w*|здравствуй\w*)\b[\s,!?.—-]*"
        r"(?:(?:неос?|нева|нео|нелл?)\b[\s,!?.—-]*)?",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    content_source = re.sub(
        r"^\s*(?:неос?|нева|нео|нелл?)\b[\s,!?.—-]*", "", content_source, count=1, flags=re.IGNORECASE
    )
    content_source = re.split(r"[.!?](?:\s|$)", content_source, maxsplit=1)[0]
    content_source = re.sub(
        r"^\s*(?:(?:можешь(?:\s+ли)?|можете(?:\s+ли)?|могли\s+бы\s+вы|"
        r"не\s+мог(?:ла|ли)?\s+бы(?:\s+ты|\s+вы)?|пожалуйста|мне|"
        r"запиши|записать|запланируй|создай|добавь|поставь|назначь|"
        r"напомни(?:ть)?)\b\s*[,!:—-]*\s*)+",
        "",
        content_source,
        count=1,
        flags=re.IGNORECASE,
    )
    content = command_prefix.sub("", content_source, count=1)
    content = re.sub(
        rf"\b{relative_day_pattern}\b",
        " ",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(
        r"\b(?:в|на)\s*\d{1,2}(?:[:.]\d{2})?"
        r"(?:\s*час(?:а|ов)?(?:\s+(?:утра|дня|вечера|ночи))?)?\b",
        " ",
        content,
        flags=re.IGNORECASE,
    )
    hour_words = "|".join(sorted(_HOUR_WORDS, key=len, reverse=True))
    content = re.sub(
        rf"\b(?:в|на|к)\s+(?:{hour_words})(?:\s+час(?:а|ов)?)?\b|"
        rf"\b(?:{hour_words})\s+час(?:а|ов)?\b",
        " ",
        content,
        flags=re.IGNORECASE,
    )
    if action == "create_meeting":
        content = re.sub(r"^\s*встреч\w*\s+", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\b(?:утра|дня|вечера|ночи|давай|пожалуйста)\b", " ", content, flags=re.IGNORECASE)
    content = re.sub(r"\s+\b(?:на|в|к|около|до|после)\b\s*$", "", content, flags=re.IGNORECASE)
    content = re.sub(r"^\s*(?:на|в|к|около)\s+", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s+", " ", content).strip(" ,.!?—-\t")
    if action == "create_meeting" and content.startswith(("с ", "по ", "о ", "об ")):
        if re.search(r"\bвстреч\w*\b", normalized):
            content = f"встреча {content}"
        elif re.search(r"\bсозвон\w*\b", normalized):
            content = f"созвон {content}"
        elif re.search(r"\bпереговор\w*\b", normalized):
            content = f"переговоры {content}"
    if not content:
        return None

    if invalid_clock and not day_matches:
        action_label = (
            f"встречу «{content}»" if action == "create_meeting"
            else f"задачу «{content}»"
        )
        return Intent(
            action=action,
            content=content,
            clarify_question=(
                f"Время «{invalid_clock}» недопустимо: часы должны быть от 0 до 23, "
                f"а минуты — от 00 до 59. На какую дату и время записать {action_label}?"
            ),
        )

    # Даже без даты/времени явная встреча остаётся встречей и должна
    # уточняться детерминированно, а не переименовываться LLM.
    if not day_matches and time_match is None:
        if action == "create_meeting":
            description = re.sub(r"^(?:встреча|созвон|переговоры)\s+", "", content, flags=re.IGNORECASE).strip()
            meeting_label = f"встречу {description}" if description else "встречу"
            return Intent(
                action="clarify",
                content=content,
                clarify_question=f"На какую дату и время записать {meeting_label}?",
            )
        return None

    if not day_matches:
        target_date = now.date()
        scheduled_at = datetime.combine(
            target_date,
            datetime_time(hours, minutes),
            tzinfo=now.tzinfo,
        )
        if action == "create_meeting":
            meeting_description = re.sub(r"^встреча\s+", "", content, flags=re.IGNORECASE)
            question = f"На какую дату записать встречу {meeting_description}? Время — {scheduled_at:%H:%M}."
        else:
            question = f"На какую дату поставить задачу «{content}»? Время — {scheduled_at:%H:%M}."
        return Intent(
            action="clarify",
            content=content,
            datetime_iso=scheduled_at.isoformat(),
            clarify_question=question,
        )

    day_match = day_matches[-1]
    day_phrase = day_match.group(1)
    named_month_match = re.fullmatch(
        rf"(\d{{1,2}})(?:-?(?:го|е))?\s+({_MONTH_WORDS_PATTERN})(?:\s+(\d{{4}}))?",
        day_phrase,
    )
    month_day_match = re.fullmatch(r"(\d{1,2})(?:-?(?:го|е))?\s+числ\w*", day_phrase)
    if named_month_match:
        day = int(named_month_match.group(1))
        month = _MONTH_NUMBERS[named_month_match.group(2)]
        specified_year = named_month_match.group(3)
        year = int(specified_year) if specified_year else now.year
        try:
            target_date = datetime(year, month, day).date()
            if not specified_year and target_date < now.date():
                target_date = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    elif month_day_match:
        target_date = _date_for_day_of_month(int(month_day_match.group(1)), now)
        if target_date is None:
            return None
    elif day_phrase == "сегодня":
        days_ahead = 0
    elif day_phrase == "послезавтра" or re.search(
        r"через\s+(?:пару|два|двое|2)\s+(?:дня|дней|суток)", day_phrase
    ):
        days_ahead = 2
    elif match := re.search(r"через\s+(\d+)\s+дн", day_phrase):
        days_ahead = int(match.group(1))
    elif match := re.search(r"через\s+(три|четыре)\s+дня", day_phrase):
        days_ahead = {"три": 3, "четыре": 4}[match.group(1)]
    elif day_phrase == "завтра":
        days_ahead = 1
    else:
        days_ahead = 1
    if not month_day_match and not named_month_match:
        target_date = now.date() + timedelta(days=days_ahead)
    if invalid_clock:
        date_only = datetime.combine(target_date, datetime_time.min, tzinfo=now.tzinfo)
        action_label = (
            f"встречу «{content}»" if action == "create_meeting"
            else f"задачу «{content}»"
        )
        return Intent(
            action=action,
            content=content,
            datetime_iso=date_only.isoformat(),
            clarify_question=(
                f"Время «{invalid_clock}» недопустимо: часы должны быть от 0 до 23, "
                f"а минуты — от 00 до 59. На какое время записать {action_label} "
                f"на {target_date:%d.%m.%Y}?"
            ),
        )
    if time_match is None:
        if action == "create_meeting":
            meeting_content = re.sub(r"^встреч\w*\s+", "", content, flags=re.IGNORECASE)
            if meeting_content != content:
                event_phrase = f"встречу {meeting_content}"
            elif content.startswith("прогулка"):
                event_phrase = "прогулку" + content[len("прогулка"):]
            elif content.startswith(("созвон", "переговор", "обед", "ужин", "завтрак")):
                event_phrase = content
            else:
                event_phrase = f"событие «{content}»"
            question = f"На какое время запланировать {event_phrase}?"
        else:
            question = f"На какое время запланировать задачу «{content}»?"
        date_only = datetime.combine(target_date, datetime_time.min, tzinfo=now.tzinfo)
        return Intent(
            action=action,
            content=content,
            datetime_iso=date_only.isoformat(),
            clarify_question=question,
        )

    scheduled_at = datetime.combine(
        target_date,
        datetime_time(hours, minutes),
        tzinfo=now.tzinfo,
    )
    return Intent(action=action, content=content, datetime_iso=scheduled_at.isoformat())

def parse_list_events_query(text: str, now: datetime) -> Intent | None:
    """Распознаёт обычные вопросы о расписании пользователя без участия LLM."""
    normalized = text.casefold().replace("ё", "е")
    future_calls_lookup = bool(
        re.search(r"\b(?:найди|покажи|какие|список|все|есть\s+ли)\b", normalized)
        and re.search(r"\bсозвон\w*\b", normalized)
        and re.search(r"\b(?:будут|будет|предстоят|запланирован\w*|намечен\w*)\b", normalized)
    )
    if future_calls_lookup:
        return Intent(
            action="search_events",
            search_query="созвон",
            datetime_iso=now.isoformat(),
        )

    # Вопрос по ключевому слову запрашивает совпадающие сохранённые названия
    # по всему календарю, даже если распознавание речи добавляет в начало
    # формулировку создания напоминания.
    walk_match = re.search(r"\bпрогулк\w*\b", normalized)
    is_keyword_lookup = bool(
        walk_match
        and re.search(r"\b(?:какие|какой|что|найди|покажи|есть\s+ли)\b", normalized)
        and re.search(r"\b(?:есть|найти|покажи|встречал\w*|были)\b|\?", normalized)
    )
    if is_keyword_lookup:
        return Intent(action="search_events", search_query="прогулк")

    # Не позволяем широкому разговорному фолбэку перехватывать явные команды создания.
    if re.search(r"\b(?:напомни|напомнить|запиши|запланируй|создай|добавь|поставь)\b", normalized):
        return None
    has_query_form = bool(re.search(
        r"\b(?:есть\s+ли|какие|какой|что\s+(?:у\s+меня|мне)|покажи|подскаж\w*|"
        r"выведи|список|расписани\w*|запланирован\w*|что\s+записано)\b",
        normalized,
    ))
    has_event_word = bool(re.search(
        r"\b(?:дел\w*|задач\w*|встреч\w*|событи\w*|мероприяти\w*|план\w*|напоминан\w*|запис\w*)\b",
        normalized,
    ))
    has_period = bool(re.search(
        r"\b(?:сегодня|вчера|позавчера|завтра|послезавтра|недел\w*|понедельник\w*|вторник\w*|"
        r"сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|"
        r"месяц\w*|год\w*|лет)\b",
        normalized,
    ))
    # Распознавание речи часто искажает «есть ли у меня дела?» в формы вроде
    # «что-то я сделал есть?». Даты плюс экзистенциального вопроса достаточно,
    # чтобы считать это запросом расписания, если это не была явная команда создания.
    conversational_lookup = (
        has_period
        and bool(re.search(r"\b(?:что-то|что-нибудь|какие-то|какой-то|есть\s+ли|есть|имеется)\b", normalized))
        and bool(re.search(r"\?|\b(?:подскаж\w*|посмотр\w*|скаж\w*)\b", normalized))
    )
    if not ((has_query_form and has_event_word) or conversational_lookup):
        return None

    target_date = now.date()
    if re.search(r"\bпозавчера\b", normalized):
        target_date -= timedelta(days=2)
    elif re.search(r"\bвчера\b", normalized):
        target_date -= timedelta(days=1)
    elif re.search(r"\bпослезавтра\b", normalized):
        target_date += timedelta(days=2)
    elif re.search(r"\bзавтра\b", normalized):
        target_date += timedelta(days=1)
    elif match := re.search(r"\bчерез\s+(\d+)\s+дн", normalized):
        target_date += timedelta(days=int(match.group(1)))
    elif re.search(r"\bследующ\w*\s+недел", normalized):
        target_date += timedelta(days=7 - now.weekday())
    else:
        weekdays = {
            "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2,
            "четверг": 3, "пятницу": 4, "пятница": 4,
            "субботу": 5, "суббота": 5, "воскресенье": 6,
        }
        for day_name, weekday in weekdays.items():
            if re.search(rf"\b{day_name}\b", normalized):
                offset = (weekday - now.weekday()) % 7
                if offset == 0 or re.search(r"\bследующ\w*\b", normalized):
                    offset += 7
                target_date += timedelta(days=offset)
                break

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=now.tzinfo)
    return Intent(action="list_events", datetime_iso=start.isoformat())

def process(
    user_text: str,
    now: datetime | None = None,
    session_id: str = "default",
    cancellation_event=None,
) -> Intent:
    """
    Главная точка входа с гибридным ИИ + регулярные выражения парсингом контекста.
    """
    if now is None:
        now = datetime.now().astimezone()

    list_query = parse_list_events_query(user_text, now)
    if list_query is not None:
        context_manager.clear_context(session_id)
        return list_query

    reminder_intent = parse_explicit_reminder_command(user_text, now)
    explicit_create_intent = (
        parse_explicit_create_command(user_text, now)
        if reminder_intent is None else None
    )
    previous_intent = context_manager.get_last_intent(session_id)
    context_flags = context_manager.get_context_flags(session_id)
    current_intent = reminder_intent or explicit_create_intent
    resolved_date_from_context = False
    resolved_meridiem_from_context = False
    save_awaiting_meridiem = False
    save_awaiting_date = False
    save_awaiting_time = False

    # «Напомни завтра позвонить Ивану»: парсер сам спрашивает «Во сколько?». Запоминаем, что ждём
    # именно время (дата уже известна), иначе после ответа «в 10» дату спросят второй раз.
    if (
        reminder_intent is not None
        and reminder_intent.action == "clarify"
        and reminder_intent.datetime_iso
        and not has_explicit_clock_time(user_text)
    ):
        save_awaiting_time = True

    if current_intent is None and previous_intent and context_flags["awaiting_meridiem"]:
        meridiem = meridiem_from_reply(user_text)
        if meridiem and previous_intent.datetime_iso:
            try:
                previous_datetime = datetime.fromisoformat(
                    previous_intent.datetime_iso.replace("Z", "+00:00")
                )
                ambiguous_hour = previous_datetime.hour
                resolved_hour = resolve_ambiguous_hour(ambiguous_hour, meridiem)
                resolved_datetime = previous_datetime.replace(hour=resolved_hour)
                if context_flags["awaiting_date"]:
                    current_intent = Intent(
                        action="clarify",
                        content=previous_intent.content,
                        datetime_iso=resolved_datetime.isoformat(),
                        clarify_question=(
                            f"На какую дату записать «{previous_intent.content}»? "
                            f"Время — {resolved_datetime:%H:%M}."
                        ),
                    )
                    save_awaiting_date = True
                else:
                    current_intent = Intent(
                        action=previous_intent.action,
                        content=previous_intent.content,
                        datetime_iso=resolved_datetime.isoformat(),
                    )
                resolved_meridiem_from_context = True
            except ValueError:
                pass

    # Мы спросили "утра или вечера?", но ответ не распознали как meridiem.
    # Не зовём LLM — он не видит истории диалога, и получится "Уточните детали".
    if (
            current_intent is None
            and previous_intent
            and context_flags["awaiting_meridiem"]
            and not context_flags["awaiting_date"]
            and not context_flags["awaiting_time"]
    ):
        previous_dt = None
        if previous_intent.datetime_iso:
            try:
                previous_dt = datetime.fromisoformat(
                    previous_intent.datetime_iso.replace("Z", "+00:00")
                )
            except ValueError:
                previous_dt = None
        if previous_dt is not None:
            hour = previous_dt.hour
            question = (
                "Не расслышал: 12 дня или 12 ночи?"
                if hour == 12
                else f"Не расслышал: {hour} — это утро или вечер?"
            )
            context_manager.save_intent(
                previous_intent,
                session_id,
                awaiting_meridiem=True,
            )
            return Intent(
                action="clarify",
                content=previous_intent.content,
                datetime_iso=previous_intent.datetime_iso,
                clarify_question=question,
            )
    # Если мы спросили дату после того, как пользователь уже указал событие и время,
    # объединяем ответ только с датой и запомненное время.
    if current_intent is None and previous_intent and previous_intent.action in {"create_task", "create_meeting"}:
        try:
            previous_datetime = datetime.fromisoformat(
                previous_intent.datetime_iso.replace("Z", "+00:00")
            ) if previous_intent.datetime_iso else None
        except ValueError:
            previous_datetime = None
        date_answer = user_text.casefold().replace("ё", "е").strip(" .,!?\t")
        date_offsets = {
            "сегодня": 0,
            "на сегодня": 0,
            "завтра": 1,
            "на завтра": 1,
            "послезавтра": 2,
            "на послезавтра": 2,
        }
        selected_date = None
        if date_answer in date_offsets:
            selected_date = now.date() + timedelta(days=date_offsets[date_answer])
        else:
            numbered_date_reply = re.fullmatch(
                r"(?:на\s+)?(\d{1,2})(?:-?(?:го|е))?(?:\s+числ\w*)?",
                date_answer,
            )
            month_numbers = {
                "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
                "мая": 5, "июня": 6, "июля": 7, "августа": 8,
                "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
            }
            month_match = re.search(
                r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|"
                r"июля|августа|сентября|октября|ноября|декабря)"
                r"(?:\s+(\d{4}))?\b",
                date_answer,
            )
            if numbered_date_reply:
                selected_date = _date_for_day_of_month(int(numbered_date_reply.group(1)), now)
            elif month_match:
                day = int(month_match.group(1))
                month = month_numbers[month_match.group(2)]
                year = int(month_match.group(3) or now.year)
                try:
                    selected_date = datetime(year, month, day).date()
                    if not month_match.group(3) and selected_date < now.date():
                        selected_date = datetime(year + 1, month, day).date()
                except ValueError:
                    selected_date = None
        if previous_datetime and selected_date is not None and context_flags["awaiting_time"]:
            if has_explicit_clock_time(user_text):
                parsed_time = parse_short_time(
                    user_text, now, previous_datetime.isoformat()
                )
                if parsed_time:
                    parsed_datetime = datetime.fromisoformat(
                        parsed_time.replace("Z", "+00:00")
                    )
                    current_intent = Intent(
                        action=previous_intent.action,
                        content=previous_intent.content,
                        datetime_iso=datetime.combine(
                            selected_date,
                            parsed_datetime.timetz().replace(tzinfo=None),
                            tzinfo=previous_datetime.tzinfo,
                        ).isoformat(),
                    )
                    resolved_date_from_context = True
            else:
                event_kind = "встречи" if previous_intent.action == "create_meeting" else "задачи"
                date_label = previous_datetime.strftime("%d.%m")
                current_intent = Intent(
                    action="clarify",
                    content=previous_intent.content,
                    datetime_iso=previous_datetime.isoformat(),
                    clarify_question=(
                        f"Вы назвали дату «{date_answer}», а я уточняю время {event_kind} "
                        f"«{previous_intent.content}» на {date_label}. Какое время поставить?"
                    ),
                )
                resolved_date_from_context = True
                save_awaiting_time = True
        elif (
            previous_datetime
            and (previous_datetime.hour or previous_datetime.minute)
            and selected_date is not None
        ):
            current_intent = Intent(
                action=previous_intent.action,
                content=previous_intent.content,
                datetime_iso=datetime.combine(
                    selected_date,
                    previous_datetime.timetz().replace(tzinfo=None),
                    tzinfo=previous_datetime.tzinfo,
                ).isoformat(),
            )
            resolved_date_from_context = True

    # === ХИТРОСТЬ: Если в памяти есть контекст, проверяем на короткий ввод времени ===
    if current_intent is None and previous_intent and (
        len(user_text.strip()) <= 12 or has_explicit_clock_time(user_text)
    ):
        extracted_iso = parse_short_time(user_text, now, previous_intent.datetime_iso)
        if extracted_iso:
            # Создаем интент сразу на основе памяти, минуя запрос к LLM
            current_intent = Intent(
                action=previous_intent.action,
                content=previous_intent.content,
                datetime_iso=extracted_iso
            )

    if current_intent is None and looks_like_time_only_reply(user_text):
        return Intent(
            action="clarify",
            clarify_question=(
                "Я услышал время, но не вижу, какое событие нужно запланировать. "
                "Что именно и на какую дату записать?"
            ),
        )

    # === Если это не короткий ввод времени, гоним через Ollama ===
    if not current_intent:
        system_prompt = build_system_prompt(now)
        from neo.backend.app.services.request_cancellation import raise_if_cancelled

        raise_if_cancelled(cancellation_event)
        stream = chat(
            model="t-tech/T-lite-it-2.1:q4_K_M",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            format=Intent.model_json_schema(),
            options={
                "temperature": 0,
                "num_predict": 256,
            },
            stream=True,
        )

        chunks = []
        try:
            for chunk in stream:
                raise_if_cancelled(cancellation_event)
                chunks.append(chunk.message.content)
        finally:
            stream.close()
        raise_if_cancelled(cancellation_event)
        raw_content = "".join(chunks).strip()
        if "```" in raw_content:
            match = re.search(r"```(?:json)?(.*?)```", raw_content, re.DOTALL)
            if match:
                raw_content = match.group(1).strip()

        try:
            data = json.loads(raw_content)
            if "slots" in data and isinstance(data["slots"], dict):
                slots = data.pop("slots")
                for key, value in slots.items():
                    if key not in data or data[key] is None:
                        data[key] = value
                raw_content = json.dumps(data)
        except Exception:
            pass

        # 3. Валидируем через Pydantic
        try:
            current_intent = Intent.model_validate_json(raw_content)
        except Exception:
            current_intent = Intent(
                action="clarify",
                clarify_question="Я не совсем понял команду. Перефразируйте, пожалуйста."
            )

    if current_intent.action in {"create_task", "create_meeting"}:
        current_intent.content = _clean_created_title(current_intent.content)

    ambiguous_hour = ambiguous_clock_hour(user_text)
    if (
        ambiguous_hour is not None
        and current_intent.action in {"create_task", "create_meeting", "clarify"}
        and current_intent.content
        and current_intent.datetime_iso
    ):
        if ambiguous_hour == 12:
            time_question = "Вы имеете в виду 12 дня или 12 ночи?"
        else:
            time_question = f"Вы имеете в виду {ambiguous_hour} утра или вечера?"
        date_already_known = bool(
            context_flags["awaiting_time"] and previous_intent and previous_intent.datetime_iso
        )
        if not explicit_date_was_given(user_text) and not date_already_known:
            current_intent.clarify_question = (
                f"{time_question} После этого уточню дату."
            )
            save_awaiting_date = True
        else:
            current_intent.clarify_question = time_question
        current_intent.action = "clarify"
        save_awaiting_meridiem = True

    missing_time_action = None
    missing_time_date_iso = None
    if (
        current_intent.action in {"create_task", "create_meeting"}
        and not has_explicit_clock_time(user_text)
        and not resolved_date_from_context
        and not resolved_meridiem_from_context
    ):
        missing_time_action = current_intent.action
        save_awaiting_time = True
        if current_intent.datetime_iso:
            try:
                parsed_date = datetime.fromisoformat(current_intent.datetime_iso.replace("Z", "+00:00"))
                missing_time_date_iso = parsed_date.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat()
            except ValueError:
                missing_time_date_iso = None
        current_intent.action = "clarify"
        current_intent.datetime_iso = None
        if not current_intent.clarify_question and context_flags["awaiting_time"]:
            # Время уже спрашивали, но ответ не распознан: подсказываем формат вместо того же вопроса.
            current_intent.clarify_question = (
                f"Не расслышал время для «{current_intent.content}». "
                "Скажите, например: «в 18:00» или «в 6 вечера»."
            )
        if not current_intent.clarify_question:
            if missing_time_action == "create_meeting":
                current_intent.clarify_question = f"На какое время запланировать «{current_intent.content}»?"
            else:
                current_intent.clarify_question = f"На какое время поставить задачу «{current_intent.content}»?"

    # === АВТОМАТИЧЕСКАЯ НОРМАЛИЗАЦИЯ ПОИСКОВОГО ЗАПРОСА (Раздел 5.6 ТЗ) ===
    if current_intent.action == "search_events" and current_intent.search_query:
        from .tools.search import normalize_query
        current_intent.search_query = normalize_query(current_intent.search_query)

    # === ЛОГИКА ОБЪЕДИНЕНИЯ КОНТЕКСТА ===
    if (
        previous_intent
        and explicit_create_intent is None
        and not missing_time_action
        and not save_awaiting_time
        and not (resolved_meridiem_from_context and save_awaiting_date)
        and not save_awaiting_meridiem  # иначе вопрос «утра или вечера?» превращается обратно в create_task
        and current_intent.action in ["clarify", previous_intent.action]
    ):
        if current_intent.action == "clarify" or current_intent.datetime_iso or current_intent.content:
            current_intent.action = previous_intent.action
            if not current_intent.content:
                current_intent.content = previous_intent.content
            if not current_intent.datetime_iso:
                current_intent.datetime_iso = previous_intent.datetime_iso

    # Прогоняем через пост-обработку дат
    current_intent = postprocess_datetime(current_intent, now)

    # === УПРАВЛЕНИЕ ПАМЯТЬЮ ===
    if current_intent.action == "clarify":
        text_lower = user_text.lower()
        remembered_action = missing_time_action
        if not remembered_action:
            remembered_action = "clarify"
            if any(w in text_lower for w in ["напомни", "задача", "напоминание", "сделать", "помыть", "купить"]):
                remembered_action = "create_task"
            elif any(w in text_lower for w in ["встреча", "встречу", "созвон", "свидание", "переговоры", "созвониться", "прогулка", "прогулку", "обед", "ужин"]):
                remembered_action = "create_meeting"

        intent_to_save = Intent(
            action=remembered_action if remembered_action != "clarify" else (
                previous_intent.action if previous_intent else "clarify"),
            content=current_intent.content or (previous_intent.content if previous_intent else None),
            datetime_iso=(current_intent.datetime_iso or missing_time_date_iso
                          or (previous_intent.datetime_iso if previous_intent else None))
        )

        if not current_intent.clarify_question:
            if current_intent.content and not current_intent.datetime_iso:
                current_intent.clarify_question = (
                    f"На какую дату и время записать «{current_intent.content}»?"
                )
            elif current_intent.datetime_iso and not current_intent.content:
                current_intent.clarify_question = (
                    "Что именно нужно запланировать?"
                )
            elif previous_intent and previous_intent.content:
                current_intent.clarify_question = (
                    f"Уточните, пожалуйста, время для события "
                    f"«{previous_intent.content}»."
                )
            else:
                current_intent.clarify_question = (
                    "Уточните, пожалуйста, что и на какое время запланировать."
                )
        context_manager.save_intent(
            intent_to_save,
            session_id,
            awaiting_meridiem=save_awaiting_meridiem,
            awaiting_date=save_awaiting_date,
            awaiting_time=save_awaiting_time,
        )
    else:
        context_manager.clear_context(session_id)

    return current_intent

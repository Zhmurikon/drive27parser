"""
Настройки монитора слотов вождения.
Все значения берутся из переменных окружения (файл .env).
"""
import os
from datetime import time as dtime


def _get(key: str, default=None, required: bool = False):
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(f"Обязательная переменная окружения не задана: {key}")
    return val


def _parse_schedule() -> dict[int, tuple[dtime, dtime] | None]:
    """
    Парсит переменные SLOT_MONDAY … SLOT_SUNDAY из окружения.
    Формат значения: HH:MM-HH:MM  или  No (не задан → No).
    Возвращает словарь {weekday_int: (time_from, time_to) | None}.
    """
    day_keys = [
        ("SLOT_MONDAY",    0),
        ("SLOT_TUESDAY",   1),
        ("SLOT_WEDNESDAY", 2),
        ("SLOT_THURSDAY",  3),
        ("SLOT_FRIDAY",    4),
        ("SLOT_SATURDAY",  5),
        ("SLOT_SUNDAY",    6),
    ]
    result: dict[int, tuple[dtime, dtime] | None] = {}
    for env_key, weekday in day_keys:
        raw = os.environ.get(env_key, "No").strip()
        if not raw or raw.lower() == "no":
            result[weekday] = None
            continue
        try:
            start_str, end_str = raw.split("-")
            sh, sm = map(int, start_str.strip().split(":"))
            eh, em = map(int, end_str.strip().split(":"))
            result[weekday] = (dtime(sh, sm), dtime(eh, em))
        except Exception:
            raise EnvironmentError(
                f"{env_key}: неверный формат '{raw}'. "
                f"Ожидается 'HH:MM-HH:MM' или 'No'"
            )
    return result


class Config:
    # ── Авторизация ────────────────────────────────────────────────────────────
    LOGIN      = _get("DS_LOGIN",    required=True)
    PASSWORD   = _get("DS_PASSWORD", required=True)

    # ── ID курсанта ────────────────────────────────────────────────────────────
    # Если не задан — будет получен автоматически после логина
    _sid = _get("DS_STUDENT_ID", "")
    STUDENT_ID = int(_sid) if _sid else None

    # ── Уведомления через Leads API ────────────────────────────────────────────
    # Если LEADS_API_KEY не задан или пустой — уведомления полностью отключены
    LEADS_API_URL = _get("LEADS_API_URL", "http://185.119.57.217/api/v1/leads/")
    LEADS_API_KEY = _get("LEADS_API_KEY", "")   # пусто = уведомления выключены
    LEADS_NAME    = _get("LEADS_NAME",    "Монитор вождения")

    # ── Расписание поиска ──────────────────────────────────────────────────────
    CHECK_INTERVAL  = int(_get("CHECK_INTERVAL",  "120"))   # секунды
    LOOK_AHEAD_DAYS = int(_get("LOOK_AHEAD_DAYS", "14"))    # дней вперёд

    # ── Фильтры слотов ─────────────────────────────────────────────────────────
    # Через запятую: TEACHER_IDS=160823,93718  или пусто = любой инструктор
    _teacher_ids_raw = _get("TEACHER_IDS", "")
    TEACHER_IDS = [int(x) for x in _teacher_ids_raw.split(",") if x.strip()]

    # ── Расписание подходящих слотов ───────────────────────────────────────────
    # SLOT_MONDAY=No
    # SLOT_TUESDAY=17:45-22:00
    # SLOT_WEDNESDAY=No
    # SLOT_THURSDAY=17:45-22:00
    # SLOT_FRIDAY=No
    # SLOT_SATURDAY=08:00-17:00
    # SLOT_SUNDAY=No
    SLOT_SCHEDULE: dict[int, tuple[dtime, dtime] | None] = _parse_schedule()

    # ── Автозапись ─────────────────────────────────────────────────────────────
    AUTO_BOOK = _get("AUTO_BOOK", "false").lower() == "true"

    # ── Часовой пояс ───────────────────────────────────────────────────────────
    # Хабаровск = UTC+10 → timeshift = -600
    TIMESHIFT = int(_get("TIMESHIFT", "-600"))

    DAILY_BOOK_LIMIT  = int(_get("DAILY_BOOK_LIMIT",  "1"))
    WEEKLY_BOOK_LIMIT = int(_get("WEEKLY_BOOK_LIMIT", "0"))  # 0 = без ограничения
    STOP_AFTER_BOOK   = _get("STOP_AFTER_BOOK", "false").lower() == "true"
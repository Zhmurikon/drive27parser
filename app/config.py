"""
Настройки монитора слотов вождения.
Все значения берутся из переменных окружения (файл .env).
"""
import os


def _get(key: str, default=None, required: bool = False):
    val = os.environ.get(key, default)
    if required and not val:
        raise EnvironmentError(f"Обязательная переменная окружения не задана: {key}")
    return val


class Config:
    # ── Авторизация ────────────────────────────────────────────────────────────
    LOGIN      = _get("DS_LOGIN",    required=True)
    PASSWORD   = _get("DS_PASSWORD", required=True)

    # ── ID курсанта ────────────────────────────────────────────────────────────
    # Если не задан — будет получен автоматически после логина
    _sid = _get("DS_STUDENT_ID", "")
    STUDENT_ID = int(_sid) if _sid else None

    # ── Уведомления через Leads API ────────────────────────────────────────────
    LEADS_API_URL = _get("LEADS_API_URL", "http://185.119.57.217/api/v1/leads/")
    LEADS_API_KEY = _get("LEADS_API_KEY", required=True)
    LEADS_NAME    = _get("LEADS_NAME",    "Монитор вождения")

    # ── Расписание поиска ──────────────────────────────────────────────────────
    CHECK_INTERVAL  = int(_get("CHECK_INTERVAL",  "120"))   # секунды
    LOOK_AHEAD_DAYS = int(_get("LOOK_AHEAD_DAYS", "14"))    # дней вперёд

    # ── Фильтры слотов ─────────────────────────────────────────────────────────
    # Через запятую: TEACHER_IDS=160823,93718  или пусто = любой инструктор
    _teacher_ids_raw = _get("TEACHER_IDS", "")
    TEACHER_IDS = [int(x) for x in _teacher_ids_raw.split(",") if x.strip()]

    # ── Автозапись ─────────────────────────────────────────────────────────────
    AUTO_BOOK = _get("AUTO_BOOK", "false").lower() == "true"

    # ── Часовой пояс ───────────────────────────────────────────────────────────
    # Хабаровск = UTC+10 → timeshift = -600
    TIMESHIFT = int(_get("TIMESHIFT", "-600"))
"""
Монитор свободных слотов на вождение — app.dscontrol.ru
=========================================================
Автоматически проверяет наличие свободных занятий и при нахождении
подходящего слота — записывается и отправляет заявку в Leads API.

Настройка: отредактируй .env
Запуск:    python monitor.py
"""

import json
import time
import logging
import requests
from datetime import datetime, timedelta, date
from pathlib import Path
from config import Config

# ── Логирование ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── Файл учёта записей ────────────────────────────────────────────────────────
BOOKINGS_FILE = Path("bookings.json")


def _load_bookings() -> dict:
    """Загружает данные о записях из файла. Формат: {"YYYY-MM-DD": count}"""
    if BOOKINGS_FILE.exists():
        try:
            return json.loads(BOOKINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_bookings(data: dict):
    BOOKINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_bookings_today() -> int:
    """Возвращает количество записей за сегодняшний календарный день."""
    data = _load_bookings()
    today = date.today().isoformat()
    return data.get(today, 0)


def increment_bookings_today():
    """Увеличивает счётчик записей за сегодня на 1."""
    data = _load_bookings()
    today = date.today().isoformat()
    data[today] = data.get(today, 0) + 1
    _save_bookings(data)
    log.info("Счётчик записей за %s: %d", today, data[today])


# ── Сессия с сайтом ──────────────────────────────────────────────────────────
class DsControlClient:
    BASE = "https://app.dscontrol.ru"

    def __init__(self):
        self.session = requests.Session()
        self._set_headers()

    def _set_headers(self):
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.BASE + "/",
                "Connection": "close",
            }
        )

    def reset_session(self):
        """Пересоздаёт HTTP-сессию, сбрасывая все куки и соединения."""
        self.session.close()
        self.session = requests.Session()
        self._set_headers()
        log.debug("Сессия пересоздана")

    def login(self) -> bool:
        """Авторизация. Возвращает True при успехе."""
        r = self.session.get(self.BASE + "/Login", timeout=15)
        r.raise_for_status()
        csrf = self.session.cookies.get("__RequestVerificationToken", "")

        payload = {
            "Login": Config.LOGIN,
            "TextPassword": "",
            "Password": Config.PASSWORD,
            "PreventPass": "false",
        }
        r = self.session.post(
            self.BASE + "/Login",
            data=payload,
            headers={"__RequestVerificationToken": csrf},
            timeout=15,
            allow_redirects=True,
        )
        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            log.info("Авторизация: редирект (успех)")
            return True

        if data.get("success"):
            log.info("Авторизация успешна")
            self._fetch_student_id()
            return True
        else:
            log.error("Ошибка авторизации: %s", data.get("data"))
            return False

    def _fetch_student_id(self):
        if Config.STUDENT_ID:
            return
        try:
            r = self.session.get(
                self.BASE + "/apia/ChatPrepareIntragramConnection",
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            user_id = data["data"]["User"]["id"]
            Config.STUDENT_ID = user_id
            log.info("Student ID получен автоматически: %s", user_id)
        except Exception as e:
            log.error("Не удалось получить Student ID: %s", e)
            raise RuntimeError("Student ID не задан и не удалось получить автоматически") from e

    def get_slots(self, from_date: datetime, to_date: datetime) -> list[dict]:
        """Возвращает список свободных слотов (State=2)."""
        params = {
            "Kinds": "D",
            "OnlyMine": "false",
            "MasterIds": ",".join(str(i) for i in Config.TEACHER_IDS) if Config.TEACHER_IDS else "",
            "AutodromeId": "",
            "VehicleId": "",
            "SessionTypeIds": "",
            "TeacherIds": "",
            "ThemeId": "",
            "RoomId": "",
            "timeshift": Config.TIMESHIFT,
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }
        r = self.session.get(
            self.BASE + "/Api/StudentSchedulerList",
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()

        if not body.get("success"):
            log.warning("StudentSchedulerList вернул success=false: %s", body)
            return []

        all_slots = body.get("data") or []
        log.debug("Всего слотов от API: %d", len(all_slots))

        free = [s for s in all_slots if s.get("State") == 2]
        log.debug("Свободных (State=2): %d", len(free))
        return free

    def book_slot(self, slot: dict) -> bool:
        """Записаться на свободный слот."""
        session_id = slot.get("Id") or slot.get("id")
        if not session_id:
            log.error("book_slot: нет Id в слоте: %s", slot)
            return False

        csrf = self.session.cookies.get("__RequestVerificationToken", "")
        payload = {"SessionId": session_id}
        log.info("Отправляю запись на слот %s", session_id)

        r = self.session.post(
            self.BASE + "/api/MobileSigninSessionV2",
            json=payload,
            headers={"__RequestVerificationToken": csrf},
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()

        if not body.get("success"):
            log.warning("MobileSigninSessionV2 вернул ошибку: %s", body)
            return False

        result = body.get("data")
        if result == "RESERVED":
            log.warning("Слот %s забронирован (RESERVED), не записан — нужна оплата", session_id)
            notify_telegram(f"⚠️ Слот занят в бронь, но не записан (нет оплаты):\n{format_slot(slot)}")
            return False

        log.info("Успешно записан на слот %s", session_id)
        return True


# ── Фильтрация слотов ─────────────────────────────────────────────────────────
def is_slot_suitable(slot: dict) -> bool:
    """
    Проверяет, подходит ли слот под условия из SLOT_SCHEDULE.
    """
    if slot.get("State", 1) != 2:
        return False

    start_str = slot.get("start_date", "")
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False

    window = Config.SLOT_SCHEDULE.get(start.weekday())
    if window is None:
        return False  # день не разрешён

    time_from, time_to = window
    if not (time_from <= start.time() < time_to):
        return False

    if Config.TEACHER_IDS and slot.get("EmployeeId") not in Config.TEACHER_IDS:
        return False

    return True


# ── Уведомления через Leads API ───────────────────────────────────────────────
def notify_telegram(message: str):
    try:
        r = requests.post(
            Config.LEADS_API_URL,
            json={"name": Config.LEADS_NAME, "message": message},
            headers={"X-Api-Key": Config.LEADS_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            log.info("Leads API: заявка #%s отправлена", data.get("lead_id"))
        else:
            log.warning("Leads API вернул ошибку: %s", data)
    except Exception as e:
        log.error("Ошибка отправки в Leads API: %s", e)


def format_slot(slot: dict) -> str:
    name = slot.get("EmployeeName", "?")
    start = slot.get("start_date", "?")
    end = slot.get("end_date", "?")
    return f"🚗 Свободный слот!\n👨‍🏫 {name}\n🕐 {start} — {end}"


# ── Основной цикл ─────────────────────────────────────────────────────────────
def main():
    client = DsControlClient()
    booked_ids: set[int] = set()
    session_ok = False

    log.info(
        "Монитор запущен. Интервал: %d сек. Лимит записей в день: %d. "
        "Остановка после записи: %s.",
        Config.CHECK_INTERVAL,
        Config.DAILY_BOOK_LIMIT,
        Config.STOP_AFTER_BOOK,
    )

    while True:
        try:
            if not session_ok:
                session_ok = client.login()
                if not session_ok:
                    log.error("Не удалось авторизоваться, жду %d сек.", Config.CHECK_INTERVAL)
                    time.sleep(Config.CHECK_INTERVAL)
                    continue

            now = datetime.now()
            date_from = now
            date_to = now + timedelta(days=Config.LOOK_AHEAD_DAYS)

            log.info(
                "Проверяю слоты с %s по %s...",
                date_from.strftime("%d.%m"),
                date_to.strftime("%d.%m"),
            )

            slots = client.get_slots(date_from, date_to)
            log.info("Получено слотов: %d", len(slots))

            suitable = [s for s in slots if is_slot_suitable(s)]
            log.info("Подходящих слотов: %d", len(suitable))

            for slot in suitable:
                slot_id = slot.get("Id")
                if slot_id in booked_ids:
                    continue

                if Config.AUTO_BOOK:
                    # Проверяем дневной лимит перед каждой попыткой записи
                    bookings_today = get_bookings_today()
                    if bookings_today >= Config.DAILY_BOOK_LIMIT:
                        log.info(
                            "Дневной лимит записей достигнут (%d/%d), слот %s пропускаю",
                            bookings_today, Config.DAILY_BOOK_LIMIT, slot_id,
                        )
                        continue  # продолжаем мониторить, но не записываемся

                    success = client.book_slot(slot)
                    if success:
                        increment_bookings_today()
                        notify_telegram(
                            f"✅ Записан на слот #{slot_id}!\n{format_slot(slot)}"
                        )
                        booked_ids.add(slot_id)

                        if Config.STOP_AFTER_BOOK:
                            log.info("STOP_AFTER_BOOK=true — завершаю работу")
                            return

                        # Проверяем лимит после успешной записи
                        if get_bookings_today() >= Config.DAILY_BOOK_LIMIT:
                            log.info("Дневной лимит записей исчерпан, жду следующего дня")
                            break  # выходим из цикла по слотам, но не из while
                    else:
                        log.warning("Не удалось записаться на слот %s", slot_id)
                else:
                    # AUTO_BOOK=false — только уведомляем
                    msg = format_slot(slot)
                    notify_telegram(msg)
                    booked_ids.add(slot_id)

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                log.warning("Сессия истекла (HTTP %s), переавторизуюсь", e.response.status_code)
                session_ok = False
                client.reset_session()
            else:
                log.error("HTTP ошибка: %s", e)
        except requests.RequestException as e:
            log.warning("Сетевая ошибка (%s) — сброс сессии, переавторизуюсь", e)
            session_ok = False
            client.reset_session()
        except Exception as e:
            log.exception("Неожиданная ошибка: %s", e)

        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
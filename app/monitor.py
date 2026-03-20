"""
Монитор свободных слотов на вождение — app.dscontrol.ru
=========================================================
Автоматически проверяет наличие свободных занятий и при нахождении
подходящего слота — записывается и отправляет заявку в Leads API.

Настройка: отредактируй config.py
Запуск:    python monitor.py
"""

import time
import logging
import requests
from datetime import datetime, timedelta
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


# ── Сессия с сайтом ──────────────────────────────────────────────────────────
class DsControlClient:
    BASE = "https://app.dscontrol.ru"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.BASE + "/",
                # Отключаем keep-alive: сервер всё равно рвёт соединение
                "Connection": "close",
            }
        )

    # ------------------------------------------------------------------
    def reset_session(self):
        """Пересоздаёт HTTP-сессию, сбрасывая все куки и соединения."""
        self.session.close()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self.BASE + "/",
                # Отключаем keep-alive — сервер всё равно рвёт соединение
                "Connection": "close",
            }
        )
        log.debug("Сессия пересоздана")

    def login(self) -> bool:
        """Авторизация. Возвращает True при успехе."""
        # Шаг 1: получаем страницу логина, чтобы взять антиподдельный токен
        r = self.session.get(self.BASE + "/Login", timeout=15)
        r.raise_for_status()

        # Извлекаем __RequestVerificationToken из cookies
        csrf = self.session.cookies.get("__RequestVerificationToken", "")

        # Шаг 2: POST авторизация
        payload = {
            "Login": Config.LOGIN,
            "TextPassword": "",          # поле есть в форме, но пустое
            "Password": Config.PASSWORD,
            "PreventPass": "false",
        }
        headers = {"__RequestVerificationToken": csrf}
        r = self.session.post(
            self.BASE + "/Login",
            data=payload,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            # Сервер редиректнул на главную страницу — тоже успех
            log.info("Авторизация: редирект (успех)")
            return True

        if data.get("success"):
            log.info("Авторизация успешна")
            self._fetch_student_id()
            return True
        else:
            log.error("Ошибка авторизации: %s", data.get("data"))
            return False

    # ------------------------------------------------------------------
    def _fetch_student_id(self):
        """
        Получает Student ID из ChatPrepareIntragramConnection.
        Если DS_STUDENT_ID задан в .env — использует его.
        Если нет — получает автоматически и сохраняет в Config.
        """
        if Config.STUDENT_ID:
            return  # уже задан вручную

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

    # ------------------------------------------------------------------
    def get_slots(self, from_date: datetime, to_date: datetime) -> list[dict]:
        """
        Возвращает список СВОБОДНЫХ слотов из StudentSchedulerList.

        Из app.js (studentcalendar view):
          State=0 — занято чужим студентом
          State=1 — моё занятие
          State=2 — СВОБОДНЫЙ слот (для записи)  ← нам нужен именно этот
          State=3 — забронировано мной

        При клике на State=2 → MobileSigninSessionV2({SessionId, AutodromeId})
        """
        params = {
            "Kinds": "D",              # D = вождение, T = теория, DT = оба
            "OnlyMine": "false",       # показывать все, включая свободные
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

        # State=2 — свободный слот, доступный для записи
        free = [s for s in all_slots if s.get("State") == 2]
        log.debug("Свободных (State=2): %d", len(free))
        return free

    # ------------------------------------------------------------------
    def book_slot(self, slot: dict) -> bool:
        """
        Записаться на свободный слот (State=2).

        Подтверждено HAR-файлом:
          POST /api/MobileSigninSessionV2  (строчная /api/!)
          Body: {"SessionId": <id>}        (AutodromeId не нужен — None в слоте)
          Header: __RequestVerificationToken из куки
          Ответ успеха: {"success":true, "data":"SIGNED"}
          Ответ брони:  {"success":true, "data":"RESERVED"}
        """
        session_id = slot.get("Id") or slot.get("id")
        if not session_id:
            log.error("book_slot: нет Id в слоте: %s", slot)
            return False

        # CSRF-токен: берём из куки (он туда попадает при авторизации)
        csrf = self.session.cookies.get("__RequestVerificationToken", "")

        payload = {"SessionId": session_id}
        log.info("Отправляю запись на слот %s", session_id)

        r = self.session.post(
            self.BASE + "/api/MobileSigninSessionV2",   # строчная /api/
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
            # Забронировано, но ещё не записано (нет достаточно часов/баланса)
            log.warning("Слот %s забронирован (RESERVED), не записан — нужна оплата", session_id)
            notify_telegram(f"⚠️ Слот занят в бронь, но не записан (нет оплаты):\n{format_slot(slot)}")
            return False  # считаем неудачей — уведомление уже отправлено выше

        log.info("Успешно записан на слот %s", session_id)
        return True




# ── Фильтрация слотов ─────────────────────────────────────────────────────────
def is_slot_suitable(slot: dict) -> bool:
    """
    Проверяет, подходит ли слот под условия.

    Правила:
      • Вторник (1) или четверг (3): начало >= 17:45
      • Суббота (5):                 начало < 17:00
      • Остальные дни:               не подходят
    """
    # Только свободные слоты (State=0; в HAR занятые были State=1)
    if slot.get("State", 1) != 0:
        return False

    start_str = slot.get("start_date", "")
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False

    wd = start.weekday()   # 0=пн 1=вт 2=ср 3=чт 4=пт 5=сб 6=вс
    t  = start.time()

    from datetime import time as dtime

    TUESDAY   = 1
    THURSDAY  = 3
    SATURDAY  = 5

    if wd in (TUESDAY, THURSDAY):
        # начало в 18:00 (проверяем с 17:45 для подстраховки)
        ok = t >= dtime(17, 45)
    elif wd == SATURDAY:
        # до 17:00 (начало строго раньше)
        ok = t < dtime(17, 0)
    else:
        ok = False

    if not ok:
        return False

    # Фильтр по инструктору (если задан)
    if Config.TEACHER_IDS and slot.get("EmployeeId") not in Config.TEACHER_IDS:
        return False

    return True


# ── Уведомления через Leads API ───────────────────────────────────────────────
def notify_telegram(message: str):
    """Отправляет уведомление как заявку в Leads API."""
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
    return f"🚗 <b>Свободный слот!</b>\n👨‍🏫 {name}\n🕐 {start} — {end}"


# ── Основной цикл ─────────────────────────────────────────────────────────────
def main():
    client = DsControlClient()
    booked_ids: set[int] = set()   # не дублируем уведомления и записи
    session_ok = False

    log.info("Монитор запущен. Интервал проверки: %d сек.", Config.CHECK_INTERVAL)

    while True:
        try:
            # Переавторизуемся если нужно
            if not session_ok:
                session_ok = client.login()
                if not session_ok:
                    log.error("Не удалось авторизоваться, жду %d сек.", Config.CHECK_INTERVAL)
                    time.sleep(Config.CHECK_INTERVAL)
                    continue

            # Окно поиска: от сегодня + N дней вперёд
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

                msg = format_slot(slot)
                notify_telegram(msg)

                if Config.AUTO_BOOK:
                    success = client.book_slot(slot)
                    if success:
                        notify_telegram(f"✅ Записан на слот #{slot_id}! Скрипт завершает работу.")
                        log.info("Успешно записан на слот %s — завершаю работу", slot_id)
                        return  # задача выполнена, выходим
                    else:
                        log.warning("Не удалось записаться на слот %s", slot_id)
                else:
                    # Просто уведомляем, не записываемся
                    booked_ids.add(slot_id)  # чтобы не спамить

        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                log.warning("Сессия истекла (HTTP %s), переавторизуюсь", e.response.status_code)
                session_ok = False
                client.reset_session()
            else:
                log.error("HTTP ошибка: %s", e)
        except requests.RequestException as e:
            # RemoteDisconnected и другие сетевые ошибки — сервер закрыл соединение.
            # Скорее всего сессия протухла. Сбрасываем и переавторизуемся.
            log.warning("Сетевая ошибка (%s) — сброс сессии, переавторизуюсь", e)
            session_ok = False
            client.reset_session()
        except Exception as e:
            log.exception("Неожиданная ошибка: %s", e)

        time.sleep(Config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
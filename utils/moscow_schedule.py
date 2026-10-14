"""
Helpers for converting Moscow-time schedules into system-local-time strings
that the ``schedule`` library can use.

``schedule`` library uses naive LOCAL time only — it has no timezone awareness.
User-configured schedules are stored as "HH:MM" in Moscow time, regardless
of what timezone the server runs in. Without conversion, ``.at("08:00")`` would
fire at the server's local 08:00, which might be e.g. 06:00 МСК if the server
is in UTC+5. This module fixes that.

Usage:
    from utils.moscow_schedule import moscow_to_local_fire

    local_day, local_hhmm = moscow_to_local_fire("08:00", "monday")
    schedule.every()[local_day].at(local_hhmm).do(my_job)
"""
from datetime import datetime, timedelta
import pytz
import logging


MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def moscow_to_local_fire(moscow_hhmm: str, moscow_weekday: str, local_tz=None):
    """Convert (Moscow weekday, Moscow HH:MM) -> (local weekday, local HH:MM).

    Args:
        moscow_hhmm: "HH:MM" string in Moscow time.
        moscow_weekday: "monday".."sunday" in Moscow time.
        local_tz: optional pytz/timezone for system local time. Defaults to
            the system's actual local timezone. Exposed for testability.

    Returns:
        Tuple (local_weekday, local_hhmm). Both lowercase, same formats.
    """
    weekdays = ["monday","tuesday","wednesday","thursday",
                "friday","saturday","sunday"]
    target_wd_idx = weekdays.index(moscow_weekday.lower())
    h, m = map(int, moscow_hhmm.split(":"))

    # Find the NEXT occurrence of <moscow_weekday> at <HH:MM> in Moscow time.
    moscow_now = datetime.now(MOSCOW_TZ)
    days_ahead = (target_wd_idx - moscow_now.weekday()) % 7
    moscow_target = (moscow_now + timedelta(days=days_ahead)).replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    # Convert to system local time
    if local_tz is None:
        local_target = moscow_target.astimezone()  # system local TZ
    else:
        local_target = moscow_target.astimezone(local_tz)
    local_naive = local_target.replace(tzinfo=None)

    local_day = local_naive.strftime("%A").lower()
    local_hhmm = local_naive.strftime("%H:%M")

    # Day-shift warning (e.g. Moscow Mon 23:00 in UTC+5 = Local Tue 01:00).
    if local_day != moscow_weekday.lower():
        logging.info(
            f"[moscow_schedule] Day shift: Moscow target "
            f"{moscow_weekday} {moscow_hhmm} МСК → system-local "
            f"{local_day} {local_hhmm}. Schedule will fire weekly on "
            f"local {local_day} (= every Moscow {moscow_weekday})."
        )

    return local_day, local_hhmm

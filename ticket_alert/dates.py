"""Даты вылета: месяц (YYYY-MM) или конкретный день (YYYY-MM-DD)."""

from __future__ import annotations

import re
from datetime import date

MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAY = re.compile(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?$")
_MONTH_NAME = re.compile(r"^([а-яё]+)(?:\s+(\d{4}))?$")


def upcoming_months(today: date, count: int = 6) -> list[str]:
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def describe_departure(value: str) -> str:
    """2026-12 → «декабрь 2026», 2026-12-05 → «05.12.2026»."""
    match = _ISO_MONTH.match(value)
    if match:
        return f"{MONTHS[int(match[2]) - 1]} {match[1]}"
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def is_expired(value: str, today: date) -> bool:
    if _ISO_MONTH.match(value):
        return value < today.strftime("%Y-%m")
    return value < today.isoformat()


def parse_departure(text: str, today: date) -> str | None:
    """Дата из сообщения: «05.12», «05.12.2026», «2026-12-05», «декабрь», «2026-12»."""
    text = text.strip().lower()
    try:
        if match := _ISO_MONTH.match(text):
            year, month = int(match[1]), int(match[2])
            value = f"{date(year, month, 1):%Y-%m}"
            return None if is_expired(value, today) else value

        if match := _MONTH_NAME.match(text):
            month = next(
                (i for i, name in enumerate(MONTHS, 1) if match[1][:3] == name[:3]), None
            )
            if month is None:
                return None
            year = int(match[2]) if match[2] else today.year + (month < today.month)
            value = f"{year:04d}-{month:02d}"
            return None if is_expired(value, today) else value

        if _ISO_DAY.match(text):
            day = date.fromisoformat(text)
        elif match := _DAY.match(text):
            d, m, y = int(match[1]), int(match[2]), match[3]
            if y is None:
                day = date(today.year, m, d)
                if day < today:
                    day = date(today.year + 1, m, d)
            else:
                day = date(int(y) + (2000 if len(y) == 2 else 0), m, d)
        else:
            return None
    except ValueError:
        return None
    return None if day < today else day.isoformat()

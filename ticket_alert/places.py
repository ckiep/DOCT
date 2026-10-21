"""Справочник городов: название ↔ IATA-код.

Полный список берётся из «Файлов данных» Travelpayouts и кэшируется на диске.
Если скачать не получилось, работает встроенный список популярных городов.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

CITIES_URL = "https://api.travelpayouts.com/data/ru/cities.json"
COUNTRIES_URL = "https://api.travelpayouts.com/data/ru/countries.json"
MAX_AGE_SECONDS = 30 * 24 * 3600

# Код города: (название, код страны). Нужен, когда справочник не скачался,
# и чтобы отличать внутренние рейсы от зарубежных.
BUILTIN = {
    "ALA": ("Алматы", "KZ"),
    "NQZ": ("Астана", "KZ"),
    "CIT": ("Шымкент", "KZ"),
    "SCO": ("Актау", "KZ"),
    "AKX": ("Актобе", "KZ"),
    "KGF": ("Караганда", "KZ"),
    "GUW": ("Атырау", "KZ"),
    "UKK": ("Усть-Каменогорск", "KZ"),
    "KSN": ("Костанай", "KZ"),
    "PWQ": ("Павлодар", "KZ"),
    "URA": ("Уральск", "KZ"),
    "KZO": ("Кызылорда", "KZ"),
    "DMB": ("Тараз", "KZ"),
    "PLX": ("Семей", "KZ"),
    "HSA": ("Туркестан", "KZ"),
    "KOV": ("Кокшетау", "KZ"),
    "PPK": ("Петропавловск", "KZ"),
    "TDK": ("Талдыкорган", "KZ"),
    "DZN": ("Жезказган", "KZ"),
    "BXH": ("Балхаш", "KZ"),
    "AYK": ("Аркалык", "KZ"),
    "IST": ("Стамбул", "TR"),
    "AYT": ("Анталья", "TR"),
    "DXB": ("Дубай", "AE"),
    "TBS": ("Тбилиси", "GE"),
    "TAS": ("Ташкент", "UZ"),
    "FRU": ("Бишкек", "KG"),
    "BAK": ("Баку", "AZ"),
    "EVN": ("Ереван", "AM"),
    "MOW": ("Москва", "RU"),
    "LED": ("Санкт-Петербург", "RU"),
    "BKK": ("Бангкок", "TH"),
    "HKT": ("Пхукет", "TH"),
    "SEL": ("Сеул", "KR"),
    "BJS": ("Пекин", "CN"),
    "URC": ("Урумчи", "CN"),
    "DEL": ("Дели", "IN"),
    "SSH": ("Шарм-эль-Шейх", "EG"),
}

COUNTRY_NAMES = {
    "KZ": "Казахстан",
    "TR": "Турция",
    "AE": "ОАЭ",
    "GE": "Грузия",
    "UZ": "Узбекистан",
    "KG": "Киргизия",
    "AZ": "Азербайджан",
    "AM": "Армения",
    "RU": "Россия",
    "TH": "Таиланд",
    "KR": "Южная Корея",
    "CN": "Китай",
    "IN": "Индия",
    "EG": "Египет",
}

POPULAR_ORIGINS = ["ALA", "NQZ", "CIT", "SCO", "AKX", "KGF", "GUW", "UKK", "KSN"]
POPULAR_DESTINATIONS = ["IST", "DXB", "AYT", "TBS", "TAS", "FRU", "MOW", "BKK", "SEL", "ALA", "NQZ", "CIT"]


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


@dataclass(frozen=True)
class Place:
    code: str
    name: str
    country: str = ""
    country_code: str = ""
    aliases: tuple[str, ...] = ()  # другие написания, например английское

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}" if self.country else f"{self.name} ({self.code})"


def _builtin_places() -> dict[str, Place]:
    return {
        code: Place(code, name, COUNTRY_NAMES.get(country_code, ""), country_code)
        for code, (name, country_code) in BUILTIN.items()
    }


class Places:
    def __init__(self, places: Iterable[Place]) -> None:
        self._by_code = {p.code: p for p in places}

    @classmethod
    def builtin(cls) -> Places:
        return cls(_builtin_places().values())

    @classmethod
    def from_json(cls, cities: list[dict[str, Any]], countries: list[dict[str, Any]]) -> Places:
        country_names = {c.get("code"): c.get("name") for c in countries if c.get("code")}
        places = _builtin_places()
        for city in cities:
            code = city.get("code")
            if not code or city.get("has_flightable_airport") is False:
                continue
            english = (city.get("name_translations") or {}).get("en") or ""
            name = city.get("name") or english or code
            country_code = city.get("country_code") or ""
            # Встроенное название тоже ищется, даже если в справочнике город назван иначе.
            builtin_name = BUILTIN.get(code, ("",))[0]
            aliases = tuple(a for a in (english, builtin_name) if a and a != name)
            places[code] = Place(
                code=code,
                name=name,
                country=country_names.get(country_code) or COUNTRY_NAMES.get(country_code) or country_code,
                country_code=country_code,
                aliases=aliases,
            )
        return cls(places.values())

    @classmethod
    def load(
        cls,
        cache_dir: Path,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.time,
    ) -> Places:
        cities = _cached_json(cache_dir / "cities.json", CITIES_URL, urlopen, clock)
        countries = _cached_json(cache_dir / "countries.json", COUNTRIES_URL, urlopen, clock)
        if not isinstance(cities, list):
            log.warning("справочник городов недоступен — работает встроенный список популярных городов")
            return cls.builtin()
        return cls.from_json(cities, countries if isinstance(countries, list) else [])

    def get(self, code: str) -> Place | None:
        return self._by_code.get(code)

    def name(self, code: str) -> str:
        place = self._by_code.get(code)
        return place.name if place else code

    def same_country(self, a: str, b: str) -> bool:
        """True, только если страна обоих городов известна и совпадает."""
        first, second = self._by_code.get(a), self._by_code.get(b)
        return bool(first and second and first.country_code and first.country_code == second.country_code)

    def search(self, text: str, limit: int = 6) -> list[Place]:
        """Города по названию (русскому или английскому) или по IATA-коду."""
        query = _normalize(text)
        if not query:
            return []
        exact, prefix = [], []
        code_match = self._by_code.get(query.upper()) if len(query) == 3 else None
        for place in self._by_code.values():
            names = [_normalize(n) for n in (place.name, *place.aliases)]
            if query in names:
                exact.append(place)
            elif any(n.startswith(query) for n in names):
                prefix.append(place)
        prefix.sort(key=lambda p: len(p.name))
        result = ([code_match] if code_match else []) + exact + prefix
        unique = list(dict.fromkeys(result))
        return unique[:limit]

    def resolve(self, text: str) -> Place | None:
        """Один город, если ответ однозначный: точный код или единственное точное название."""
        query = _normalize(text)
        if len(query) == 3 and query.upper() in self._by_code:
            return self._by_code[query.upper()]
        exact = [
            p for p in self._by_code.values()
            if query in (_normalize(n) for n in (p.name, *p.aliases))
        ]
        return exact[0] if len(exact) == 1 else None


def _cached_json(
    path: Path, url: str, urlopen: Callable[..., Any], clock: Callable[[], float]
) -> Any | None:
    fresh = path.exists() and clock() - path.stat().st_mtime < MAX_AGE_SECONDS
    if not fresh:
        try:
            log.info("загружаю %s", url)
            request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
            with urlopen(request, timeout=60) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            json.loads(body)  # не сохраняем на диск битый ответ
            path.write_bytes(body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("не удалось загрузить %s: %s", url, e)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_bytes())
    except ValueError:
        return None

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

BUILTIN = {
    "ALA": ("Алматы", "Казахстан"),
    "NQZ": ("Астана", "Казахстан"),
    "CIT": ("Шымкент", "Казахстан"),
    "SCO": ("Актау", "Казахстан"),
    "AKX": ("Актобе", "Казахстан"),
    "KGF": ("Караганда", "Казахстан"),
    "GUW": ("Атырау", "Казахстан"),
    "UKK": ("Усть-Каменогорск", "Казахстан"),
    "KSN": ("Костанай", "Казахстан"),
    "PWQ": ("Павлодар", "Казахстан"),
    "URA": ("Уральск", "Казахстан"),
    "KZO": ("Кызылорда", "Казахстан"),
    "DMB": ("Тараз", "Казахстан"),
    "PLX": ("Семей", "Казахстан"),
    "HSA": ("Туркестан", "Казахстан"),
    "IST": ("Стамбул", "Турция"),
    "AYT": ("Анталья", "Турция"),
    "DXB": ("Дубай", "ОАЭ"),
    "TBS": ("Тбилиси", "Грузия"),
    "TAS": ("Ташкент", "Узбекистан"),
    "FRU": ("Бишкек", "Киргизия"),
    "BAK": ("Баку", "Азербайджан"),
    "EVN": ("Ереван", "Армения"),
    "MOW": ("Москва", "Россия"),
    "LED": ("Санкт-Петербург", "Россия"),
    "BKK": ("Бангкок", "Таиланд"),
    "HKT": ("Пхукет", "Таиланд"),
    "SEL": ("Сеул", "Южная Корея"),
    "BJS": ("Пекин", "Китай"),
    "URC": ("Урумчи", "Китай"),
    "DEL": ("Дели", "Индия"),
    "SSH": ("Шарм-эль-Шейх", "Египет"),
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
    aliases: tuple[str, ...] = ()  # другие написания, например английское

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}" if self.country else f"{self.name} ({self.code})"


class Places:
    def __init__(self, places: Iterable[Place]) -> None:
        self._by_code = {p.code: p for p in places}

    @classmethod
    def builtin(cls) -> Places:
        return cls(Place(code, name, country) for code, (name, country) in BUILTIN.items())

    @classmethod
    def from_json(cls, cities: list[dict[str, Any]], countries: list[dict[str, Any]]) -> Places:
        country_names = {c.get("code"): c.get("name") for c in countries if c.get("code")}
        places = {code: Place(code, name, country) for code, (name, country) in BUILTIN.items()}
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
                country=country_names.get(country_code) or country_code,
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

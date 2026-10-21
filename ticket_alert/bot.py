"""Telegram-бот: направления выбираются кнопками прямо в чате."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from datetime import date
from html import escape
from typing import Any, Callable

from .checker import PriceSource, check_route, relevant_offers, typical_price
from .dates import describe_departure, is_expired, parse_departure, upcoming_months
from .formatting import (
    about_price,
    cheapest_by_destination,
    currency_symbol,
    describe_route,
    format_cheapest,
    format_price,
    route_title,
)
from .models import SMART_DISCOUNT, Offer, Route
from .places import POPULAR_DESTINATIONS, POPULAR_ORIGINS, Places
from .storage import Storage
from .telegram import Button, NotifyError, TelegramAPI, inline_keyboard
from .travelpayouts import SourceError

log = logging.getLogger(__name__)

MENU_ADD = "➕ Добавить направление"
MENU_LIST = "📋 Мои направления"
MENU_CHEAP = "🔥 Куда дёшево"
MENU_CHECK = "🔄 Проверить сейчас"

MAIN_KEYBOARD = {
    "keyboard": [[{"text": MENU_ADD}, {"text": MENU_LIST}], [{"text": MENU_CHEAP}, {"text": MENU_CHECK}]],
    "resize_keyboard": True,
    "is_persistent": True,
}

COMMANDS = [
    ("add", "добавить направление"),
    ("list", "мои направления"),
    ("cheap", "самые дешёвые билеты куда угодно"),
    ("check", "проверить цены сейчас"),
    ("cancel", "отменить ввод"),
    ("help", "как пользоваться"),
]

HELP = (
    "Я слежу за ценами на авиабилеты и пишу, когда появляется билет не дороже вашего бюджета.\n\n"
    f"{MENU_ADD} — выбрать, откуда и куда лететь (можно «🌍 куда угодно за границу»), месяц, "
    "в одну сторону или туда-обратно, и бюджет. Вместо бюджета можно выбрать «🧠 только выгодные цены» — "
    "тогда я сам слежу за обычной ценой и пишу, когда билет заметно дешевле. "
    "Направлений может быть несколько.\n"
    f"{MENU_LIST} — посмотреть и удалить направления.\n"
    f"{MENU_CHEAP} — самые дешёвые билеты из вашего города за границу.\n"
    f"{MENU_CHECK} — проверить цены, не дожидаясь плановой проверки.\n\n"
    "Цены берутся из недавних поисков на Aviasales, поэтому перед покупкой проверяйте их по ссылке."
)

ANYWHERE = "*"

# Поездка туда-обратно: (минимум дней, максимум дней, надпись на кнопке).
TRIPS = {
    "weekend": (2, 4, "🔁 Выходные, 2–4 дня"),
    "week": (5, 9, "🔁 Неделя, 5–9 дней"),
    "two": (10, 16, "🔁 Две недели, 10–16 дней"),
    "any": (2, 30, "🔁 Туда-обратно, любой срок"),
}
MAX_ROUTES_PER_CHAT = 20
CANCEL_BUTTON: Button = ("✖ Отмена", "x")

# Какой шаг диалога ждёт нажатия кнопки с этим префиксом.
_CALLBACK_STEPS = {
    "o": "origin",
    "d": "destination",
    "m": "month",
    "t": "trip",
    "b": "budget",
    "s": "confirm",
    "w": "results",
    "r": "results",
}


def parse_amount(text: str) -> int | None:
    """«60000», «60 000 ₸», «60к» → 60000."""
    cleaned = re.sub(r"[\s  .,]|₸|тенге|тг|kzt|руб|₽|\$|€", "", text.lower())
    match = re.fullmatch(r"(\d{1,9})(к|k|тыс)?", cleaned)
    if not match:
        return None
    amount = int(match[1]) * (1000 if match[2] else 1)
    return amount or None


def budget_options(price: int) -> list[tuple[int, int]]:
    """Подсказки бюджета ниже текущей цены: (процент скидки, сумма)."""
    step = 1000 if price >= 20000 else 100
    options = []
    for percent in (10, 20, 30):
        amount = price * (100 - percent) // 100 // step * step
        if 0 < amount < price and amount not in (a for _, a in options):
            options.append((percent, amount))
    return options


def _rows(buttons: list[Button], columns: int) -> list[list[Button]]:
    return [buttons[i : i + columns] for i in range(0, len(buttons), columns)]


class Bot:
    def __init__(
        self,
        api: TelegramAPI,
        storage: Storage,
        source: PriceSource,
        places: Places,
        currency: str,
        owner_chat_id: int | None,
        public: bool = False,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.api = api
        self.storage = storage
        self.source = source
        self.places = places
        self.currency = currency
        self.owner_chat_id = owner_chat_id
        self.public = public
        self.today = today
        # Незаконченные диалоги «добавить направление» / «куда дёшево» по чатам.
        self.dialogs: dict[int, dict[str, Any]] = {}

    # --- входящие события

    def handle_update(self, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update:
                self._on_callback(update["callback_query"])
            elif "message" in update:
                self._on_message(update["message"])
        except NotifyError as e:
            log.error("%s", e)
        except Exception:
            # Ошибка в одной команде не должна останавливать бота.
            log.exception("ошибка при обработке сообщения")

    def _allowed(self, chat_id: int) -> bool:
        return (
            self.public
            or chat_id == self.owner_chat_id
            or self.storage.user_status(chat_id) == "allowed"
        )

    def _on_message(self, message: dict[str, Any]) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()
        if not self._allowed(chat_id):
            self._request_access(chat_id, message.get("from") or {})
            return

        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else text
        actions: dict[str, Callable[[int], None]] = {
            "/start": self._welcome,
            "/help": self._welcome,
            "/menu": self._welcome,
            "/add": self._start_add,
            MENU_ADD: self._start_add,
            "/list": self._show_list,
            MENU_LIST: self._show_list,
            "/cheap": self._start_cheap,
            MENU_CHEAP: self._start_cheap,
            "/check": self._check_now,
            MENU_CHECK: self._check_now,
            "/cancel": self._cancel,
        }
        if chat_id == self.owner_chat_id:
            actions["/users"] = self._show_users
        if command in actions:
            actions[command](chat_id)
            return

        dialog = self.dialogs.get(chat_id)
        step = dialog["step"] if dialog else None
        if step in ("origin", "destination"):
            self._city_typed(chat_id, dialog, text)
        elif step == "month":
            value = parse_departure(text, self.today())
            if value:
                self._set_departure(chat_id, dialog, value)
            else:
                self.api.send_message(
                    chat_id,
                    "Не понял дату. Напишите, например, «05.12» или «декабрь», либо выберите месяц кнопкой.",
                )
        elif step == "budget":
            amount = parse_amount(text)
            if amount:
                self._set_budget(chat_id, dialog, amount)
            else:
                self.api.send_message(chat_id, "Напишите сумму числом, например 60000.")
        elif step:
            self.api.send_message(chat_id, "Нажмите кнопку под сообщением выше или /cancel.")
        else:
            self.api.send_message(chat_id, "Выберите действие в меню 👇", MAIN_KEYBOARD)

    def _on_callback(self, query: dict[str, Any]) -> None:
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        notice = None
        try:
            if chat_id is None or not self._allowed(chat_id):
                notice = "Это личный бот."
            else:
                notice = self._dispatch(chat_id, message.get("message_id"), query.get("data") or "")
        finally:
            # Убираем «часики» на кнопке, даже если что-то пошло не так.
            self.api.answer_callback(query["id"], notice)

    def _dispatch(self, chat_id: int, message_id: int | None, data: str) -> str | None:
        kind, _, value = data.partition(":")
        if kind in ("acc", "rej", "rm"):
            if chat_id != self.owner_chat_id:
                return "Это может только владелец бота"
            return self._decide(message_id, kind, value)
        if kind == "add":
            self._start_add(chat_id)
            return None
        if kind == "del":
            return self._delete(chat_id, message_id, value)
        if kind == "x":
            self._cancel(chat_id, message_id)
            return None

        dialog = self.dialogs.get(chat_id)
        if dialog is None or dialog["step"] != _CALLBACK_STEPS.get(kind):
            return "Это меню устарело — начните заново"

        if kind == "o":
            self._set_origin(chat_id, dialog, value, message_id)
        elif kind == "d":
            self._set_destination(chat_id, dialog, None if value == ANYWHERE else value, message_id)
        elif kind == "m":
            self._set_departure(chat_id, dialog, value, message_id)
        elif kind == "t":
            return self._set_trip(chat_id, dialog, value, message_id)
        elif kind == "b":
            if value == "smart":
                self._set_smart(chat_id, dialog, message_id)
            else:
                self._set_budget(chat_id, dialog, int(value), message_id)
        elif kind == "s":
            self._save(chat_id, dialog, value == "direct", message_id)
        elif kind == "w":
            # Из подборки «куда дёшево» — сразу подписаться на неё.
            dialog.update(mode="add", destination=None, step="budget")
            self._ask_budget(chat_id, dialog)
        elif kind == "r":
            dialog["step"] = "month"
            self._ask_month(chat_id, dialog)
        return None

    # --- общие экраны

    def _show(
        self, chat_id: int, message_id: int | None, text: str, keyboard: dict[str, Any] | None = None
    ) -> None:
        """Нажали кнопку — меняем то же сообщение, написали текст — отвечаем новым."""
        if message_id is None:
            self.api.send_message(chat_id, text, keyboard)
        else:
            self.api.edit_message(chat_id, message_id, text, keyboard)

    def _welcome(self, chat_id: int) -> None:
        self.dialogs.pop(chat_id, None)
        self.api.send_message(chat_id, HELP, MAIN_KEYBOARD)

    def _cancel(self, chat_id: int, message_id: int | None = None) -> None:
        self.dialogs.pop(chat_id, None)
        if message_id is None:
            self.api.send_message(chat_id, "Отменено.", MAIN_KEYBOARD)
        else:
            self.api.edit_message(chat_id, message_id, "Отменено.")

    # --- диалог: откуда → куда → когда → бюджет → сохранить

    def _start_add(self, chat_id: int) -> None:
        self.dialogs[chat_id] = {"mode": "add", "step": "origin"}
        self._ask_origin(chat_id)

    def _start_cheap(self, chat_id: int) -> None:
        self.dialogs[chat_id] = {"mode": "cheap", "step": "origin"}
        self._ask_origin(chat_id)

    def _city_keyboard(self, prefix: str, codes: list[str], first: Button | None = None) -> dict[str, Any]:
        rows = _rows([(self.places.name(c), f"{prefix}:{c}") for c in codes], 3)
        if first:
            rows.insert(0, [first])
        rows.append([CANCEL_BUTTON])
        return inline_keyboard(rows)

    def _ask_origin(self, chat_id: int) -> None:
        home = self.storage.home(chat_id)
        codes = list(dict.fromkeys(([home] if home else []) + POPULAR_ORIGINS))[:9]
        self.api.send_message(
            chat_id,
            "Откуда летим? Выберите город или напишите его название.",
            self._city_keyboard("o", codes),
        )

    def _city_typed(self, chat_id: int, dialog: dict[str, Any], text: str) -> None:
        origin_step = dialog["step"] == "origin"
        place = self.places.resolve(text)
        if place:
            if origin_step:
                self._set_origin(chat_id, dialog, place.code)
            else:
                self._set_destination(chat_id, dialog, place.code)
            return

        matches = self.places.search(text)
        if not matches:
            self.api.send_message(
                chat_id, "Не нашёл такой город. Попробуйте написать иначе, например «Стамбул» или код IST."
            )
            return
        prefix = "o" if origin_step else "d"
        rows = [[(p.label, f"{prefix}:{p.code}")] for p in matches] + [[CANCEL_BUTTON]]
        self.api.send_message(chat_id, "Какой именно?", inline_keyboard(rows))

    def _set_origin(self, chat_id: int, dialog: dict[str, Any], code: str, message_id: int | None = None) -> None:
        dialog["origin"] = code
        self.storage.set_home(chat_id, code)
        if dialog["mode"] == "cheap":
            dialog["step"] = "month"
            self._ask_month(chat_id, dialog, message_id)
            return

        dialog["step"] = "destination"
        codes = [c for c in POPULAR_DESTINATIONS if c != code][:9]
        text = (
            f"Из {escape(self.places.name(code))}. Куда летим?\n"
            "Выберите город, «Куда угодно за границу» или напишите название."
        )
        anywhere: Button = ("🌍 Куда угодно за границу", f"d:{ANYWHERE}")
        self._show(chat_id, message_id, text, self._city_keyboard("d", codes, first=anywhere))

    def _set_destination(
        self, chat_id: int, dialog: dict[str, Any], code: str | None, message_id: int | None = None
    ) -> None:
        if code == dialog["origin"]:
            self.api.send_message(chat_id, "Город прилёта совпадает с городом вылета — выберите другой.")
            return
        dialog["destination"] = code
        dialog["step"] = "month"
        self._ask_month(chat_id, dialog, message_id)

    def _draft_title(self, dialog: dict[str, Any]) -> str:
        origin = escape(self.places.name(dialog["origin"]))
        if dialog["mode"] == "cheap":
            return f"Из {origin} за границу"
        destination = dialog.get("destination")
        return f"{origin} → {escape(self.places.name(destination)) if destination else 'за границу'}"

    def _ask_month(self, chat_id: int, dialog: dict[str, Any], message_id: int | None = None) -> None:
        months = upcoming_months(self.today())
        buttons = [(describe_departure(m).capitalize(), f"m:{m}") for m in months]
        text = (
            f"{self._draft_title(dialog)}\n\n"
            "Когда летим? Выберите месяц или напишите дату, например 05.12."
        )
        self._show(chat_id, message_id, text, inline_keyboard(_rows(buttons, 2) + [[CANCEL_BUTTON]]))

    def _set_departure(
        self, chat_id: int, dialog: dict[str, Any], value: str, message_id: int | None = None
    ) -> None:
        dialog["departure"] = value
        if dialog.get("trip_chosen"):  # «другой месяц» в подборке — длительность уже знаем
            self._after_trip(chat_id, dialog, message_id)
            return
        dialog["step"] = "trip"
        text = (
            f"{self._draft_title(dialog)}, {escape(describe_departure(value))}\n\n"
            "В одну сторону или туда-обратно?"
        )
        rows = [[("➡️ В одну сторону", "t:oneway")]]
        rows += [[(label, f"t:{key}")] for key, (_, _, label) in TRIPS.items()]
        self._show(chat_id, message_id, text, inline_keyboard(rows + [[CANCEL_BUTTON]]))

    def _set_trip(self, chat_id: int, dialog: dict[str, Any], value: str, message_id: int | None) -> str | None:
        if value == "oneway":
            dialog["min_days"] = dialog["max_days"] = None
        elif value in TRIPS:
            dialog["min_days"], dialog["max_days"], _ = TRIPS[value]
        else:
            return "Это меню устарело — начните заново"
        dialog["trip_chosen"] = True
        self._after_trip(chat_id, dialog, message_id)
        return None

    def _after_trip(self, chat_id: int, dialog: dict[str, Any], message_id: int | None) -> None:
        if dialog["mode"] == "cheap":
            self._show_cheapest(chat_id, dialog, message_id)
        else:
            dialog["step"] = "budget"
            self._ask_budget(chat_id, dialog, message_id)

    def _draft_route(self, dialog: dict[str, Any], chat_id: int = 0) -> Route:
        return Route(
            origin=dialog["origin"],
            destination=dialog.get("destination"),
            departure=dialog["departure"],
            max_price=dialog.get("max_price", 0),
            chat_id=chat_id,
            min_days=dialog.get("min_days"),
            max_days=dialog.get("max_days"),
            smart=dialog.get("smart", False),
        )

    def _search(self, route: Route) -> list[Offer] | None:
        try:
            return self.source.search(route)
        except SourceError as e:
            log.error("поиск цен: %s", e)
            return None

    def _ask_budget(self, chat_id: int, dialog: dict[str, Any], message_id: int | None = None) -> None:
        route = self._draft_route(dialog)
        offers = self._search(route)
        if offers is not None:
            offers = relevant_offers(route, offers, self.places)
        title = f"<b>{escape(route_title(route, self.places.name))}</b>"
        symbol = currency_symbol(self.currency)
        rows: list[list[Button]] = []

        if offers:
            cheapest = min(offers, key=lambda o: o.price)
            where = f"{escape(self.places.name(cheapest.destination))}, " if route.anywhere else ""
            dates = f"{cheapest.departure_at:%d.%m}"
            if cheapest.return_at:
                dates += f"–{cheapest.return_at:%d.%m}"
            typical = None if route.anywhere else typical_price(offers)
            usually = f"\nОбычно на эти даты — {about_price(typical, self.currency)}." if typical else ""
            smart_hint = (
                f"\n🧠 «Только выгодные» — я сам слежу за обычной ценой и пишу, "
                f"когда билет дешевле неё хотя бы на {SMART_DISCOUNT}%."
                if typical
                else ""
            )
            text = (
                f"{title}\n\n"
                f"Сейчас самый дешёвый билет: <b>{format_price(cheapest.price, self.currency)}</b>, "
                f"{where}{dates}.{usually}\n\n"
                "Напишу, когда появится билет не дороже вашего бюджета. "
                f"Выберите бюджет или напишите сумму в {symbol}:{smart_hint}"
            )
            buttons = [
                (f"до {format_price(amount, self.currency)} (−{percent}%)", f"b:{amount}")
                for percent, amount in budget_options(cheapest.price)
            ]
            buttons.append((f"до {format_price(cheapest.price, self.currency)} (уже есть)", f"b:{cheapest.price}"))
            rows = _rows(buttons, 2)
            if typical:
                rows.insert(0, [("🧠 Только выгодные цены", "b:smart")])
        elif offers is None:
            text = f"{title}\n\nНе получилось узнать текущие цены. Напишите бюджет в {symbol}, например 60000:"
        else:
            text = (
                f"{title}\n\nСейчас в базе Aviasales нет билетов на эти даты. "
                f"Напишите бюджет в {symbol} — я сообщу, когда они появятся:"
            )
        self._show(chat_id, message_id, text, inline_keyboard(rows + [[CANCEL_BUTTON]]))

    def _set_budget(self, chat_id: int, dialog: dict[str, Any], amount: int, message_id: int | None = None) -> None:
        dialog.update(max_price=amount, smart=False)
        self._ask_confirm(chat_id, dialog, message_id)

    def _set_smart(self, chat_id: int, dialog: dict[str, Any], message_id: int | None = None) -> None:
        dialog.update(max_price=0, smart=True)
        self._ask_confirm(chat_id, dialog, message_id)

    def _ask_confirm(self, chat_id: int, dialog: dict[str, Any], message_id: int | None = None) -> None:
        dialog["step"] = "confirm"
        route = self._draft_route(dialog)
        text = f"Следить за направлением?\n\n<b>{escape(describe_route(route, self.places.name, self.currency))}</b>"
        keyboard = inline_keyboard(
            [
                [("✅ Сохранить", "s:all")],
                [("✅ Сохранить, только прямые рейсы", "s:direct")],
                [CANCEL_BUTTON],
            ]
        )
        self._show(chat_id, message_id, text, keyboard)

    def _save(self, chat_id: int, dialog: dict[str, Any], direct_only: bool, message_id: int | None) -> None:
        self.dialogs.pop(chat_id, None)
        if self.storage.count_routes(chat_id) >= MAX_ROUTES_PER_CHAT:
            self._show(
                chat_id,
                message_id,
                f"У вас уже {MAX_ROUTES_PER_CHAT} направлений — удалите лишние в «{MENU_LIST}».",
            )
            return

        route = self.storage.add_route(replace(self._draft_route(dialog, chat_id), direct_only=direct_only))
        text = (
            f"✅ Слежу: <b>{escape(describe_route(route, self.places.name, self.currency))}</b>\n"
            + (
                "Напишу, как только цена будет заметно ниже обычной."
                if route.smart
                else "Напишу, как только найду билет не дороже бюджета."
            )
        )
        self._show(chat_id, message_id, text, inline_keyboard([[("➕ Ещё направление", "add")]]))
        # Если подходящие билеты уже есть — присылаем сразу, не дожидаясь плановой проверки.
        try:
            check_route(route, self.source, self.storage, self.api, self.currency, self.places)
        except (SourceError, NotifyError) as e:
            log.error("проверка нового направления: %s", e)

    # --- «куда дёшево»

    def _show_cheapest(self, chat_id: int, dialog: dict[str, Any], message_id: int | None = None) -> None:
        dialog["step"] = "results"
        route = replace(self._draft_route(dialog), destination=None)
        offers = self._search(route)
        if offers is not None:
            offers = relevant_offers(route, offers, self.places)
        if offers is None:
            text = "Не получилось получить цены, попробуйте чуть позже."
        else:
            text = format_cheapest(route, offers, self.currency, self.places.name)
        rows: list[list[Button]] = [[("📅 Другой месяц", "r")]]
        if offers and cheapest_by_destination(offers):
            rows.insert(0, [("🔔 Следить за дешёвыми билетами за границу", "w")])
        self._show(chat_id, message_id, text, inline_keyboard(rows))

    # --- доступ для других людей

    def _request_access(self, chat_id: int, user: dict[str, Any]) -> None:
        status = self.storage.user_status(chat_id)
        if status == "rejected":
            self.api.send_message(chat_id, "Владелец бота не открыл вам доступ.")
            return
        if status == "pending":
            self.api.send_message(chat_id, "Запрос уже у владельца бота — как только он разрешит, я напишу.")
            return

        name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or "Без имени"
        if user.get("username"):
            name += f" (@{user['username']})"
        self.storage.set_user(chat_id, name, "pending")
        self.api.send_message(
            chat_id, "Это личный бот. Я отправил владельцу запрос на доступ — как только он разрешит, я напишу."
        )
        if self.owner_chat_id is not None:
            self.api.send_message(
                self.owner_chat_id,
                f"👤 <b>{escape(name)}</b> (ID {chat_id}) хочет пользоваться ботом.",
                inline_keyboard([[("✅ Разрешить", f"acc:{chat_id}"), ("✖ Отклонить", f"rej:{chat_id}")]]),
            )

    def _decide(self, message_id: int | None, kind: str, value: str) -> str | None:
        user_id = int(value)
        name = escape(self.storage.user_name(user_id) or str(user_id))
        if kind == "acc":
            self.storage.set_user(user_id, self.storage.user_name(user_id) or str(user_id), "allowed")
            self._show(self.owner_chat_id, message_id, f"✅ {name} теперь может пользоваться ботом. Список — /users")
            self.api.send_message(user_id, "Доступ открыт! 🎉\n\n" + HELP, MAIN_KEYBOARD)
            return None
        if kind == "rej":
            self.storage.set_user(user_id, self.storage.user_name(user_id) or str(user_id), "rejected")
            self._show(self.owner_chat_id, message_id, f"Отклонено: {name}")
            return None
        # rm: забрать доступ. Запись удаляем, чтобы человек мог попросить снова.
        raw_name = self.storage.user_name(user_id) or str(user_id)
        self.storage.forget_user(user_id)
        self.storage.delete_routes(user_id)
        self.dialogs.pop(user_id, None)
        self._show_users(self.owner_chat_id, message_id)
        return f"Доступ закрыт: {raw_name}"

    def _show_users(self, chat_id: int, message_id: int | None = None) -> None:
        users = self.storage.users("allowed")
        if not users:
            text = (
                "Кроме вас, ботом никто не пользуется.\n\n"
                "Чтобы дать доступ, пусть человек напишет боту /start — вам придёт запрос с кнопкой «Разрешить»."
            )
            self._show(chat_id, message_id, text)
            return
        lines = ["<b>Кто ещё пользуется ботом:</b>", ""]
        lines += [f"• {escape(name)} — направлений: {self.storage.count_routes(user_id)}" for user_id, name in users]
        lines += ["", "Чтобы закрыть доступ, нажмите кнопку с именем."]
        rows = [[(f"🚫 {name}", f"rm:{user_id}")] for user_id, name in users]
        self._show(chat_id, message_id, "\n".join(lines), inline_keyboard(rows))

    # --- список направлений

    def _show_list(self, chat_id: int, message_id: int | None = None) -> None:
        routes = self.storage.routes(chat_id)
        if not routes:
            self._show(
                chat_id,
                message_id,
                "Вы пока ни за чем не следите.",
                inline_keyboard([[("➕ Добавить направление", "add")]]),
            )
            return
        lines = ["<b>Ваши направления:</b>", ""]
        lines += [
            f"{i}. {escape(describe_route(r, self.places.name, self.currency))}"
            for i, r in enumerate(routes, 1)
        ]
        lines += ["", "Чтобы удалить направление, нажмите 🗑 с его номером."]
        buttons = [(f"🗑 {i}", f"del:{r.id}") for i, r in enumerate(routes, 1)]
        rows = _rows(buttons, 5) + [[("➕ Добавить направление", "add")]]
        self._show(chat_id, message_id, "\n".join(lines), inline_keyboard(rows))

    def _delete(self, chat_id: int, message_id: int | None, value: str) -> str:
        try:
            deleted = self.storage.delete_route(chat_id, int(value))
        except ValueError:
            deleted = False
        self._show_list(chat_id, message_id)
        return "Удалено" if deleted else "Уже удалено"

    # --- проверка по кнопке

    def _check_now(self, chat_id: int) -> None:
        today = self.today()
        routes = [r for r in self.storage.routes(chat_id) if not is_expired(r.departure, today)]
        if not routes:
            self.api.send_message(
                chat_id,
                "Вы пока ни за чем не следите.",
                inline_keyboard([[("➕ Добавить направление", "add")]]),
            )
            return

        self.api.send_message(chat_id, "Проверяю цены…")
        found = failed = 0
        for route in routes:
            try:
                found += len(
                    check_route(route, self.source, self.storage, self.api, self.currency, self.places)
                )
            except (SourceError, NotifyError) as e:
                failed += 1
                log.error("проверка по кнопке: %s", e)
        if failed:
            self.api.send_message(chat_id, f"Не получилось проверить направлений: {failed}. Попробуйте позже.")
        elif not found:
            self.api.send_message(
                chat_id, "Новых или подешевевших билетов нет. О тех, что уже были, я писал раньше."
            )


def serve(
    api: TelegramAPI,
    bot: Bot,
    check: Callable[[], Any],
    interval_minutes: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Главный цикл: принимает сообщения и по расписанию проверяет цены."""
    me = api.call("getMe")
    api.set_commands(COMMANDS)
    log.info("бот @%s запущен — откройте его в Telegram и нажмите /start", me.get("username"))

    offset = None
    next_check = clock()
    while True:
        if clock() >= next_check:
            check()
            next_check = clock() + interval_minutes * 60
        wait = int(max(1, min(25, next_check - clock())))
        try:
            updates = api.get_updates(offset, wait)
        except NotifyError as e:
            log.warning("не удалось получить сообщения: %s", e)
            sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            bot.handle_update(update)

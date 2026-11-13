"""AutoPikett - Fenster, Systray und Ausführung des Zeitplans."""

from __future__ import annotations

import logging
import queue
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

from autopikett import autostart, core, dialer, holidays, telephony
from autopikett.core import (
    ACTION_LABELS,
    ACTION_OFF,
    ACTION_ON,
    SLOTS_PER_DAY,
    WEEKDAYS,
    Settings,
    Slot,
)

logger = logging.getLogger("autopikett")

CHECK_INTERVAL_S = 20
EVENT_INTERVAL_MS = 150
STARTUP_UPDATE_DELAY_MS = 5000
INSTANCE_PORT = 49876
# So oft wird geprüft, ob Enterprise Telephony noch läuft.
TELEPHONY_CHECK_INTERVAL_S = 60

# Farbe für Wochentage, an denen der nächste Termin ein Feiertag ist.
HOLIDAY_COLOR = "#c0392b"


def resource_path(*parts: str) -> Path:
    """Pfad zu mitgelieferten Dateien (Entwicklung und PyInstaller-Build)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


def setup_logging() -> None:
    log_dir = core.user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "autopikett.log",
        maxBytes=512_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)


def acquire_instance_lock(port: int = INSTANCE_PORT) -> socket.socket | None:
    """Verhindert zwei parallele Instanzen (sonst doppelte Anrufe)."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", port))
        lock.listen(1)
    except OSError:
        lock.close()
        return None
    return lock


class AutoPikettApp:
    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self.settings_file = core.settings_path()
        self.history_file = core.history_path()
        # Beim Start gleich Einträge entfernen, die älter als zwei Wochen sind.
        self.history: list[core.HistoryEntry] = core.prune_history()

        self._settings_lock = threading.RLock()
        self._dial_lock = threading.Lock()
        self._telephony_lock = threading.Lock()
        self._stop = threading.Event()
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._tray = None
        self._updating = False
        self._holidays_loading = False
        self._holidays_ready = threading.Event()
        self._holiday_days: frozenset[date] = frozenset()
        self._holiday_log_day: date | None = None

        self.day_vars: dict[str, tk.BooleanVar] = {}
        self.time_vars: dict[str, list[tk.StringVar]] = {}
        self.action_vars: dict[str, list[tk.StringVar]] = {}
        self.day_checks: dict[str, tk.Checkbutton] = {}
        self._day_text_color = ""

        self._build_ui()
        self._setup_tray()
        self._refresh_tray(self.settings.pikett_on)
        self._refresh_status()

        self.root.after(EVENT_INTERVAL_MS, self._poll_events)
        self.root.after(STARTUP_UPDATE_DELAY_MS, self._auto_update_tick)
        self.root.after(EVENT_INTERVAL_MS, self._load_holidays)
        self.root.after(holidays.milliseconds_until_midnight(), self._holidays_tick)

        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        if sys.platform == "win32":
            threading.Thread(target=self._telephony_loop, daemon=True).start()

    # --------------------------------------------------------
    # AUFBAU DER OBERFLÄCHE
    # --------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.title(f"{core.APP_NAME} v{core.APP_VERSION}")
        self.root.geometry("1380x860")
        self.root.minsize(1180, 760)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        numbers = ttk.LabelFrame(outer, text="Telefonnummern", padding=12)
        numbers.pack(fill="x")
        numbers.columnconfigure(1, weight=1)

        ttk.Label(numbers, text="Pikett EIN (einschalten):").grid(
            row=0, column=0, sticky="w"
        )
        self.on_number_var = tk.StringVar(value=self.settings.on_number)
        ttk.Entry(numbers, textvariable=self.on_number_var, width=26).grid(
            row=0, column=1, sticky="w", padx=(8, 28)
        )

        ttk.Label(numbers, text="Pikett AUS (ausschalten):").grid(
            row=0, column=2, sticky="w"
        )
        self.off_number_var = tk.StringVar(value=self.settings.off_number)
        ttk.Entry(numbers, textvariable=self.off_number_var, width=26).grid(
            row=0, column=3, sticky="w", padx=(8, 0)
        )

        week = ttk.LabelFrame(
            outer, text="Woche - vier Umschaltungen pro aktivem Tag", padding=12
        )
        week.pack(fill="both", expand=True, pady=12)

        for row, day in enumerate(WEEKDAYS):
            plan = self.settings.days[day]

            day_var = tk.BooleanVar(value=plan.enabled)
            self.day_vars[day] = day_var
            # tk statt ttk: nur so lässt sich die Schrift an Feiertagen rot färben.
            check = tk.Checkbutton(
                week,
                text=day,
                variable=day_var,
                anchor="w",
                justify="left",
                background=self.root.cget("background"),
                activebackground=self.root.cget("background"),
                highlightthickness=0,
                borderwidth=0,
            )
            check.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 18))
            self.day_checks[day] = check
            if not self._day_text_color:
                self._day_text_color = str(check.cget("foreground"))

            self.time_vars[day] = []
            self.action_vars[day] = []
            for index in range(SLOTS_PER_DAY):
                slot = plan.slots[index]
                cell = ttk.Frame(week)
                cell.grid(row=row, column=1 + index, sticky="w", padx=(0, 16), pady=5)

                ttk.Label(cell, text=f"{index + 1}.").pack(side="left")

                time_var = tk.StringVar(value=slot.time)
                ttk.Entry(cell, textvariable=time_var, width=7).pack(
                    side="left", padx=(4, 6)
                )

                action_var = tk.StringVar(value=ACTION_LABELS[slot.action])
                ttk.Combobox(
                    cell,
                    textvariable=action_var,
                    values=(ACTION_LABELS[ACTION_ON], ACTION_LABELS[ACTION_OFF]),
                    state="readonly",
                    width=5,
                ).pack(side="left")

                self.time_vars[day].append(time_var)
                self.action_vars[day].append(action_var)

        self._build_holidays(week)

        footer = ttk.Frame(outer)
        footer.pack(fill="x")

        ttk.Button(footer, text="Speichern", command=self.on_save).pack(side="left")
        ttk.Button(footer, text="Ins Systray", command=self._hide_to_tray).pack(
            side="left", padx=8
        )

        self.auto_update_var = tk.BooleanVar(value=self.settings.auto_update)
        ttk.Checkbutton(
            footer,
            text="Automatisch nach Updates suchen",
            variable=self.auto_update_var,
            command=self._on_auto_update_toggle,
        ).pack(side="left", padx=8)

        self.autostart_var = tk.BooleanVar(value=self.settings.autostart)
        ttk.Checkbutton(
            footer,
            text="Mit Windows starten",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
        ).pack(side="left", padx=8)

        ttk.Label(
            footer, text="Grüner Punkt = Pikett ein · Roter Punkt = Pikett aus"
        ).pack(side="right")

        self.status_var = tk.StringVar()
        ttk.Label(
            outer, textvariable=self.status_var, font=("Segoe UI", 10, "bold")
        ).pack(fill="x", pady=(12, 0))

        self._build_history(outer)

        ttk.Label(
            outer,
            text=f"Gespeichert unter: {self.settings_file}",
            foreground="#666666",
        ).pack(fill="x", pady=(6, 0))

    def _build_history(self, parent: tk.Widget) -> None:
        """Status-Tabelle unten: genau ein Eintrag pro Auftrag."""
        frame = ttk.LabelFrame(
            parent, text="Status der Umschaltungen (ein Eintrag pro Auftrag)", padding=8
        )
        frame.pack(fill="x", pady=(12, 0))

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Label(toolbar, text="Neueste zuerst", foreground="#666666").pack(side="left")
        ttk.Button(
            toolbar, text="Verlauf leeren", command=self._clear_history
        ).pack(side="right")

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)

        columns = ("zeit", "auftrag", "nummer", "ausloeser", "ergebnis")
        headings = {
            "zeit": "Zeit",
            "auftrag": "Auftrag",
            "nummer": "Gewählte Nummer",
            "ausloeser": "Ausgelöst durch",
            "ergebnis": "Ergebnis",
        }
        widths = {
            "zeit": 150,
            "auftrag": 120,
            "nummer": 150,
            "ausloeser": 120,
            "ergebnis": 130,
        }

        self.history_tree = ttk.Treeview(
            body, columns=columns, show="headings", height=7, selectmode="browse"
        )
        for name in columns:
            self.history_tree.heading(name, text=headings[name])
            self.history_tree.column(
                name, width=widths[name], anchor="w", stretch=name == "ergebnis"
            )

        scroll = ttk.Scrollbar(
            body, orient="vertical", command=self.history_tree.yview
        )
        self.history_tree.configure(yscrollcommand=scroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.history_tree.tag_configure("failed", foreground="#b91c1c")

        for entry in self.history:
            self._insert_history_row(entry)

    def _insert_history_row(
        self, entry: core.HistoryEntry, index: str | int = "end"
    ) -> None:
        """Eine Zeile pro Auftrag in die Status-Tabelle schreiben."""
        values = (
            core.format_timestamp(entry.timestamp),
            entry.action_label(),
            entry.number,
            entry.source,
            entry.result_label(),
        )
        tags = () if entry.ok else ("failed",)
        self.history_tree.insert("", index, values=values, tags=tags)

    def _refresh_history_table(self, entries: list[core.HistoryEntry]) -> None:
        """Tabelle komplett neu aufbauen (z. B. nach dem Entfernen alter Einträge)."""
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for entry in entries:
            self._insert_history_row(entry)

    def _clear_history(self) -> None:
        if not messagebox.askyesno(
            core.APP_NAME, "Status-Tabelle wirklich leeren?"
        ):
            return

        try:
            core.clear_history(self.history_file)
        except OSError as error:
            logger.exception("Verlauf konnte nicht geleert werden")
            messagebox.showerror(
                core.APP_NAME, f"Verlauf konnte nicht geleert werden:\n{error}"
            )
            return

        self.history = []
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        logger.info("Status-Tabelle geleert.")

    def _set_window_icon(self) -> None:
        try:
            self.root.iconbitmap(default=str(resource_path("assets", "AutoPikett.ico")))
        except Exception:
            pass

    # --------------------------------------------------------
    # EINSTELLUNGEN LESEN UND SPEICHERN
    # --------------------------------------------------------

    def _collect_settings(self) -> Settings | None:
        """Liest die Oberfläche; zeigt bei ungültigen Zeiten einen Fehler."""
        days = {}
        for day in WEEKDAYS:
            slots: list[Slot] = []
            for index in range(SLOTS_PER_DAY):
                raw_time = self.time_vars[day][index].get()
                try:
                    slot_time = core.normalize_time(raw_time)
                except ValueError:
                    messagebox.showerror(
                        core.APP_NAME,
                        f"{day}, Zeit {index + 1}: „{raw_time}“ ist keine "
                        "gültige Uhrzeit (erwartet HH:MM).",
                    )
                    return None

                label = self.action_vars[day][index].get()
                action = ACTION_ON if label == ACTION_LABELS[ACTION_ON] else ACTION_OFF
                slots.append(Slot(slot_time, action))

            days[day] = core.DayPlan(
                enabled=bool(self.day_vars[day].get()), slots=slots
            )

        with self._settings_lock:
            previous = self.settings

        return Settings(
            on_number=self.on_number_var.get().strip() or core.DEFAULT_ON_NUMBER,
            off_number=self.off_number_var.get().strip() or core.DEFAULT_OFF_NUMBER,
            days=days,
            pikett_on=previous.pikett_on,
            auto_update=bool(self.auto_update_var.get()),
            autostart=bool(self.autostart_var.get()),
            last_runs=dict(previous.last_runs),
        )

    def _store(self, settings: Settings) -> None:
        with self._settings_lock:
            self.settings = settings
        core.save_settings(settings, self.settings_file)

    def on_save(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return
        self._store(settings)
        self._refresh_status()
        self.status_var.set(
            f"{self.status_var.get()} · gespeichert {datetime.now():%H:%M:%S}"
        )

    def _on_auto_update_toggle(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return
        self._store(settings)

    def _on_autostart_toggle(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return
        self._store(settings)

        if autostart.is_supported() and not autostart.apply(settings.autostart):
            self.status_var.set(
                "Autostart konnte nicht geändert werden (siehe Log)."
            )
            return
        self._refresh_status()

    def _build_holidays(self, parent: tk.Widget) -> None:
        """Rechts neben Mo-So die kommenden Feiertage von Balgach (SG)."""
        frame = ttk.LabelFrame(
            parent,
            text=(
                "Feiertage Balgach (SG) - Automatische Pikett-Umschaltungen "
                "werden an diesen Tagen übersprungen"
            ),
            padding=8,
        )
        frame.grid(
            row=0,
            column=1 + SLOTS_PER_DAY,
            rowspan=len(WEEKDAYS),
            sticky="nsew",
            padx=(8, 0),
        )
        parent.columnconfigure(1 + SLOTS_PER_DAY, weight=1)

        head = ttk.Frame(frame)
        head.pack(fill="x")
        ttk.Label(head, text="die nächsten Termine", foreground="#666666").pack(
            side="left"
        )
        ttk.Button(
            head, text="Aktualisieren", command=lambda: self._load_holidays(refresh=True)
        ).pack(side="right")

        self.holidays_text = tk.Text(
            frame,
            width=26,
            height=8,
            wrap="word",
            borderwidth=0,
            relief="flat",
            background=self.root.cget("background"),
            state="disabled",
        )
        self.holidays_text.pack(fill="both", expand=True, pady=(6, 0))

        self.holidays_status_var = tk.StringVar(value="lade …")
        ttk.Label(
            frame,
            textvariable=self.holidays_status_var,
            foreground="#666666",
        ).pack(fill="x", pady=(4, 0))

    def _load_holidays(self, refresh: bool = False) -> None:
        """Feiertage im Hintergrund laden, damit das Fenster nicht hängt.

        ``refresh`` holt den Export neu, statt die Wochendatei zu verwenden.
        """
        if self._holidays_loading:
            return
        self._holidays_loading = True
        self.holidays_status_var.set("lade …")
        threading.Thread(
            target=self._fetch_holidays, args=(refresh,), daemon=True
        ).start()

    def _fetch_holidays(self, refresh: bool = False) -> None:
        try:
            found = holidays.fetch_holidays(force=refresh)
        except Exception:  # noqa: BLE001 - die Liste darf die App nicht stören
            logger.exception("Feiertage konnten nicht geladen werden")
            found = None
        self._post("holidays", found)

    def _show_holidays(self, found: object) -> None:
        self._holidays_loading = False
        loaded = found if isinstance(found, list) else None

        if loaded is None:
            # Ohne Verbindung bleibt die zuletzt geladene Liste stehen.
            self.holidays_status_var.set("nicht geladen - Internetverbindung prüfen")
            self._holidays_ready.set()
            return

        with self._settings_lock:
            self._holiday_days = frozenset(entry.day for entry in loaded)

        self._mark_holiday_weekdays()

        upcoming = holidays.pick_upcoming(loaded)
        self.holidays_text.configure(state="normal")
        self.holidays_text.delete("1.0", "end")
        if upcoming:
            for entry in upcoming:
                self.holidays_text.insert("end", f"{entry.label()}\n")
        else:
            self.holidays_text.insert("end", "Keine Feiertage in den nächsten Wochen.")
        self.holidays_text.configure(state="disabled")
        self.holidays_status_var.set(f"Stand {datetime.now():%d.%m.%Y %H:%M}")
        self._holidays_ready.set()

        # Der nächste Wechsel kann sich durch einen Feiertag verschieben.
        self._refresh_status()

    def _mark_holiday_weekdays(self) -> None:
        """Wochentage rot schreiben, deren nächster Termin ein Feiertag ist."""
        with self._settings_lock:
            days = self._holiday_days

        marked = core.holiday_weekdays(days)
        for day, check in self.day_checks.items():
            color = HOLIDAY_COLOR if day in marked else self._day_text_color
            check.configure(foreground=color, activeforeground=color)

    def _holidays_tick(self) -> None:
        """Beim Tageswechsel neu laden, damit vergangene Tage verschwinden."""
        self._load_holidays()
        # Auch ohne neue Liste wandert die rote Markierung einen Tag weiter.
        self._mark_holiday_weekdays()
        if not self._stop.is_set():
            self.root.after(
                holidays.milliseconds_until_midnight(), self._holidays_tick
            )

    def _on_close(self) -> None:
        if self._updating:
            self.quit_app()
            return

        settings = self._collect_settings()
        if settings is None:
            return
        self._store(settings)
        self._hide_to_tray()

    # --------------------------------------------------------
    # ZEITPLAN
    # --------------------------------------------------------

    def _scheduler_loop(self) -> None:
        # Erst schalten, wenn die Feiertage bekannt sind (sonst würde an einem
        # Feiertag gleich nach dem Start noch gewählt).
        self._holidays_ready.wait(timeout=20)
        while not self._stop.is_set():
            try:
                now = datetime.now()
                with self._settings_lock:
                    holiday_days = self._holiday_days
                    if now.date() in holiday_days and core.due_slots(
                        self.settings, now
                    ):
                        if self._holiday_log_day != now.date():
                            self._holiday_log_day = now.date()
                            logger.info(
                                "%s ist ein Feiertag - die Umschaltungen werden "
                                "übersprungen.",
                                now.date(),
                            )
                    jobs = core.pending_jobs(self.settings, now, skip_days=holiday_days)
                    # Sofort als erledigt markieren, damit kein zweiter
                    # Durchlauf denselben Auftrag erneut wählt.
                    for key, _ in jobs:
                        self.settings.last_runs[key] = now.isoformat(timespec="seconds")
                    if jobs:
                        core.save_settings(self.settings, self.settings_file)

                for _, action in jobs:
                    threading.Thread(
                        target=self._run_job, args=(action,), daemon=True
                    ).start()
            except Exception:
                logger.exception("Zeitplan-Prüfung fehlgeschlagen")
            self._stop.wait(CHECK_INTERVAL_S)

    def _run_job(self, action: str, manual: bool = False) -> None:
        if not self._dial_lock.acquire(blocking=False):
            if manual:
                self._post("info", "Es läuft bereits ein Anruf.")
            return

        source = "Manuell" if manual else "Zeitplan"
        number = ""
        ok = False
        detail = ""

        try:
            with self._settings_lock:
                on_number = self.settings.on_number
                off_number = self.settings.off_number
            number = on_number if action == ACTION_ON else off_number

            label = ACTION_LABELS[action]
            self._post("status", "Enterprise Telephony wird geprüft …")

            if not self._ensure_telephony():
                detail = "Enterprise Telephony nicht bereit"
                message = (
                    f"Pikett {label}: Enterprise Telephony läuft nicht und konnte "
                    "nicht gestartet werden."
                )
                logger.error(message)
                if manual:
                    self._post("error", message)
                else:
                    self._post("status", message)
                return

            self._post(
                "status",
                f"Wähle Pikett {label} … ({core.HITS_PER_TRIGGER} Anrufe)",
            )

            ok = dialer.switch_pikett(action, on_number, off_number, log=logger)

            with self._settings_lock:
                if ok:
                    self.settings.pikett_on = action == ACTION_ON
                state = self.settings.pikett_on
                core.save_settings(self.settings, self.settings_file)

            self._post("state", state)
            if ok:
                logger.info("Pikett %s umgeschaltet.", label)
            else:
                detail = "kein Anruf gestartet"
                message = f"Pikett {label}: kein Anruf konnte gestartet werden."
                logger.error(message)
                if manual:
                    self._post("error", message)
                else:
                    self._post("status", message)
        except Exception as error:
            detail = str(error)
            logger.exception("Umschalten fehlgeschlagen")
            if manual:
                self._post("error", f"Umschalten fehlgeschlagen:\n{error}")
            else:
                self._post("status", f"Umschalten fehlgeschlagen: {error}")
        finally:
            self._dial_lock.release()
            self._record_history(action, number, source, ok, detail)

    # --------------------------------------------------------
    # ENTERPRISE TELEPHONY
    # --------------------------------------------------------

    def _ensure_telephony(self) -> bool:
        """Prüfen, ob Enterprise Telephony läuft, und sonst starten."""
        # Nur eine Prüfung/Suche gleichzeitig (Systray, Anruf, Hintergrund).
        with self._telephony_lock:
            with self._settings_lock:
                saved = self.settings.telephony_path

            status = telephony.ensure_running(saved)

            if status.path and status.path != saved:
                with self._settings_lock:
                    self.settings.telephony_path = status.path
                    core.save_settings(self.settings, self.settings_file)
                logger.info("Enterprise Telephony gemerkt: %s", status.path)

            if not status.running:
                logger.error("Enterprise Telephony nicht bereit: %s", status.message)
            return status.running

    def _telephony_loop(self) -> None:
        """Enterprise Telephony am Laufen halten, damit Anrufe möglich sind."""
        while not self._stop.is_set():
            try:
                self._ensure_telephony()
            except Exception:  # noqa: BLE001 - darf die App nicht stoppen
                logger.exception("Prüfung von Enterprise Telephony fehlgeschlagen")
            self._stop.wait(TELEPHONY_CHECK_INTERVAL_S)

    def _record_history(
        self, action: str, number: str, source: str, ok: bool, detail: str
    ) -> None:
        """Einen Auftrag (nicht die einzelnen Anrufe) in der Tabelle festhalten."""
        entry = core.HistoryEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            action=action,
            number=number,
            source=source,
            ok=ok,
            detail=detail,
        )
        try:
            self.history = core.record_history(entry, path=self.history_file)
        except OSError:
            logger.exception("Status-Eintrag konnte nicht gespeichert werden")
        self._post("history", list(self.history))

    # --------------------------------------------------------
    # SYSTRAY
    # --------------------------------------------------------

    def _setup_tray(self) -> None:
        try:
            import pystray

            from autopikett.tray import make_tray_image
        except Exception as error:  # pragma: no cover - nur ohne Tray-Umgebung
            logger.warning("Systray nicht verfügbar: %s", error)
            return

        menu = pystray.Menu(
            pystray.MenuItem(
                "Einstellungen öffnen", lambda *_: self._post("open", None), default=True
            ),
            pystray.MenuItem(
                "Pikett jetzt einschalten", lambda *_: self._post("manual", ACTION_ON)
            ),
            pystray.MenuItem(
                "Pikett jetzt ausschalten", lambda *_: self._post("manual", ACTION_OFF)
            ),
            pystray.MenuItem(
                "Auf Updates prüfen", lambda *_: self._post("update_check", None)
            ),
            pystray.MenuItem("Beenden", lambda *_: self._post("quit", None)),
        )

        self._tray = pystray.Icon(
            "autopikett",
            make_tray_image(self.settings.pikett_on),
            f"{core.APP_NAME} - {core.state_label(self.settings.pikett_on)}",
            menu,
        )
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _refresh_tray(self, pikett_on: bool) -> None:
        if self._tray is None:
            return
        try:
            from autopikett.tray import make_tray_image

            self._tray.icon = make_tray_image(pikett_on)
            self._tray.title = f"{core.APP_NAME} - {core.state_label(pikett_on)}"
        except Exception:
            logger.exception("Systray-Symbol konnte nicht aktualisiert werden")

    def _hide_to_tray(self) -> None:
        self.root.withdraw()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    # --------------------------------------------------------
    # STATUSZEILE
    # --------------------------------------------------------

    def _refresh_status(self) -> None:
        now = datetime.now()
        with self._settings_lock:
            settings = self.settings
            text = core.state_label(settings.pikett_on)
            event = core.next_event(settings, now, skip_days=self._holiday_days)

        if event is None:
            text += " · kein weiterer Wechsel geplant"
        else:
            moment, action, day = event
            if moment.date() == now.date():
                when = "heute"
            elif (moment.date() - now.date()).days == 1:
                when = "morgen"
            else:
                when = day
            text += (
                f" · nächster Wechsel: {when} {moment:%H:%M} "
                f"({ACTION_LABELS[action]})"
            )

        self.status_var.set(text)

    # --------------------------------------------------------
    # EREIGNISSE ZWISCHEN THREADS UND GUI
    # --------------------------------------------------------

    def _post(self, kind: str, payload: object = None) -> None:
        self._events.put((kind, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass

        if not self._stop.is_set():
            self.root.after(EVENT_INTERVAL_MS, self._poll_events)

    def _handle_event(self, kind: str, payload: object) -> None:
        if kind == "status":
            self.status_var.set(str(payload))
        elif kind == "state":
            self._refresh_tray(bool(payload))
            self._refresh_status()
        elif kind == "history":
            self._refresh_history_table(payload)  # type: ignore[arg-type]
        elif kind == "info":
            messagebox.showinfo(core.APP_NAME, str(payload))
        elif kind == "error":
            messagebox.showerror(core.APP_NAME, str(payload))
        elif kind == "open":
            self._show_window()
        elif kind == "manual":
            threading.Thread(
                target=self._run_job, args=(str(payload),), kwargs={"manual": True},
                daemon=True,
            ).start()
        elif kind == "holidays":
            self._show_holidays(payload)
        elif kind == "update_check":
            self._start_update_check(manual=True)
        elif kind == "ask_update":
            version, url = payload  # type: ignore[misc]
            if messagebox.askyesno(
                "Update verfügbar",
                f"Version {version} ist verfügbar "
                f"(installiert: {core.APP_VERSION}).\n\n"
                "Jetzt installieren? Die App wird dafür beendet und "
                "anschliessend neu gestartet.",
            ):
                threading.Thread(
                    target=self._install_update, args=(version, url), daemon=True
                ).start()
        elif kind == "updating":
            self.status_var.set(f"Update {payload} wird installiert …")
        elif kind == "quit":
            self.quit_app()

    # --------------------------------------------------------
    # UPDATES
    # --------------------------------------------------------

    def _auto_update_tick(self) -> None:
        if self._stop.is_set():
            return
        with self._settings_lock:
            enabled = self.settings.auto_update
        if enabled:
            self._start_update_check(manual=False)
        self.root.after(int(core.UPDATE_INTERVAL_S * 1000), self._auto_update_tick)

    def _start_update_check(self, manual: bool) -> None:
        threading.Thread(
            target=self._update_worker, args=(manual,), daemon=True
        ).start()

    def _update_worker(self, manual: bool) -> None:
        try:
            result = core.available_update()
        except Exception:
            logger.exception("Update-Prüfung fehlgeschlagen")
            if manual:
                self._post("info", "Update-Prüfung nicht möglich.")
            return

        if not result:
            if manual:
                self._post(
                    "info",
                    f"Kein Update gefunden.\nInstallierte Version: {core.APP_VERSION}",
                )
            return

        version, url = result
        logger.info("Update verfügbar: %s", version)

        with self._settings_lock:
            automatic = self.settings.auto_update

        if manual:
            self._post("ask_update", (version, url))
        elif automatic:
            self._install_update(version, url)

    def _install_update(self, version: str, url: str) -> None:
        target = Path(tempfile.gettempdir()) / f"AutoPikett-Setup-{version}.exe"
        try:
            core.download_file(url, target)
            logger.info("Installer heruntergeladen: %s", target)

            creationflags = 0
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creationflags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                )

            subprocess.Popen(
                [str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                creationflags=creationflags,
                close_fds=True,
            )
            self._post("updating", version)
            self._post("quit", None)
        except Exception as error:
            logger.exception("Update konnte nicht installiert werden")
            self._post("error", f"Update {version} fehlgeschlagen:\n{error}")

    # --------------------------------------------------------
    # BEENDEN
    # --------------------------------------------------------

    def quit_app(self) -> None:
        self._updating = True
        self._stop.set()
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    start_in_tray = autostart.TRAY_ARGUMENT in args

    setup_logging()
    logger.info(
        "%s %s startet%s.",
        core.APP_NAME,
        core.APP_VERSION,
        " (Systray)" if start_in_tray else "",
    )

    settings = core.load_settings()
    # Autostart-Eintrag passend zur Einstellung setzen/entfernen.
    autostart.apply(settings.autostart)

    lock = acquire_instance_lock()
    if lock is None:
        logger.warning("AutoPikett läuft bereits - zweite Instanz beendet sich.")
        if not start_in_tray:
            try:
                root = tk.Tk()
                root.withdraw()
                messagebox.showinfo(
                    core.APP_NAME, "AutoPikett läuft bereits im Systray."
                )
                root.destroy()
            except Exception:
                pass
        return 0

    root = tk.Tk()
    if start_in_tray:
        # Direkt in den Systray, ohne dass ein Fenster aufblitzt.
        root.withdraw()
    AutoPikettApp(root, settings)
    root.mainloop()
    lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

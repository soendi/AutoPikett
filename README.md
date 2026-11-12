# AutoPikett

AutoPikett schaltet das Telefon-Pikett zeitgesteuert ein und aus: pro aktivem
Wochentag werden zu vier festgelegten Zeiten Telefonanrufe über das
Swisscom-Fenster "Enterprise Telephony" gestartet.

Aufbau und Anruf-Technik orientieren sich an
[soendi/telefonerkennung](https://github.com/soendi/telefonerkennung): Nummer
ins Wählfeld (`DialNumberTextBox`) schreiben, Anruf-Taste (`CallButton`)
drücken - per `pywinauto`.

## Funktionen

- Fenster mit allen sieben Wochentagen als Checkbox
- vier frei einstellbare Zeiten pro Tag, je mit Ziel **Ein** oder **Aus**
- zwei Nummern: eine für "Pikett einschalten", eine für "Pikett ausschalten"
- rechts neben den Wochentagen die nächsten Feiertage des Kantons St. Gallen,
  live von der [OpenHolidays API](https://www.openholidaysapi.org/) geladen
  (beim Start, bei jedem Tageswechsel und über "Aktualisieren"); vergangene
  Tage verschwinden, ohne Internet zeigt die Liste einen Hinweis
- an Feiertagen des Kantons St. Gallen fallen die geplanten Umschaltungen aus
  (manuelles Schalten bleibt möglich); der nächste Wechsel im Status überspringt
  Feiertage ebenfalls
- jede Umschaltung wird dreimal gewählt (wählen, 10 s warten, wählen,
  10 s warten, wählen), damit das Pikett sicher umgeschaltet ist
- Status-Tabelle im Fenster: ein Eintrag pro Auftrag mit Zeit, Auftrag,
  gewählter Nummer, Auslöser und Ergebnis (erfolgreich / fehlgeschlagen),
  inklusive Schaltfläche "Verlauf leeren"
- Systray-Symbol mit Statuspunkt: **grün = Pikett ein**, **rot = Pikett aus**
- Einstellungen liegen im Benutzerordner
- automatischer Start mit Windows, direkt in den Systray (`--tray`), damit
  der Zeitplan ohne offenes Fenster läuft - im Fenster abschaltbar
- automatisches Selbst-Update über die GitHub-Releases

## Voreinstellungen

Montag bis Freitag aktiv, Samstag und Sonntag aus. Alle Tage starten mit:

| Zeit  | Ziel |
|-------|------|
| 08:00 | Aus  |
| 12:00 | Ein  |
| 13:30 | Aus  |
| 17:00 | Ein  |

Hinterlegt sind die echten Pikett-Nummern:

| Ziel | Nummer                |
|------|-----------------------|
| Ein  | `*21+41860812851990`  |
| Aus  | `#21`                 |

Die Nummern werden Zeichen für Zeichen ins Wählfeld geschrieben - `*`,
`#` und `+` gehören zur Nummer und werden mitgeschickt, nicht nur die
Ziffern. Beide Nummern lassen sich im Fenster ändern.

## Autostart

Der Installer legt einen Eintrag unter
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` an. AutoPikett prüft
diesen Eintrag bei jedem Start und hält ihn passend zur Einstellung
„Mit Windows starten" im Fenster aktuell. Gestartet wird mit `--tray`:
die App landet direkt im Systray, ohne Fenster. Ein normaler Doppelklick
auf die Verknüpfung öffnet das Fenster wie gewohnt.

## Speicherort der Daten

```text
%APPDATA%\AutoPikett\settings.json
%APPDATA%\AutoPikett\history.json
%APPDATA%\AutoPikett\logs\autopikett.log
```

`history.json` enthält die Status-Tabelle (die letzten 200 Aufträge), damit
nach einem Neustart sichtbar bleibt, ob die Anrufe getätigt wurden.

## Updates

Beim Start und danach alle sechs Stunden prüft die App
`https://api.github.com/repos/soendi/AutoPikett/releases/latest`. Ist eine
neuere Version vorhanden, wird `AutoPikett-Setup-<version>.exe` geladen und
still installiert; die App startet danach neu. Das automatische Update lässt
sich im Fenster abschalten, manuell prüfen über das Systray-Menü.

## Installer bauen

Der Installer wird von GitHub Actions gebaut: ein Tag `v*` pushen, zum
Beispiel

```bash
git tag v1.0.1
git push origin v1.0.1
```

Der Workflow `.github/workflows/build.yml` erstellt die EXE (PyInstaller,
onedir) und kompiliert `installer.iss` mit Inno Setup. Das Setup landet als
Asset `AutoPikett-Setup-<version>.exe` im Release und wird von dort
automatisch installiert.

Vor dem Taggen die Version in `autopikett/core.py` (`APP_VERSION`) und in
`pyproject.toml` anpassen.

## Entwicklung

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[windows]"
python main.py
```

Tests der Kernlogik (laufen ohne Windows und ohne GUI):

```bash
pip install pytest
pytest
```

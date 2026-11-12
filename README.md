# AutoPikett

AutoPikett ist ein kleines Projekt zur Automatisierung und Verwaltung von Bereitschafts-/Pikett-Zeitplänen. Die Idee: Verantwortlichkeiten, Schichten und Eskalationsregeln zentral verwalten, ohne manuelle Verteilungslogik im Alltag wiederholen zu müssen.

## Ziele

- Verwaltung von Teammitgliedern und Schichten
- Automatische Einteilung nach Regeln und Verfügbarkeiten
- einfache Auswertung von Bereitschaften und Eskalationsstufen
- skalierbar für kleine Teams bis zu größeren Betriebsteams

## Projektstruktur

```text
.
├── README.md
├── pyproject.toml
├── .gitignore
├── src/
│   └── autopikett/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       └── scheduler.py
├── tests/
│   └── test_basic.py
└── .venv/
```

## Schnellstart

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# oder .venv\Scripts\activate  # Windows
pip install -e .
autopikett --help
```

## Beispiel

```bash
autopikett list
```

## Status

Das Repository ist aktuell als Starter-Projekt vorbereitet und kann schrittweise erweitert werden.

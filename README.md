# BeichtBot

BeichtBot ist ein Discord-Bot, der anonyme Beichten in einem sicheren Rahmen ermöglicht. Er wurde mit
[`discord.py`](https://discordpy.readthedocs.io/) entwickelt und unterstützt Slash Commands sowie Modals
für eine moderne Nutzererfahrung.

## Features

- `/beichten` – öffnet ein Modal zum anonymen Posten mit optionalen Triggerwarnungen
  und konfigurierbaren Thread-Einstellungen.
- `/beichtantwort` – ermöglicht anonyme Antworten in dem automatisch erstellten Thread.
- `/melden` – sendet eine anonyme Meldung an den Moderations-Channel.
- `/hilfe` – liefert eine Kurzbeschreibung aller Befehle und Hinweise zum Datenschutz.
- Umfassende Admin-Befehle (`/beichtbot-*`) zum Konfigurieren von Ziel-Channels, Wortlisten,
  Bannern, Cooldowns und Statistiken.
- PII- und Krisen-Erkennung via einfache Keyword-Heuristiken mit diskreter Mod-Benachrichtigung.
- Optionale Auto-Delete-Funktion, Hash-Speicherung für Missbrauchsprüfungen und Bannermanagement.

## Installation

1. **Python installieren:** Es wird Python 3.10 oder neuer benötigt.
2. **Abhängigkeiten installieren:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -U pip
   pip install -r requirements.txt
   ```

3. **Bot-Token konfigurieren:**
   - Erstelle im [Discord Developer Portal](https://discord.com/developers/applications) eine neue Anwendung.
   - Aktiviere den Bot-User und kopiere das Token.
   - Setze die Umgebungsvariable `DISCORD_TOKEN` auf dieses Token.

4. **Bot starten:**

   ```bash
   python main.py
   ```

## Erforderliche Bot-Berechtigungen

Der Bot benötigt mindestens die folgenden Berechtigungen im Ziel-Server:

- Nachrichten lesen und senden
- Threads erstellen und verwalten
- Nachrichten verwalten (für Auto-Delete und Moderationsfunktionen)
- Anwendungskommandos verwenden

## Konfiguration

Nutze `/beichtbot-setup`, um den Standard-Ziel-Channel sowie optionale Einstellungen (Cooldown,
Auto-Delete, KI-Moderation etc.) festzulegen. Weitere Einstellungen erfolgen über die übrigen
Admin-Befehle. Eine Übersicht der aktiven Einstellungen kann mit `/beichtbot-stats` eingesehen werden.

Konfigurationsdaten werden lokal in `beichtbot_state.json` gespeichert. User-Daten werden ausschließlich
als HMAC-Hashes abgelegt, um Missbrauchsprüfungen zu ermöglichen, ohne Klartext-IDs zu speichern.

## Entwicklung

- `beichtbot/config.py` enthält den Persistenz-Layer sowie Helper-Funktionen.
- `beichtbot/bot.py` implementiert sämtliche Slash Commands, Modals und Moderations-Logik.
- `main.py` startet den Bot und liest das Token aus `DISCORD_TOKEN`.

Zum Testen des Codes kann `python -m py_compile beichtbot/*.py main.py` verwendet werden.

## Lizenz

Dieses Projekt verwendet keine spezifische Lizenz. Bitte den Autor kontaktieren, bevor es produktiv
verwendet wird.

# Infozentrum KI · Innovation · Finanzen

Ein persönliches Nachrichten-Informationszentrum: Es sammelt **stündlich** Meldungen
von Bloomberg, The Economist, Financial Times, WSJ, Handelsblatt, FAZ, EZB, BIZ,
Finextra u. v. m., filtert sie auf die Themen **KI, Innovation/Fintech, Banken &
Finanzen und Regulierung**, bewertet ihre Wichtigkeit und zeigt sie in einer
**Web-App, die man sich wie eine App aufs Handy legen kann**.

Optional:
- **Push-Nachrichten aufs Handy** bei besonders wichtigen Meldungen (über die kostenlose App *ntfy*)
- **Tägliches Morgen-Briefing durch Claude** – zusammengefasst und eingeordnet für den Bankensektor

```
 RSS-Feeds (Bloomberg, Economist, FT, …)
            │  stündlich, GitHub Actions
            ▼
 scripts/fetch_news.py ── filtert, bewertet, entfernt Duplikate
            │
            ├─► Web-App (GitHub Pages)      → Handy-Homescreen
            ├─► Push bei Top-Meldungen (ntfy) → Handy-Benachrichtigung
            └─► data/news.json + briefing.md → Claude-Morgen-Briefing
```

## Einrichtung (einmalig, ca. 10 Minuten)

1. **Branch `main` anlegen:** Diesen Stand nach `main` mergen und `main` unter
   *Settings → General → Default branch* als Standard setzen. (Zeitgesteuerte
   GitHub-Workflows laufen nur auf dem Standard-Branch.)
2. **Ersten Lauf starten:** *Actions → „Nachrichten aktualisieren“ → Run workflow*.
   Danach existiert der Branch `gh-pages`.
3. **Web-App veröffentlichen:** *Settings → Pages → Source: „Deploy from a branch“ →
   Branch `gh-pages`, Ordner `/ (root)`*. Nach 1–2 Minuten ist die App erreichbar unter
   `https://spookyone1.github.io/Nachrichten/`.
   > Hinweis: Bei privaten Repositories ist GitHub Pages erst ab einem kostenpflichtigen
   > GitHub-Plan verfügbar. Alternative: Repository öffentlich machen (es enthält keine
   > persönlichen Daten) oder nur die Push-Nachrichten und das Claude-Briefing nutzen.
4. **Aufs Handy legen:** Seite im Handy-Browser öffnen →
   iPhone: *Teilen → Zum Home-Bildschirm*; Android: *Menü → App installieren*.

### Push-Nachrichten einrichten (optional)

1. App **ntfy** installieren (iOS/Android, kostenlos).
2. In der App ein Thema abonnieren – einen schwer erratbaren Namen wählen,
   z. B. `infozentrum-7f3k9q2x` (wer den Namen kennt, kann mitlesen).
3. Im Repository unter *Settings → Secrets and variables → Actions* ein Secret
   **`NTFY_TOPIC`** mit genau diesem Namen anlegen.

Ab dann kommt eine Push-Nachricht, sobald eine Meldung die Push-Schwelle erreicht
(standardmäßig vor allem „KI × Finanzen“ aus starken Quellen, max. 5 pro Stunde).

### Morgen-Briefing durch Claude (optional)

Auf [claude.ai/code](https://claude.ai/code) eine **Routine** anlegen, die werktags
um 7 Uhr mit dem Inhalt von [`prompts/morgen-briefing.md`](prompts/morgen-briefing.md)
startet. Das Briefing erscheint dann in der Claude-App auf dem Handy. Claude kann
diese Routine auch direkt für dich einrichten.

## Anpassen

| Was | Wo |
| --- | --- |
| Quellen hinzufügen/entfernen | `config/sources.json` |
| Stichwörter, Themen, Gewichtung | `config/topics.json` → `topics` |
| Ab wann „Top“ bzw. Push | `config/topics.json` → `settings.top_score` / `notify_score` |
| Aufbewahrungsdauer | `config/topics.json` → `settings.keep_days` |
| Aktualisierungs-Rhythmus | `.github/workflows/update-news.yml` → `cron` |

**Bewertung:** Jedes Thema bringt Punkte (Titel-Treffer zählen voll, Treffer nur im
Teaser halb), Kombinationen wie „KI × Finanzen“ geben Bonuspunkte, dazu kommen
Signalwörter („exclusive“, „Übernahme“ …) und die Priorität der Quelle.

**Quellen-Status:** Unten in der App unter „Quellen“ sieht man, welche Feeds gerade
nicht erreichbar sind. Verlage ändern ihre Feed-Adressen gelegentlich – dann einfach
die URL in `config/sources.json` anpassen oder den Eintrag entfernen.

**Bezahlschranken:** Bloomberg, The Economist, FT und WSJ liefern per Feed Überschrift
und Teaser. Für den vollständigen Artikel ist ein Abo nötig.

## Lokal ausprobieren

```bash
python3 scripts/fetch_news.py --no-notify   # sammelt nach docs/data/
python3 -m http.server -d docs 8000        # → http://localhost:8000
python3 -m unittest discover -s tests       # Tests
```

Keine Abhängigkeiten nötig – nur Python 3.10+.

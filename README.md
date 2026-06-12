# Python: Garmin Connect — Streamlit Fork

> **Fork von [cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect/releases)**
>
> Ein riesiges Dankeschön an **[@cyberjunky](https://github.com/cyberjunky)** für die brillante Arbeit an der
> ursprünglichen Bibliothek. Der saubere API-Wrapper, das durchdachte Token-Management und das
> eingebrachte Know-how — ohne dieses Fundament wäre dieses Hobby-Projekt nicht möglich.
> Bitte unterstützt cyberjunky direkt: [GitHub Sponsors](https://github.com/sponsors/cyberjunky)

## Dateien in diesem Fork

| Datei | Zweck |
|---|---|
| `gpx_analysis.py` | Analyse-Bibliothek (Kalman, Leistungsmodell, Karten) — kein UI, wird importiert |
| `pipeline.py` | Garmin-Daten herunterladen (GPX + FIT) |
| `dashboard.py` | Cycling-Dashboard-Logik aus FIT-Daten — wird von `app.py` genutzt |
| `app.py` | Streamlit-App — alle vier Seiten in einer UI |

## Schnellstart

```bash
# 1. Abhängigkeiten installieren
pip install -r requirements.txt

# 2. Garmin-Daten herunterladen (einmalig / bei Bedarf)
python pipeline.py

# 3. Streamlit-App starten
streamlit run app.py
```

> **Windows:** `setup_env.ps1` legt eine virtuelle Umgebung an und installiert alle Pakete automatisch.

---

## Skripte im Detail

### `gpx_analysis.py` — GPX-Analyse-Bibliothek

Berechnungs- und Visualisierungsfunktionen — enthält keine UI, wird von `app.py` importiert.

- **Kalman-Filter (2D)**: GPS-Rauschen glätten, realistische Geschwindigkeit und Beschleunigung ableiten
- **Höhen- & Steigungsberechnung**: Median-Filter + Savitzky-Golay-Glättung, gleichmäßiges Resampling
- **Leistungsmodell**: Rollwiderstand, Luftwiderstand, Steigungskraft, Beschleunigungskraft → Fahrerleistung (W)
- **Kartenvisualisierung**: Interaktive Folium-Karte mit farbcodierter Geschwindigkeitsspur
- **Statistiken**: Distanz, Dauer, Höhenmeter, Spitzengeschwindigkeit, Durchschnittsleistung

### `pipeline.py` — Garmin-Export-Pipeline

Lädt automatisch GPX- und FIT-Dateien der letzten N Aktivitäten aus Garmin Connect herunter
und speichert sie lokal unter `garmin_export/`.

- Login mit Token-Persistenz (kein erneutes Eingeben von Passwort nötig)
- Unterstützt MFA
- Filtert GPS-Aktivitäten und extrahiert FIT-Dateien aus dem Original-ZIP
- Konfigurierbar über Umgebungsvariablen (`GARMIN_EMAIL`, `GARMIN_PASSWORD`, `GARMIN_ACTIVITY_LIMIT`)


### `dashboard.py` — Cycling-Dashboard-Modul

Liest FIT-Dateien aus `garmin_export/fit/` und stellt Diagramme und KPIs bereit.
Wird von `app.py` als Modul geladen; kann auch standalone ein HTML-Dashboard erzeugen.

- **Wöchentliches Volumen**: Gestapeltes Balkendiagramm nach Fahrttyp (Pendeln, Training, Regeneration …)
- **Fitness-Trend**: Herzfrequenz-Drift und Tempo auf der Pendelstrecke als Kontrollroute
- **Aktivitäts-Kalender**: GitHub-style Heatmap der täglichen Kilometer
- **HF-Zonen**: Monatliche Zonenverteilung (Z1–Z5)
- **Distanz-Histogramm**: Verteilung der Fahrtlängen nach Typ
- **Pendel-Quote**: Monatlicher Anteil der Pendel-Fahrten an Werktagen
- **KPI-Kacheln**: Fahrten, Distanz, Fahrzeit, Ø Tempo, Ø HF, Höhenmeter auf einen Blick
- **Fahrt-Tabelle**: Letzte 15 Fahrten mit Datum, Typ, km, min, km/h, HF, hm


### `app.py` — Streamlit-App

Vier Seiten in einer Oberfläche:

1. **Aktivitäten laden** — `pipeline.py` aufrufen, Download-Log anzeigen
2. **Cycling Dashboard** — Alle Radfahrten aus FIT-Daten analysieren (`dashboard.py`)
3. **GPX Analyse** — Lokale GPX-Datei hochladen & mit Kalman-Filter auswerten (`gpx_analysis.py`)
4. **Fahrradphysik** — Widerstandsmodell & interaktiver Rechner

```bash
streamlit run app.py
```

---


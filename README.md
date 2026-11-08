# Garmin Cycling App

Streamlit-App zur Analyse von Garmin-Radfahrten — GPX-Analyse, FIT-Dashboard, Leistungsmodell.

## Schnellstart

```bash
pip install -r requirements.txt

# Garmin-Daten herunterladen (einmalig / bei Bedarf)
python pipeline.py

# App starten
streamlit run app.py
```

> **Windows:** `setup_env.ps1` legt eine virtuelle Umgebung an und installiert alle Pakete automatisch.

---

## Dateien

| Datei | Zweck |
|---|---|
| `app.py` | Streamlit-App – alle vier Seiten in einer UI |
| `pipeline.py` | Garmin-Daten herunterladen (GPX + FIT) |
| `dashboard.py` | Cycling-Dashboard-Logik aus FIT-Daten |
| `gpx_analysis.py` | Analyse-Bibliothek (Kalman, Leistungsmodell, Karten) |

---

## Seiten

### 1. Aktivitäten laden
Ruft `pipeline.py` als Subprocess auf und zeigt den Download-Log live an.
Login mit Token-Persistenz — kein erneutes Passwort nötig, MFA wird unterstützt.
Konfigurierbar über Umgebungsvariablen: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `GARMIN_ACTIVITY_LIMIT`.

### 2. Cycling Dashboard
Liest alle `*cycling*.fit`-Dateien aus `garminexports/standard/fit/` und zeigt:
- KPI-Kacheln: Fahrten, Distanz, Fahrzeit, Ø Tempo, Ø HF, Höhenmeter, Pendel-Quote
- Wöchentliches Volumen nach Fahrttyp (Pendeln, Training, Regeneration …)
- Fitness-Trend: HF-Drift und Tempo auf der Pendelstrecke als Kontrollroute
- Aktivitäts-Kalender (GitHub-style Heatmap)
- HF-Zonen-Verteilung (Z1–Z5), Distanz-Histogramm, Pendel-Quote
- Tabelle der letzten 15 Fahrten

### 3. GPX Einzel-Analyse
GPX-Datei hochladen oder aus importierten Tracks auswählen:
- **Kalman-Filter (2D):** GPS-Rauschen glätten, realistische Geschwindigkeit und Beschleunigung
- **Höhen- & Steigungsberechnung:** Median-Filter + Savitzky-Golay, gleichmäßiges Resampling
- **Leistungsmodell:** Roll-, Luft-, Steigungswiderstand, Beschleunigungskraft → Fahrerleistung (W)
- **Karte:** Interaktive Folium-Karte mit farbcodierter Geschwindigkeitsspur
- **Export:** CSV (alle Daten / Metriken / Zusammenfassung)

### 4. Fahrradphysik
Interaktiver Rechner für das Widerstandsmodell:
- Roll-, Luft- und Steigungswiderstand als Kraft (N) und Leistung (W)
- Eingaben: Fahrermasse, Fahrradmasse, Geschwindigkeit, c_rr, cwA, Steigung

---

## Updates vom Upstream

```bash
git fetch upstream
git merge upstream/master
```

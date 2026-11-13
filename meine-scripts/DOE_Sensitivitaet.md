# DOE: Sensitivität der Analyse-Parameter (GPX-Einzel-Analyse)

Stand: 2026-11-13

## Ziel

Wie empfindlich reagieren die Kennwerte der GPX-Einzel-Analyse (Geschwindigkeit,
Beschleunigung, Leistung, Steigung) auf die „Analyse-Parameter“ aus
[app.py](app.py) / [gpx_analysis.py](gpx_analysis.py)?

Das eigentliche Kalman-Modell besteht nur aus **GPS Std** und **Accel Std**.
Die übrigen vier Parameter sind nachgelagerte Glättungen und wurden mitgeprüft.

## Versuchsplan

- **Design:** vollfaktoriell, 6 Faktoren × 3 Stufen = 3⁶ = **729 Läufe**
- **Zusätzlich:** OAT (One-At-A-Time), je Faktor eine Variation um den Default
- **Track:** `2026-11-13_09-52-21_cycling_25062915214.gpx`
  (461 Punkte, 9,7 km, Ø 20,4 km/h)
- **Konstant gehalten:** Fahrerparameter und Widerstandsmodell
  (m_fahrer 80 kg, m_fahrrad 15 kg, c_rr 0,007, cwA 0,4, Rotationszuschlag 3 %)

| Faktor | Niedrig | Default | Hoch | Modellteil |
|---|---|---|---|---|
| GPS_STD | 3 | 6 | 12 | Kalman |
| ACCEL_STD | 0,5 | 1,5 | 4 | Kalman |
| accel_smooth_window | 1 | 3 | 7 | Glättung Beschleunigung |
| grade_smooth_window | 1 | 3 | 7 | Glättung Steigung |
| power_smooth_window | 1 | 5 | 11 | Glättung Leistung |
| ELEV_PLOT_MEDIAN_KERNEL | 3 | 7 | 13 | Höhenfilter |

**Zielgrößen:** Distanz, v_max, v_mean, P_mean, P_95, P_max, RMS der
Beschleunigung, maximale Steigung.

**Auswertung:** Varianzanteil des Haupteffekts je Faktor (ANOVA-Zerlegung der
Quadratsummen); der Rest sind Wechselwirkungen („Inter“).

## Referenzwerte (Default-Parameter)

| Kennwert | Wert |
|---|---|
| Distanz | 9,69 km |
| v_max | 40,2 km/h |
| v_mean | 20,4 km/h |
| P_mean | 93,6 W |
| P_95 | 248,7 W |
| P_max | 504 W |
| a_rms | 0,23 m/s² |
| max. Steigung | 7,9 % |

## Ergebnis: Varianzanteil je Parameter in %

| Kennwert | GPS Std | Accel Std | Accel Smooth | Grade Smooth | Power Smooth | Elev Median | Inter |
|---|---|---|---|---|---|---|---|
| v_max | 27,8 | **52,7** | 0 | 0 | 0 | 0 | 19,5 |
| v_mean | 28,4 | **61,6** | 0 | 0 | 0 | 0 | 10,0 |
| P_mean | 13,6 | 22,0 | **36,8** | 0,2 | 0 | 0 | 27,3 |
| P_95 | 7,6 | 11,7 | 26,6 | 0,1 | **32,6** | 0 | 21,2 |
| P_max | 9,4 | 16,6 | 11,3 | 0 | 12,7 | 0 | **50,1** |
| a_rms | 14,6 | 25,0 | **43,0** | 0 | 0 | 0 | 17,3 |
| max. Steigung | 0,1 | 0,7 | 0 | **94,4** | 0 | 2,8 | 2,0 |

Spannweite über alle 729 Läufe:

| Kennwert | Min | Max | Referenz |
|---|---|---|---|
| Distanz (km) | 9,67 | 9,76 | 9,69 |
| v_max (km/h) | 37,7 | 87,2 | 40,2 |
| v_mean (km/h) | 20,2 | 21,0 | 20,4 |
| P_mean (W) | 76,6 | 214,7 | 93,6 |
| P_95 (W) | 164,5 | 614,8 | 248,7 |
| P_max (W) | 224 | 25.433 | 504 |
| a_rms (m/s²) | 0,12 | 0,79 | 0,23 |
| max. Steigung (%) | 6,6 | 8,5 | 7,9 |

## OAT um den Default (Spannweite relativ zum Referenzwert)

| Faktor | v_max | P_mean | P_95 | P_max | a_rms |
|---|---|---|---|---|---|
| GPS_STD (3 / 6 / 12) | 35 % | 6,6 % | 6,5 % | 125 % | 32 % |
| ACCEL_STD (0,5 / 1,5 / 4) | 57 % | 14,7 % | 12,6 % | 208 % | 48 % |
| accel_smooth_window (1 / 3 / 7) | 0 % | **33,8 %** | 16,9 % | 183 % | 90 % |
| grade_smooth_window (1 / 3 / 7) | 0 % | 3,6 % | 2,3 % | 1,6 % | 0 % |
| power_smooth_window (1 / 5 / 11) | 0 % | 0,5 % | **30,0 %** | 126 % | 0 % |
| ELEV_PLOT_MEDIAN_KERNEL (3 / 7 / 13) | 0 % | 0,9 % | 1,4 % | 1,6 % | 0 % |

Einzelwerte, die zeigen, wie die Parameter wirken:

- P_mean bei Accel Smooth 1 / 3 / 7: 114 W / 94 W / 83 W
- P_max bei Accel Smooth 1 / 3 / 7: 1276 W / 504 W / 352 W
- P_95 bei Power Smooth 1 / 5 / 11: 274 W / 249 W / 200 W
- Distanz: nur 9,67 bis 9,72 km über alle Kalman-Stufen

## Wechselwirkung GPS Std × Accel Std

Mittelwerte über alle übrigen Faktoren.

v_max (km/h):

| GPS Std \ Accel Std | 0,5 | 1,5 | 4 |
|---|---|---|---|
| 3 | 40,4 | 54,4 | **87,2** |
| 6 | 39,5 | 40,2 | 62,6 |
| 12 | 37,7 | 40,3 | 45,8 |

P_mean (W):

| GPS Std \ Accel Std | 0,5 | 1,5 | 4 |
|---|---|---|---|
| 3 | 94,0 | 105,5 | **149,2** |
| 6 | 88,7 | 96,1 | 113,7 |
| 12 | 85,2 | 91,3 | 98,2 |

## Erkenntnisse

1. **Accel Std ist der empfindlichste Kalman-Parameter.** Entscheidend ist das
   Verhältnis Accel Std / GPS Std, weil es bestimmt, wie stark das Filter den
   GPS-Messungen folgt (Kalman-Gain).
2. **Kleines GPS Std + großes Accel Std ist gefährlich.** Das Filter übernimmt
   das GPS-Rauschen fast ungefiltert: bei 3 / 4 steigt v_max auf 87 km/h und
   P_mean auf 149 W. Der Default 6 / 1,5 liegt auf der stabilen Seite.
3. **P_max ist als Kennwert unbrauchbar** (224 bis 25.433 W). Beschleunigungs-
   rauschen wird mit v multipliziert und durch `clip(lower=0)` nur nach oben
   durchgelassen. Besser P_95 oder eine gleitende Spitzenleistung (z. B. 5 s)
   verwenden. 50 % der P_max-Varianz stammen aus Wechselwirkungen.
4. **Accel Smooth ist der wichtigste Glättungsparameter.** Ungeglättetes
   Rauschen erzeugt durch die Gleichrichtung (`clip(lower=0)`) Scheinleistung:
   P_mean ändert sich um ca. 34 %.
5. **Power Smooth** wirkt nur auf Spitzen (P_95, P_max), nicht auf den
   Mittelwert.
6. **Grade Smooth und Elev Median Kernel** haben kaum Einfluss auf die Leistung
   (< 4 %). Grade Smooth bestimmt aber fast allein die maximale Steigung
   (94 % der Varianz).
7. **Distanz und v_mean sind robust**: Distanz ±0,5 %, v_mean ±1,9 % um den
   Default.

## Empfehlung

- Defaults beibehalten (GPS Std 6, Accel Std 1,5, Accel Smooth 3, Power Smooth 5).
- Accel Smooth nicht unter 3 setzen.
- Accel Std / GPS Std nicht über ca. 0,5 wählen.
- Für Spitzenleistung nicht P_max, sondern P_95 oder eine gleitende Spitze
  auswerten.
- Für mehr Genauigkeit der Leistung sind vermutlich Masse, c_rr und cwA
  wichtiger als die Filterparameter. Diese wurden hier nicht variiert.

## Einschränkungen

- Nur **ein Track** (461 Punkte). Ergebnisse können bei anderen Strecken
  (Stadt, Berg, anderes GPS-Rauschen) abweichen.
- Es gibt **keine Referenzleistung** (z. B. Powermeter). Die Analyse zeigt die
  Sensitivität, nicht welche Einstellung der Wahrheit am nächsten kommt.
- Die Varianzzerlegung betrachtet nur Haupteffekte. Wechselwirkungen sind als
  Rest („Inter“) zusammengefasst.
- Fahrer- und Widerstandsparameter, `DIST_RESAMPLE_M`, `GRADE_SMOOTH_WINDOW_M`
  und die Savitzky-Golay-Parameter wurden nicht variiert.

## Reproduktion

Skripte und Rohdaten liegen im Scratchpad der Session (nicht im Repo):

- `doe.py`: Versuchsplan und Läufe (Laufzeit ca. 45 s mit dem Projekt-venv)
- `analyze.py`: Auswertung
- `doe_full.csv`: alle 729 Läufe
- `doe_oat.csv`: OAT-Läufe

Ausführen mit `.venv\Scripts\python.exe`, da im System-Python `branca` fehlt.

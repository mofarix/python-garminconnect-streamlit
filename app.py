"""app.py – Garmin Cycling App (Streamlit)

Seiten:
  1. Aktivitäten laden   – Neue Garmin-Tracks herunterladen (pipeline.py)
  2. Cycling Dashboard   – Analyse aller Radfahrten (dashboard.py)
  3. GPX Einzel-Analyse         – Lokale GPX-Datei hochladen & analysieren (gpx_analysis.py)
  4. Fahrradphysik       – Widerstandsmodell & interaktiver Rechner (gpx_analysis.py)

    pip install streamlit
    streamlit run app.py
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import dashboard as dash
import pandas as pd
import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components

from gpx_analysis import (
    DEFAULT_PARAMS,
    analyze_gpx_data,
    create_speed_map,
    get_stats,
    load_gpx_data_from_upload,
    plot_time_profiles,
    g,
    rho,
)

# ============================================================
# CONFIG
# ============================================================
PIPELINE_SCRIPT = Path(__file__).parent / "pipeline.py"

st.set_page_config(
    page_title="Garmin Cycling",
    page_icon="🚴",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# USER MANAGEMENT
# ============================================================
EXPORTS_ROOT = Path("garminexports")


def _detect_users() -> dict[str, dict]:
    home = Path.home()
    users: dict[str, dict] = {}

    default_token = home / ".garminconnect"
    users["Standard"] = {
        "token_path": str(default_token),
        "export_dir": EXPORTS_ROOT / "standard",
        "has_tokens": default_token.exists(),
    }

    for p in sorted(home.glob(".garminconnect_*")):
        if p.is_dir():
            name = p.name[len(".garminconnect_"):]
            users[name] = {
                "token_path": str(p),
                "export_dir": EXPORTS_ROOT / name,
                "has_tokens": True,
            }

    return users


# n_fit_files dient nur als Cache-Key: neue Datei → Cache wird automatisch invalidiert
@st.cache_data
def _load_dashboard_data(fit_dir_str: str, n_fit_files: int):
    return dash.load(Path(fit_dir_str), dash.DATE_FROM)


class _LocalGpxFile:
    """Wrapper um eine lokale GPX-Datei — gleiche Schnittstelle wie st.UploadedFile."""
    def __init__(self, path: Path):
        self.name = path.name
        self._data = path.read_bytes()

    def getvalue(self) -> bytes:
        return self._data


# ============================================================
# SESSION STATE
# ============================================================
if "uploaded_filename" not in st.session_state:
    st.session_state.uploaded_filename = None
if "analysis_df" not in st.session_state:
    st.session_state.analysis_df = pd.DataFrame()
if "analysis_params" not in st.session_state:
    st.session_state.analysis_params = DEFAULT_PARAMS.copy()
if "selected_user" not in st.session_state:
    st.session_state.selected_user = "Standard"

# ============================================================
# SIDEBAR – BENUTZER-WECHSLER
# ============================================================
st.sidebar.title("🚴 Garmin App")

users = _detect_users()
user_names = list(users.keys())

if st.session_state.selected_user not in user_names:
    st.session_state.selected_user = user_names[0]

selected_user = st.sidebar.selectbox(
    "Benutzer",
    user_names,
    index=user_names.index(st.session_state.selected_user),
)
if selected_user != st.session_state.selected_user:
    st.session_state.selected_user = selected_user
    _load_dashboard_data.clear()

user_cfg   = users[selected_user]
TOKENSTORE = user_cfg["token_path"]
EXPORT_DIR = user_cfg["export_dir"]
FIT_DIR_U  = EXPORT_DIR / "fit"
GPX_DIR_U  = EXPORT_DIR / "gpx"

with st.sidebar.expander("Benutzer verwalten"):
    st.write("**Hinzufügen**")
    new_name = st.text_input("Name (z.B. 'anna')", key="new_user_name")
    if st.button("Erstellen", key="add_user_btn"):
        new_name = new_name.strip().lower()
        # Nur a-z, 0-9, Bindestrich — kein '.', '/', '\' oder andere Sonderzeichen
        if not re.fullmatch(r"[a-z][a-z0-9\-]{0,30}", new_name):
            st.warning("Name muss mit a-z beginnen, nur a-z / 0-9 / '-' enthalten (max. 31 Zeichen).")
        elif new_name == "standard":
            st.warning("Der Name 'standard' ist reserviert.")
        elif new_name in users:
            st.warning(f"Benutzer '{new_name}' existiert bereits.")
        else:
            token_dir = Path.home() / f".garminconnect_{new_name}"
            token_dir.mkdir(exist_ok=True)
            new_export = EXPORTS_ROOT / new_name
            (new_export / "fit").mkdir(parents=True, exist_ok=True)
            (new_export / "gpx").mkdir(parents=True, exist_ok=True)
            st.session_state.selected_user = new_name
            st.rerun()

    st.markdown("---")
    st.write("**Entfernen**")
    removable = [n for n in user_names if n != "Standard"]
    if not removable:
        st.caption("Keine weiteren Benutzer vorhanden.")
    else:
        del_name = st.selectbox("Benutzer wählen", removable, key="del_user_select")
        del_data = st.checkbox("Exportdaten mitlöschen", key="del_data_chk")
        if "confirm_delete" not in st.session_state:
            st.session_state.confirm_delete = False

        if not st.session_state.confirm_delete:
            if st.button("Entfernen …", key="del_user_btn"):
                st.session_state.confirm_delete = True
                st.rerun()
        else:
            st.warning(
                f"**'{del_name}' wirklich löschen?**"
                + (" Exportdaten werden **dauerhaft gelöscht**." if del_data else "")
            )
            c1, c2 = st.columns(2)
            if c1.button("Ja, löschen", key="del_confirm_yes"):
                import shutil
                token_dir = Path.home() / f".garminconnect_{del_name}"
                if token_dir.exists():
                    shutil.rmtree(token_dir)
                if del_data:
                    export_dir = EXPORTS_ROOT / del_name
                    if export_dir.exists():
                        shutil.rmtree(export_dir)
                if selected_user == del_name:
                    st.session_state.selected_user = "Standard"
                st.session_state.confirm_delete = False
                _load_dashboard_data.clear()
                st.rerun()
            if c2.button("Abbrechen", key="del_confirm_no"):
                st.session_state.confirm_delete = False
                st.rerun()

st.sidebar.markdown("---")

# ============================================================
# SIDEBAR NAVIGATION
# ============================================================
page = st.sidebar.radio(
    "Navigation",
    ["Aktivitäten laden", "Cycling Dashboard", "GPX Einzel-Analyse", "Fahrradphysik"],
    label_visibility="collapsed",
)

# ============================================================
# PAGE 1 – AKTIVITÄTEN LADEN
# ============================================================
if page == "Aktivitäten laden":
    st.title("Neue Garmin-Aktivitäten laden")

    st.caption(f"Benutzer: **{selected_user}** · Tokens: `{TOKENSTORE}`")
    token_path = Path(TOKENSTORE)
    if token_path.exists():
        st.success("Tokens vorhanden — kein erneuter Login nötig.")
        show_credentials = False
    else:
        st.info("Noch keine gespeicherten Tokens — bitte einloggen.")
        show_credentials = True

    with st.form("download_form"):
        activity_limit = st.number_input(
            "Anzahl Aktivitäten (ACTIVITY_LIMIT)",
            min_value=1,
            max_value=1000,
            value=20,
            step=5,
            help="Wie viele der neuesten Aktivitäten sollen heruntergeladen werden?",
        )

        if show_credentials:
            email    = st.text_input("Garmin E-Mail")
            password = st.text_input("Garmin Passwort", type="password")
        else:
            email    = ""
            password = ""
            st.caption("Login erfolgt via gespeicherte Tokens – keine Eingabe nötig.")

        submitted = st.form_submit_button("Aktivitäten herunterladen", width='stretch')

    if submitted:
        env = {
            **os.environ,
            "GARMIN_ACTIVITY_LIMIT": str(int(activity_limit)),
            "GARMINTOKENS":          TOKENSTORE,
            "GARMIN_EXPORT_DIR":     str(EXPORT_DIR),
        }
        if email:
            env["GARMIN_EMAIL"] = email

        # Passwort nicht als Env-Var übergeben (lesbar via /proc/<pid>/environ).
        # Stattdessen: kurzlebige Tempfile mit Mode 0600, Pfad wird übergeben.
        _pw_file = None
        if password:
            fd, pw_path = tempfile.mkstemp(prefix=".gcpw_", suffix=".tmp")
            try:
                os.chmod(pw_path, 0o600)
                os.write(fd, password.encode())
            finally:
                os.close(fd)
            env["GARMIN_PASSWORD_FILE"] = pw_path
            _pw_file = pw_path

        st.markdown("**Download-Log:**")
        log_box = st.empty()
        lines: list[str] = []

        with st.spinner("Verbinde mit Garmin Connect..."):
            proc = subprocess.Popen(
                [sys.executable, str(PIPELINE_SCRIPT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            for line in iter(proc.stdout.readline, ""):  # type: ignore[union-attr]
                lines.append(line.rstrip())
                log_box.code("\n".join(lines[-60:]))
            proc.wait()

        if _pw_file and os.path.exists(_pw_file):
            os.unlink(_pw_file)

        if proc.returncode == 0:
            st.success("Download abgeschlossen.")
            _load_dashboard_data.clear()
        else:
            st.error(f"Fehler beim Download (exit code {proc.returncode}).")

    # Vorhandene Dateien anzeigen
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        fit_files = list(FIT_DIR_U.glob("*.fit")) if FIT_DIR_U.exists() else []
        st.metric("FIT-Dateien lokal", len(fit_files))
    with col2:
        gpx_files = list(GPX_DIR_U.glob("*.gpx")) if GPX_DIR_U.exists() else []
        st.metric("GPX-Dateien lokal", len(gpx_files))

    cycling_fit = list(FIT_DIR_U.glob("*cycling*.fit")) if FIT_DIR_U.exists() else []
    if cycling_fit:
        st.caption(f"davon Radfahrten (FIT): **{len(cycling_fit)}**")


# ============================================================
# PAGE 2 – CYCLING DASHBOARD
# ============================================================
elif page == "Cycling Dashboard":
    st.title("Cycling Dashboard")

    col_btn, col_reload = st.columns([2, 5])
    with col_btn:
        if st.button("Daten neu laden", width='stretch'):
            _load_dashboard_data.clear()
            st.rerun()

    cycling_fit = list(FIT_DIR_U.glob("*cycling*.fit")) if FIT_DIR_U.exists() else []
    if not cycling_fit:
        st.info(
            "Keine Radfahrten (FIT-Dateien) gefunden. "
            "Lade zuerst Aktivitäten auf Seite **Aktivitäten laden**."
        )
        st.stop()

    try:
        with st.spinner("Lade FIT-Daten..."):
            df = _load_dashboard_data(str(FIT_DIR_U), len(cycling_fit))
    except Exception as e:
        st.error(f"Fehler beim Laden der FIT-Daten: {e}")
        st.stop()

    k = dash.kpi(df)

    def _fmt(v, dec=1, suffix=""):
        return f"{v:,.{dec}f}{suffix}" if pd.notna(v) else "—"

    # KPIs
    kpi_cols = st.columns(4)
    kpi_data = [
        ("Fahrten",        str(k["rides"]),              "gesamt",            None),
        ("Distanz",        _fmt(k["km"], 0),              "km",                None),
        ("Fahrzeit",       _fmt(k["hours"], 1),           "h",                 None),
        ("Ø Tempo",        _fmt(k["avg_speed"]),          "km/h",              None),
        ("Ø Herzfrequenz", _fmt(k["avg_hr"], 0),          "bpm",               None),
        ("Höhenmeter",     f"{int(k['elevation']):,}",   "hm",
         "Aus Trackpunkten berechnet: Median- und Savitzky-Golay-Glättung, dann Anstiege summiert "
         "(Gerätewert total_ascent überschätzt durch GPS-Rauschen)."),
        ("Pendel-Quote",   _fmt(k["commute_pct"], 0),    "% der Werktage",    None),
        ("Wochen aktiv",   str(k["n_weeks"]),             "Wochen",            None),
    ]
    for i, (label, val, unit, help_text) in enumerate(kpi_data):
        kpi_cols[i % 4].metric(label, f"{val} {unit}".strip(), help=help_text)

    st.markdown("---")

    # Volumen & Fitness
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Wöchentliches Volumen nach Fahrttyp")
        st.plotly_chart(pio.from_json(dash.chart_volume(df)), width='stretch')
    with c2:
        st.caption("Fitness-Trend · HF & Tempo (gleitend)")
        st.plotly_chart(pio.from_json(dash.chart_fitness(df)), width='stretch')

    st.caption("Aktivitäts-Kalender · km pro Tag")
    st.plotly_chart(pio.from_json(dash.chart_calendar(df)), width='stretch')

    # Detailanalyse
    c3, c4, c5 = st.columns(3)
    with c3:
        st.caption("HF-Zonen-Verteilung (geschätzt) pro Monat")
        st.plotly_chart(pio.from_json(dash.chart_zones(df)), width='stretch')
    with c4:
        st.caption("Distanz-Verteilung nach Fahrttyp")
        st.plotly_chart(pio.from_json(dash.chart_dist_hist(df)), width='stretch')
    with c5:
        st.caption("Pendel-Quote vs. Fahrten gesamt")
        st.plotly_chart(pio.from_json(dash.chart_commute(df)), width='stretch')

    # Letzte Fahrten
    st.markdown("---")
    st.subheader("Letzte Fahrten")
    recent = df.sort_values("datum", ascending=False).head(15)[
        ["datum", "kategorie", "km", "dauer_min", "avg_speed", "avg_bpm", "elevation"]
    ].copy()
    recent["datum"] = recent["datum"].dt.strftime("%d.%m.%Y")
    recent.columns = ["Datum", "Typ", "km", "min", "km/h", "HF", "hm"]
    st.dataframe(recent, width='stretch', hide_index=True)


# ============================================================
# PAGE 3 – GPX EINZEL-ANALYSE
# ============================================================
elif page == "GPX Einzel-Analyse":
    st.title("GPX Einzel-Analyse")
    st.caption("Kalman-Filter · Steigungsberechnung · Leistungsmodell · Kartenvisualisierung")

    quelle = st.radio(
        "Quelle",
        ["Manueller Upload", "Importierte Tracks"],
        horizontal=True,
        label_visibility="collapsed",
        key="gpx_quelle",
    )

    active_file = None

    if quelle == "Manueller Upload":
        uploaded_file = st.file_uploader("GPX-Datei hochladen", type=["gpx"])
        if uploaded_file is not None:
            active_file = uploaded_file

    else:
        gpx_files = (
            sorted(GPX_DIR_U.glob("*.gpx"), key=lambda p: p.name, reverse=True)
            if GPX_DIR_U.exists() else []
        )
        if not gpx_files:
            st.info(
                f"Keine GPX-Dateien in `{GPX_DIR_U}` — "
                "lade zuerst Aktivitäten auf Seite **Aktivitäten laden**."
            )
        else:
            def _gpx_label(p: Path) -> str:
                name = p.stem  # ohne .gpx
                parts = name.split("_")
                # Garmin-Dateiname: YYYY-MM-DD_HH-MM-SS_sport_id
                try:
                    date = parts[0]          # 2025-06-12
                    time = parts[1].replace("-", ":")  # 08:30:00
                    sport = parts[2] if len(parts) > 2 else ""
                    return f"{date}  {time}  {sport}"
                except IndexError:
                    return name

            labels = [_gpx_label(p) for p in gpx_files]
            sel_idx = st.selectbox(
                "Track auswählen",
                range(len(labels)),
                format_func=lambda i: labels[i],
                key="gpx_track_select",
            )
            active_file = _LocalGpxFile(gpx_files[sel_idx])

    if active_file is not None:
        if active_file.name != st.session_state.uploaded_filename:
            st.session_state.uploaded_filename = active_file.name
            st.session_state.analysis_df = pd.DataFrame()

        st.success(f"Datei: **{active_file.name}**")

        with st.expander("Parameter anpassen", expanded=False):
            st.write("**Fahrerparameter:**")
            col1, col2 = st.columns(2)
            with col1:
                st.session_state.analysis_params["m_fahrer"] = st.slider(
                    "Fahrermasse (kg)", 40.0, 150.0,
                    st.session_state.analysis_params["m_fahrer"], 1.0, key="m_fahrer"
                )
                st.session_state.analysis_params["c_rr"] = st.slider(
                    "Rollwiderstand (c_rr)", 0.001, 0.015,
                    st.session_state.analysis_params["c_rr"], 0.0005, key="c_rr"
                )
            with col2:
                st.session_state.analysis_params["m_fahrrad"] = st.slider(
                    "Fahrradmasse (kg)", 5.0, 25.0,
                    st.session_state.analysis_params["m_fahrrad"], 0.5, key="m_fahrrad"
                )
                st.session_state.analysis_params["cwA"] = st.slider(
                    "Luftwiderstand (cwA)", 0.04, 0.70,
                    st.session_state.analysis_params["cwA"], 0.01, key="cwA"
                )

            st.markdown("---")
            st.write("**Analyse-Parameter:**")
            col_a1, col_a2, col_a3 = st.columns(3)
            with col_a1:
                st.session_state.analysis_params["GPS_STD"] = st.slider(
                    "GPS Std", 0.1, 20.0, st.session_state.analysis_params["GPS_STD"], 0.1, key="gps_std"
                )
                st.session_state.analysis_params["accel_smooth_window"] = st.slider(
                    "Accel Smooth", 1, 11,
                    st.session_state.analysis_params["accel_smooth_window"], 2, key="accel_smooth"
                )
            with col_a2:
                st.session_state.analysis_params["ACCEL_STD"] = st.slider(
                    "Accel Std", 0.1, 5.0, st.session_state.analysis_params["ACCEL_STD"], 0.1, key="accel_std"
                )
                st.session_state.analysis_params["grade_smooth_window"] = st.slider(
                    "Grade Smooth", 1, 11,
                    st.session_state.analysis_params["grade_smooth_window"], 2, key="grade_smooth"
                )
            with col_a3:
                st.session_state.analysis_params["power_smooth_window"] = st.slider(
                    "Power Smooth", 1, 11,
                    st.session_state.analysis_params["power_smooth_window"], 2, key="power_smooth"
                )
                st.session_state.analysis_params["ELEV_PLOT_MEDIAN_KERNEL"] = st.slider(
                    "Elev Median Kernel", 1, 15,
                    st.session_state.analysis_params["ELEV_PLOT_MEDIAN_KERNEL"], 2, key="elev_median"
                )

            st.markdown("---")
            if st.button("Parameter zurücksetzen", key="reset_params"):
                st.session_state.analysis_params = DEFAULT_PARAMS.copy()
                st.rerun()

        if st.button("Analysieren", width='stretch', key="analyze_btn"):
            try:
                with st.spinner("Analysiere GPX..."):
                    df_raw = load_gpx_data_from_upload(active_file)
                    df_analyzed = analyze_gpx_data(df_raw, st.session_state.analysis_params)
                    st.session_state.analysis_df = df_analyzed
                st.success("Analyse abgeschlossen.")
            except Exception as e:
                st.error(f"Fehler: {e}")

    if not st.session_state.analysis_df.empty:
        df = st.session_state.analysis_df
        stats = get_stats(df)

        st.markdown("---")
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Distanz", f"{stats['distance_km']:.2f} km")
        c2.metric("Dauer", f"{stats['duration_hours']:.2f} h")
        c3.metric("Aufstieg", f"{stats['total_gain_m']:.0f} m")
        c4.metric("Ø Tempo", f"{stats['avg_speed_kmh']:.1f} km/h")
        c5.metric("Max Tempo", f"{stats['max_speed_kmh']:.1f} km/h")
        c6.metric("Ø Leistung", f"{stats['avg_power_w']:.0f} W")

        st.markdown("---")
        subtab1, subtab2, subtab3 = st.tabs(["Zeitverläufe", "Karte", "Export"])

        with subtab1:
            fig = plot_time_profiles(df, st.session_state.analysis_params)
            st.pyplot(fig, width='stretch')

        with subtab2:
            try:
                m = create_speed_map(df)
                components.html(m._repr_html_(), height=700)
            except Exception as e:
                st.error(f"Karte konnte nicht erstellt werden: {e}")

        with subtab3:
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                csv_full = df.to_csv(index=False).encode("utf-8")
                fname = (active_file.name if active_file else "gpx").replace(".gpx", "")
                st.download_button("Alle Daten (CSV)", csv_full,
                                   file_name=f"garmin_full_{fname}.csv", mime="text/csv",
                                   width='stretch')

                export_cols = ["elapsed_time", "latitude_smooth", "longitude_smooth",
                               "speed_smooth", "a_smooth", "ele_smooth",
                               "steigung_modell_smooth", "P_gesamt_fahrer_smooth"]
                if all(c in df.columns for c in export_cols):
                    csv_metrics = df[export_cols].to_csv(index=False).encode("utf-8")
                    st.download_button("Wichtige Metriken (CSV)", csv_metrics,
                                       file_name=f"garmin_metrics_{fname}.csv", mime="text/csv",
                                       width='stretch')

            with col_e2:
                summary = {
                    "Metrik": ["Distanz", "Dauer", "Aufstieg", "Ø Tempo",
                               "Max Tempo", "Max Leistung", "Ø Leistung"],
                    "Wert": [
                        f"{stats['distance_km']:.2f} km",
                        f"{stats['duration_hours']:.2f} h",
                        f"{stats['total_gain_m']:.0f} m",
                        f"{stats['avg_speed_kmh']:.1f} km/h",
                        f"{stats['max_speed_kmh']:.1f} km/h",
                        f"{stats['max_power_w']:.0f} W",
                        f"{stats['avg_power_w']:.0f} W",
                    ],
                }
                df_sum = pd.DataFrame(summary)
                st.dataframe(df_sum, width='stretch', hide_index=True)
                st.download_button(
                    "Zusammenfassung (CSV)",
                    df_sum.to_csv(index=False).encode("utf-8"),
                    file_name=f"garmin_summary_{fname}.csv",
                    mime="text/csv",
                    width='stretch',
                )


# ============================================================
# PAGE 4 – FAHRRADPHYSIK
# ============================================================
elif page == "Fahrradphysik":
    st.title("Fahrradphysik – Widerstandsmodell")
    st.caption("Roll- · Luft- · Steigungswiderstand · Leistungsberechnung")

    st.markdown("""
## Grundlagen

Die Gesamtleistung des Fahrers setzt sich aus vier Anteilen zusammen:

```
F_gesamt = F_roll + F_luft + F_steigung + F_beschl
P = F × v
```

---

### 1. Rollwiderstand
```
F_roll = c_rr × m_ges × g
```
| Untergrund | c_rr |
|---|---|
| Glatter Asphalt | 0,004 – 0,005 |
| Rauer Asphalt / Schotter | 0,006 – 0,008 |
| Grober Untergrund / Waldweg | 0,010 – 0,015 |

---

### 2. Luftwiderstand
```
F_luft = 0,5 × ρ × cwA × v²
```
| Position | cwA (m²) |
|---|---|
| Aufrecht (City/Hollandrad) | 0,55 – 0,70 |
| Sportlich (Trekking/MTB) | 0,40 – 0,50 |
| Gravel/Rennrad Oberlenker | 0,32 – 0,40 |
| Rennrad Unterlenker | 0,28 – 0,32 |

> Der Luftwiderstand wächst **quadratisch** mit der Geschwindigkeit.
> Bei 40 km/h macht er ~50 % der Gesamtkraft aus.

---

### 3. Steigungswiderstand
```
F_steigung = m_ges × g × grade   (grade in Dezimalform)
```

---

### 4. Beschleunigungskraft
```
F_beschl = m_eff × a    mit   m_eff = m_ges × (1 + m_rot_zuschlag)
```
Rotationszuschlag (Standard: 3 %) berücksichtigt die Trägheit der Laufräder.

---

### Gesamtleistung
```
P_gesamt = P_roll + P_luft + P_steigung + P_beschl
P_fahrer  = max(0, P_gesamt)   # bergab = 0
```
""")

    st.markdown("---")
    st.subheader("Interaktiver Rechner")

    col_l, col_r = st.columns(2)
    with col_l:
        st.write("**Fahrerparameter:**")
        m_f   = st.number_input("Fahrermasse (kg)", value=80.0, min_value=40.0, max_value=150.0)
        m_b   = st.number_input("Fahrradmasse (kg)", value=15.0, min_value=5.0, max_value=30.0)
        v_kmh = st.slider("Geschwindigkeit (km/h)", 5.0, 60.0, 30.0, 1.0)

    with col_r:
        st.write("**Widerstände:**")
        c_rr_c = st.select_slider(
            "Rollwiderstand (c_rr)",
            options=[0.004, 0.005, 0.006, 0.007, 0.008, 0.010, 0.012],
            value=0.007,
        )
        cwA_c = st.select_slider(
            "Luftwiderstand (cwA m²)",
            options=[0.28, 0.32, 0.35, 0.40, 0.50, 0.60, 0.70],
            value=0.40,
        )

    grade_pct = st.slider("Steigung (%)", -10.0, 20.0, 0.0, 0.5)

    m_ges  = m_f + m_b
    v_ms   = v_kmh / 3.6
    grade  = grade_pct / 100.0

    F_roll      = c_rr_c * m_ges * g
    F_luft      = 0.5 * rho * cwA_c * v_ms**2
    F_steigung  = m_ges * g * grade
    F_gesamt    = F_roll + F_luft + F_steigung

    P_roll      = F_roll * v_ms
    P_luft      = F_luft * v_ms
    P_steigung  = F_steigung * v_ms
    P_gesamt    = F_gesamt * v_ms

    st.markdown("---")
    st.subheader("Kräfte (N)")
    cf1, cf2, cf3, cf4 = st.columns(4)
    cf1.metric("F_roll",      f"{F_roll:.1f} N")
    cf2.metric("F_luft",      f"{F_luft:.1f} N")
    cf3.metric("F_steigung",  f"{F_steigung:.1f} N")
    cf4.metric("F_gesamt",    f"{F_gesamt:.1f} N")

    st.subheader("Leistung (W)")
    cp1, cp2, cp3, cp4 = st.columns(4)
    cp1.metric("P_roll",      f"{max(0, P_roll):.0f} W")
    cp2.metric("P_luft",      f"{max(0, P_luft):.0f} W")
    cp3.metric("P_steigung",  f"{max(0, P_steigung):.0f} W")
    cp4.metric("P_gesamt",    f"{max(0, P_gesamt):.0f} W")

    st.markdown("---")
    col_ch1, col_ch2 = st.columns(2)
    with col_ch1:
        st.subheader("Kraft-Verteilung")
        st.bar_chart({"Rollwiderstand": abs(F_roll), "Luftwiderstand": abs(F_luft), "Steigung": abs(F_steigung)})
    with col_ch2:
        st.subheader("Leistungs-Verteilung")
        st.bar_chart({"Rollwiderstand": max(0, P_roll), "Luftwiderstand": max(0, P_luft), "Steigung": max(0, P_steigung)})

    st.markdown("---")
    st.markdown("""
**Tipps:**
- **Bergauf:** Steigungswiderstand dominiert → Gewicht entscheidend
- **Flach, schnell:** Luftwiderstand dominiert → Körperhaltung entscheidend
- **Sprinten:** Beschleunigungskraft ist kurzzeitig sehr hoch
""")

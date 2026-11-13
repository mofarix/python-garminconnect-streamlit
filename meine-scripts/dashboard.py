"""
dashboard.py – Cycling-Dashboard-Modul aus Garmin FIT-Daten

Liest FIT-Dateien aus garminexports/standard/fit/ und stellt Diagramme und KPIs bereit.
Wird von app.py als Modul genutzt; kann auch standalone KPIs ausgeben.

    pip install fitdecode pandas plotly numpy
    python meine-scripts/dashboard.py
"""

import math
from pathlib import Path

import json
import time

import fitdecode
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from gpx_analysis import total_ascent_m

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
FIT_DIR    = Path("./garminexports/standard/fit")
DATE_FROM  = pd.Timestamp("2024-01-01")   # alles ab hier einlesen

# Höhenmeter: Garmin summiert in total_ascent jeden GPS-Jitter mit. Stattdessen
# werden die rohen Altitude-Trackpunkte mit Median- + Savitzky-Golay-Filter
# geglättet (gpx_analysis.total_ascent_m) und erst dann die Anstiege summiert.
# Cache-Version: bei Änderungen an der Parse-Logik erhöhen → automatische Re-Parse.
CACHE_VERSION = "v3-elev-savgol"

# Klassifikations-Schwellen (identisch mit 33_)
KURZ_MAX_KM   = 7
PENDEL_KM     = (7, 13)
PENDEL_MIN    = (20, 45)
MITTEL_MIN_KM = 13
MITTEL_MAX_KM = 20
LANG_MIN_KM   = 20
TRAIN_MIN_BPM = 140

# ---------------------------------------------------------------------------
# Farben
# ---------------------------------------------------------------------------
C = {
    "Training":     "#e05252",
    "Regeneration": "#4f98a3",
    "Pendeln":      "#6daa45",
    "Mittelstrecke":"#e8af34",
    "Kurzstrecke":  "#666662",
    "Unbekannt":    "#444440",
    "Z1": "#1b4332", "Z2": "#52b788",
    "Z3": "#f4a261", "Z4": "#e76f51", "Z5": "#c1121f",
    "hr": "#e05252", "speed": "#52b788",
    "grid": "rgba(255,255,255,0.06)",
    "text": "#d4d3d0", "muted": "#7a7976",
    "surface": "#161614", "border": "rgba(255,255,255,0.07)",
}

# ---------------------------------------------------------------------------
# FIT einlesen
# ---------------------------------------------------------------------------
def _read_fit_session(fit_path: Path) -> dict | None:
    session: dict = {}
    altitudes: list[float] = []
    try:
        with fitdecode.FitReader(fit_path) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue

                if frame.name == "record":
                    for field in frame.fields:
                        if field.name in ("enhanced_altitude", "altitude") and field.value is not None:
                            altitudes.append(float(field.value))
                            break

                elif frame.name == "session":
                    def get_first(*names):
                        for name in names:
                            for field in frame.fields:
                                if field.name == name and field.value is not None:
                                    return field.value
                        return None

                    start = get_first("start_time")
                    if start is None:
                        continue

                    dist    = get_first("total_distance")
                    elapsed = get_first("total_elapsed_time")
                    avg_spd = get_first("enhanced_avg_speed", "avg_speed")
                    max_spd = get_first("enhanced_max_speed", "max_speed")

                    session = {
                        "datum":     pd.Timestamp(start).tz_convert(None),
                        "km":        dist / 1000 if dist is not None else float("nan"),
                        "dauer_min": elapsed / 60 if elapsed is not None else float("nan"),
                        "avg_speed": avg_spd * 3.6 if avg_spd is not None else float("nan"),
                        "max_speed": max_spd * 3.6 if max_spd is not None else float("nan"),
                        "avg_bpm":   float(get_first("avg_heart_rate") or float("nan")),
                        "max_bpm":   float(get_first("max_heart_rate") or float("nan")),
                    }

        if not session:
            return None

        # Geglättete Höhenmeter aus Trackpunkten (gleiche Methode wie GPX-Analyse)
        session["elevation"] = int(round(total_ascent_m(altitudes)))

        return session

    except Exception as e:
        print(f"  Warnung: {fit_path.name} ({e})")
    return None


def classify(row) -> str:
    km, min_, bpm = row["km"], row["dauer_min"], row["avg_bpm"]
    if pd.isna(km):
        return "Unbekannt"
    if km < KURZ_MAX_KM:
        return "Kurzstrecke"
    if (PENDEL_KM[0] <= km <= PENDEL_KM[1]
            and pd.notna(min_) and PENDEL_MIN[0] <= min_ <= PENDEL_MIN[1]):
        return "Pendeln"
    if MITTEL_MIN_KM <= km < MITTEL_MAX_KM:
        return "Mittelstrecke"
    if km >= LANG_MIN_KM:
        return "Training" if pd.notna(bpm) and bpm >= TRAIN_MIN_BPM else "Regeneration"
    return "Unbekannt"


def _zones(kat: str) -> list[float]:
    if kat == "Regeneration":  return [0.18, 0.52, 0.23, 0.06, 0.01]
    if kat in ("Pendeln", "Mittelstrecke"): return [0.08, 0.33, 0.42, 0.15, 0.02]
    if kat == "Training":      return [0.04, 0.18, 0.36, 0.31, 0.11]
    return                            [0.15, 0.40, 0.30, 0.12, 0.03]


def _load_cache(cache_file: Path) -> dict:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
        if data.get("_version") != CACHE_VERSION:
            return {}   # veraltete Logik → alle Dateien neu parsen
        return data
    except Exception:
        return {}


def _save_cache(cache: dict, cache_file: Path) -> None:
    cache["_version"] = CACHE_VERSION
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def load(fit_dir: Path, date_from: pd.Timestamp) -> pd.DataFrame:
    fit_files = sorted(fit_dir.glob("*cycling*.fit"))
    if not fit_files:
        raise FileNotFoundError(f"Keine *cycling*.fit Dateien in {fit_dir}")

    cache_file = fit_dir.parent / ".dashboard_cache.json"
    cache = _load_cache(cache_file)
    rows: list[dict] = []
    new_parsed = 0
    t0 = time.perf_counter()

    for f in fit_files:
        key = f.name
        if key in cache:
            raw = {**cache[key], "datum": pd.Timestamp(cache[key]["datum"])}
            rows.append(raw)
        else:
            r = _read_fit_session(f)
            if r is not None:
                cache[key] = {**r, "datum": r["datum"].isoformat()}
                rows.append(r)
                new_parsed += 1

    if new_parsed:
        _save_cache(cache, cache_file)
        elapsed = time.perf_counter() - t0
        print(f"  {new_parsed} neue Dateien geparst ({elapsed:.1f}s), "
              f"{len(fit_files) - new_parsed} aus Cache.")
    else:
        print(f"  Alle {len(fit_files)} Dateien aus Cache geladen.")

    if not rows:
        raise ValueError("Keine session-Daten in den FIT-Dateien gefunden.")

    df = pd.DataFrame(rows)
    df = df[df["datum"] >= date_from].sort_values("datum").reset_index(drop=True)
    df["kategorie"] = df.apply(classify, axis=1)

    # Abgeleitete Felder
    _hr = df["avg_bpm"].fillna(150)
    df["load"]       = (df["dauer_min"] / 60 * (_hr / 180) ** 2 * 100).round(1)
    df["efficiency"] = (df["km"] / (_hr * df["dauer_min"] / 60) * 1000).round(3)
    df["week"]       = df["datum"].dt.to_period("W").apply(lambda p: p.start_time)
    df["month"]      = df["datum"].dt.to_period("M").apply(lambda p: p.start_time)
    for i, col in enumerate(["z1","z2","z3","z4","z5"]):
        df[col] = df["kategorie"].map(lambda k, i=i: _zones(k)[i])
    return df

# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------
def kpi(df: pd.DataFrame) -> dict:
    start, end = df["datum"].min(), df["datum"].max()
    weekdays = sum(1 for d in pd.date_range(start, end) if d.weekday() < 5)
    commutes = (df["kategorie"] == "Pendeln").sum()

    pen = df[df["kategorie"] == "Pendeln"].sort_values("datum")
    if len(pen) >= 4:
        mid = max(1, len(pen) // 2)
        hr_delta = pen.iloc[:mid]["avg_bpm"].mean() - pen.iloc[mid:]["avg_bpm"].mean()
    else:
        hr_delta = float("nan")

    return {
        "rides":       len(df),
        "km":          df["km"].sum(),
        "hours":       df["dauer_min"].sum() / 60,
        "avg_speed":   df["avg_speed"].mean(),
        "avg_hr":      df["avg_bpm"].mean(),
        "elevation":   df["elevation"].sum(),
        "commute_pct": commutes / weekdays * 100 if weekdays else 0,
        "hr_delta":    hr_delta,
        "n_weeks":     df["week"].nunique(),
    }

# ---------------------------------------------------------------------------
# Chart-Basis
def _kw_label(t: pd.Timestamp) -> str:
    kw, yr = t.isocalendar()[1], t.year % 100
    return f"KW {kw:02d} '{yr:02d}"

def _kw_ticks(min_week: pd.Timestamp, max_week: pd.Timestamp, step: int = 8):
    weeks = pd.date_range(min_week, max_week, freq="W-MON")
    ticks = weeks[::step]
    if max_week not in ticks:
        ticks = ticks.append(pd.DatetimeIndex([max_week]))
    return ticks.tolist(), [_kw_label(t) for t in ticks]

# ---------------------------------------------------------------------------
def _base(height=320, b=75) -> dict:
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C["text"], family="'Inter',system-ui,sans-serif", size=12),
        margin=dict(l=52, r=16, t=10, b=b), height=height,
        legend=dict(orientation="h", y=-0.30, x=0.5, xanchor="center",
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        xaxis=dict(showgrid=False, linecolor="#393836", tickfont=dict(size=11)),
        yaxis=dict(gridcolor=C["grid"], zeroline=False, tickfont=dict(size=11)),
    )

# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart_volume(df: pd.DataFrame) -> str:
    wk = df.groupby(["week", "kategorie"])["km"].sum().reset_index()
    cats = [c for c in ["Pendeln","Training","Mittelstrecke","Regeneration","Kurzstrecke","Unbekannt"]
            if c in wk["kategorie"].values]
    fig = go.Figure()
    for cat in cats:
        s = wk[wk["kategorie"] == cat]
        fig.add_trace(go.Bar(x=s["week"], y=s["km"].round(1), name=cat,
                             marker_color=C.get(cat,"#555"), marker_line_width=0))
    trend = df.groupby("week")["km"].sum().rolling(4, min_periods=1).mean()
    fig.add_trace(go.Scatter(x=trend.index, y=trend.round(1).values,
                             name="Ø 4W", mode="lines",
                             line=dict(color="#fff", width=1.5, dash="dot")))
    tickvals, ticktext = _kw_ticks(df["week"].min(), df["week"].max())
    fig.update_layout(**_base(), barmode="stack", yaxis_title="km / Woche")
    fig.update_xaxes(tickmode="array", tickvals=tickvals, ticktext=ticktext)
    return pio.to_json(fig)


def chart_fitness(df: pd.DataFrame) -> str:
    """HF-Drift + Tempo auf der Pendelstrecke. Fallback auf alle Fahrten."""
    pen = df[df["kategorie"] == "Pendeln"]
    src = pen if len(pen) >= 3 else df
    label = "Pendeln" if len(pen) >= 3 else "alle Fahrten"

    roll = min(4, max(2, src["week"].nunique() // 2))
    hr  = src.groupby("week")["avg_bpm"].mean().rolling(roll, min_periods=1).mean()
    spd = src.groupby("week")["avg_speed"].mean().rolling(roll, min_periods=1).mean()

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=hr.index, y=hr.round(1).values,
                             name=f"Ø HF {label}", mode="lines",
                             line=dict(color=C["hr"], width=2.5)),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=spd.index, y=spd.round(2).values,
                             name=f"Ø Tempo {label}", mode="lines",
                             line=dict(color=C["speed"], width=2.5)),
                  secondary_y=True)
    base = _base()
    base.pop("yaxis", None)
    fig.update_layout(**base)
    fig.update_yaxes(title_text="HF (bpm)", secondary_y=False,
                     gridcolor=C["grid"], zeroline=False, tickfont=dict(size=11))
    fig.update_yaxes(title_text="km/h", secondary_y=True,
                     gridcolor="rgba(0,0,0,0)", zeroline=False, tickfont=dict(size=11))
    tickvals, ticktext = _kw_ticks(src["week"].min(), src["week"].max())
    fig.update_xaxes(showgrid=False, linecolor="#393836", tickfont=dict(size=11),
                     tickmode="array", tickvals=tickvals, ticktext=ticktext)
    return pio.to_json(fig)


def chart_calendar(df: pd.DataFrame) -> str:
    # dt.normalize() entfernt die Uhrzeit — sonst findet reindex() keine Matches
    day_col = df["datum"].dt.normalize()
    daily_km    = df.groupby(day_col)["km"].sum()
    daily_count = df.groupby(day_col)["km"].count()

    all_days = pd.date_range(day_col.min(), day_col.max(), freq="D")
    daily_km    = daily_km.reindex(all_days, fill_value=0)
    daily_count = daily_count.reindex(all_days, fill_value=0)

    daily = pd.DataFrame({"datum": all_days, "km": daily_km.values,
                          "n": daily_count.values})
    daily["dow"]        = daily["datum"].dt.weekday
    daily["week_start"] = daily["datum"] - pd.to_timedelta(daily["dow"], unit="D")

    pivot_km = daily.pivot_table(index="dow", columns="week_start",
                                 values="km", fill_value=0)
    pivot_n  = daily.pivot_table(index="dow", columns="week_start",
                                 values="n",  fill_value=0)

    # zmax auf 95. Perzentile der aktiven Tage setzen – Colorscale nicht von Ausreißern dominiert
    nonzero = pivot_km.values[pivot_km.values > 0]
    zmax = float(np.percentile(nonzero, 95)) if len(nonzero) else 30

    # Tatsächliches Datum jeder Zelle: week_start + dow Tage
    date_grid = np.array([
        [(ws + pd.Timedelta(days=int(d))).strftime("%d.%m.%Y") for ws in pivot_km.columns]
        for d in pivot_km.index
    ], dtype=object)
    customdata = np.stack([date_grid, pivot_n.values.astype(int)], axis=-1)

    fig = go.Figure(go.Heatmap(
        z=pivot_km.values,
        customdata=customdata,
        x=[c.strftime("%d.%m.%y") for c in pivot_km.columns],
        y=["Mo","Di","Mi","Do","Fr","Sa","So"],
        zmin=0, zmax=zmax,
        colorscale=[[0,"#141412"],[0.001,"#1a3328"],[0.15,"#2d6a4f"],
                    [0.55,"#52b788"],[1.0,"#b7e4c7"]],
        hoverongaps=False,
        hovertemplate="<b>%{customdata[0]}</b><br>%{z:.1f} km · %{customdata[1]} Fahrt(en)<extra></extra>",
        showscale=True,
        colorbar=dict(thickness=8, len=0.85, x=1.01,
                      tickfont=dict(size=9, color="#7a7976"),
                      title=dict(text="km", font=dict(size=9, color="#7a7976"),
                                 side="right")),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=C["text"], family="'Inter',system-ui,sans-serif", size=11),
        margin=dict(l=36, r=10, t=6, b=28), height=148,
        xaxis=dict(showgrid=False, tickformat="%b %y", nticks=10,
                   tickfont=dict(size=10), linecolor="#393836"),
        yaxis=dict(showgrid=False, tickfont=dict(size=10), autorange="reversed"),
    )
    return pio.to_json(fig)


def chart_zones(df: pd.DataFrame) -> str:
    m = df.groupby("month")[["z1","z2","z3","z4","z5"]].mean()
    labels = [str(x.date())[:7] for x in m.index]
    zones = [("Z1 <60%","z1",C["Z1"]), ("Z2 60–70%","z2",C["Z2"]),
             ("Z3 70–80%","z3",C["Z3"]), ("Z4 80–90%","z4",C["Z4"]),
             ("Z5 >90%", "z5",C["Z5"])]
    fig = go.Figure()
    for name, col, color in zones:
        fig.add_trace(go.Bar(x=labels, y=(m[col]*100).round(1),
                             name=name, marker_color=color, marker_line_width=0))
    fig.update_layout(**_base(280, b=80), barmode="stack", yaxis_title="% Zeit")
    return pio.to_json(fig)


def chart_dist_hist(df: pd.DataFrame) -> str:
    max_km = math.ceil(df["km"].max() / 10) * 10
    bins = [0, 5, 8, 12, 20, 30, 40, 55, 75, max(110, max_km + 1)]
    labels = [f"{bins[i]}–{bins[i+1]}" for i in range(len(bins)-1)]
    cats = [c for c in ["Pendeln","Training","Mittelstrecke","Regeneration","Kurzstrecke"]
            if c in df["kategorie"].values]
    fig = go.Figure()
    for cat in cats:
        counts, _ = np.histogram(df[df["kategorie"]==cat]["km"].dropna(), bins=bins)
        fig.add_trace(go.Bar(x=labels, y=counts, name=cat,
                             marker_color=C.get(cat,"#555"), marker_line_width=0))
    base = _base(280, b=60)
    base["xaxis"]["title_text"] = "Distanz (km)"
    fig.update_layout(**base, barmode="stack", yaxis_title="Anzahl")
    return pio.to_json(fig)


def chart_commute(df: pd.DataFrame) -> str:
    months = sorted(df["month"].unique())
    rates, totals = [], []
    for m in months:
        m_end = m + pd.offsets.MonthEnd(1)
        wd = sum(1 for d in pd.date_range(m, m_end) if d.weekday() < 5)
        commutes = ((df["month"]==m) & (df["kategorie"]=="Pendeln")).sum()
        rates.append(round(commutes / wd * 100, 1) if wd else 0)
        totals.append(int((df["month"]==m).sum()))
    xlabels = [str(m.date())[:7] for m in months]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=xlabels, y=rates, name="Pendel-Quote %",
                         marker_color=C["Pendeln"], marker_line_width=0),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=xlabels, y=totals, name="Fahrten gesamt",
                             mode="lines+markers",
                             line=dict(color="#aaa", width=1.5),
                             marker=dict(size=5)),
                  secondary_y=True)
    base = _base(280, b=60)
    base.pop("yaxis", None)
    fig.update_layout(**base)
    fig.update_yaxes(title_text="Pendel-Quote (%)", range=[0, 105],
                     secondary_y=False, gridcolor=C["grid"],
                     zeroline=False, tickfont=dict(size=11))
    fig.update_yaxes(title_text="Fahrten", secondary_y=True,
                     gridcolor="rgba(0,0,0,0)", zeroline=False, tickfont=dict(size=11))
    fig.update_xaxes(showgrid=False, linecolor="#393836", tickfont=dict(size=10))
    return pio.to_json(fig)

# ---------------------------------------------------------------------------
# Jahresübersicht (neues Dashboard-Layout, UI in dashboard_view.py)
#
# Höhenmeter kommen in allen Funktionen aus der Spalte "elevation", also aus
# gpx_analysis.total_ascent_m (geglättete Trackpunkte) – nie aus dem
# Gerätewert total_ascent der FIT-Session.
# ---------------------------------------------------------------------------
ACCENT = "#3FCF8E"
WARN   = "#F2A541"
N_CARD = "#181D24"
N_EMPTY = "#262C35"
N_LINE = "#3A424D"
N_TEXT = "#E8EAED"
N_MUTED = "#9AA3AE"
MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
METRICS = {  # Schlüssel → (Spalte, Faktor, Einheit)
    "km":   ("km", 1.0, "km"),
    "Hm":   ("elevation", 1.0, "m"),
    "Zeit": ("dauer_min", 1 / 60, "h"),
}


def de(x, dec: int = 0) -> str:
    """Zahl im deutschen Format (1.234,5); NaN → Gedankenstrich."""
    if x is None or pd.isna(x):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _mix(fg: str, bg: str, a: float) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(a * x + (1 - a) * y) for x, y in zip(f, b))


def _nbase(height: int = 300) -> dict:
    return dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=N_TEXT, family="'Inter',system-ui,sans-serif", size=12),
        margin=dict(l=48, r=12, t=10, b=36), height=height, showlegend=False,
        hoverlabel=dict(bgcolor="#0F1318", bordercolor=N_LINE, font=dict(color=N_TEXT)),
        xaxis=dict(showgrid=False, linecolor=N_LINE, tickfont=dict(size=11, color=N_MUTED)),
        yaxis=dict(gridcolor=N_EMPTY, zeroline=False, tickfont=dict(size=11, color=N_MUTED)),
    )


def years(df: pd.DataFrame) -> list[int]:
    return sorted((int(y) for y in df["datum"].dt.year.unique()), reverse=True)


def year_df(df: pd.DataFrame, year: int) -> pd.DataFrame:
    return df[df["datum"].dt.year == year]


def _cutoff_doy(df: pd.DataFrame, year: int) -> int:
    """Letzter Tag des Jahres mit Daten: im laufenden Jahr die jüngste Fahrt, sonst Jahresende."""
    last = df["datum"].max()
    return int(last.dayofyear) if year == last.year else 366


def _totals(d: pd.DataFrame) -> dict:
    v = d.dropna(subset=["km", "avg_speed"])
    v = v[v["avg_speed"] > 0]
    move_h = (v["km"] / v["avg_speed"]).sum()
    return {
        "rides": len(d),
        "km": d["km"].sum(),
        "elevation": d["elevation"].sum(),
        "hours": d["dauer_min"].sum() / 60,
        "speed": v["km"].sum() / move_h if move_h else float("nan"),
    }


def kpi_year(df: pd.DataFrame, year: int) -> list[dict]:
    """Kennzahlen eines Jahres plus Veränderung zum Vorjahr am selben Stichtag (delta in %, None ohne Vorjahr)."""
    cut = _cutoff_doy(df, year)
    cur = _totals(year_df(df, year))
    prev_d = year_df(df, year - 1)
    prev_d = prev_d[prev_d["datum"].dt.dayofyear <= cut]
    prev = _totals(prev_d) if len(prev_d) else None

    def delta(key):
        if not prev or not prev[key] or pd.isna(prev[key]) or pd.isna(cur[key]):
            return None
        return (cur[key] - prev[key]) / prev[key] * 100

    return [
        {"label": "Fahrten",    "value": de(cur["rides"]),      "unit": "",     "delta": delta("rides")},
        {"label": "Distanz",    "value": de(cur["km"]),         "unit": "km",   "delta": delta("km")},
        {"label": "Höhenmeter", "value": de(cur["elevation"]),  "unit": "m",    "delta": delta("elevation")},
        {"label": "Fahrzeit",   "value": de(cur["hours"]),      "unit": "h",    "delta": delta("hours")},
        {"label": "Ø Tempo",    "value": de(cur["speed"], 1),   "unit": "km/h", "delta": delta("speed")},
    ]


def activity_stats(df: pd.DataFrame, year: int) -> dict:
    d = year_df(df, year)
    weeks = sorted(d["week"].unique())
    best = run = 0
    prev = None
    for w in weeks:
        run = run + 1 if prev is not None and (w - prev).days == 7 else 1
        best = max(best, run)
        prev = w
    return {"active_days": int(d["datum"].dt.normalize().nunique()), "streak_weeks": best}


def chart_activity(df: pd.DataFrame, year: int) -> str:
    """Heatmap im GitHub-Stil: Spalten = Wochen, Zeilen = Mo–So, Farbe = Tagesdistanz (Quartile der aktiven Tage)."""
    d = year_df(df, year)
    g = d.groupby(d["datum"].dt.normalize()).agg(
        km=("km", "sum"), hm=("elevation", "sum"), n=("km", "count"))
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    g = g.reindex(days)
    last = df["datum"].max().normalize()
    off = days[0].weekday()
    ncol = (len(days) + off - 1) // 7 + 1

    active = g["km"].dropna()
    qs = np.quantile(active, [0.25, 0.5, 0.75]) if len(active) else np.zeros(3)
    z = np.full((7, ncol), np.nan)
    text = np.full((7, ncol), "", dtype=object)
    for i, day in enumerate(days):
        if day > last:
            continue                      # Zukunft bleibt leer
        col, row = (i + off) // 7, day.weekday()
        km = g["km"].iloc[i]
        if pd.isna(km):
            z[row, col] = 0
            text[row, col] = f"{day:%d.%m.%Y} · keine Fahrt"
        else:
            z[row, col] = 1 + int(np.searchsorted(qs, km, side="right"))
            text[row, col] = (f"<b>{day:%d.%m.%Y}</b><br>{de(km, 1)} km · {de(g['hm'].iloc[i])} m"
                              f" · {int(g['n'].iloc[i])} Fahrt(en)")

    lv = [N_EMPTY] + [_mix(ACCENT, N_CARD, a) for a in (0.30, 0.52, 0.76, 1.0)]
    scale = []
    for k, color in enumerate(lv):
        scale += [[k / 5, color], [(k + 1) / 5, color]]

    fig = go.Figure(go.Heatmap(
        z=z, text=text, x=list(range(ncol)), y=list(range(7)),
        zmin=-0.5, zmax=4.5, colorscale=scale, showscale=False,
        xgap=4, ygap=4, hoverongaps=False,
        hovertemplate="%{text}<extra></extra>",
    ))
    month_cols = [(pd.Timestamp(year, m, 1).dayofyear - 1 + off) // 7 for m in range(1, 13)]
    layout = _nbase(190)
    layout["margin"] = dict(l=30, r=6, t=22, b=6)
    layout["xaxis"] = dict(showgrid=False, zeroline=False, side="top", tickmode="array",
                           tickvals=month_cols, ticktext=MONATE,
                           tickfont=dict(size=11, color=N_MUTED), fixedrange=True)
    layout["yaxis"] = dict(showgrid=False, zeroline=False, autorange="reversed", tickmode="array",
                           tickvals=[0, 2, 4, 6], ticktext=["Mo", "Mi", "Fr", "So"],
                           tickfont=dict(size=11, color=N_MUTED), fixedrange=True)
    fig.update_layout(**layout)
    return pio.to_json(fig)


def activity_legend_colors() -> list[str]:
    return [N_EMPTY] + [_mix(ACCENT, N_CARD, a) for a in (0.30, 0.52, 0.76, 1.0)]


def year_forecast(df: pd.DataFrame, year: int) -> float | None:
    """Hochrechnung der Jahresdistanz; nur im laufenden Jahr, sonst None."""
    last = df["datum"].max()
    if year != last.year:
        return None
    ndays = 366 if pd.Timestamp(year, 1, 1).is_leap_year else 365
    return float(year_df(df, year)["km"].sum() / last.dayofyear * ndays)


def chart_progress(df: pd.DataFrame, year: int, goal_km: float) -> str:
    """Kumulierte Distanz des Jahres gegen Vorjahr und linearen Zielpfad."""
    ndays = 366 if pd.Timestamp(year, 1, 1).is_leap_year else 365
    upto = min(_cutoff_doy(df, year), ndays)

    def cum(y, n):
        d = year_df(df, y)
        s = d.groupby(d["datum"].dt.dayofyear)["km"].sum()
        return s.reindex(range(1, n + 1), fill_value=0).cumsum()

    fig = go.Figure()
    if goal_km:
        fig.add_trace(go.Scatter(x=[1, ndays], y=[0, goal_km], mode="lines", name="Zielpfad",
                                 line=dict(color=WARN, width=1.5, dash="dash"),
                                 hovertemplate="Zielpfad: %{y:.0f} km<extra></extra>"))
    if (df["datum"].dt.year == year - 1).any():
        p = cum(year - 1, 365)
        fig.add_trace(go.Scatter(x=p.index, y=p.round(0).values, mode="lines", name=str(year - 1),
                                 line=dict(color="#6B7684", width=1.5),
                                 hovertemplate=f"{year - 1}: %{{y:.0f}} km<extra></extra>"))
    c = cum(year, upto)
    fig.add_trace(go.Scatter(x=c.index, y=c.round(0).values, mode="lines", name=str(year),
                             line=dict(color=ACCENT, width=2.5),
                             hovertemplate=f"{year}: %{{y:.0f}} km<extra></extra>"))
    fig.add_trace(go.Scatter(x=[c.index[-1]], y=[round(c.iloc[-1])], mode="markers", name="Stand",
                             marker=dict(color=ACCENT, size=9, line=dict(color=N_CARD, width=2)),
                             hoverinfo="skip"))
    layout = _nbase(300)
    layout["hovermode"] = "x unified"
    layout["xaxis"].update(tickmode="array", range=[1, ndays], ticktext=MONATE,
                           tickvals=[pd.Timestamp(year, m, 1).dayofyear for m in range(1, 13)])
    layout["yaxis"].update(title_text="km", rangemode="tozero")
    fig.update_layout(**layout)
    return pio.to_json(fig)


def chart_tour_hist(df: pd.DataFrame, year: int) -> str:
    """Histogramm der Tourlänge in gleich breiten Klassen (10 km, ab 120 km Maximaldistanz 20 km)."""
    km = year_df(df, year)["km"].dropna()
    kmax = float(km.max()) if len(km) else 10.0
    step = 10 if kmax <= 120 else 20
    edges = np.arange(0, math.floor(kmax / step) * step + 2 * step, step)
    counts, _ = np.histogram(km, bins=edges)
    labels = [f"{int(a)}–{int(a + step)}" for a in edges[:-1]]
    fig = go.Figure(go.Bar(
        x=labels, y=counts, marker_color=ACCENT, marker_line_width=0,
        text=[str(c) if c else "" for c in counts], textposition="outside",
        textfont=dict(color=N_TEXT, size=11), cliponaxis=False,
        hovertemplate="%{y} Fahrten mit %{x} km<extra></extra>",
    ))
    layout = _nbase(260)
    layout["margin"] = dict(l=36, r=8, t=22, b=44)
    layout["xaxis"].update(title_text="km", type="category")
    layout["bargap"] = 0.18
    fig.update_layout(**layout)
    return pio.to_json(fig)


def eddington(km: pd.Series) -> tuple[int, int]:
    """Eddington-Zahl E (E Fahrten mit mindestens E km) und wie viele Fahrten für E+1 noch fehlen."""
    v = np.sort(km.dropna().to_numpy())[::-1]
    e = 0
    while e < len(v) and v[e] >= e + 1:
        e += 1
    return e, e + 1 - int((v >= e + 1).sum())


def week_volume(df: pd.DataFrame, year: int, metric: str = "km") -> pd.Series:
    col, factor, _ = METRICS[metric]
    d = year_df(df, year)
    s = d.groupby("week")[col].sum() * factor
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="W-MON"), fill_value=0)


def chart_week_volume(df: pd.DataFrame, year: int, metric: str = "km") -> str:
    """Wochenbalken für km, berechnete Höhenmeter oder Fahrzeit."""
    unit = METRICS[metric][2]
    s = week_volume(df, year, metric)
    kw = [f"KW {int(w.isocalendar()[1])}" for w in s.index]
    fig = go.Figure(go.Bar(
        x=s.index, y=s.round(1).values, customdata=kw,
        marker_color=ACCENT, marker_line_width=0,
        hovertemplate="%{customdata}: %{y:.0f} " + unit + "<extra></extra>",
    ))
    layout = _nbase(260)
    layout["xaxis"].update(tickformat="%b", dtick="M1")
    layout["yaxis"].update(title_text=unit)
    layout["bargap"] = 0.25
    fig.update_layout(**layout)
    return pio.to_json(fig)


def records(df: pd.DataFrame, year: int) -> list[dict]:
    d = year_df(df, year)
    out = []

    def add(label, sub_df, col, fmt):
        s = sub_df.dropna(subset=[col])
        if s.empty:
            return
        r = s.loc[s[col].idxmax()]
        out.append({"label": label, "value": fmt(r[col]),
                    "sub": f"{r['kategorie']} · {r['datum']:%d.%m.%Y}"})

    add("Längste Fahrt", d, "km", lambda v: f"{de(v, 1)} km")
    add("Meiste Höhenmeter", d, "elevation", lambda v: f"{de(v)} m")
    add("Schnellster Schnitt (ab 20 km)", d[d["km"] >= LANG_MIN_KM], "avg_speed",
        lambda v: f"{de(v, 1)} km/h")
    wk = d.groupby("week")["km"].sum()
    if len(wk):
        w = wk.idxmax()
        out.append({"label": "Stärkste Woche", "value": f"{de(wk.max())} km",
                    "sub": f"KW {int(w.isocalendar()[1])} · ab {w:%d.%m.%Y}"})
    return out


def rides_table(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Alle Fahrten des Jahres, neueste zuerst, mit Rohwerten je FIT-Datei."""
    d = year_df(df, year).sort_values("datum", ascending=False)
    return pd.DataFrame({
        "Datum":      d["datum"],
        "Typ":        d["kategorie"],
        "Distanz":    d["km"].round(1),
        "Höhenmeter": d["elevation"],
        "Fahrzeit":   d["dauer_min"].round(0),
        "Ø km/h":     d["avg_speed"].round(1),
        "Max km/h":   d["max_speed"].round(1),
        "Ø Puls":     d["avg_bpm"],
        "Max Puls":   d["max_bpm"],
        "m / km":     (d["elevation"] / d["km"]).round(1),
        "Load":       d["load"],
    })


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    df = load(FIT_DIR, DATE_FROM)
    k  = kpi(df)
    cats = df["kategorie"].value_counts().to_dict()
    print(f"  {k['rides']} Fahrten · {k['km']:.0f} km · {k['hours']:.0f} h")
    print(f"  Kategorien: {cats}")

"""
dashboard.py – Cycling-Dashboard-Modul aus Garmin FIT-Daten

Liest FIT-Dateien aus garminexports/standard/fit/ und stellt Diagramme und KPIs bereit.
Wird von app.py als Modul genutzt; kann auch standalone KPIs ausgeben.

    pip install fitdecode pandas plotly numpy
    python dashboard.py
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
if __name__ == "__main__":
    df = load(FIT_DIR, DATE_FROM)
    k  = kpi(df)
    cats = df["kategorie"].value_counts().to_dict()
    print(f"  {k['rides']} Fahrten · {k['km']:.0f} km · {k['hours']:.0f} h")
    print(f"  Kategorien: {cats}")

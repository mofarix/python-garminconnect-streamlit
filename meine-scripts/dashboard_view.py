"""dashboard_view.py – Streamlit-Ansicht des Cycling Dashboards.

Enthält nur UI; Daten und Diagramme kommen aus dashboard.py.
Aufruf aus app.py:

    import dashboard_view
    dashboard_view.render(df)
"""

import html

import pandas as pd
import plotly.io as pio
import streamlit as st

import dashboard as dash

HM_HELP = (
    "Höhenmeter sind aus den Trackpunkten berechnet: Median- und Savitzky-Golay-Glättung, "
    "dann Anstiege summiert. Der Gerätewert total_ascent wird nicht verwendet."
)

_CSS = """
<style>
.gv-kpis{display:flex;flex-wrap:wrap;gap:16px;margin:4px 0 8px}
.gv-kpi{flex:1 1 170px;min-width:0;box-sizing:border-box;background:#181D24;border:1px solid #262C35;
  border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:4px}
.gv-kpi-label{color:#9AA3AE;font-size:12px;font-weight:500;text-transform:uppercase;letter-spacing:.6px}
.gv-kpi-value{color:#E8EAED;font-size:30px;font-weight:600;letter-spacing:-.5px;line-height:1.15;
  font-variant-numeric:tabular-nums}
.gv-kpi-unit{color:#9AA3AE;font-size:14px;font-weight:400;margin-left:4px}
.gv-kpi-delta{font-size:12px;color:#9AA3AE;font-variant-numeric:tabular-nums}
.gv-head{display:flex;flex-wrap:wrap;gap:8px 16px;justify-content:space-between;align-items:baseline}
.gv-title{color:#E8EAED;font-size:17px;font-weight:600;margin:0}
.gv-note{color:#9AA3AE;font-size:13px;font-variant-numeric:tabular-nums}
.gv-note b{color:#E8EAED;font-weight:600}
.gv-legend{display:flex;gap:6px;align-items:center;justify-content:flex-end;color:#9AA3AE;font-size:12px}
.gv-legend i{width:14px;height:14px;border-radius:3px;display:inline-block}
.gv-edd{display:flex;gap:14px;align-items:center;background:#0F1318;border-radius:10px;padding:12px 16px}
.gv-edd-num{font-size:28px;font-weight:600;color:#3FCF8E;font-variant-numeric:tabular-nums}
.gv-edd-txt{color:#9AA3AE;font-size:12px}
.gv-rec{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;
  border-top:1px solid #262C35}
.gv-rec-label{color:#E8EAED;font-size:14px;font-weight:500}
.gv-rec-sub{color:#9AA3AE;font-size:12px}
.gv-rec-value{color:#E8EAED;font-size:20px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
</style>
"""


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _plot(chart_json: str) -> None:
    st.plotly_chart(pio.from_json(chart_json), width="stretch", config={"displayModeBar": False})


def _head(title: str, note: str = "") -> None:
    _html(f'<div class="gv-head"><p class="gv-title">{html.escape(title)}</p>'
          f'<span class="gv-note">{note}</span></div>')


def _kpis(items: list[dict], prev_year: int) -> None:
    cards = []
    for k in items:
        if k["delta"] is None:
            delta = "kein Vorjahreswert"
        else:
            up = k["delta"] >= 0
            color = dash.ACCENT if up else dash.WARN
            arrow = "▲ +" if up else "▼ "
            delta = (f'<span style="color:{color}">{arrow}{dash.de(k["delta"])} %</span> '
                     f'ggü. {prev_year}')
        cards.append(
            f'<div class="gv-kpi"><span class="gv-kpi-label">{k["label"]}</span>'
            f'<span class="gv-kpi-value">{k["value"]}<span class="gv-kpi-unit">{k["unit"]}</span></span>'
            f'<span class="gv-kpi-delta">{delta}</span></div>'
        )
    _html(f'<div class="gv-kpis">{"".join(cards)}</div>')


def _overview(df: pd.DataFrame, year: int, goal: int) -> None:
    last = df["datum"].max()
    current = year == last.year

    _kpis(dash.kpi_year(df, year), year - 1)
    st.caption(("Vergleich mit dem Vorjahr bis zum selben Stichtag. " if current else "") + HM_HELP)

    # Aktivität
    with st.container(border=True):
        a = dash.activity_stats(df, year)
        _head(f"Aktivität {year}",
              f'<b>{a["active_days"]}</b> aktive Tage · längste Serie '
              f'<b>{a["streak_weeks"]}</b> Wochen in Folge')
        _plot(dash.chart_activity(df, year))
        swatches = "".join(f'<i style="background:{c}"></i>' for c in dash.activity_legend_colors())
        _html(f'<div class="gv-legend">Tagesdistanz: keine Fahrt {swatches} oberstes Viertel</div>')

    # Jahresfortschritt | Tourlänge
    left, right = st.columns([7, 5])
    with left, st.container(border=True):
        forecast = dash.year_forecast(df, year)
        total = dash.year_df(df, year)["km"].sum()
        note = (f'Prognose Jahresende <b>{dash.de(forecast)} km</b> · Ziel {dash.de(goal)} km'
                if forecast is not None else
                f'Gesamt <b>{dash.de(total)} km</b> · Ziel {dash.de(goal)} km')
        _head("Jahresfortschritt", note)
        _plot(dash.chart_progress(df, year, goal))
        st.caption(f"Grün: {year} kumuliert · Grau: {year - 1} · Gestrichelt: Zielpfad")
    with right, st.container(border=True):
        km = dash.year_df(df, year)["km"]
        _head("Tourlänge", f'Median <b>{dash.de(km.median(), 1)} km</b>')
        _plot(dash.chart_tour_hist(df, year))
        e, missing = dash.eddington(km)
        e_all, _ = dash.eddington(df["km"])
        _html(f'<div class="gv-edd"><span class="gv-edd-num">{e}</span><span class="gv-edd-txt">'
              f'Eddington-Zahl {year}: {e} Fahrten mit mindestens {e} km. '
              f'Für {e + 1} fehlen noch {missing} Fahrten. Über alle Jahre: {e_all}.</span></div>')

    # Wochenumfang | Bestwerte
    left, right = st.columns([7, 5])
    with left, st.container(border=True):
        h, sel = st.columns([3, 2])
        with sel:
            metric = st.segmented_control("Messgröße", list(dash.METRICS), default="km",
                                          key="gv_metric", label_visibility="collapsed") or "km"
        s = dash.week_volume(df, year, metric)
        unit = dash.METRICS[metric][2]
        with h:
            _head("Wochenumfang",
                  f'Ø <b>{dash.de(s.mean())} {unit}</b> pro Woche · Spitze {dash.de(s.max())} {unit}')
        _plot(dash.chart_week_volume(df, year, metric))
    with right, st.container(border=True):
        _head(f"Bestwerte {year}")
        rows = "".join(
            f'<div class="gv-rec"><div><div class="gv-rec-label">{html.escape(r["label"])}</div>'
            f'<div class="gv-rec-sub">{html.escape(r["sub"])}</div></div>'
            f'<span class="gv-rec-value">{r["value"]}</span></div>'
            for r in dash.records(df, year)
        )
        _html(rows)

    # Fahrten
    with st.container(border=True):
        table = dash.rides_table(df, year)
        _head("Letzte Fahrten", f"{len(table)} Fahrten · Rohdaten je FIT-Datei")
        st.dataframe(
            table, width="stretch", height=380, hide_index=True,
            column_config={
                "Datum":      st.column_config.DatetimeColumn(format="DD.MM.YYYY"),
                "Distanz":    st.column_config.NumberColumn(format="%.1f km"),
                "Höhenmeter": st.column_config.NumberColumn(format="%d m", help=HM_HELP),
                "Fahrzeit":   st.column_config.NumberColumn(format="%d min"),
                "Ø Puls":     st.column_config.NumberColumn(format="%d"),
                "Max Puls":   st.column_config.NumberColumn(format="%d"),
            },
        )


def _commute_fitness(df: pd.DataFrame) -> None:
    """Bisherige Auswertungen nach Fahrttyp, unverändert über den gesamten Zeitraum."""
    k = dash.kpi(df)
    c = st.columns(3)
    c[0].metric("Ø Herzfrequenz", f"{dash.de(k['avg_hr'])} bpm")
    c[1].metric("Pendel-Quote", f"{dash.de(k['commute_pct'])} % der Werktage")
    c[2].metric("Wochen aktiv", f"{k['n_weeks']}")

    c1, c2 = st.columns(2)
    with c1:
        st.caption("Wöchentliches Volumen nach Fahrttyp")
        _plot(dash.chart_volume(df))
    with c2:
        st.caption("Fitness-Trend · HF & Tempo (gleitend)")
        _plot(dash.chart_fitness(df))

    c3, c4, c5 = st.columns(3)
    with c3:
        st.caption("HF-Zonen-Verteilung (geschätzt) pro Monat")
        _plot(dash.chart_zones(df))
    with c4:
        st.caption("Distanz-Verteilung nach Fahrttyp")
        _plot(dash.chart_dist_hist(df))
    with c5:
        st.caption("Pendel-Quote vs. Fahrten gesamt")
        _plot(dash.chart_commute(df))


def render(df: pd.DataFrame) -> None:
    """Zeichnet das Dashboard für den von dashboard.load() gelieferten DataFrame."""
    _html(_CSS)
    yrs = dash.years(df)

    c_year, c_goal, _ = st.columns([3, 2, 4])
    with c_year:
        year = st.segmented_control("Jahr", yrs, default=yrs[0], key="gv_year") or yrs[0]
    with c_goal:
        goal = st.number_input("Jahresziel (km)", min_value=0, max_value=50000, value=5000,
                               step=500, key="gv_goal")

    tab_year, tab_types = st.tabs(["Jahresübersicht", "Pendeln & Fitness"])
    with tab_year:
        _overview(df, int(year), int(goal))
    with tab_types:
        _commute_fitness(df)

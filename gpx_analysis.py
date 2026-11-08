"""gpx_analysis.py – Berechnungs- und Visualisierungsfunktionen für Garmin GPX-Analyse.

Enthält keine Streamlit-UI. Wird von app.py importiert.
"""
import math

import branca.colormap as cm
import folium
import gpxpy
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter, medfilt
import matplotlib.pyplot as plt

# ============================================================
# CONSTANTS
# ============================================================
g = 9.81
rho = 1.225

DEFAULT_PARAMS = {
    "GPS_STD": 6.0,
    "ACCEL_STD": 1.5,
    "ELEV_PLOT_MEDIAN_KERNEL": 7,
    "ELEV_PLOT_SAVGOL_WINDOW": 51,
    "ELEV_PLOT_POLYORDER": 2,
    "DIST_RESAMPLE_M": 5.0,
    "GRADE_SMOOTH_WINDOW_M": 80.0,
    "GRADE_CLIP": 0.25,
    "m_fahrer": 80.0,
    "m_fahrrad": 15.0,
    "c_rr": 0.007,
    "cwA": 0.4,
    "m_rot_zuschlag": 0.03,
    "accel_smooth_window": 3,
    "grade_smooth_window": 3,
    "power_smooth_window": 5,
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def latlon_to_local_xy(lat_deg, lon_deg, lat0_deg, lon0_deg):
    R = 6371000.0
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    x = (lon - lon0) * math.cos(lat0) * R
    y = (lat - lat0) * R
    return x, y


def local_xy_to_latlon(x, y, lat0_deg, lon0_deg):
    R = 6371000.0
    lat0 = math.radians(lat0_deg)
    lon0 = math.radians(lon0_deg)
    lat = y / R + lat0
    lon = x / (R * math.cos(lat0)) + lon0
    return math.degrees(lat), math.degrees(lon)


def make_odd_window(n, data_len):
    n = max(3, int(n))
    if n % 2 == 0:
        n += 1
    if n >= data_len:
        n = data_len - 1 if data_len % 2 == 0 else data_len
    if n < 3:
        n = 3
    if n % 2 == 0:
        n -= 1
    return n


def safe_medfilt_1d(x, kernel_size):
    x = np.asarray(x, dtype=float)
    if len(x) < 3:
        return x.copy()
    k = int(kernel_size)
    if k % 2 == 0:
        k += 1
    if k > len(x):
        k = len(x) if len(x) % 2 == 1 else len(x) - 1
    k = max(3, k)
    return medfilt(x, kernel_size=k)


def smooth_elevation_for_plot(elevation, median_kernel=7, savgol_window=51, polyorder=2):
    z = np.asarray(elevation, dtype=float)
    if len(z) < 5:
        return z.copy()
    z_med = safe_medfilt_1d(z, median_kernel)
    window = make_odd_window(savgol_window, len(z_med))
    polyorder = min(polyorder, window - 1)
    z_smooth = savgol_filter(z_med, window_length=window, polyorder=polyorder)
    return z_smooth


def total_ascent_m(elevation):
    """Gesamtaufstieg in Metern: Median + Savitzky-Golay glätten, dann positive Differenzen summieren.

    Einzige Quelle für Höhenmeter in Dashboard und GPX-Analyse, damit beide
    für dieselbe Fahrt denselben Wert liefern.
    """
    z = np.asarray(elevation, dtype=float)
    z = z[~np.isnan(z)]
    if len(z) < 2:
        return 0.0
    diffs = np.diff(smooth_elevation_for_plot(z))
    return float(diffs[diffs > 0].sum())


def load_gpx_data_from_upload(uploaded_file):
    gpx = gpxpy.parse(uploaded_file.getvalue().decode("utf-8"))
    data = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                data.append({
                    "time": point.time,
                    "latitude": point.latitude,
                    "longitude": point.longitude,
                    "elevation": point.elevation,
                })

    if not data:
        raise ValueError("Keine Trackpunkte im GPX gefunden.")

    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time", "latitude", "longitude"]).reset_index(drop=True)

    if len(df) < 2:
        raise ValueError("Zu wenige gültige GPX-Punkte gefunden.")

    if df["elevation"].isna().any():
        df["elevation"] = df["elevation"].interpolate().bfill().ffill()

    df["elapsed_time"] = (df["time"] - df["time"].iloc[0]).dt.total_seconds()
    df = df[df["elapsed_time"].diff().fillna(1) > 0].reset_index(drop=True)

    lat0 = df["latitude"].iloc[0]
    lon0 = df["longitude"].iloc[0]

    xy = df.apply(
        lambda r: latlon_to_local_xy(r["latitude"], r["longitude"], lat0, lon0),
        axis=1,
    )
    df["x_raw"] = [p[0] for p in xy]
    df["y_raw"] = [p[1] for p in xy]

    df["time_diff"] = df["elapsed_time"].diff().fillna(0.0)
    return df


# ============================================================
# KALMAN FILTER (2D)
# ============================================================
def kalman_filter_2d_variable_dt(x_meas, y_meas, t, gps_std=6.0, accel_std=1.5):
    n = len(t)
    X = np.zeros((n, 4))
    P_list = np.zeros((n, 4, 4))

    X[0] = np.array([x_meas[0], y_meas[0], 0.0, 0.0])
    P = np.diag([gps_std**2, gps_std**2, 25.0, 25.0])

    H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
    R = np.diag([gps_std**2, gps_std**2])

    P_list[0] = P

    for k in range(1, n):
        dt = max(t[k] - t[k - 1], 1e-3)

        F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ])

        q = accel_std**2
        Q = q * np.array([
            [dt**4 / 4, 0, dt**3 / 2, 0],
            [0, dt**4 / 4, 0, dt**3 / 2],
            [dt**3 / 2, 0, dt**2, 0],
            [0, dt**3 / 2, 0, dt**2]
        ])

        x_pred = F @ X[k - 1]
        P_pred = F @ P @ F.T + Q

        z = np.array([x_meas[k], y_meas[k]])
        innovation = z - (H @ x_pred)
        S = H @ P_pred @ H.T + R
        K = P_pred @ H.T @ np.linalg.inv(S)

        X[k] = x_pred + K @ innovation
        P = (np.eye(4) - K @ H) @ P_pred
        P_list[k] = P

    return X, P_list


# ============================================================
# XY KINEMATICS
# ============================================================
def compute_xy_kinematics(df, X):
    df["x"] = X[:, 0]
    df["y"] = X[:, 1]
    df["vx"] = X[:, 2]
    df["vy"] = X[:, 3]

    dx_raw = df["x_raw"].diff().fillna(0.0)
    dy_raw = df["y_raw"].diff().fillna(0.0)
    ds_raw = np.sqrt(dx_raw**2 + dy_raw**2)

    df["v_xy_raw"] = np.where(df["time_diff"] > 0, ds_raw / df["time_diff"], 0.0)
    df["v_xy"] = np.sqrt(df["vx"]**2 + df["vy"]**2)

    dv_raw = df["v_xy_raw"].diff().fillna(0.0)
    dv = df["v_xy"].diff().fillna(0.0)

    df["a_xy_raw"] = np.where(df["time_diff"] > 0, dv_raw / df["time_diff"], 0.0)
    df["a_xy"] = np.where(df["time_diff"] > 0, dv / df["time_diff"], 0.0)

    df["v_xy_raw_kmh"] = df["v_xy_raw"] * 3.6
    df["v_xy_kmh"] = df["v_xy"] * 3.6

    dx = df["x"].diff().fillna(0.0)
    dy = df["y"].diff().fillna(0.0)
    df["dist_diff"] = np.sqrt(dx**2 + dy**2)
    df["distance_m"] = df["dist_diff"].cumsum()
    df["cumulative_distance"] = df["distance_m"] / 1000.0

    return df


def add_smoothed_latlon(df):
    lat0 = df["latitude"].iloc[0]
    lon0 = df["longitude"].iloc[0]

    latlon_smooth = [
        local_xy_to_latlon(x, y, lat0, lon0)
        for x, y in zip(df["x"], df["y"])
    ]

    df["latitude_smooth"] = [p[0] for p in latlon_smooth]
    df["longitude_smooth"] = [p[1] for p in latlon_smooth]
    return df


# ============================================================
# ELEVATION & GRADE
# ============================================================
def compute_grade_from_smoothed_elevation(distance_m, z_smooth, ds=5.0,
                                          grade_smooth_window_m=80.0,
                                          grade_clip=0.25):
    s_orig = np.asarray(distance_m, dtype=float)
    z_orig = np.asarray(z_smooth, dtype=float)

    valid = np.isfinite(s_orig) & np.isfinite(z_orig)
    s = s_orig[valid]
    z = z_orig[valid]

    if len(s) < 5:
        zeros = np.zeros_like(s_orig)
        return zeros, zeros, s, z, zeros

    keep = np.r_[True, np.diff(s) > 1e-6]
    s = s[keep]
    z = z[keep]

    if len(s) < 5:
        zeros = np.zeros_like(s_orig)
        return zeros, zeros, s, z, zeros

    s_uniform = np.arange(s[0], s[-1] + ds, ds)
    z_uniform = np.interp(s_uniform, s, z)

    grade_uniform = np.gradient(z_uniform, ds)

    grade_window_pts = max(5, int(round(grade_smooth_window_m / ds)))
    grade_window_pts = make_odd_window(grade_window_pts, len(s_uniform))

    grade_uniform_smooth = savgol_filter(
        grade_uniform,
        window_length=grade_window_pts,
        polyorder=min(2, grade_window_pts - 1)
    )

    if grade_clip is not None:
        grade_uniform = np.clip(grade_uniform, -grade_clip, grade_clip)
        grade_uniform_smooth = np.clip(grade_uniform_smooth, -grade_clip, grade_clip)

    grade_orig = np.interp(s_orig, s_uniform, grade_uniform)
    grade_smooth_orig = np.interp(s_orig, s_uniform, grade_uniform_smooth)

    return grade_orig, grade_smooth_orig, s_uniform, z_uniform, grade_uniform_smooth


def add_elevation_and_grade(df, params):
    df["z_plot_smooth"] = smooth_elevation_for_plot(
        df["elevation"].to_numpy(),
        median_kernel=int(params["ELEV_PLOT_MEDIAN_KERNEL"]),
        savgol_window=int(params["ELEV_PLOT_SAVGOL_WINDOW"]),
        polyorder=int(params["ELEV_PLOT_POLYORDER"])
    )

    grade, grade_smooth, s_uniform, z_uniform, grade_uniform_smooth = compute_grade_from_smoothed_elevation(
        df["distance_m"].to_numpy(),
        df["z_plot_smooth"].to_numpy(),
        ds=params["DIST_RESAMPLE_M"],
        grade_smooth_window_m=params["GRADE_SMOOTH_WINDOW_M"],
        grade_clip=params["GRADE_CLIP"]
    )

    df["z_grade_smooth"] = df["z_plot_smooth"]
    df["steigung"] = grade
    df["steigung_smooth"] = grade_smooth
    df["speed_kmh"] = df["v_xy_raw_kmh"]
    df["speed_smooth"] = df["v_xy_kmh"]
    df["ele_smooth"] = df["z_plot_smooth"]

    return df, s_uniform, z_uniform, grade_uniform_smooth


# ============================================================
# RESISTANCE MODEL & POWER
# ============================================================
def add_resistance_model(df, params):
    m_fahrer = params["m_fahrer"]
    m_fahrrad = params["m_fahrrad"]
    m_ges = m_fahrer + m_fahrrad
    c_rr = params["c_rr"]
    cwA = params["cwA"]
    m_rot_zuschlag = params["m_rot_zuschlag"]
    accel_smooth_window = int(params["accel_smooth_window"])
    grade_smooth_window = int(params["grade_smooth_window"])
    power_smooth_window = int(params["power_smooth_window"])

    df["v"] = df["speed_smooth"] / 3.6

    df["a"] = 0.0
    valid_time = df["time_diff"] > 0
    df.loc[valid_time, "a"] = df["v"].diff() / df["time_diff"]
    df["a"] = df["a"].fillna(0.0)

    df["a_smooth"] = df["a"].rolling(window=accel_smooth_window, center=True, min_periods=1).mean()

    df["steigung_modell"] = df["steigung_smooth"].clip(lower=-0.25, upper=0.25)
    df["steigung_modell_smooth"] = df["steigung_modell"].rolling(
        window=grade_smooth_window, center=True, min_periods=1
    ).mean()

    df["F_roll"] = c_rr * m_ges * g
    df["F_luft"] = 0.5 * rho * cwA * (df["v"] ** 2)
    df["F_steigung"] = m_ges * g * df["steigung_modell_smooth"]

    m_eff = m_ges * (1.0 + m_rot_zuschlag)
    df["F_beschl"] = m_eff * df["a_smooth"]

    df["F_gesamt"] = df["F_roll"] + df["F_luft"] + df["F_steigung"] + df["F_beschl"]

    df["P_roll"] = df["F_roll"] * df["v"]
    df["P_luft"] = df["F_luft"] * df["v"]
    df["P_steigung"] = df["F_steigung"] * df["v"]
    df["P_beschl"] = df["F_beschl"] * df["v"]
    df["P_gesamt"] = df["F_gesamt"] * df["v"]
    df["P_gesamt_fahrer"] = df["P_gesamt"].clip(lower=0)

    df["P_gesamt_fahrer_smooth"] = df["P_gesamt_fahrer"].rolling(
        window=power_smooth_window, center=True, min_periods=1
    ).mean()

    return df


# ============================================================
# FULL ANALYSIS PIPELINE
# ============================================================
def analyze_gpx_data(df, params):
    X, _ = kalman_filter_2d_variable_dt(
        x_meas=df["x_raw"].to_numpy(),
        y_meas=df["y_raw"].to_numpy(),
        t=df["elapsed_time"].to_numpy(),
        gps_std=params["GPS_STD"],
        accel_std=params["ACCEL_STD"]
    )

    df = compute_xy_kinematics(df, X)
    df = add_smoothed_latlon(df)
    df, s_uniform, z_uniform, grade_uniform_smooth = add_elevation_and_grade(df, params)
    df = add_resistance_model(df, params)

    return df


# ============================================================
# VISUALIZATION FUNCTIONS
# ============================================================
def plot_time_profiles(df, params):
    plt.style.use("dark_background")
    fig, axes = plt.subplots(5, 1, figsize=(14, 10), sharex=True)
    fig.patch.set_facecolor("#0e1117")

    t_min = df["elapsed_time"] / 60.0

    colors = {
        "raw": "#666666",
        "speed": "#00d9ff",
        "accel": "#ff6b35",
        "elev": "#9d4edd",
        "grade": "#ffbe0b",
        "power_total": "#fb5607",
        "power_smooth": "#3a86ff",
    }

    axes[0].plot(t_min, df["speed_kmh"], alpha=0.3, color=colors["raw"], linewidth=1.5, label="Roh")
    axes[0].plot(t_min, df["speed_smooth"], linewidth=2.5, color=colors["speed"], label="Geglättet")
    axes[0].set_ylabel("Geschwindigkeit (km/h)", fontsize=10, fontweight="bold")
    axes[0].grid(True, alpha=0.15, linestyle="--")
    axes[0].legend(loc="upper right", framealpha=0.9)
    axes[0].set_facecolor("#161b22")

    axes[1].plot(t_min, df["a_xy_raw"], alpha=0.3, color=colors["raw"], linewidth=1.5, label="Roh")
    axes[1].plot(t_min, df["a_smooth"], linewidth=2.5, color=colors["accel"], label="Gefiltert")
    axes[1].set_ylabel("Beschleunigung (m/s²)", fontsize=10, fontweight="bold")
    axes[1].set_ylim(-5, 5)
    axes[1].grid(True, alpha=0.15, linestyle="--")
    axes[1].legend(loc="upper right", framealpha=0.9)
    axes[1].set_facecolor("#161b22")

    axes[2].plot(t_min, df["elevation"], alpha=0.3, color=colors["raw"], linewidth=1.5, label="Roh")
    axes[2].plot(t_min, df["ele_smooth"], linewidth=2.5, color=colors["elev"], label="Geglättet")
    axes[2].set_ylabel("Höhe (m)", fontsize=10, fontweight="bold")
    axes[2].grid(True, alpha=0.15, linestyle="--")
    axes[2].legend(loc="upper right", framealpha=0.9)
    axes[2].set_facecolor("#161b22")

    axes[3].plot(t_min, df["steigung"] * 100, alpha=0.3, color=colors["raw"], linewidth=1.5, label="Roh")
    axes[3].plot(t_min, df["steigung_modell_smooth"] * 100, linewidth=2.5, color=colors["grade"], label="Geglättet")
    axes[3].set_ylabel("Steigung (%)", fontsize=10, fontweight="bold")
    axes[3].grid(True, alpha=0.15, linestyle="--")
    axes[3].legend(loc="upper right", framealpha=0.9)
    axes[3].set_facecolor("#161b22")

    axes[4].plot(t_min, df["P_gesamt"], alpha=0.25, color=colors["power_total"], linewidth=1.5, label="Gesamt")
    axes[4].plot(t_min, df["P_gesamt_fahrer_smooth"], linewidth=2.5, color=colors["power_smooth"], label="Fahrerleistung")
    axes[4].set_ylabel("Leistung (W)", fontsize=10, fontweight="bold")
    axes[4].set_ylim(-500, 500)
    axes[4].set_xlabel("Zeit (Minuten)", fontsize=10, fontweight="bold")
    axes[4].grid(True, alpha=0.15, linestyle="--")
    axes[4].legend(loc="upper right", framealpha=0.9)
    axes[4].set_facecolor("#161b22")

    for ax in axes:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#444444")
        ax.spines["bottom"].set_color("#444444")
        ax.tick_params(colors="#cccccc", labelsize=9)

    plt.tight_layout()
    return fig


def create_speed_map(df):
    center_lat = df["latitude_smooth"].mean()
    center_lon = df["longitude_smooth"].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=13, tiles="OpenStreetMap")

    min_speed = df["speed_smooth"].min()
    max_speed = df["speed_smooth"].max()

    colormap = cm.LinearColormap(
        colors=["blue", "green", "yellow", "orange", "red", "darkred"],
        vmin=min_speed,
        vmax=max_speed,
        caption="Geschwindigkeit (km/h)",
    )

    points = df[["latitude_smooth", "longitude_smooth"]].values.tolist()
    speed_values = df["speed_smooth"].tolist()

    folium.ColorLine(
        positions=points,
        colors=speed_values,
        colormap=colormap,
        weight=6,
        opacity=1.0,
    ).add_to(m)

    colormap.add_to(m)

    folium.Marker(points[0], popup="Start", icon=folium.Icon(color="green", icon="play", prefix="fa")).add_to(m)
    folium.Marker(points[-1], popup="Ziel", icon=folium.Icon(color="black", icon="flag-checkered", prefix="fa")).add_to(m)

    max_idx = df["speed_smooth"].idxmax()
    max_pos = [df.loc[max_idx, "latitude_smooth"], df.loc[max_idx, "longitude_smooth"]]
    folium.CircleMarker(
        location=max_pos,
        radius=8,
        color="darkred",
        fill=True,
        fill_color="white",
        popup=f"Top Speed: {max_speed:.1f} km/h",
    ).add_to(m)

    return m


# ============================================================
# STATISTICS
# ============================================================
def get_stats(df):
    total_gain = total_ascent_m(df["elevation"].to_numpy())

    max_speed = df["speed_smooth"].max()
    max_power = df["P_gesamt_fahrer_smooth"].max()
    avg_power = df["P_gesamt_fahrer_smooth"].mean()
    duration_hours = df["elapsed_time"].iloc[-1] / 3600.0
    distance_km = df["cumulative_distance"].iloc[-1]
    avg_speed_kmh = distance_km / duration_hours if duration_hours > 0 else 0

    return {
        "distance_km": distance_km,
        "duration_hours": duration_hours,
        "total_gain_m": total_gain,
        "max_speed_kmh": max_speed,
        "avg_speed_kmh": avg_speed_kmh,
        "max_power_w": max_power,
        "avg_power_w": avg_power,
    }

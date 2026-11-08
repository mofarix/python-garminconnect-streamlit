#!/usr/bin/env python3
"""Garmin-Aktivitäten-Export: GPX + FIT der letzten N Aktivitäten.

Abhängigkeiten:
    pip install garminconnect curl_cffi fitdecode gpxpy pandas matplotlib

Umgebungsvariablen (optional):
    GARMIN_EMAIL     Garmin-Account-E-Mail
    GARMIN_PASSWORD  Garmin-Passwort
    GARMINTOKENS     Token-Speicherort (Standard: ~/.garminconnect)
"""

import os
import sys
from getpass import getpass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

ACTIVITY_LIMIT = int(os.getenv("GARMIN_ACTIVITY_LIMIT", "20"))
TOKENSTORE     = os.getenv("GARMINTOKENS", "~/.garminconnect")

out_dir = Path(os.getenv("GARMIN_EXPORT_DIR", "garminexports/standard"))
gpx_dir = out_dir / "gpx"
fit_dir = out_dir / "fit"
zip_dir = out_dir / "zip"

for d in (gpx_dir, fit_dir, zip_dir):
    d.mkdir(parents=True, exist_ok=True)


# --- Login mit Token-Persistenz ---

tokenstore_path = str(Path(TOKENSTORE).expanduser())

try:
    client = Garmin()
    client.login(tokenstore_path)
    print(f"Eingeloggt via gespeicherte Tokens ({tokenstore_path})")

except (GarminConnectAuthenticationError, GarminConnectConnectionError):
    print("Keine gültigen Tokens gefunden — bitte einloggen.")

    email = os.getenv("GARMIN_EMAIL") or input("E-Mail: ").strip()
    # Passwort aus Datei lesen (gesetzt von app.py mit Mode 0600) oder interaktiv
    pw_file = os.getenv("GARMIN_PASSWORD_FILE")
    if pw_file:
        password = open(pw_file).read().strip()
    else:
        password = getpass("Passwort: ")

    if not email or not password:
        print("Fehler: E-Mail und Passwort sind erforderlich.", file=sys.stderr)
        sys.exit(1)

    try:
        client = Garmin(
            email=email,
            password=password,
            prompt_mfa=lambda: input("MFA-Code: ").strip(),
        )
        client.login(tokenstore_path)
        print(f"Login erfolgreich. Tokens gespeichert unter: {tokenstore_path}")

    except GarminConnectTooManyRequestsError as e:
        print(f"Rate-Limit erreicht: {e}", file=sys.stderr)
        sys.exit(1)
    except GarminConnectAuthenticationError as e:
        print(f"Authentifizierung fehlgeschlagen: {e}", file=sys.stderr)
        sys.exit(1)
    except GarminConnectConnectionError as e:
        print(f"Verbindungsfehler: {e}", file=sys.stderr)
        sys.exit(1)

except GarminConnectTooManyRequestsError as e:
    print(f"Rate-Limit erreicht: {e}", file=sys.stderr)
    sys.exit(1)


# --- Aktivitäten laden ---

try:
    activities = client.get_activities(0, ACTIVITY_LIMIT)
except (GarminConnectConnectionError, GarminConnectAuthenticationError) as e:
    print(f"Fehler beim Laden der Aktivitäten: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(activities, list):
    print("Unerwartetes Antwortformat von get_activities.", file=sys.stderr)
    sys.exit(1)

print(f"{len(activities)} Aktivitäten gefunden.\n")

ok = 0
skipped = 0
errors = 0

for activity in activities:
    activity_id = activity["activityId"]
    start_time = (
        activity.get("startTimeLocal", "unknown")
        .replace(":", "-")
        .replace(" ", "_")
    )
    sport = activity.get("activityType", {}).get("typeKey", "activity")
    has_gps = activity.get("hasPolyline", False)

    base = f"{start_time}_{sport}_{activity_id}"
    print(f"  {base}  (GPS: {'ja' if has_gps else 'nein'})")

    try:
        # GPX nur für Aktivitäten mit GPS-Daten
        if has_gps:
            gpx_data = client.download_activity(
                activity_id,
                dl_fmt=client.ActivityDownloadFormat.GPX,
            )
            (gpx_dir / f"{base}.gpx").write_bytes(gpx_data)

        # Original-Export (ZIP mit FIT)
        zip_data = client.download_activity(
            activity_id,
            dl_fmt=client.ActivityDownloadFormat.ORIGINAL,
        )
        zip_file = zip_dir / f"{base}.zip"
        zip_file.write_bytes(zip_data)

        # FIT aus ZIP extrahieren, dann ZIP löschen
        try:
            with ZipFile(zip_file, "r") as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".fit"):
                        (fit_dir / f"{base}.fit").write_bytes(zf.read(name))
                        break
        except BadZipFile:
            print(f"    Warnung: ZIP ist ungültig für {activity_id}")
        finally:
            zip_file.unlink(missing_ok=True)

        ok += 1

    except GarminConnectTooManyRequestsError:
        print(f"    Rate-Limit — breche ab.")
        errors += 1
        break
    except (GarminConnectConnectionError, GarminConnectAuthenticationError) as e:
        print(f"    Fehler: {e} — übersprungen.")
        errors += 1
        continue

print(f"\nFertig: {ok} exportiert, {errors} Fehler.")

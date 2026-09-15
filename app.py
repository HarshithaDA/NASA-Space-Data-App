import os
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request


NASA_FEED_URL = "https://api.nasa.gov/neo/rest/v1/feed"
DONKI_API_URL = "https://api.nasa.gov/DONKI"
DONKI_EVENT_TYPES = {
    "Coronal Mass Ejections": "CME",
    "Geomagnetic Storms": "GST",
    "Interplanetary Shocks": "IPS",
    "Solar Flares": "FLR",
    "Solar Energetic Particle Events": "SEP",
    "Magnetopause Crossing Events": "MPC",
    "Radiation Belt Enhancements": "RBE",
    "HSS Events": "HSS",
}

load_dotenv()
app = Flask(__name__)


def fetch_asteroids(start_date: date, end_date: date, api_key: str) -> dict:
    response = requests.get(
        NASA_FEED_URL,
        params={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "api_key": api_key,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def fetch_donki_events(
    event_type: str, start_date: date, end_date: date, api_key: str
) -> list[dict]:
    for attempt in range(3):
        response = requests.get(
            f"{DONKI_API_URL}/{event_type}",
            params={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "api_key": api_key,
            },
            timeout=20,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            if response.status_code not in {502, 503, 504} or attempt == 2:
                raise
            time.sleep(attempt + 1)
        else:
            payload = response.json()
            return payload if isinstance(payload, list) else [payload]

    raise RuntimeError("DONKI request did not return a response")


def http_error_message(service_name: str, error: requests.HTTPError) -> str:
    status_code = error.response.status_code if error.response is not None else "unknown"
    if status_code in {502, 503, 504}:
        return f"{service_name} is temporarily unavailable (HTTP {status_code}). Please try again shortly."
    return f"{service_name} request failed (HTTP {status_code}). Please try again."


def prepare_asteroids(feed: dict) -> list[dict]:
    asteroids = [
        asteroid
        for asteroids_on_date in feed.get("near_earth_objects", {}).values()
        for asteroid in asteroids_on_date
    ]
    asteroids.sort(
        key=lambda asteroid: asteroid["close_approach_data"][0]["close_approach_date"]
    )
    return asteroids


def summarize_donki_event(event: dict, event_type: str) -> dict:
    event_id = next(
        (
            event.get(key)
            for key in ("activityID", "gstID", "flrID", "sepID", "mpcID", "rbeID", "hssID")
            if event.get(key)
        ),
        event.get("catalog", "DONKI event"),
    )
    summary_fields = [
        ("Event ID", event_id),
        ("Event time", event.get("startTime") or event.get("eventTime") or event.get("beginTime")),
        ("Source location", event.get("sourceLocation")),
        ("Catalog", event.get("catalog")),
        ("Event type", event.get("type") or event.get("classType")),
    ]
    summary_fields = [{"label": label, "value": value} for label, value in summary_fields if value]

    extra_fields = []
    category_fields = {
        "IPS": [("Event location", "location")],
        "FLR": [
            ("Started", "beginTime"),
            ("Peak", "peakTime"),
            ("Ended", "endTime"),
            ("Active region", "activeRegionNum"),
        ],
        "SEP": [("Event time", "eventTime")],
        "MPC": [("Event time", "eventTime")],
        "RBE": [("Event time", "eventTime")],
        "HSS": [("Event time", "eventTime")],
    }
    for label, key in category_fields.get(event_type, []):
        if event.get(key) not in (None, ""):
            extra_fields.append({"label": label, "value": event[key]})

    if event_type == "GST":
        kp_values = [
            item.get("kpIndex")
            for item in event.get("allKpIndex") or []
            if item.get("kpIndex") is not None
        ]
        if kp_values:
            extra_fields.append({"label": "Maximum Kp index", "value": max(kp_values)})
        elif event.get("kpIndex") is not None:
            extra_fields.append({"label": "Kp index", "value": event["kpIndex"]})

    instruments = [
        item.get("displayName")
        for item in event.get("instruments") or []
        if item.get("displayName")
    ]
    if instruments:
        extra_fields.append({"label": "Observed by", "value": ", ".join(instruments)})

    cme_analysis = None
    impacts = []
    if event_type == "CME":
        analyses = event.get("cmeAnalyses") or []
        analysis = next(
            (item for item in analyses if item.get("isMostAccurate")),
            analyses[0] if analyses else None,
        )
        if analysis:
            cme_analysis = {
                "speed": f"{analysis['speed']} km/s" if analysis.get("speed") else None,
                "direction": (
                    f"latitude {analysis['latitude']}°, longitude {analysis['longitude']}°"
                    if analysis.get("latitude") is not None
                    and analysis.get("longitude") is not None
                    else None
                ),
                "width": (
                    f"{analysis['halfAngle']}° half-angle"
                    if analysis.get("halfAngle")
                    else None
                ),
                "arrival": analysis.get("time21_5"),
            }
            for model in analysis.get("enlilList") or []:
                for impact in model.get("impactList") or []:
                    impacts.append(
                        {
                            "location": impact.get("location") or "Earth system",
                            "arrival": impact.get("arrivalTime"),
                            "glancing_blow": impact.get("isGlancingBlow", False),
                        }
                    )

    return {
        "id": event_id,
        "time": event.get("startTime") or event.get("eventTime") or event.get("beginTime") or "Date unavailable",
        "summary_fields": summary_fields,
        "extra_fields": extra_fields,
        "note": event.get("note"),
        "cme_analysis": cme_analysis,
        "impacts": impacts,
        "link": event.get("link"),
        "raw": event,
    }


@app.route("/", methods=["GET", "POST"])
def index():
    today = date.today()
    default_end_date = today + timedelta(days=7)
    form_values = {
        "neo_start_date": today.isoformat(),
        "neo_end_date": default_end_date.isoformat(),
        "donki_start_date": today.isoformat(),
        "donki_end_date": default_end_date.isoformat(),
        "donki_event_type": "Coronal Mass Ejections",
    }
    active_tab = "neo"
    asteroids = None
    donki_events = None
    error_message = None

    if request.method == "POST":
        form_values.update(request.form)
        active_tab = request.form.get("active_tab", "neo")
        search_type = request.form.get("search_type")
        api_key = os.getenv("NASA_API_KEY")

        if not api_key:
            error_message = "NASA_API_KEY is missing. Add it to a .env file and restart the app."
        else:
            try:
                if search_type == "neo":
                    start_date = date.fromisoformat(request.form["neo_start_date"])
                    end_date = date.fromisoformat(request.form["neo_end_date"])
                    if end_date < start_date:
                        raise ValueError("End date must be on or after the start date.")
                    if (end_date - start_date).days > 7:
                        raise ValueError("NASA's NEO Feed supports a maximum range of 7 days.")
                    asteroids = prepare_asteroids(fetch_asteroids(start_date, end_date, api_key))
                elif search_type == "donki":
                    start_date = date.fromisoformat(request.form["donki_start_date"])
                    end_date = date.fromisoformat(request.form["donki_end_date"])
                    if end_date < start_date:
                        raise ValueError("End date must be on or after the start date.")
                    event_type = DONKI_EVENT_TYPES[request.form["donki_event_type"]]
                    donki_events = [
                        summarize_donki_event(event, event_type)
                        for event in fetch_donki_events(event_type, start_date, end_date, api_key)
                    ]
            except ValueError as error:
                error_message = str(error)
            except requests.HTTPError as error:
                service_name = "NASA API" if search_type == "neo" else "DONKI"
                error_message = http_error_message(service_name, error)
            except requests.RequestException:
                service_name = "NASA API" if search_type == "neo" else "DONKI"
                error_message = f"Could not reach {service_name}. Please check your connection and try again."

    return render_template(
        "index.html",
        active_tab=active_tab,
        asteroids=asteroids,
        donki_events=donki_events,
        error_message=error_message,
        event_types=DONKI_EVENT_TYPES,
        form_values=form_values,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

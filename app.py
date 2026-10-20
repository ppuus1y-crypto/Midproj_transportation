
from pathlib import Path
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_from_directory

from database import (
    init_db,
    get_connection as _connect_settings_db,
    get_all_journeys,
    get_journey,
    save_journey,
    delete_journey,
    delete_journeys,
)


# 專案根目錄
BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)


@app.after_request
def add_local_dev_cors(response):
    origin = request.headers.get("Origin")
    if origin in {"http://127.0.0.1:5500", "http://localhost:5500"}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Vary"] = "Origin"
    return response


# 啟動時確認資料表已建立
init_db()


def format_journey(journey):
    """將資料庫欄位轉成前端使用的命名格式。"""
    if journey is None:
        return None

    return {
        "id": journey["id"],
        "title": journey["title"],
        "departureTime": journey["departure_time"],
        "savedAt": journey["saved_at"],
        "updatedAt": journey["updated_at"],
        "routes": journey["routes"],
    }


@app.route("/")
def index():
    """顯示首頁。"""
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/styles.css")
def styles():
    """提供網頁樣式。"""
    return send_from_directory(BASE_DIR, "styles.css")


@app.route("/api/journeys", methods=["GET"])
def list_journeys():
    """取得所有路線紀錄。"""
    journeys = get_all_journeys()

    return jsonify([
        format_journey(journey)
        for journey in journeys
    ])


@app.route("/api/journeys/<int:journey_id>", methods=["GET"])
def read_journey(journey_id):
    """取得指定路線及其路段。"""
    journey = get_journey(journey_id)

    if journey is None:
        return jsonify({
            "error": "找不到這筆路線紀錄。"
        }), 404

    return jsonify(format_journey(journey))


@app.route("/api/journeys", methods=["POST"])
def create_or_update_journey():
    """新增路線，或更新同名路線。"""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "請提供有效的 JSON 資料。"
        }), 400

    title = str(data.get("title") or "").strip()
    departure_time = str(
        data.get("departureTime") or "09:00"
    ).strip()
    routes = data.get("routes", [])

    if not title:
        return jsonify({
            "error": "路線名稱不可為空。"
        }), 400

    if not isinstance(routes, list):
        return jsonify({
            "error": "routes 必須是列表。"
        }), 400

    try:
        # 判斷原本是否已有同名路線
        existing = next(
            (
                item for item in get_all_journeys()
                if item["title"] == title
            ),
            None
        )

        journey = save_journey(
            title=title,
            departure_time=departure_time,
            routes=routes,
            saved_at=data.get("savedAt"),
        )

        status_code = 200 if existing else 201

        return jsonify(format_journey(journey)), status_code

    except (ValueError, TypeError) as error:
        return jsonify({
            "error": str(error)
        }), 400

    except Exception:
        app.logger.exception("儲存路線時發生錯誤")

        return jsonify({
            "error": "儲存失敗，請檢查伺服器終端機的錯誤訊息。"
        }), 500


@app.route("/api/journeys/<int:journey_id>", methods=["DELETE"])
def remove_journey(journey_id):
    """刪除單筆路線紀錄。"""
    deleted = delete_journey(journey_id)

    if not deleted:
        return jsonify({
            "error": "找不到這筆路線紀錄。"
        }), 404

    return jsonify({
        "success": True,
        "message": "路線已刪除。"
    })


@app.route("/api/journeys/delete-batch", methods=["POST"])
def remove_journeys():
    """批次刪除多筆路線紀錄。"""
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "請提供有效的 JSON 資料。"
        }), 400

    ids = data.get("ids", [])

    if not isinstance(ids, list):
        return jsonify({
            "error": "ids 必須是列表。"
        }), 400

    try:
        # 只接受正整數 ID，不接受布林值或其他格式
        if any(
            type(item) is not int or item <= 0
            for item in ids
        ):
            return jsonify({
                "error": "每個 ID 都必須是正整數。"
            }), 400

        deleted_count = delete_journeys(ids)

        return jsonify({
            "success": True,
            "deletedCount": deleted_count,
        })

    except Exception:
        app.logger.exception("批次刪除時發生錯誤")

        return jsonify({
            "error": "批次刪除失敗。"
        }), 500


# ---------------- Google Places API（New）設定 ----------------
PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
ROUTES_API_KEY = (
    os.getenv("GOOGLE_ROUTES_API_KEY", "").strip() or PLACES_API_KEY
)
MAPS_BROWSER_API_KEY = os.getenv("GOOGLE_MAPS_BROWSER_API_KEY", "").strip()


def _format_duration_minutes(minutes):
    hours, remaining_minutes = divmod(minutes, 60)
    if hours and remaining_minutes:
        return f"{hours}小時{remaining_minutes}分"
    if hours:
        return f"{hours}小時"
    return f"{remaining_minutes}分"


def _get_setting(key, default="false"):
    conn = _connect_settings_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT setting_value FROM app_settings WHERE setting_key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else default
    finally:
        cur.close()
        conn.close()


def _set_setting(key, value):
    conn = _connect_settings_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)""",
            (key, value),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


@app.get("/api/places/status")
def places_status():
    return jsonify({
        "enabled": _get_setting("google_places_enabled") == "true",
        "configured": bool(MAPS_BROWSER_API_KEY),
    })


@app.post("/api/places/toggle")
def places_toggle():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled 必須是 true 或 false。"}), 400
    if enabled and not MAPS_BROWSER_API_KEY:
        return jsonify({
            "error": "使用 Google Autocomplete widget 需要設定 GOOGLE_MAPS_BROWSER_API_KEY。"
        }), 400
    _set_setting("google_places_enabled", "true" if enabled else "false")
    return jsonify({
        "enabled": enabled,
        "configured": bool(MAPS_BROWSER_API_KEY),
    })


@app.get("/api/routes/status")
def routes_status():
    return jsonify({
        "enabled": _get_setting("google_routes_enabled") == "true",
        "configured": bool(ROUTES_API_KEY),
    })


@app.post("/api/routes/toggle")
def routes_toggle():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled 必須是 true 或 false。"}), 400
    if enabled and not ROUTES_API_KEY:
        return jsonify({
            "error": "尚未設定 GOOGLE_ROUTES_API_KEY 或可共用的 GOOGLE_PLACES_API_KEY。"
        }), 400
    _set_setting("google_routes_enabled", "true" if enabled else "false")
    return jsonify({
        "enabled": enabled,
        "configured": bool(ROUTES_API_KEY),
    })


@app.get("/api/maps/status")
def maps_status():
    return jsonify({
        "enabled": _get_setting("google_maps_enabled") == "true",
        "configured": bool(MAPS_BROWSER_API_KEY),
    })


@app.post("/api/maps/toggle")
def maps_toggle():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled 必須是 true 或 false。"}), 400
    if enabled and not MAPS_BROWSER_API_KEY:
        return jsonify({
            "error": "尚未設定 GOOGLE_MAPS_BROWSER_API_KEY 瀏覽器地圖金鑰。"
        }), 400
    _set_setting("google_maps_enabled", "true" if enabled else "false")
    return jsonify({
        "enabled": enabled,
        "configured": bool(MAPS_BROWSER_API_KEY),
    })


@app.get("/api/maps/config")
def maps_config():
    if (
        _get_setting("google_maps_enabled") != "true"
        and _get_setting("google_places_enabled") != "true"
    ):
        return jsonify({"error": "Google Maps 載入功能目前已關閉。"}), 403
    if not MAPS_BROWSER_API_KEY:
        return jsonify({
            "error": "尚未設定 GOOGLE_MAPS_BROWSER_API_KEY 瀏覽器地圖金鑰。"
        }), 503
    return jsonify({"apiKey": MAPS_BROWSER_API_KEY})


@app.post("/api/routes/compute")
def compute_routes():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("routes"), list):
        return jsonify({"error": "請提供 routes 路段列表。"}), 400

    routes = data["routes"]
    if not routes:
        return jsonify({"error": "沒有可計算的完整路段。"}), 400

    if _get_setting("google_routes_enabled") != "true":
        return jsonify({
            "error": "Google Routes API 已關閉，請至設定頁開啟。",
            "disabled": True,
        }), 403
    if not ROUTES_API_KEY:
        return jsonify({"error": "伺服器尚未設定 Routes API 金鑰。"}), 503

    prepared_routes = []
    google_modes = {
        "步行": ("WALK", None),
        "ubike": ("BICYCLE", None),
        "捷運": ("TRANSIT", ["SUBWAY"]),
        "公車": ("TRANSIT", ["BUS"]),
        "火車": ("TRANSIT", ["TRAIN"]),
    }
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            return jsonify({"error": f"第 {index + 1} 個路段格式錯誤。"}), 400
        origin = str(route.get("from") or "").strip()
        destination = str(route.get("to") or "").strip()
        mode = str(route.get("mode") or "").strip()
        route_id = route.get("id")
        stay_minutes = route.get("stayMinutes", 0)
        if not origin or not destination:
            return jsonify({"error": f"第 {index + 1} 個路段缺少起點或終點。"}), 400
        if mode not in google_modes:
            return jsonify({"error": f"第 {index + 1} 個路段的交通方式不支援。"}), 400
        if type(stay_minutes) is not int or stay_minutes < 0:
            return jsonify({"error": f"第 {index + 1} 個路段的停留時間格式錯誤。"}), 400
        prepared_routes.append({
            "id": route_id,
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "stayMinutes": stay_minutes,
        })

    departure_time_text = str(data.get("departureTime") or "").strip()
    try:
        departure_time = datetime.fromisoformat(
            departure_time_text.replace("Z", "+00:00")
        )
        if departure_time.tzinfo is None:
            raise ValueError("departureTime 必須包含時區。")
        departure_time = departure_time.astimezone(timezone.utc)
    except ValueError:
        return jsonify({
            "error": "departureTime 必須是包含時區的 ISO 8601 日期時間。"
        }), 400

    results = []
    for index, route in enumerate(prepared_routes):
        travel_mode, allowed_modes = google_modes[route["mode"]]
        body = {
            "origin": {"address": route["origin"]},
            "destination": {"address": route["destination"]},
            "travelMode": travel_mode,
            "departureTime": departure_time.isoformat().replace("+00:00", "Z"),
            "languageCode": "zh-TW",
            "regionCode": "TW",
        }
        if allowed_modes:
            body["transitPreferences"] = {
                "allowedTravelModes": allowed_modes,
            }
        req = urllib.request.Request(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": ROUTES_API_KEY,
                "X-Goog-FieldMask": (
                    "routes.duration,routes.polyline.encodedPolyline"
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
            route_results = result.get("routes") or []
            if not route_results:
                return jsonify({
                    "error": f"Google Routes API 未找到第 {index + 1} 個路段的路線。"
                }), 422
            match = re.fullmatch(
                r"(\d+(?:\.\d+)?)s",
                str(route_results[0].get("duration") or ""),
            )
            if not match:
                app.logger.error(
                    "Routes API 回傳無效的 duration：%r",
                    route_results[0].get("duration"),
                )
                return jsonify({
                    "error": f"Google Routes API 回傳第 {index + 1} 個路段的時間格式無效。"
                }), 502
            duration_minutes = math.ceil(float(match.group(1)) / 60)
            results.append({
                "id": route["id"],
                "durationMinutes": duration_minutes,
                "time": _format_duration_minutes(duration_minutes),
                "encodedPolyline": (
                    route_results[0].get("polyline", {}).get("encodedPolyline")
                ),
            })
            departure_time += timedelta(
                minutes=duration_minutes + route["stayMinutes"]
            )
        except urllib.error.HTTPError as error:
            app.logger.error(
                "Routes API HTTP %s: %s",
                error.code,
                error.read().decode("utf-8", errors="replace"),
            )
            return jsonify({
                "error": (
                    "Google Routes API 查詢失敗，請確認 API 金鑰限制、"
                    "Routes API 啟用狀態及帳單設定。"
                )
            }), 502
        except (urllib.error.URLError, TimeoutError):
            app.logger.exception("Routes API 連線失敗")
            return jsonify({
                "error": "目前無法連線至 Google Routes API，請稍後再試。"
            }), 502

    return jsonify({"routes": results})


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )

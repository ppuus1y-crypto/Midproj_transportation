
from pathlib import Path
import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage

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


# ---------------- Google Places API（New）使用量控制 ----------------
PLACES_MONTHLY_LIMIT = int(os.getenv("PLACES_MONTHLY_LIMIT", "100"))
PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()


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


def _month_key():
    return datetime.now().astimezone().strftime("%Y-%m")


def _usage_status():
    month = _month_key()
    conn = _connect_settings_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT request_count FROM places_api_usage WHERE usage_month=%s",
            (month,),
        )
        row = cur.fetchone()
        count = row[0] if row else 0
        return {
            "month": month, "count": count, "limit": PLACES_MONTHLY_LIMIT,
            "enabled": _get_setting("google_places_enabled") == "true",
            "percent": min(100, round(count / max(PLACES_MONTHLY_LIMIT, 1) * 100)),
            "configured": bool(PLACES_API_KEY),
        }
    finally:
        cur.close()
        conn.close()


def _send_80_percent_email(month, count):
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", username).strip()
    recipient = os.getenv("SMTP_TO", "").strip()
    if not all([host, username, password, sender, recipient]):
        app.logger.warning("Places API 已達 80%%，但 SMTP 環境變數未完整設定，無法寄信。")
        return
    msg = EmailMessage()
    msg["Subject"] = f"[交通路線專題] Places API 使用量已達 80%（{month}）"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(f"本月已使用 {count}/{PLACES_MONTHLY_LIMIT} 次 Google Places API 自動完成請求。")
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def _reserve_places_request():
    month = _month_key()
    conn = _connect_settings_db()
    cur = conn.cursor()
    try:
        conn.start_transaction()
        cur.execute(
            "INSERT IGNORE INTO places_api_usage (usage_month, request_count, notified_80) VALUES (%s, 0, 0)",
            (month,),
        )
        cur.execute(
            "SELECT request_count, notified_80 FROM places_api_usage WHERE usage_month=%s FOR UPDATE",
            (month,),
        )
        count, notified = cur.fetchone()
        if count >= PLACES_MONTHLY_LIMIT:
            conn.commit()
            return False, count, False
        count += 1
        should_notify = count >= int(PLACES_MONTHLY_LIMIT * 0.8) and not bool(notified)
        cur.execute(
            "UPDATE places_api_usage SET request_count=%s, notified_80=%s WHERE usage_month=%s",
            (count, int(bool(notified) or should_notify), month),
        )
        conn.commit()
        return True, count, should_notify
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@app.get("/api/places/status")
def places_status():
    return jsonify(_usage_status())


@app.post("/api/places/toggle")
def places_toggle():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled 必須是 true 或 false。"}), 400
    status = _usage_status()
    if enabled and not PLACES_API_KEY:
        return jsonify({"error": "尚未設定 GOOGLE_PLACES_API_KEY 環境變數。"}), 400
    if enabled and status["count"] >= PLACES_MONTHLY_LIMIT:
        return jsonify({"error": "本月請求已達上限，無法啟用。"}), 429
    _set_setting("google_places_enabled", "true" if enabled else "false")
    return jsonify(_usage_status())


@app.post("/api/places/autocomplete")
def places_autocomplete():
    data = request.get_json(silent=True) or {}
    text = str(data.get("input") or "").strip()
    if len(text) < 2:
        return jsonify({"suggestions": []})
    status = _usage_status()
    if not status["enabled"]:
        return jsonify({"error": "Google 地點自動完成已關閉，請手動輸入。", "disabled": True}), 403
    if status["count"] >= PLACES_MONTHLY_LIMIT:
        _set_setting("google_places_enabled", "false")
        return jsonify({"error": "本月已達 100 次上限，Google 地點自動完成已停用。", "disabled": True}), 429
    if not PLACES_API_KEY:
        return jsonify({"error": "伺服器尚未設定 API 金鑰。"}), 503

    allowed, count, notify = _reserve_places_request()
    if not allowed:
        _set_setting("google_places_enabled", "false")
        return jsonify({"error": "本月已達請求上限，已自動停用。", "disabled": True}), 429
    if notify:
        try:
            _send_80_percent_email(_month_key(), count)
        except Exception:
            app.logger.exception("Places API 80%% 電子郵件通知失敗")

    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:autocomplete",
        data=json.dumps({"input": text, "languageCode": "zh-TW", "includedRegionCodes": ["tw"]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": PLACES_API_KEY,
            "X-Goog-FieldMask": "suggestions.placePrediction.placeId,suggestions.placePrediction.text",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        suggestions = []
        for item in result.get("suggestions", []):
            prediction = item.get("placePrediction", {})
            label = prediction.get("text", {}).get("text", "")
            if label:
                suggestions.append({"text": label, "placeId": prediction.get("placeId", "")})
        return jsonify({"suggestions": suggestions, "usage": _usage_status()})
    except urllib.error.HTTPError as error:
        app.logger.error("Places API HTTP %s: %s", error.code, error.read().decode("utf-8", errors="replace"))
        return jsonify({"error": "Google Places API 查詢失敗，請確認金鑰、API 啟用狀態及帳單設定。"}), 502
    except (urllib.error.URLError, TimeoutError):
        app.logger.exception("Places API 連線失敗")
        return jsonify({"error": "目前無法連線至 Google Places API，請稍後再試。"}), 502


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )

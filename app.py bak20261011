
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from database import (
    init_db,
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


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
    )

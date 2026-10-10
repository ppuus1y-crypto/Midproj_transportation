  
import sqlite3
from pathlib import Path
from datetime import datetime


# 專案根目錄與資料庫路徑
BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DATABASE_PATH = INSTANCE_DIR / "routes.db"


def get_connection():
    """建立資料庫連線。"""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def init_db():
    """建立資料表；資料表已存在時不會重複建立。"""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS journeys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL UNIQUE,
                departure_time TEXT NOT NULL DEFAULT '09:00',
                saved_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS route_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journey_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                origin TEXT NOT NULL DEFAULT '',
                destination TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT '預設',
                travel_time TEXT NOT NULL DEFAULT '',
                stay_time TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (journey_id)
                    REFERENCES journeys(id)
                    ON DELETE CASCADE,
                UNIQUE (journey_id, position)
            )
        """)

    print(f"資料庫初始化完成：{DATABASE_PATH}")


def save_journey(title, departure_time, routes, saved_at=None):
    """
    新增或更新路線。
    路線名稱相同時更新原紀錄，不同名稱時新增紀錄。
    """
    title = str(title or "").strip()
    departure_time = str(departure_time or "09:00").strip()

    if not title:
        raise ValueError("路線名稱不可為空。")

    if not isinstance(routes, list):
        raise ValueError("routes 必須是列表。")

    route_ids = []
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("每個路段都必須是字典。")

        try:
            route_ids.append(
                int(route["id"]) if route.get("id") is not None else None
            )
        except (TypeError, ValueError) as error:
            raise ValueError("路段 ID 必須是整數。") from error

    now = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    saved_at = str(saved_at or "").strip() or now

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM journeys WHERE title = ?",
            (title,)
        ).fetchone()

        if existing:
            journey_id = existing["id"]
            existing_route_rows = conn.execute("""
                SELECT id
                FROM route_legs
                WHERE journey_id = ?
                ORDER BY position
            """, (journey_id,)).fetchall()
            existing_route_ids = {
                row["id"] for row in existing_route_rows
            }
            retained_route_ids = set()

            conn.execute("""
                UPDATE journeys
                SET departure_time = ?, updated_at = ?
                WHERE id = ?
            """, (departure_time, now, journey_id))

            conn.execute("""
                UPDATE route_legs
                SET position = -id
                WHERE journey_id = ?
            """, (journey_id,))

        else:
            existing_route_rows = []
            existing_route_ids = set()
            retained_route_ids = set()

            cursor = conn.execute("""
                INSERT INTO journeys (
                    title, departure_time, saved_at, updated_at
                )
                VALUES (?, ?, ?, ?)
            """, (title, departure_time, saved_at, now))

            journey_id = cursor.lastrowid

        has_known_route_id = any(
            route_id in existing_route_ids
            for route_id in route_ids
        )

        for position, (route, route_id) in enumerate(
            zip(routes, route_ids),
            start=1
        ):
            if (
                not has_known_route_id
                and position <= len(existing_route_rows)
            ):
                route_id = existing_route_rows[position - 1]["id"]

            route_values = (
                position,
                str(route.get("from") or "").strip(),
                str(route.get("to") or "").strip(),
                str(route.get("mode") or "預設").strip(),
                str(route.get("time") or "").strip(),
                str(route.get("stay") or "").strip()
            )

            if route_id in existing_route_ids:
                if route_id in retained_route_ids:
                    raise ValueError("路段 ID 不可重複。")

                conn.execute("""
                    UPDATE route_legs
                    SET position = ?, origin = ?, destination = ?,
                        mode = ?, travel_time = ?, stay_time = ?
                    WHERE id = ? AND journey_id = ?
                """, (*route_values, route_id, journey_id))
                retained_route_ids.add(route_id)
            else:
                conn.execute("""
                    INSERT INTO route_legs (
                        journey_id, position, origin, destination,
                        mode, travel_time, stay_time
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (journey_id, *route_values))

        for row in existing_route_rows:
            if row["id"] not in retained_route_ids:
                conn.execute(
                    "DELETE FROM route_legs WHERE id = ?",
                    (row["id"],)
                )

    return get_journey(journey_id)


def get_journey(journey_id):
    """依照資料庫 ID 取得單筆路線及其所有路段。"""
    with get_connection() as conn:
        journey = conn.execute("""
            SELECT *
            FROM journeys
            WHERE id = ?
        """, (journey_id,)).fetchone()

        if journey is None:
            return None

        routes = conn.execute("""
            SELECT *
            FROM route_legs
            WHERE journey_id = ?
            ORDER BY position
        """, (journey_id,)).fetchall()

        result = dict(journey)
        result["routes"] = [
            {
                "id": route["id"],
                "from": route["origin"],
                "to": route["destination"],
                "mode": route["mode"],
                "time": route["travel_time"],
                "stay": route["stay_time"]
            }
            for route in routes
        ]

        return result


def get_all_journeys():
    """取得所有路線紀錄，最新更新的排在前面。"""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id
            FROM journeys
            ORDER BY updated_at DESC, id DESC
        """).fetchall()

    return [
        get_journey(row["id"])
        for row in rows
    ]


def delete_journey(journey_id):
    """刪除一筆路線及其所屬路段。"""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM journeys WHERE id = ?",
            (journey_id,)
        )

        return cursor.rowcount > 0


def delete_journeys(journey_ids):
    """批次刪除多筆路線及其所屬路段。"""
    ids = list(dict.fromkeys(journey_ids))

    if not ids:
        return 0

    with get_connection() as conn:
        placeholders = ",".join("?" for _ in ids)

        cursor = conn.execute(
            f"DELETE FROM journeys WHERE id IN ({placeholders})",
            ids
        )

        return cursor.rowcount


if __name__ == "__main__":
    init_db()

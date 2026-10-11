  
import os
import mysql.connector
from datetime import datetime


def get_connection():
    """建立 MySQL 連線。"""
    password = os.getenv("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("請先設定 MYSQL_PASSWORD 環境變數。")

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=password,
        database="tran",
        charset="utf8mb4",
    )


def init_db():
    """確認 MySQL 資料庫已建立必要資料表。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for table_name in ("records", "route"):
            cursor.execute(f"SELECT 1 FROM `{table_name}` LIMIT 0")
            cursor.fetchall()
    except mysql.connector.Error as error:
        raise RuntimeError(
            "請確認 MySQL 的 tran 資料庫已建立 records 和 route 資料表。"
        ) from error
    finally:
        cursor.close()
        conn.close()

    print("MySQL 資料表確認完成：tran")


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

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id FROM `records` WHERE title = %s",
            (title,)
        )
        existing = cursor.fetchone()

        if existing:
            records_id = existing["id"]
            cursor.execute("""
                SELECT id
                FROM `route`
                WHERE records_id = %s
                ORDER BY `position`
            """, (records_id,))
            existing_route_rows = cursor.fetchall()
            existing_route_ids = {
                row["id"] for row in existing_route_rows
            }
            retained_route_ids = set()

            cursor.execute("""
                UPDATE `records`
                SET departure_time = %s, updated_at = %s
                WHERE id = %s
            """, (departure_time, now, records_id))

            cursor.execute("""
                UPDATE `route`
                SET `position` = -id
                WHERE records_id = %s
            """, (records_id,))

        else:
            existing_route_rows = []
            existing_route_ids = set()
            retained_route_ids = set()

            cursor.execute("""
                INSERT INTO `records` (
                    title, departure_time, saved_at, updated_at
                )
                VALUES (%s, %s, %s, %s)
            """, (title, departure_time, saved_at, now))

            records_id = cursor.lastrowid

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

                cursor.execute("""
                    UPDATE `route`
                    SET `position` = %s, origin = %s, destination = %s,
                        mode = %s, travel_time = %s, stay_time = %s
                    WHERE id = %s AND records_id = %s
                """, (*route_values, route_id, records_id))
                retained_route_ids.add(route_id)
            else:
                cursor.execute("""
                    INSERT INTO `route` (
                        records_id, `position`, origin, destination,
                        mode, travel_time, stay_time
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (records_id, *route_values))

        for row in existing_route_rows:
            if row["id"] not in retained_route_ids:
                cursor.execute(
                    "DELETE FROM `route` WHERE id = %s",
                    (row["id"],)
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return get_journey(records_id)


def get_journey(records_id):
    """依照資料庫 ID 取得單筆路線及其所有路段。"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT *
            FROM `records`
            WHERE id = %s
        """, (records_id,))
        journey = cursor.fetchone()

        if journey is None:
            return None

        cursor.execute("""
            SELECT *
            FROM `route`
            WHERE records_id = %s
            ORDER BY `position`
        """, (records_id,))
        routes = cursor.fetchall()

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
    finally:
        cursor.close()
        conn.close()


def get_all_journeys():
    """取得所有路線紀錄，最新更新的排在前面。"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id
            FROM `records`
            ORDER BY updated_at DESC, id DESC
        """)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    return [
        get_journey(row["id"])
        for row in rows
    ]


def delete_journey(records_id):
    """刪除一筆路線及其所屬路段。"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM `records` WHERE id = %s",
            (records_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def delete_journeys(records_ids):
    """批次刪除多筆路線及其所屬路段。"""
    ids = list(dict.fromkeys(records_ids))

    if not ids:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ",".join("%s" for _ in ids)
        cursor.execute(
            f"DELETE FROM `records` WHERE id IN ({placeholders})",
            ids
        )
        conn.commit()
        return cursor.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    init_db()

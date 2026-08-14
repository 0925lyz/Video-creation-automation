import sqlite3
import sys

def migrate(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(candidates)")
    columns = [col[1] for col in cursor.fetchall()]
    if "parent_id" not in columns:
        print("Adding parent_id to candidates...")
        cursor.execute("ALTER TABLE candidates ADD COLUMN parent_id TEXT;")
        conn.commit()
    else:
        print("parent_id already exists.")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python migrate_db.py <path_to_db>")
        sys.exit(1)
    migrate(sys.argv[1])

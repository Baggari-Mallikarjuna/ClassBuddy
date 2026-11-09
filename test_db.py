#!/usr/bin/env python
from utils.database import managed_connection

try:
    with managed_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        print("SQLite Database Connected Successfully!")
        print(f"\nDatabase Location: {conn.execute('PRAGMA database_list').fetchone()['file']}")
        print(f"Tables Found: {len(tables)}")
        print("\nTables:")
        for table in tables:
            print(f"  - {table[0]}")

            cursor.execute(f"PRAGMA table_info({table[0]});")
            columns = cursor.fetchall()
            for col in columns:
                print(f"      - {col[1]} ({col[2]})")

    print("\n✓ Database connection closed successfully!")
    
except Exception as e:
    print(f"✗ Error connecting to database: {e}")

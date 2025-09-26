import sqlite3

# --- Replace with your SQLite database file ---
db_path = "app.db"   # Example: 'instance/app.db' if using Flask default

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Delete all rows from the table
    cursor.execute("DELETE FROM hackathon_post;")
    conn.commit()

    print("All posts deleted from hackathon_post.")

    # If you also want to reset AUTOINCREMENT (IDs start from 1 again):
    cursor.execute("DELETE FROM sqlite_sequence WHERE name='hackathon_post';")
    conn.commit()

except sqlite3.Error as e:
    print("Error:", e)

finally:
    conn.close()

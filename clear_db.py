import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app, db

def clear_database_records():
    print("Connecting to the database...")
    with app.app_context():
        try:
            print("Starting transaction to clear all records...")
            for table in reversed(db.metadata.sorted_tables):
                print(f"  - Deleting records from: {table.name}")
                db.session.execute(table.delete())

            db.session.commit()
            print("\nSuccess! All records have been deleted. Table structures remain intact.")

        except Exception as e:
            print(f"\nAn error occurred: {e}")
            print("Rolling back transaction.")
            db.session.rollback()
        finally:
            print("Process finished.")

if __name__ == '__main__':
    confirmation = input(
        "This will delete ALL RECORDS from the database without deleting the tables.\n"
        " This action cannot be undone. Are you sure? (Type 'yes' to confirm): "
    )
    if confirmation.lower() == 'yes':
        clear_database_records()
    else:
        print("Operation cancelled.")


import os
import sys
from sqlalchemy import create_engine, text

# Add parent directory to path so we can import from app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.config import get_settings

def run_migration():
    settings = get_settings()
    engine = create_engine(settings.database_url)
    
    migration_path = os.path.join(os.path.dirname(__file__), 'migrate.sql')
    
    with open(migration_path, 'r', encoding='utf-8') as f:
        sql_script = f.read()

    # Split script into individual statements to avoid multiple commands errors if necessary,
    # or just execute it as a single block if the driver supports it. psycopg2 usually supports it.
    
    with engine.begin() as conn:
        for statement in sql_script.split(';'):
            statement = statement.strip()
            if statement:
                print(f"Executing: {statement[:50]}...")
                conn.execute(text(statement))
                
    print("Migration executed successfully.")

if __name__ == "__main__":
    run_migration()

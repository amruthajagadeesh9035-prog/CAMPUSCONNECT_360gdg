
from database import create_tables, get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash
from getpass import getpass

create_tables()

username = input("Enter admin username: ").strip()
password = getpass("Enter admin password: ")

if not username or len(password) < 8:
    print("Username is required and password must have at least 8 characters.")
else:
    conn = get_db_connection()

    existing = conn.execute(
        "SELECT id FROM admins WHERE username = ?",
        (username,)
    ).fetchone()

    if existing:
        print("This admin username already exists.")
    else:
        hashed_password = generate_password_hash(password)

        conn.execute(
            "INSERT INTO admins (username, password) VALUES (?, ?)",
            (username, hashed_password)
        )

        conn.commit()
        print("Admin account created successfully!")

    conn.close()

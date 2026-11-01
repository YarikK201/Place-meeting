from catalog import connect


def init():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                bio TEXT DEFAULT '',
                waiting INTEGER DEFAULT 1
            )
        """)


def choose(user_id, role):
    with connect() as db:
        db.execute("""
            INSERT INTO profiles (user_id, role, waiting)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                role=excluded.role, waiting=1
        """, (user_id, role))


def waiting(user_id):
    with connect() as db:
        row = db.execute(
            "SELECT waiting FROM profiles WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return bool(row and row["waiting"])


def save(user_id, bio):
    with connect() as db:
        db.execute("""
            UPDATE profiles SET bio=?, waiting=0
            WHERE user_id=?
        """, (bio, user_id))


def get(user_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM profiles WHERE user_id=?",
            (user_id,)
        ).fetchone()
import argparse
import secrets
from datetime import timedelta
from urllib.parse import urlencode, quote

from app import auth, db


EMAIL = "operator@example.com"


def seed(path="./controlplane.db", reset=False):
    db.initialize(path)
    connection = db.get_connection()
    credentials = None
    with connection:
        if reset:
            connection.execute("DELETE FROM sessions")
            connection.execute("DELETE FROM login_attempts")
            connection.execute("DELETE FROM operators")
            connection.execute("DELETE FROM appeals")
            connection.execute("DELETE FROM match_players")
            connection.execute("DELETE FROM matches")
            connection.execute("DELETE FROM accounts")
            connection.execute("DELETE FROM db_samples")
        current = auth.utc_now()
        if connection.execute("SELECT id FROM operators WHERE email=?", (EMAIL,)).fetchone() is None:
            password = secrets.token_urlsafe(18)
            secret = auth.new_totp_secret()
            connection.execute(
                "INSERT INTO operators(email, password_hash, totp_secret, created_at) VALUES (?, ?, ?, ?)",
                (EMAIL, auth.password_hash(password), secret, auth.iso_now(current)),
            )
            credentials = {"email": EMAIL, "password": password, "totp_secret": secret}
        if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            humans = []
            bots = []
            for index in range(8):
                created = current.replace(hour=0, minute=0, second=0, microsecond=0) if index < 7 else current - timedelta(days=3)
                humans.append(connection.execute(
                    "INSERT INTO accounts(kind, created_at) VALUES (?, ?)", ("human", auth.iso_now(created))
                ).lastrowid)
            for index in range(12):
                bots.append(connection.execute(
                    "INSERT INTO accounts(kind, created_at) VALUES (?, ?)",
                    ("bot", auth.iso_now(current - timedelta(days=4))),
                ).lastrowid)
            for index in range(156):
                age = timedelta(minutes=index * 4 + 1) if index < 12 else timedelta(minutes=60 + (index - 12) * 39)
                match_id = connection.execute(
                    "INSERT INTO matches(started_at, status, is_private) VALUES (?, ?, ?)",
                    (auth.iso_now(current - age), "completed", int(index < 2)),
                ).lastrowid
                participants = [humans[index % 8]]
                if index != 155:
                    participants.extend([bots[index % 12], bots[(index + 1) % 12]])
                else:
                    participants.append(humans[(index + 1) % 8])
                connection.executemany(
                    "INSERT INTO match_players(match_id, account_id) VALUES (?, ?)",
                    [(match_id, account_id) for account_id in participants],
                )
    return credentials


def main():
    parser = argparse.ArgumentParser(description="Seed Control Plane demo data")
    parser.add_argument("--db", default="./controlplane.db")
    parser.add_argument("--reset", action="store_true", help="Wipe database contents before seeding")
    args = parser.parse_args()
    try:
        credentials = seed(args.db, args.reset)
        if credentials:
            uri = "otpauth://totp/" + quote("Control Plane:" + EMAIL, safe="") + "?" + urlencode({
                "secret": credentials["totp_secret"], "issuer": "Control Plane", "algorithm": "SHA1", "digits": 6, "period": 30,
            })
            print(f"Email: {EMAIL}\nPassword: {credentials['password']}\nTOTP secret: {credentials['totp_secret']}\n{uri}")
            print("Save these credentials; they are printed only when the operator is created.")
        else:
            print("Seed data already exists. Credentials unchanged.")
    finally:
        db.close_connection()


if __name__ == "__main__":
    main()

# Reading Room: Online Library Management System

Django 5 + PostgreSQL. Members browse a catalogue and borrow/return books. Sign-in uses
**email and password**, with failed-login lockout, tokenised password reset and a security test suite.
Librarians manage books and loans in the Django admin.

## Project layout

```
library_project/   settings.py (env-driven, hardened), urls.py, wsgi/asgi
accounts/          custom User, password login, lockout, reset views, tests.py
catalog/           Author, Category, Book, Loan, borrow/return services, views, admin, tests.py
templates/ static/ server-rendered UI and catalogue interactions
scripts/create_db.sql creates the PostgreSQL user + database
.env.example       configuration template
```

## Database models

```
User(id, email UNIQUE(lower), full_name, password(hash), is_active, is_staff, is_superuser,
     failed_login_attempts, locked_until, date_joined, last_login)
Author(id, name UNIQUE)            Category(id, name UNIQUE)
Book(id, title, isbn UNIQUE[10|13 digits], category_id→Category NULL, description,
     published_year, total_copies, available_copies,  CHECK available_copies <= total_copies)
Book ⇄ Author                      (many-to-many)
Loan(id, user_id→User, book_id→Book, borrowed_at, due_date, returned_at NULL,
     UNIQUE(user, book) WHERE returned_at IS NULL)   -- one active loan per member per title
```

Rules (configurable in `settings.py`): 14-day loans, max 5 active loans, fine 0.25/day overdue.
Borrow/return run in transactions with `SELECT … FOR UPDATE`, so concurrent requests can't oversell copies.

## Setup

Requires Python 3.10+ and a natively installed PostgreSQL 14+ (no Docker or containers).

### 1. Install PostgreSQL natively

- **Ubuntu/Debian:** `sudo apt install postgresql` (starts automatically)
- **macOS (Homebrew):** `brew install postgresql@16 && brew services start postgresql@16`
- **Windows:** use the installer from postgresql.org (remember the `postgres` password you set; make sure `psql` is on your PATH)

### 2. Create the database and user

```bash
# Linux
sudo -u postgres psql -f scripts/create_db.sql
# macOS (your own user is the superuser)
psql postgres -f scripts/create_db.sql
# Windows (prompts for the postgres password)
psql -U postgres -f scripts\create_db.sql
```
Edit the password in `scripts/create_db.sql` first if you like, and use the same value in `.env`.

### 3. Install and run the project

```bash
cd library_system
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
# generate a real secret and paste it as DJANGO_SECRET_KEY in .env:
python -c "import secrets; print(secrets.token_urlsafe(60))"

python manage.py migrate
python manage.py seed_library            # 12 sample books
python manage.py createsuperuser         # your librarian/admin account (email + password)
python manage.py runserver
```

Open http://127.0.0.1:8000. The `.env.example` is configured for Gmail SMTP. Before signing in,
replace `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` in `.env` with your
Gmail address, a Google App Password, and that same Gmail address. To create an App Password,
enable 2-Step Verification on your Google Account, then create an App Password in your account's
security settings. Use the generated password (without spaces), not your normal Gmail password.
The sign-in code is sent to the email address on the account; Gmail is the sending account.

Keep `.env` private and never commit your App Password. To use the development console instead,
set `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`; emails will be printed to the
terminal running `runserver`.

Troubleshooting: `connection refused` means PostgreSQL isn't running; `password authentication failed`
means `.env` doesn't match the password in `create_db.sql`.
(`DB_ENGINE=sqlite` exists only for quick experiments without PostgreSQL.)

## Run the tests

```bash
python manage.py test            # 56 tests; ~1 min because real password hashing is slow
```
The DB user needs `CREATEDB` (Django creates a throwaway `test_library` database).

## Demonstration steps

1. **Register**: `/accounts/register/`. Try a weak password (`password123`): it is rejected. Use a strong one.
2. **Sign in**: enter your email and password at `/accounts/login/`. Successful sign-in takes you straight to
   the catalogue (or to the page you originally requested).
3. **Lockout**: sign out, then enter a wrong password 5 times. Even the right password is refused for 15 min,
   and the message is identical to an unknown-email error (no account enumeration).
4. **Password reset**: "Forgot your password?" → enter email → open the link from the terminal → set a new
   password. This also clears any lockout and signs out other devices.
   An unknown email gets the exact same on-screen response.
5. **Borrow & return**: open a book → *Borrow for 14 days*. Check *My loans*. Try borrowing the same
   title twice, or a 6th book: blocked. Return it: stock goes back up.
6. **Librarian desk**: sign in as the superuser → *Librarian desk* (`/admin/`). Add books/authors,
   view all loans, use the **Mark selected loans as returned** action. Make a normal user staff by ticking
   *is_staff* and granting permissions.
7. **Overdue fine**: in the admin edit a loan's due date into the past, then view *My loans* as that member.

## Security design

| Concern | Control |
|---|---|
| Passwords | PBKDF2-SHA256 (salted), 10+ char minimum, common/numeric/similarity validators |
| Brute force | Per-account lockout (5 failures → 15 min); password is not checked while locked |
| Enumeration | Same error for unknown email / wrong password / locked; reset page always shows the same confirmation; dummy hash on unknown email to blunt timing |
| Sessions | Key rotated at login; HttpOnly, SameSite=Lax, Secure (when `DEBUG=False`); 1 h sliding expiry; password change invalidates all other sessions |
| Reset | Django signed tokens, 30 min, single-use (token embeds the old hash) |
| Web | CSRF on every POST, logout/borrow/return are POST-only, open-redirect-safe `next`, clickjacking/nosniff/referrer headers, HSTS in production, `no-store` on auth pages |
| Authorization | Login required on all catalogue pages; loans queried by `user=request.user` (IDOR → 404); admin needs `is_staff` |
| Data integrity | DB constraints (stock ≤ total, one active loan per title), row locks on borrow/return, ORM everywhere (no raw SQL), template auto-escaping |
| Secrets | All config from environment; app refuses to start without `DJANGO_SECRET_KEY` unless `DEBUG=True` |

Security tests live in `accounts/tests.py` (auth, lockout, reset, CSRF, headers) and
`catalog/tests.py` (access control, IDOR, business rules).

## Going to production

```bash
DJANGO_DEBUG=False DJANGO_SECRET_KEY=… DJANGO_ALLOWED_HOSTS=library.example.org \
DJANGO_SECURE_SSL_REDIRECT=True python manage.py check --deploy      # passes cleanly
python manage.py collectstatic
gunicorn library_project.wsgi
```
Serve behind HTTPS (nginx/Caddy) that sets `X-Forwarded-Proto`, and serve `/static/` from the proxy (or add WhiteNoise).
Use real SMTP; restrict PostgreSQL network access; back up the DB.

## Known limitations / next steps

- Lockout is per account, not per IP. Add a reverse-proxy or `django-axes` IP limit for credential stuffing across many accounts.
- Registration reveals if an email is taken (a usability trade-off); the sign-in and reset paths do not.
- Password-reset requests are not rate-limited per IP/email.
- Fines are computed, not collected; there are no reservations/holds or renewal. The `Loan` model and services are the extension points.

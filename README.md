# CONATOC Net (local-ready Dash app)

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open in your browser:
- http://127.0.0.1:8050
- http://localhost:8050

Note: `0.0.0.0` is a bind address, not a URL.

## Admin bootstrap
Default (change ASAP):
- Email: admin@conatoc.net
- Password: ChangeMeNow!

You can override using environment variables:
- ADMIN_EMAIL
- ADMIN_PASSWORD
- SECRET_KEY

## Member access modes
- Guests can browse read-only pages.
- Members can post papers/data/news and chat.
- Invite-only registration (recommended): set `INVITE_CODE` and share it with your group.

Optional shared login (not recommended):
- SHARED_MEMBER_EMAIL
- SHARED_MEMBER_PASSWORD

## Images
Replace:
- assets/images/conatoc_hero.jpg
- assets/images/lab_group.jpg

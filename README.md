# AI Shorts Agent

Multi-user SaaS: users log in to a Streamlit dashboard, enter a topic and sub-topics, and a GitHub Actions
worker makes one 15-second vertical video per active user and uploads it to YouTube (and optionally Facebook Reels).

## Modes
| Mode | Output |
|---|---|
| table | 7 rows x 2 columns = 14 verified images + labels + 3 tips, in one poster |
| story | 1 verified picture + a 45-70 word story |
| puzzle | 4 pictures (odd-one-out or quiz); 10 s countdown, then the answer is revealed |
| mixed | random mode each run |

Sub-topics: users can enter any number (50+ is fine). Each run picks ONE at random.

## Image accuracy (the important part)
1. Candidates from Pixabay, Pexels (optional) and Openverse, ranked by tag match.
2. A Groq vision model must confirm the picture shows the label's subject. Otherwise: alternative query, simplified query.
3. Last resort: an AI-generated image, accepted only if the vision model confirms it (then Facebook is told the video is AI-generated).
4. If nothing passes, that row/option is dropped instead of showing a wrong picture. A text check also drops items whose label and English query disagree.

## Setup
1. **Supabase**: run `schema.sql`. Get the URL, anon key and service-role key.
2. **Encryption key**: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` -> `MASTER_ENCRYPTION_KEY`.
3. **Streamlit Cloud**: deploy `app.py`; secrets from `secrets.toml.example`.
4. **GitHub**: add secrets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `MASTER_ENCRYPTION_KEY`. The workflow is `.github/workflows/shorts-cycle.yml`.
5. **Fonts**: put `NotoSansTelugu-Regular.ttf` and `NotoSansTelugu-Bold.ttf` in `assets/fonts/` (Google Fonts). The workflow also installs system Noto fonts for other scripts.
6. **Music**: put royalty-free `.mp3` files in `assets/music/` (rotates automatically; none = silent video).
7. **YouTube token** per channel: see `scripts/get_youtube_token.py`.
8. **Facebook (optional)**: a Page access token with `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, and the Page ID.

## Local test
```
pip install -r requirements.txt && python -m playwright install chromium
# .env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, MASTER_ENCRYPTION_KEY
python main.py
```

## Limits to know
- YouTube Data API default quota is 10,000 units/day per Google Cloud project and an upload costs about 1,600 units (check Google's quota page), so roughly 6 uploads/day across ALL users. The workflow runs every 4 hours (6/day); request a quota increase before adding users.
- Videos uploaded through API projects that have not passed Google's audit may be forced to private.
- Keep the OAuth consent screen "In production", otherwise refresh tokens expire after 7 days.
- Facebook Reels API: 30 API-published posts per Page per 24 hours; video must be 9:16, 3-90 s.
- GitHub cron can be delayed by several minutes.
- Groq model names change; override with env vars `GROQ_TEXT_MODEL` / `GROQ_VISION_MODEL`.

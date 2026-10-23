# Tests (mock ServiceNow — not a real instance)

**One command (developers):** `python run_all.py` — starts the stand-ins, runs every suite below, stops them.


```bash
pip install -r ../snow-ai-service/requirements.txt playwright
export SNOW_VERIFY_TLS=false   # the mock uses a self-signed certificate
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 2 -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1"
uvicorn fake_sn2:app --port 9443 --ssl-keyfile key.pem --ssl-certfile cert.pem &   # mock ServiceNow (HTTPS)
uvicorn fake_flow:app --port 9998 &                                                # mock Flow Server
python e2e_v6.py          # 92 API checks (start from a fresh mock)
python story_count_test.py   # story counts 1 / 3 / 150, long notes with 120 requirements
python run_agent_mock.py & python ui_test.py    # 12 browser checks (Playwright/Chromium)
```
The mock imitates what matters for update sets: a session reads the user's `sys_update_set` / `apps.current_app`
preferences once when it starts, and metadata is captured into `sys_update_xml` of that update set only if the
update set's application matches the record's scope. AI calls are replaced by canned answers (`mock_ai.py`).
Last run: 92/92 API checks and 24/24 browser checks passed (`sample_user_stories.xlsx` is your sample file).

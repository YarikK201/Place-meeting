# MAX SQLite catalog prototype

Review changes before using; demo events.json is not real data.
This package assumes the existing Place-meeting branch `setup/max-bot-bootstrap`.
Keep existing trusted `root.cer` and `sub.cer` files locally; never commit keys or credentials.

## Local test

`python -m unittest discover -s tests -v`

`$env:EVENTS_DB = "$PWD\data\events.db"`

`python import_events.py events.json`

Repeat import. Rows should remain 1. Editing title with same `source_id` updates the row.

## Docker (PowerShell)

`mkdir data -ErrorAction SilentlyContinue`

`docker build -t place-meeting-bot .`

`docker run --rm -v "${PWD}/data:/data" place-meeting-bot python import_events.py events.json`

`$env:MAX_BOT_TOKEN = Read-Host "MAX token"`

`docker run -d --name place-meeting -v "${PWD}/data:/data" -e MAX_BOT_TOKEN -e MAX_BOT_RUN=1 place-meeting-bot`

`docker logs place-meeting`

`Remove-Item Env:MAX_BOT_TOKEN`

Use `/start` or `/catalog` in MAX personal chat. Click next, details, source URL.
If the database is not writable in a Windows bind mount, create a Docker named volume instead and mount it with `-v place-meeting-data:/data` for both import and bot. NEVER run duplicate polling processes with the same MAX token.

## Format

`events.json` must contain an array of event objects or an object with `events` array. Fields: `source_id` (stable unique from source), `title`, `description`, `event_date`, `location`, `source_url` (http or https).

No deletion on missing items: import updates/inserts only. `source_id` is the identity; do not derive it from list position or changing title.

Security: Keep token in environment, enable TLS and install trusted certs; do not commit data of students/minors. The project AGENTS.md warns that hackathon rules may restrict AI-generated final submissions. Review and substantially implement the work as required by organizers.

# TrumpScraper

Monitor Donald Trump's public statements for **company mentions** and tell you
whether he's talking about each company **positively or negatively** — then
deliver a **daily digest to Telegram**.

It pulls from multiple sources, analyzes each statement with Claude
(auto-detecting *any* company mentioned and scoring sentiment), accumulates the
results in SQLite, and compiles a report on a daily schedule via GitHub Actions.

```
sources ──► analysis (Claude) ──► storage (SQLite) ──► daily report ──► Telegram
```

## Why these sources?

Trump produces "spoken"/statement content across several channels. This tool
treats them uniformly:

| Source | What it captures | Notes |
|---|---|---|
| **`rss: trumps_truth`** | His Truth Social posts (highest volume) | Via [trumpstruth.org](https://trumpstruth.org), an archive that mirrors every post and works from cloud IPs. **Primary source.** |
| **`rss: white_house`** | Official transcripts of speeches, remarks, press events | whitehouse.gov remarks feed; full transcript fetched from each linked page, filtered to "President Trump" items. |
| **`truth_social`** | Direct Truth Social API | Disabled by default — blocks most cloud IPs, and the mirror above covers the same content. |
| **`audio`** | Speeches, rallies, live streams (anything yt-dlp can reach) | yt-dlp pulls audio, Whisper transcribes. Optional deps. |
| **`local`** | Any text/JSON you drop into `inbox/` | Manual path: paste posts, or transcripts you produced elsewhere. |

All sources feed the same Claude analysis → sentiment → report path.

## Quick start

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml          # edit if you like the defaults, you can skip
cp .env.example .env && $EDITOR .env         # add your keys
set -a && source .env && set +a

python -m trumpscraper init-db               # create the database
echo '{"text":"Apple is doing a GREAT job. Crooked Amazon is failing!"}' > inbox/test.json
python -m trumpscraper run                   # fetch -> analyze -> report -> send
```

`run` writes a Markdown report to `reports/YYYY-MM-DD.md` and (if configured)
sends an HTML digest to Telegram.

### Commands

| Command | Does |
|---|---|
| `run` | Full pipeline: fetch → analyze → report → deliver |
| `fetch` | Fetch + store new content only |
| `analyze` | Analyze stored, unprocessed content |
| `report` | Rebuild the report, write the file, print it (no send) |
| `send` | Rebuild + deliver to Telegram |
| `init-db` | Create the SQLite database |

Add `-v` for debug logging, `-c path/to/config.yaml` for a custom config.

## Configuration

Settings live in `config.yaml` (see `config.example.yaml`). **Secrets are read
from the environment only:**

- `ANTHROPIC_API_KEY` — from the [Anthropic Console](https://console.anthropic.com/)
- `TELEGRAM_BOT_TOKEN` — create a bot via [@BotFather](https://t.me/BotFather)
- `TELEGRAM_CHAT_ID` — your user/group/channel id (e.g. via @userinfobot, or the
  bot's `getUpdates` endpoint)

### Enabling audio transcription

```bash
pip install -r requirements-audio.txt   # yt-dlp + faster-whisper
# ensure ffmpeg is installed and on PATH
```

Then in `config.yaml`:

```yaml
sources:
  audio:
    enabled: true
    urls:
      - "https://www.youtube.com/watch?v=..."   # a rally, presser, or stream
    whisper_model: base
```

## How the analysis works

Each statement is sent to Claude (`claude-opus-4-8` by default) with a
structured-output schema. Claude returns, per statement, every company/brand it
detects, the sentiment Trump expresses toward each (`positive` / `negative` /
`neutral` / `mixed`), a −1…+1 score, a confidence, the verbatim quote, a stock
ticker when identifiable, and a one-line rationale. The instruction prompt
carries a prompt-caching breakpoint (it engages once the prompt exceeds the
model's minimum cacheable size).

See `trumpscraper/analyze.py`.

## Automated daily reports (GitHub Actions)

`.github/workflows/daily-report.yml` runs the pipeline on a cron schedule
(default 13:00 UTC) and on demand. Set these repository secrets
(**Settings → Secrets and variables → Actions**):

- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow caches `data/` (the SQLite DB) between runs so dedupe and the
lookback window persist, and commits the generated `reports/*.md`.

## Development

```bash
python -m unittest discover -s tests      # tests run without network or API keys
```

## Project layout

```
trumpscraper/
  config.py        settings (YAML + env)
  models.py        RawItem, Mention, StoredItem
  storage.py       SQLite store (dedupe + accumulate)
  analyze.py       Claude structured-output analysis
  report.py        digest building + Markdown/Telegram renderers
  telegram.py      Telegram Bot API delivery (with chunking)
  pipeline.py      orchestration (fetch/analyze/build/deliver/run)
  cli.py           command-line interface
  sources/
    truth_social.py
    audio.py
    local.py
.github/workflows/daily-report.yml
```

## Notes & limitations

- **Truth Social** has anti-bot protection; from cloud IPs the API may return
  403/empty. The source degrades gracefully — use the `local` source or run from
  a residential IP if needed.
- Sentiment is Claude's assessment of the tone Trump expresses, not financial
  advice. Tune `min_confidence` in `config.yaml` to trade recall for precision.

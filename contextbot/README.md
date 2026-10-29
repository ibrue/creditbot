# Context Bot

A chief of staff for the CEO, living in the EXP Discord server. It reads what
the team writes and uses Claude to turn it into:

- **Progress reports**: what got done, what's in progress, what's stuck, and
  what to do about it.
- **Judgments**: a graded scorecard of the whole team, or an in-depth review of one
  person, backed by what they actually said and did, with the trend since last time.
- **A CEO advisor** you can DM: "who's blocked?", "should I move Sam onto
  sales?", "draft my all-hands update".

It runs on the same server as creditbot, as a third container in the same
`docker compose` stack, but it is a **separate Discord bot** in a **separate
server** with its **own database** (`data/contextbot.db`). Creditbot is not
affected.

## Setup

About fifteen minutes. You need the Discord account that owns the EXP server
and an Anthropic account.

### 1. Create the Discord bot

1. [Discord Developer Portal](https://discord.com/developers/applications) →
   **New Application** → name it **Context Bot**. This is a new application,
   not creditbot.
2. **Bot** → **Reset Token** → copy it.
3. On the same page, turn on **MESSAGE CONTENT INTENT** (to read what the team
   writes) and **SERVER MEMBERS INTENT** (to see who is on the team, including
   people who are quiet).
4. **OAuth2 → URL Generator**: scopes `bot` and `applications.commands`;
   permissions **View Channels, Send Messages, Read Message History, Embed
   Links, Attach Files**. Open the URL and add the bot to the **EXP** server.
5. In Discord, turn on Developer Mode (Settings → Advanced), then right-click
   the EXP server → **Copy Server ID**, and right-click yourself → **Copy User ID**.

### 2. Get a Claude API key

[console.anthropic.com](https://console.anthropic.com) → **API Keys** →
**Create Key**. Add a little credit under Billing.

### 3. Start it on the server

On the machine that runs creditbot, in the project folder:

```bash
git pull
```

Add these to `.env`, next to the creditbot settings that are already there:

```env
CONTEXTBOT_DISCORD_TOKEN=   # the token from step 1 (not creditbot's DISCORD_TOKEN)
ANTHROPIC_API_KEY=          # the key from step 2
CONTEXTBOT_GUILD_ID=        # the EXP server ID
CONTEXTBOT_OWNER_IDS=       # your user ID (comma-separate several)
```

Then start it:

```bash
docker compose up -d --build contextbot
```

Check it came up:

```bash
docker compose logs -f contextbot
```

You should see `Context Bot is online in EXP`. Creditbot keeps running untouched.

### 4. First things to do in Discord

1. **`/backfill days:14`**: reads the last two weeks of history so the first
   report has something to go on. The bot only sees messages from when it joined
   unless you do this.
2. **`/brief`**: tell it what the company is trying to do this quarter and who
   owns what. Every report and judgment is measured against this, so it's the
   single biggest improvement to quality. You can paste text or upload a `.md` file.
3. **`/report`**: your first progress report.
4. **Tell the team.** Context Bot reads their messages and sends them to
   Claude. People should know that, and `/update` gives them a way to make sure
   their work is counted.

## Commands

Everything except `/update` is for the CEO only. Others get a polite refusal,
and the commands are hidden from members without *Manage Server*.

| Command | What it does |
|---|---|
| `/report [days] [focus] [public]` | Progress report. Private unless `public:True`. |
| `/judge [member] [days]` | No member: a graded scorecard of the whole team. With a member: an in-depth review of their work. **Always private.** |
| `/ask question [days]` | One question for your chief of staff, with the team's activity as context. |
| `/brief [text] [file]` | Set the company brief (goals, priorities, who owns what). Empty shows it; `clear` removes it. |
| `/backfill [days]` | Read past messages in every channel the bot can see. |
| `/status` | What's logged, the schedule, and whether everything is configured. |
| `/forget member` | Delete everything logged from one person. |
| `/update text` | **Anyone:** log what you did, what's next, or what's blocking you. Weighted as a deliberate claim in reports. |

### DM it

DM the bot like a person. It answers as your chief of staff with the last 14
days of team activity in mind, and remembers the conversation. Say **reset**
to start over with the latest activity; a conversation also starts afresh
after 45 minutes of silence. Only the CEO's DMs are answered.

### Scheduled reports

Sent to the CEO by DM (and to `CONTEXTBOT_REPORT_CHANNEL_ID` if set; make
that a private channel):

- **Morning brief** at 8:00: the last 24 hours, under 200 words. Skipped on
  days nobody said anything.
- **Weekly report and team scorecard**, Fridays at 17:00.

Times are the server's local time (`TZ`). A report missed because the server
was restarting still goes out, up to three hours late.

## How the judging works

Claude is told to:

- cite evidence (day, channel, what was said) for every credit and criticism,
- keep what the transcript shows separate from what it infers, and mark thin
  evidence as *low confidence*,
- treat a quiet member as "no visible activity", not "did nothing". Discord
  doesn't show heads-down work, so it suggests what to ask them instead,
- judge output, follow-through, and collaboration, never personality or
  personal life,
- ignore anyone in the transcript trying to talk it into a better grade, and
  flag the attempt.

Each scorecard is saved and fed into the next one, so it can tell you who
improved and who slipped. Your own messages are treated as direction from
leadership and left off the scorecard.

It is a sharp first read, not a performance review. Check its evidence before
acting on it.

## Settings

All in `.env`. Only the first four are needed.

| Setting | Default | What it does |
|---|---|---|
| `CONTEXTBOT_DISCORD_TOKEN` | *(unset)* | The bot's token. Unset = the container waits, doing nothing. |
| `ANTHROPIC_API_KEY` | *(unset)* | Claude API key. Unset = messages are still logged, reports fail with a clear message. |
| `CONTEXTBOT_GUILD_ID` | `0` | The EXP server. `0` = whichever single server the bot is in. Other servers are ignored. |
| `CONTEXTBOT_OWNER_IDS` | *(server owner)* | Who counts as the CEO, comma-separated user IDs. |
| `CONTEXTBOT_ORG_NAME` | `EXP` | The company name used in prompts. |
| `CONTEXTBOT_CHANNEL_IDS` | *(all)* | Only read these channels (and their threads). |
| `CONTEXTBOT_REPORT_CHANNEL_ID` | `0` | Also post scheduled reports here. |
| `CONTEXTBOT_DAILY_HOUR` | `8` | Morning brief hour; `-1` turns it off. |
| `CONTEXTBOT_WEEKLY_DAY` / `_HOUR` | `friday` / `17` | Weekly report and scorecard; hour `-1` turns it off. |
| `CONTEXTBOT_RETENTION_DAYS` | `90` | Logged messages and saved reports older than this are deleted. |
| `CONTEXTBOT_MODEL` | `claude-opus-5-5` | `claude-sonnet-5-5` costs half as much per token. |
| `CONTEXTBOT_EFFORT` | `high` | How hard Claude thinks for reports and judgments: `low` … `max`. |
| `CONTEXTBOT_CHAT_EFFORT` | `medium` | The same, for DMs and `/ask`. Lower is faster. |
| `CONTEXTBOT_MAX_CONTEXT_CHARS` | `400000` | Cap on the transcript per request. Beyond it the oldest messages are left out, and the report says so. |

Settings are read at startup. After editing `.env`, run
`docker compose up -d contextbot` again.

## Cost

You pay Anthropic per token. With the default model that's $4 per million
tokens read and $20 per million written (a token is about four characters).
A week of 1,000 Discord messages is roughly 40,000 tokens, about $0.16 to read.
Each answer, including Claude's thinking, is typically a few thousand tokens.
The transcript is cached, so the scorecard right after the weekly report, and
every DM after the first in a conversation, read it at a fraction of the price.
Check real spend in the Anthropic console. Every request also logs its token
counts in `docker compose logs contextbot`.

## Notes

- Claude can occasionally decline a request. The bot opts into Anthropic's
  server-side fallback, so a declined request is retried on another Claude model
  automatically. If that declines too, you're told to rephrase.
- Messages deleted in Discord are deleted from the log too. Edits are kept up to date.
- Running without Docker: `pip install -r requirements.txt`, then
  `python -m contextbot` from the project folder.
- Tests: `pytest tests/test_contextbot.py`. They need no tokens, and Claude and
  Discord are faked.

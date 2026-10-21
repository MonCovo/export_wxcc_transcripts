# Webex Contact Center – Call Transcript Exporter

Exports voice call transcripts from **Webex Contact Center (WxCC)** for a date range, together with per-call
metadata (queue, agent, wrap-up code, talk time, and **Topic Analytics** topic / contact driver).

The output is intended as source data for downstream analysis, e.g. a **Copilot Studio** agent
(Dataverse table + Azure AI Search index).

> ⚠️ **The output contains customer personal data** (conversation content, names, phone numbers, possibly card
> details). Read the [Data protection](#data-protection) section before running a full export.

---

## How it works

```
1. Search API (GraphQL)      ->  list handled telephony calls in the window (one day per query)
                                 + metadata: queue, agent, wrap-up, talk time, topicName, contactDriver
2. POST /v1/captures/query   ->  short-lived download URLs (max 60 min) for each call's captures,
                                 10 calls per request
3. GET <pre-signed URL>      ->  download voiceTranscript.json immediately and save as <taskId>.json
```

- Only the **transcript** is downloaded – the `.wav` call recording returned by the Captures API is ignored.
- Each run **appends** to `index.csv` and **skips** calls whose `<taskId>.json` already exists, so an interrupted
  export can be resumed by re-running the same command.
- HTTP `429` / `5xx` responses are retried automatically, honouring the `Retry-After` header.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | Tested with Python 3.14 on Windows |
| `requests` | `pip install requests` |
| WxCC access token | Admin / Service App token with `cjp:config_read` (and `cjp-analyzer:read` if available) |
| Org ID | Control Hub › Account › Organization ID |
| API base URL | Your WxCC region, e.g. `https://api.wxcc-eu1.cisco.com` |

**Token choice**

- **Dry runs / small tests:** a developer-portal personal access token is fine (valid 12 hours).
- **Full export:** use an **Integration or Service App** token. Developer-portal tokens have stricter rate limits
  and are not intended for production use. A dedicated Service App also avoids sharing rate-limit quota with
  other integrations (wallboards, CRM, Power BI, etc.).

---

## Setup (Windows / VS Code)

> Keep the project in a **local, non-synced folder** (e.g. `C:\dev\export_wxcc_transcripts`), **not OneDrive** –
> otherwise exported transcripts sync to the cloud and the `.venv` can break.

```powershell
cd C:\dev\export_wxcc_transcripts

# 'py' works even if the 'python' Microsoft Store alias is still enabled
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install requests
```

If activation fails with *"running scripts is disabled"*:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Set the token (current terminal session only)

```powershell
$env:WXCC_TOKEN = Read-Host "Token"    # type 'Token' as the prompt, then paste the token when asked
$env:WXCC_TOKEN.Length                 # should be > 0 – confirms it is set without printing it
```

Never hard-code the token in the script or commit it. If using a `.env` file for VS Code debugging, make sure it is
git-ignored.

---

## Usage

```
python export_wxcc_transcripts.py --org ORG_ID --api API_BASE --from YYYY-MM-DD --to YYYY-MM-DD [options]
```

| Argument | Required | Description |
|---|---|---|
| `--org` | ✔ | WxCC organisation ID |
| `--api` | ✔ | Regional API base URL, e.g. `https://api.wxcc-eu1.cisco.com` |
| `--from` | ✔ | Start date, UTC, **inclusive** |
| `--to` | ✔ | End date, UTC, **exclusive** |
| `--out` | | Output folder (default `./transcripts`) |
| `--batch` | | Calls per Captures API request (default `10`) |
| `--max-tasks` | | Only process the first *N* calls – for testing |
| `--dry-run-one` | | Print one raw Captures API response and stop (downloads nothing) |

### Recommended test sequence

**1. Dry run** – confirms auth, region, org ID and response format. Writes only the CSV header.

```powershell
python export_wxcc_transcripts.py --org <ORG_ID> --api https://api.wxcc-eu1.cisco.com `
  --from 2026-10-20 --to 2026-10-21 --out .\test_out --dry-run-one
```

Expected: `N handled telephony calls found`, followed by JSON containing `data[].taskId` and a
`transcription[]` block with `fileName: "voiceTranscript.json"`.

> The dry-run output contains **live download URLs** (valid up to 60 min) and caller numbers – don't paste it into
> chats, tickets or screenshots without redacting.

**2. Small real test** – downloads 5 transcripts.

```powershell
python export_wxcc_transcripts.py --org <ORG_ID> --api https://api.wxcc-eu1.cisco.com `
  --from 2026-10-20 --to 2026-10-21 --out .\test_out --max-tasks 5
```

**3. One week** – measure real runtime and disk usage, then extrapolate (×~26 for 6 months).

**4. Full export** – run out of hours with a dedicated Service App token.

```powershell
python export_wxcc_transcripts.py --org <ORG_ID> --api https://api.wxcc-eu1.cisco.com `
  --from 2026-04-21 --to 2026-10-21 --out .\transcripts
```

---

## Output

```
<out>/
├── index.csv            # one row per call
├── <taskId>.json        # Cisco voiceTranscript.json for that call
└── ...
```

### `index.csv`

| Column | Source | Description |
|---|---|---|
| `taskId` | Search API | Unique call (task) ID – join key to the transcript file |
| `createdTime` / `endedTime` | Search API | ISO 8601, UTC |
| `direction` | Search API | `inbound` / `outdial` |
| `entryPoint` | Search API | Final entry point name |
| `queue` | Search API | **Final** queue (transferred calls show the last queue only) |
| `agent` | Search API | Final handling agent |
| `wrapUp` | Search API | Wrap-up code |
| `talkSec` | Search API | Connected (talk) time, seconds |
| `topicName` | Topic Analytics | AI-assigned topic label (empty = not analysed; `No topic assigned` = transcript too short/unclear) |
| `contactDriver` | Topic Analytics | AI one-line summary of the reason for the call |
| `transcriptFile` | Script | `<taskId>.json`, or empty if no transcript was available |

### Transcript files

Raw `voiceTranscript.json` as produced by Cisco transcription (`provider: CISCO`, `source: recording`).
<!-- TODO: document the transcript JSON structure (speaker labels, timestamps) once confirmed from a real file. -->

---

## Runtime & rate limits

Estimates for **~22,000 calls (6 months)**:

| Item | Estimate |
|---|---|
| API requests (rate-limited) | ~2,400–2,800 (Search + Captures) |
| Transcript downloads | ~22,000 (pre-signed storage URLs) |
| Runtime | **~2.5–4 hours** (sequential downloads are the bottleneck) |
| Disk | ~0.5–2 GB |

- Pacing is ~12–15 API requests/min, well under the documented default of **300 requests/min per client ID**
  (Cisco staff have also cited ~50/min for Contact Center APIs).
- **Read-only** – no impact on call routing, agents, IVR or recording. Minimal load on reporting.
- Access tokens expire after **12 hours**; if one expires mid-run, set a new token and re-run (it resumes).

---

## Data protection

- Run from a **local, encrypted** disk or a secured company VM – **not OneDrive/SharePoint-synced folders**.
- **Never commit output** to Git – see `.gitignore` below.
- Mask PII (names, phone numbers, card numbers) **before** indexing transcripts into Copilot Studio / Azure AI
  Search.
- Agree a retention period for exported data and delete the raw export once ingested.
- Confirm the export is approved by your Data Protection lead / DPO.

### Recommended `.gitignore`

```gitignore
.venv/
.env
test_out/
transcripts/
*.json
index.csv
__pycache__/
```

Verify with `git status` that no output files are listed before committing.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Python was not found; run without arguments to install from the Microsoft Store` | Use `py` instead of `python`, or disable the `python.exe` / `python3.exe` App execution aliases (Settings › Apps › Advanced app settings) and restart VS Code |
| `Set WXCC_TOKEN in the environment.` | Token not set in this terminal – use `$env:WXCC_TOKEN = Read-Host "Token"` and paste at the prompt (not inside the quotes) |
| `unrecognized arguments: --max-tasks` | Old copy of the script – update to the latest version |
| `401` / `403` | Token expired or missing `cjp:config_read` |
| `0 handled telephony calls found` | Wrong region / org ID, or `--from`/`--to` reversed |
| GraphQL error mentioning `pagination` / `cursor` | Remove `pagination: { cursor: $after }` from the query (a single day usually fits in one page) |
| `429` | Rate limited – handled automatically via `Retry-After` |
| `transcriptFile` empty | Call wasn't recorded/transcribed, or media has been purged by the retention policy |
| `topicName` empty | Call wasn't processed by Topic Analytics (requires a recorded, English voice call) |

### Known limitations

- Calls **without** a transcript are re-listed on every re-run, so `index.csv` may contain duplicate rows for them –
  de-duplicate on `taskId` when loading.
- Telephony, agent-handled calls only (no chat/email, no IVR-only or abandoned calls).
- `queue` / `agent` reflect the **final** attribution; use CLR (`taskLegDetails`) for full transfer paths.
- Transcripts are currently tagged `en-US`; check transcription language settings for UK callers.

---

## References

- [Webex Contact Center Captures API](https://developer.webex.com/webex-contact-center/docs/api/v1/captures)
- [CSR data dictionary (Search API fields)](https://github.com/WebexSamples/webex-contact-center-api-samples/blob/main/reporting-samples/graphql-sample/DataDictionary/CSR.md)
- [Webex Contact Center rate limiting](https://developer.webex.com/docs/rate-limiting)
- [Get started with Topic Analytics](https://help.webex.com/en-us/article/v72wjab/Get-started-with-Topic-Analytics)
- [Manage retention of recordings and transcripts](https://help.webex.com/en-us/article/uimme/Manage-Retention-and-Purge-Recordings-and-Transcripts-in-Webex-Contact-Center)

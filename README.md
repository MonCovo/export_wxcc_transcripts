# What the script does

* Lists all answered calls in the date range from the reporting (Search) API, one day at a time.
* Asks POST /v1/captures/query for download links, sending your organisation ID and the call IDs 2. Each link expires after at most 60 minutes 3, so the script downloads each transcript straight away.
* Saves one <callId>.json file per call, plus an index.csv listing date, queue, agent, outcome code and talk time. If it stops part-way, running it again skips calls already saved. It also waits and retries when Webex rate-limits it.

## To run it

*last six months*

```
pip install requests
export WXCC_TOKEN="<admin or Service App token>"
python export_wxcc_transcripts.py --org <ORG_ID> --api https://api.wxcc-eu1.cisco.com \
  --from 2026-04-21 --to 2026-10-21 --out ./transcripts --dry-run-one
  ```

### Important Note  
* check one response first
* then run again without --dry-run-one
* **Token:** it needs cjp:config_read or cjp-analyzer:read. You can get a temporary token from the [developer portal](https://developer.webex.com/) (sign-in, click your avatar and you should see your bearer token and org id), but it expires after 12 hours 5. If it runs out mid-export, get a new token and run the script again; it picks up where it stopped.
* **API address:** use your region's address. I've used eu1 (UK), ammend accordingly.
* **Personal data:** the files may contain customer names, phone numbers and other details. Store them securely, mask personal data and delete the raw export when you're done.

## Impact on production

**Area & Impact**

**Call routing, agents, IVR, recording -** None. The script never changes anything or touches live call handling.

**Reporting and Analyzer -** Very low. It runs about 190–550 small queries spread over the run.

**Other API integrations -** Low, but possible if they share the same app or user quota. Use a dedicated Service App and ideally run it out of hours.

**Download links -** Each link expires after 60 minutes at most . The script downloads each file straight away, so this isn't a problem.

**Access token -** It expires after 12 hours . A 3-hour run fits. If it expires, the script stops, and re-running it picks up where it stopped.

**Before you run it**

- Run it out of hours, for example overnight or at the weekend, using a dedicated Service App token.
- Test with a small date period first! This confirms the response format, the actual speed and the file sizes.
- Store it securely. Transcripts containing data will be on your laptop. Use an encrypted disk, or ideally a secured company VM, and delete the files once they're ingested.
- Optional speed-up. Change the script to download 4–5 files at once. That should bring time down whilst still sending under 50 API requests a minute to avoid rate-limiting.
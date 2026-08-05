# OpenTable Booking Automation

Python CLI for opening OpenTable with a persistent browser profile, checking reservations, and starting booking flows. It supports two paths:

- GuestCenter/admin booking with `admin-book`
- Public customer booking with `book`

This uses Camoufox as the browser engine. Camoufox is launched through a Playwright-compatible Python API, so the automation code still uses Playwright-style calls like `page.get_by_role(...)`, but the browser profile and executable are Camoufox/Firefox. It does not bypass CAPTCHA, payment verification, SMS checks, or OpenTable account security. The safest workflow is to run `book` without `--confirm`, review the browser, then rerun with `--confirm` only when the details are correct.

## Setup

```powershell
cd "C:\TablesManagement\automated opentable"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
python -m camoufox fetch
```

## Delivery to a restaurant mini PC

Build a clean delivery zip from the development machine:

```powershell
cd "C:\TablesManagement\automated opentable"
.\package-delivery.ps1
```

Copy the generated zip from `delivery\` to the mini PC, extract it, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-target.ps1 `
  -InstallDir "C:\OpenTableAutomation" `
  -ReservationsUrl "https://guestcenter.opentable.com/restaurant/YOUR_RESTAURANT_ID/front-of-house#/reservations/live" `
  -JobsUrl "https://YOUR-N8N/webhook/opentable-next-job" `
  -StatusUrl "https://YOUR-N8N/webhook/opentable-job-status" `
  -RegisterStartup
```

The installer copies only source/config templates, installs dependencies, applies the Camoufox/Playwright patch, creates `.env` if needed, sets a 60 second browser timeout, runs the health check, and optionally registers two Windows logon tasks:

- `OpenTable Automation Daemon`
- `OpenTable Automation Poller`

After install, log in once:

```powershell
cd "C:\OpenTableAutomation"
.\.venv\Scripts\python.exe run.py login
```

Complete GuestCenter login in the Camoufox window. After GuestCenter is visibly loaded, return to PowerShell and press Enter.

Start manually when testing:

```powershell
.\start-all.ps1
```

Check health:

```powershell
.\check-installation.ps1
.\.venv\Scripts\python.exe run.py profile-check
Invoke-RestMethod http://127.0.0.1:8765/health
```

To remove automatic startup:

```powershell
.\unregister-startup-tasks.ps1
```

For a new Windows PC, copy this project folder to the machine and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-windows.ps1
```

Then start the two long-running processes in separate PowerShell windows:

```powershell
.\start-daemon.ps1
```

```powershell
.\start-poller.ps1
```

Use `.\check-installation.ps1` after the daemon starts to check the local service and n8n connectivity.

If Windows reports `DLL load failed while importing _greenlet`, run:

```powershell
.\repair-windows.ps1
```

If Camoufox binaries are missing, run `python -m camoufox fetch`.

If Camoufox crashes with `Cannot read properties of undefined (reading 'url')`, patch the local Playwright driver used by Camoufox:

```powershell
python scripts\patch_playwright_driver.py
```

## Configure

Edit `config.json`:

- `browser.engine`: use `camoufox` for Camoufox/Firefox.
- `browser.profile_dir`: local browser profile folder used to stay logged in. Use separate profile folders per browser engine.
- `opentable.login_url`: use `https://guestcenter.opentable.com/login` for the in-house/admin interface.
- `admin.dashboard_url`: GuestCenter landing page after login.
- `admin.reservations_url`: GuestCenter reservations page, ideally the exact URL shown in your browser, for example `/restaurant/{id}/front-of-house#/reservations/live`.
- `admin.selectors`: optional exact Playwright/CSS selectors for your GuestCenter UI.
- `reservation.restaurant_url`: the exact OpenTable restaurant URL.
- `reservation.party_size`: guest count.
- `reservation.date`: ISO date, for example `2026-06-20`.
- `reservation.time`: 24-hour time, for example `19:00`.
- `reservation.guest`: details used if OpenTable asks for guest fields.

The `reservation` values are defaults. For normal usage, pass date/time/party size at runtime instead of editing `config.json`.

Create a local `.env` file for login credentials:

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```env
OPENTABLE_EMAIL=you@example.com
OPENTABLE_PASSWORD=your-password
```

Do not commit `.env`, `.opentable-profile`, or `artifacts`; they are ignored.

## Commands

Log in once and keep the session:

```powershell
python run.py login
```

If `.env` contains credentials, the script tries to fill and submit the login form. If OpenTable asks for CAPTCHA, SMS, or email verification, complete that manually in the browser, then press Enter in the terminal to save the session.

To force Camoufox for a run:

```powershell
python run.py --engine camoufox --profile-dir .opentable-profile-camoufox login
```

Check existing reservations:

```powershell
python run.py check
```

Open GuestCenter reservations and print visible page text:

```powershell
python run.py admin-check
```

Keep one Camoufox browser open and run multiple commands inside it:

```powershell
python run.py session
```

Inside the `opentable>` prompt:

```text
login
admin-check
admin-book --date 2026-07-20 --time 7pm --party-size 4
admin-book --date next-month-20 --time 7pm --party-size 2 --confirm
reload
exit
```

Use `reload` after code or `config.json` changes. It reloads the automation module and config without closing the browser, so you can keep the logged-in Camoufox window open while testing.

## HTTP service / n8n queue

For n8n, run the browser once as a local HTTP service:

```powershell
python run.py service --host 127.0.0.1 --port 8765
```

The service keeps one Camoufox browser open and processes booking jobs one at a time. n8n should use an HTTP Request node:

After changing automation code, reload it without closing the browser:

```powershell
python run.py daemon-reload
```

The reload is rejected while a booking job is running. Wait for that job to finish and run the command again.

- Method: `POST`
- URL: `http://127.0.0.1:8765/admin-book`
- Body Content Type: JSON

Example body:

```json
{
  "date": "2026-08-06",
  "time": "6:15pm",
  "party_size": 7,
  "first_name": "John",
  "last_name": "Smith",
  "phone": "+15555550123",
  "confirm": true
}
```

The response is a queued job:

```json
{
  "id": "JOB_ID",
  "status": "queued"
}
```

Check status:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8765/jobs/JOB_ID"
```

Health check:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8765/health"
```

If n8n runs in Docker or on another machine, bind to your LAN interface:

```powershell
python run.py service --host 0.0.0.0 --port 8765
```

Only expose this on a trusted private network.

### Poll n8n instead of exposing this machine

If you do not want the Playwright/Camoufox machine to be reachable from n8n, run polling mode:

```powershell
python run.py poll --jobs-url "https://YOUR-N8N/webhook/opentable-next-job" --status-url "https://YOUR-N8N/webhook/opentable-job-status" --interval 5
```

In this mode, the automation machine makes outbound requests to n8n. n8n does not need to reach your machine.

The `--jobs-url` webhook can return any of these:

No job:

```json
{ "job": null }
```

One job:

```json
{
  "job": {
    "id": "row-123",
    "date": "2026-08-06",
    "time": "6:15pm",
    "party_size": 7,
    "first_name": "John",
    "last_name": "Smith",
    "phone": "+15555550123",
    "confirm": true
  }
}
```

Multiple jobs:

```json
{
  "jobs": [
    {
      "id": "row-123",
      "date": "2026-08-06",
      "time": "6:15pm",
      "party_size": 7,
      "first_name": "John",
      "last_name": "Smith",
      "phone": "+15555550123",
      "confirm": true
    }
  ]
}
```

The optional `--status-url` receives status updates:

```json
{
  "id": "row-123",
  "status": "completed",
  "timestamp": "2026-06-17T04:00:00",
  "result": {
    "status": "submitted",
    "message": "Clicked the GuestCenter final save/create button.",
    "url": "https://guestcenter.opentable.com/..."
  }
}
```

n8n can use this status webhook to update the row in its queue table.

### Split daemon and poll client

For maximum resilience, run two separate processes:

1. Browser daemon: owns the Camoufox browser and keeps it open.
2. Poll client: polls n8n and forwards queue rows to the daemon.

Terminal 1:

```powershell
python run.py service --host 127.0.0.1 --port 8765
```

Log in manually in the Camoufox window if needed, then leave this process running.

Terminal 2:

```powershell
python run.py poll-client --jobs-url "https://YOUR-N8N/webhook/opentable-next-job" --daemon-url "http://127.0.0.1:8765" --status-url "https://YOUR-N8N/webhook/opentable-job-status" --interval 5
```

If the poll client crashes or is restarted, the browser daemon and logged-in Camoufox window stay open. The browser only closes if the daemon process is stopped.

For a production n8n queue, use your real webhook URLs:

```powershell
python run.py poll-client `
  --jobs-url "https://YOUR-N8N/webhook/opentable-next-job" `
  --daemon-url "http://127.0.0.1:8765" `
  --status-url "https://YOUR-N8N/webhook/opentable-job-status" `
  --status-method PUT `
  --interval 5
```

`poll-client` sends `running` to n8n before forwarding the job to the daemon, then waits for the daemon job to become `completed` or `failed` before polling the next queue item.

Health check:

```powershell
python run.py health-check `
  --daemon-url "http://127.0.0.1:8765" `
  --jobs-url "https://YOUR-N8N/webhook/opentable-next-job" `
  --status-url "https://YOUR-N8N/webhook/opentable-job-status"
```

This checks config, profile files, Python dependencies, artifact write access, daemon `/health`, and the n8n queue endpoint.

Create a booking from GuestCenter/admin and stop before final save:

```powershell
python run.py admin-book
```

Create a booking with dynamic runtime values:

```powershell
python run.py admin-book --date 2026-07-20 --time 7pm --party-size 4
```

Supported dynamic date formats:

```powershell
python run.py admin-book --date today --time 19:00
python run.py admin-book --date tomorrow --time 7:30pm
python run.py admin-book --date +30d --time 20:00
python run.py admin-book --date next-month-20 --time 7pm
```

You can also override guest details:

```powershell
python run.py admin-book --date next-month-20 --time 7pm --party-size 2 --first-name John --last-name Smith --phone +15555550123
```

Click the final GuestCenter save/create button:

```powershell
python run.py admin-book --date next-month-20 --time 7pm --party-size 2 --confirm
```

Start the configured booking flow and stop before final confirmation:

```powershell
python run.py book
```

Click the final confirmation button:

```powershell
python run.py book --confirm
```

## Notes

OpenTable changes page markup over time. After every `check` or `book` run, the tool writes a screenshot and HTML snapshot to `artifacts/` so selectors can be adjusted if the page changes.

For GuestCenter, start with:

```powershell
python run.py login
python run.py admin-check
python run.py admin-book
```

If `admin-book` cannot find a control, run Playwright codegen, click through the GuestCenter reservation flow manually, and copy the stable selectors into `admin.selectors` in `config.json`:

```powershell
python -m playwright codegen --target python https://guestcenter.opentable.com/
```

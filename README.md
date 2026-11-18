# Python Job Agent

A standalone Python app with a local browser dashboard. Use it to find international remote roles open to applicants based in Pakistan, prepare job-specific PDFs from one master résumé, and record applications in a daily report.

## Start on Windows

Install Python 3.11 or newer. Extract the ZIP, open PowerShell in the extracted project folder, then run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) and upload a searchable PDF, DOCX, or UTF-8 TXT résumé.

On macOS or Linux, create the environment with `python3 -m venv .venv`, install with `.venv/bin/python -m pip install -r requirements.txt`, then run `.venv/bin/python app.py`.

## How the agents work

- **Search agent:** Searches the live Himalayas API by role and Pakistan eligibility. It discards expired listings and excludes known Pakistan-based employers. The default screen shows companies whose country is in the app’s sourced employer list. You can include unverified employer locations for manual review. Add employer slugs and source links to `EMPLOYERS` in `agents.py` to expand the verified list. A Himalayas key is optional and can be set as `HIMALAYAS_API_KEY`.
- **Résumé agent:** Ranks listings against your résumé, places relevant existing skills and evidence at the top of each PDF, and prepares an application note. It preserves your underlying experience, employers, dates, and credentials. It does not infer unlisted skills. A local Ollama model can optionally reorder the matching evidence. Run Ollama locally, download a model, and set `JOB_AGENT_OLLAMA_MODEL` to its installed name before launch. No paid AI key is needed. [Ollama chat API](https://docs.ollama.com/api/chat)
- **Application agent:** For an official listing that explicitly accepts email applications, import the listing and advertised inbox. The app prepares a résumé and message. Your approval on the review page sends the PDF through your configured email account. Listings discovered on Himalayas retain their original Apply link and use the employer’s application portal. Portal submissions are completed in the browser and recorded by you. The app does not automate arbitrary employer forms, sign-ins, or assessments. [Himalayas API](https://himalayas.app/api)
- **Daily report:** The dashboard and CSV use Pakistan time. “Accepted by mail server” means the outgoing SMTP server accepted the message. “Portal submission reported” is a confirmation you recorded. Check your outgoing mail if delivery is unverified or the app stopped during sending. The app never automatically resends an uncertain submission.

## Enable email application sending

Create a provider-approved SMTP app password when your email service supports one. Use your own account and the sending host/port supplied by your provider. Set the following environment variables in the same terminal before launching. Enter the password using a masked prompt:

```powershell
$env:JOB_AGENT_SMTP_HOST = 'your-provider-smtp-host'
$env:JOB_AGENT_SMTP_ADDRESS = 'your-email-address'
$secret = Read-Host 'SMTP app password' -AsSecureString
$env:JOB_AGENT_SMTP_PASSWORD = [System.Net.NetworkCredential]::new('', $secret).Password
```

The default is implicit TLS on port 465. For STARTTLS set `JOB_AGENT_SMTP_MODE=starttls`; its default port is 587. Override either with `JOB_AGENT_SMTP_PORT` if your provider specifies a different port. Settings apply only to the process launched from that terminal. Keep credentials out of the project files.

## Run discovery unattended

After uploading your résumé, run `app.py discover` with the same virtual environment Python. It searches the default roles and creates drafts without sending them. You can run this command daily with Windows Task Scheduler or cron. The email step still requires your approval in the dashboard.

The résumé, drafts, and reports are stored in your local SQLite database under `.python-job-agent` in your home directory. Use `JOB_AGENT_DATA_DIR` to choose a different directory. The app listens on localhost. Keep the dashboard private because it displays résumé content and application history.

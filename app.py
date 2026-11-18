"""Private, localhost job application workspace."""
import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from pypdf.errors import PyPdfError
from werkzeug.security import check_password_hash

from flask import Flask, Response, abort, flash, redirect, render_template, request, send_file, session, url_for

from agents import ApplicationAgent, EMAIL, MAX_FILE, MailSettings, ResumeAgent, SearchAgent, https, parse_resume

TZ = ZoneInfo('Asia/Karachi')
DEFAULT_QUERIES = 'frontend, React developer'
HOSTED = os.environ.get('JOB_AGENT_HOSTED') == '1'
HOST = os.environ.get('JOB_AGENT_HOST', '').strip().lower()
USERNAME = os.environ.get('JOB_AGENT_USERNAME', '').strip()
PASSWORD_HASH = os.environ.get('JOB_AGENT_PASSWORD_HASH', '')
SESSION_KEY = os.environ.get('JOB_AGENT_SESSION_KEY', '')
if HOSTED and (
    not HOST.endswith('.ts.net') or not HOST.replace('.', '').replace('-', '').isalnum()
    or not USERNAME or not PASSWORD_HASH.startswith('scrypt:') or len(SESSION_KEY) < 32
    or not os.environ.get('JOB_AGENT_DATA_DIR')
):
    raise RuntimeError('Set the hosted dashboard hostname, credentials, session secret, and persistent data directory.')
DATA = Path(os.environ.get('JOB_AGENT_DATA_DIR', Path.home()/'.python-job-agent'))
DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
app = Flask(__name__)
app.config.update(SECRET_KEY=SESSION_KEY if HOSTED else secrets.token_hex(32),
                  MAX_CONTENT_LENGTH=MAX_FILE+65536, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=HOSTED,
                  TRUSTED_HOSTS=[HOST, '127.0.0.1', 'localhost'] if HOSTED else ['127.0.0.1', 'localhost'])

SCHEMA = """
CREATE TABLE IF NOT EXISTS resume (
    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, payload TEXT NOT NULL, draft TEXT NOT NULL,
    status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, happened TEXT NOT NULL, company TEXT NOT NULL,
    title TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL
);
"""


@contextmanager
def database():
    db = sqlite3.connect(DATA/'workspace.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


with database() as db:
    db.executescript(SCHEMA)

search_agent, resume_agent, apply_agent = SearchAgent(), ResumeAgent(), ApplicationAgent()


def timestamp():
    return datetime.now(TZ).isoformat(timespec='seconds')


def record(db, job, status, detail=''):
    db.execute('INSERT INTO events (happened, company, title, status, detail) VALUES (?, ?, ?, ?, ?)',
               (timestamp(), job['company'], job['title'], status, detail))


def get_resume(db):
    return db.execute('SELECT name, body FROM resume WHERE id=1').fetchone()


def update_status(db, row, status, detail=''):
    job = json.loads(row['payload'])
    db.execute('UPDATE jobs SET status=?, detail=?, updated=? WHERE id=?',
               (status, detail, timestamp(), row['id']))
    record(db, job, status, detail)


def get_job(db, key):
    row = db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
    if not row:
        abort(404)
    return row


def store_draft(db, job, draft):
    key = hashlib.sha256(job['id'].encode()).hexdigest()[:32]
    cursor = db.execute('INSERT OR IGNORE INTO jobs (id,payload,draft,status,updated) VALUES (?,?,?,?,?)',
                        (key, json.dumps(job), json.dumps(draft), 'Prepared', timestamp()))
    if cursor.rowcount:
        record(db, job, 'Prepared', 'Tailored résumé and application note created.')
    return bool(cursor.rowcount)


def discover(resume, queries, verified_only, limit=5):
    jobs, warnings = search_agent.find(queries, 2, verified_only)
    with database() as db:
        seen = {json.loads(row['payload'])['id'] for row in db.execute('SELECT payload FROM jobs')}
    ranked = sorted(
        ((resume_agent.fit(resume, job)[0], job) for job in jobs if job['id'] not in seen),
        key=lambda pair: (pair[0], pair[1]['posted']), reverse=True)
    model = os.environ.get('JOB_AGENT_OLLAMA_MODEL', '').strip()
    prepared = 0
    # Network work completes before the short SQLite write transaction.
    candidates = [job for score, job in ranked if score >= 3][:limit]
    drafts = [(job, resume_agent.tailor(resume, job, model)) for job in candidates]
    with database() as db:
        for job, draft in drafts:
            prepared += store_draft(db, job, draft)
    return prepared, len(jobs), warnings


def daily_events():
    today = datetime.now(TZ).date().isoformat()
    with database() as db:
        return db.execute('SELECT * FROM events WHERE happened LIKE ? ORDER BY id DESC', (today+'%',)).fetchall()


@app.before_request
def protect():
    if HOSTED:
        auth = request.authorization
        authorized = auth and auth.type == 'basic' and secrets.compare_digest(
            (auth.username or '').encode(), USERNAME.encode()
        ) and check_password_hash(PASSWORD_HASH, auth.password or '')
        if not authorized:
            return Response('Dashboard sign-in required.', status=401,
                            headers={'WWW-Authenticate': 'Basic realm="Python Job Agent", charset="UTF-8"'})
    if not session.get('csrf'):
        session['csrf'] = secrets.token_hex(32)
    if request.method == 'POST' and not secrets.compare_digest(session['csrf'].encode(), request.form.get('csrf','').encode()):
        abort(400, 'Open the local dashboard again and retry.')


@app.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.context_processor
def csrf_context():
    return {'csrf': session.get('csrf', '')}


@app.get('/')
def index():
    with database() as db:
        resume = get_resume(db)
        rows = db.execute('SELECT * FROM jobs ORDER BY updated DESC').fetchall()
    jobs = [(row, json.loads(row['payload']), json.loads(row['draft'])) for row in rows]
    return render_template('index.html', resume=resume, jobs=jobs, queries=DEFAULT_QUERIES,
                           today=datetime.now(TZ).strftime('%d %B %Y'), events=daily_events())


@app.post('/resume')
def upload():
    document = request.files.get('resume')
    if not document or not document.filename:
        flash('Select a résumé file.')
        return redirect(url_for('index'))
    try:
        body = parse_resume(document.filename, document.read(MAX_FILE+1))
    except (OSError, ValueError, KeyError, RuntimeError, BadZipFile, ParseError, PyPdfError):
        flash('Could not read the file. Try a searchable PDF, DOCX, or UTF-8 TXT under 10 MB.')
        return redirect(url_for('index'))
    with database() as db:
        db.execute('INSERT INTO resume VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, body=excluded.body',
                   (Path(document.filename.replace('\\','/')).name, body))
    flash('Résumé loaded. New drafts will use this copy.')
    if request.form.get('discover_now') == 'yes':
        made, found, warnings = discover(body, DEFAULT_QUERIES.split(', '), True)
        flash(f'Found {found} eligible listings and prepared {made} new applications.')
        for warning in warnings:
            flash(warning)
        return redirect(url_for('index', _anchor='queue'))
    return redirect(url_for('index'))


@app.post('/discover')
def run_search():
    with database() as db:
        resume = get_resume(db)
    if not resume:
        flash('Upload your master résumé first.')
        return redirect(url_for('index'))
    queries = [q.strip()[:60] for q in request.form.get('queries','').split(',') if q.strip()][:3]
    made, found, warnings = discover(resume['body'], queries or DEFAULT_QUERIES.split(', '),
                                     not request.form.get('allow_unverified'))
    flash(f'Found {found} eligible listings and prepared {made} new applications.')
    for warning in warnings:
        flash(warning)
    return redirect(url_for('index', _anchor='queue'))


@app.post('/import-email')
def import_email():
    form = request.form
    job = {key: form.get(key,'').strip() for key in ('title','company','country','source','recipient','description')}
    if (not all(job.values()) or len(job['title'])>160 or len(job['company'])>100 or len(job['country'])>100
        or len(job['recipient'])>254 or len(job['description'])>14000 or len(job['source'])>1500
        or not EMAIL.fullmatch(job['recipient'])
        or not https(job['source']) or job['country'].lower() in {'pakistan','pk'}
        or form.get('verified_email') != 'yes'):
        flash('Provide the employer’s public listing and advertised email, and verify its location and eligibility.')
        return redirect(url_for('index'))
    with database() as db:
        resume = get_resume(db)
    if not resume:
        flash('Upload your master résumé first.')
        return redirect(url_for('index'))
    job.update(id=job['source'], proof=job['source'], link=job['source'], eligibility='Pakistan allowed, checked by you',
               expiry=0, posted=0, channel='email')
    draft = resume_agent.tailor(resume['body'], job, os.environ.get('JOB_AGENT_OLLAMA_MODEL',''))
    with database() as db:
        created = store_draft(db,job,draft)
    flash('Email application prepared.' if created else 'This listing is already in your queue.')
    return redirect(url_for('index', _anchor='queue'))


@app.get('/job/<key>')
def review(key):
    with database() as db:
        row = get_job(db,key)
    job, draft = json.loads(row['payload']), json.loads(row['draft'])
    return render_template('review.html', row=row, job=job, draft=draft, expired=expired(job))


@app.get('/job/<key>/resume.pdf')
def pdf(key):
    with database() as db:
        row = get_job(db,key)
    payload = resume_agent.pdf(json.loads(row['draft']),json.loads(row['payload']))
    return send_file(io.BytesIO(payload),mimetype='application/pdf',download_name='Tailored_Resume.pdf',max_age=0)


def expired(job):
    return bool(job['expiry'] and job['expiry'] <= datetime.now(TZ).timestamp())


@app.post('/job/<key>/send')
def send_application(key):
    with database() as db:
        row = get_job(db,key)
    job, draft = json.loads(row['payload']), json.loads(row['draft'])
    if (row['status'] != 'Prepared' or job['channel'] != 'email' or expired(job)
        or request.form.get('approve') != 'yes'):
        abort(400, 'This application is no longer eligible for email submission.')
    try:
        config = MailSettings.from_environment()
    except ValueError:
        config = None
    if not config:
        flash('Set valid SMTP details on this computer first.')
        return redirect(url_for('review',key=key))
    note = request.form.get('note','').strip()
    if not note or len(note)>5000:
        flash('Provide a message between 1 and 5,000 characters.')
        return redirect(url_for('review',key=key))
    attachment = resume_agent.pdf(draft,job)
    with database() as db:
        claim = db.execute("UPDATE jobs SET status='Sending', draft=?, updated=? WHERE id=? AND status='Prepared'",
                           (json.dumps({**draft,'note':note}),timestamp(),key))
        if not claim.rowcount:
            abort(409, 'This application was already picked up.')
        record(db,job,'Sending',job['recipient'])
    try:
        apply_agent.send(config,job,note,attachment)
    except Exception:
        # A network loss after SMTP DATA can leave delivery uncertain.
        with database() as db:
            update_status(db,row,'Delivery unverified','Check your outgoing mail before retrying.')
        flash('The mail server did not confirm delivery. Check outgoing mail. Automatic retry is disabled.')
    else:
        with database() as db:
            update_status(db,row,'Accepted by mail server',job['recipient'])
        flash('Your mail server accepted the application for delivery.')
    return redirect(url_for('review',key=key))


@app.post('/job/<key>/portal-confirmed')
def record_portal(key):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = get_job(db,key)
        job = json.loads(row['payload'])
        if row['status'] != 'Prepared' or job['channel'] != 'portal' or request.form.get('confirmed') != 'yes':
            abort(400)
        update_status(db,row,'Portal submission reported','You reported a submission confirmation from the employer.')
    flash('Portal submission recorded.')
    return redirect(url_for('review',key=key))


def sheet_cell(value):
    value = str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


@app.get('/report.csv')
def report():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Time (Asia/Karachi)','Company','Role','Status','Detail'])
    for row in daily_events():
        writer.writerow([sheet_cell(row[key]) for key in ('happened','company','title','status','detail')])
    date = datetime.now(TZ).date().isoformat()
    return Response(buffer.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':f'attachment; filename="applications-{date}.csv"'})


@app.errorhandler(413)
def too_large(_):
    return 'Choose a résumé under 10 MB.', 413


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'discover':
        with database() as db:
            stored = get_resume(db)
        if not stored:
            sys.exit('Upload a master résumé through the dashboard first.')
        count, found, warnings = discover(stored['body'],DEFAULT_QUERIES.split(', '), True)
        print(f'Found {found}; prepared {count}.')
        for warning in warnings:
            print(warning, file=sys.stderr)
    else:
        app.run(host='127.0.0.1',port=int(os.environ.get('JOB_AGENT_PORT','8765')),debug=False)

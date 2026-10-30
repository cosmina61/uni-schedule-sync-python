import os
from flask import Flask,redirect, render_template,request,session,url_for,jsonify,render_template,flash
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import google.oauth2.credentials

import pandas as pd
from orar_parser import parse_group

from sync_engine import sync_to_calendar, delete_all_app_events

import json

from werkzeug.middleware.proxy_fix import ProxyFix

SYNC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sync_cache')
os.makedirs(SYNC_DIR, exist_ok=True)

def save_sync(sync_id, data):
    with open(os.path.join(SYNC_DIR, f'{sync_id}.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

def load_sync(sync_id):
    path = os.path.join(SYNC_DIR, f'{sync_id}.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)

import uuid

app=Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-doar-local')
if not os.environ.get('RENDER'):   # Render setează automat variabila RENDER
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

SCOPES=['https://www.googleapis.com/auth/calendar']

if os.environ.get('CLIENT_SECRET_JSON') and not os.path.exists('client_secret.json'):
    with open('client_secret.json', 'w', encoding='utf-8') as f:
        f.write(os.environ['CLIENT_SECRET_JSON'])
CLIENT_SECRETS_FILE='client_secret.json'

@app.route('/')
def index():
    logged_in='credentials' in session

    if not logged_in:
        return render_template('index.html',logged_in=False)

    creds=google.oauth2.credentials.Credentials(**session['credentials'])
    service=build('calendar','v3',credentials=creds)

    calendar_list=service.calendarList().list().execute()
    calendars=[]

    for calendar in calendar_list.get('items',[]):
        if calendar.get('accessRole') in ['writer','owner']:
            calendars.append({
                'id':calendar['id'],
                'summary':calendar.get('summary','No Title')          
            })

    return render_template('index.html',logged_in=True,calendars=calendars)
   

@app.route('/login')
def login():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        autogenerate_code_verifier=False)
    flow.redirect_uri = url_for('callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent')

    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    state = session.get('state')
    if not state or state != request.args.get('state'):
        return 'Stare invalidă, încearcă din nou de la /login', 400

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        autogenerate_code_verifier=False)
    flow.redirect_uri = url_for('callback', _external=True)
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    session['credentials'] = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes,
    }
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/preview', methods=['POST'])
def preview():
    if 'schedule_file' not in request.files:
        return 'No file uploaded',400

    file=request.files['schedule_file']
    if file.filename=='':
        return 'No file selected',400

    calendar_id=request.form.get('calendar_id')
    holiday_start=request.form.get('holiday_start')
    holiday_end=request.form.get('holiday_end')
    semester_start = request.form.get('semester_start') 
    semester_end = request.form.get('semester_end')
    student_group = request.form.get('student_group')  # Get the student group from the form

    try:
        df = parse_group(file, student_group)

        if df.empty:
            return f'Grupa "{student_group}" nu a fost găsită în fișier.', 400

        parsed_classes = df.to_dict(orient='records')

        sync_id=str(uuid.uuid4())
        save_sync(sync_id,{
            'calendar_id':calendar_id,
            'holiday_start':holiday_start,
            'holiday_end':holiday_end,
            'semester_start':semester_start,
            'semester_end':semester_end,
            'student_group': student_group,  
            'classes':parsed_classes
        })
        session['sync_id']=sync_id

    except Exception as e:
        return f'Error processing file: {str(e)}',400

    return render_template('preview.html',classes=parsed_classes)

@app.route('/sync', methods=['POST'])
def sync():
    if 'credentials' not in session:
        flash('Nu ești logat. Mergi la /login ca să te conectezi.', 'warning')
        return redirect(url_for('index'))

    sync_id = session.get('sync_id')
    data = load_sync(sync_id) if sync_id else None
    if data is None:
        flash('Nu am găsit orarul. Încarcă din nou fișierul și apasă Preview.', 'warning')
        return redirect(url_for('index'))

    creds = google.oauth2.credentials.Credentials(**session['credentials'])
    service = build('calendar', 'v3', credentials=creds)

    try:
        n = sync_to_calendar(service, data)
    except Exception as e:
        flash(f'Eroare la sincronizare: {e}', 'danger')
        return redirect(url_for('index'))

    session['synced'] = True
    flash(f'Au fost create {n} evenimente recurente în Google Calendar.', 'success')
    return redirect(url_for('index'))

@app.route('/new')
def new_schedule():
    session.pop('synced',None)
    session.pop('sync_id',None)
    return redirect(url_for('index'))


@app.route('/reset',methods=['POST'])
def reset():
    if 'credentials' not in session:
        flash('Nu esti logat','warning')
        return redirect(url_for('index'))

    creds=google.oauth2.credentials.Credentials(**session['credentials'])
    service=build('calendar','v3',credentials=creds)

    total=0
    try:
        calendar_list=service.calendarList().list().execute()
        for cal in calendar_list.get('items',[]):
            if cal.get('accessRole') in ['write','owner']:
                total+=delete_all_app_events(service,cal['id'])
    except Exception as e:
        flash(f'Eroare la resetare {e}','danger')
        return redirect(url_for('index'))
    
    session.pop('sync_id',None)
    session.pop('synced',None)
    flash(f'Am sters {total} evenimente create de aplicatie.','succes')
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(port=5000, debug=True)
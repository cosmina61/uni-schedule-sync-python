import hashlib
import re
from datetime import date,datetime,time,timedelta

from googleapiclient.errors import HttpError

TZ='Europe/Bucharest'
WEEKDAYS = {'LUNI': 0, 'MARTI': 1, 'MIERCURI': 2, 'JOI': 3, 'VINERI': 4}

def to_date(v): #val din formular
    if not v: #daca val e goala
        return None
    if isinstance(v,date):
        return v
    return date.fromisoformat(str(v))

def parse_hours(hour): #text de forma x-y
    m=re.match(r'^(\d{1,2})-(\d{1,2})$',str(hour).strip())
    if not m:
        raise ValueError(f'invalid hour')
    return int(m.group(1)),int(m.group(2))  # return ora sf ora inc

def week_parity(d,semester_start):
    idx=(d-semester_start).days//7+1
    return 'impar' if idx%2==1 else 'par'

def first_occurrence(semester_start,weekday,parity):
    d=semester_start+timedelta(days=(weekday-semester_start.weekday())%7)
    if parity in ('impar','par') and week_parity(d,semester_start)!=parity:
        d+=timedelta(days=7)
    return d

def make_key(group,c):
    raw = f"{group}|{c['Week']}|{c['Day']}|{c['Hour']}|{c['Content']}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]

def build_event(c,group,semester_start,semester_end,holiday_start,holiday_end):
    weekday=WEEKDAYS[str(c['Day']).upper()] # tr nume zi in nr
    h_start,h_end=parse_hours(c['Hour'])
    week=c['Week'] #impar/par

    first=first_occurrence(semester_start,weekday,week)
    interval=1 if week=='ambele' else 2
    step=timedelta(days=7*interval)

    until=datetime.combine(semester_end,time(23,59,59)).strftime('%Y%m%dT%H%M%SZ')
    rrule = f'RRULE:FREQ=WEEKLY;INTERVAL={interval};UNTIL={until}'  

    exdates=[]
    if holiday_start and holiday_end:
        d=first
        while d<=semester_end:
            if holiday_start<=d<=holiday_end:
                exdates.append(f'EXDATE;TZID={TZ}:{d:%Y%m%d}T{h_start:02d}0000')
            d+=step

    start=datetime.combine(first,time(h_start))
    end=datetime.combine(first,time(h_end))

    return{
        'summary':str(c['Content'])[:200],
        'description': f"Grupa {group}|{c['Day']} {c['Hour']} | sapt. {week}",
        'start': {'dateTime': start.isoformat(), 'timeZone':TZ},
        'end': {'dateTime': end.isoformat(), 'timeZone':TZ},
        'recurrence': [rrule]+exdates,
        'extendedProperties': {
            'private': {
                'unischedule': '1',
                'group': group,
                'key': make_key(group,c)
            }
        }

    }

def _delete_matching(service,calendar_id,extra_filters):
    deleted=0
    page=None
    while True:
        resp=service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=['unischedule=1']+extra_filters,
            pageToken=page,
            showDeleted=False,
            maxResults=250
        ).execute()
        for ev in resp.get('items',[]):
            try:
                service.events().delete(calendarId=calendar_id,eventId=ev['id']).execute()
                deleted+=1
            except HttpError as e:
                if e.resp.status not in (404,410):
                    raise
        page=resp.get('nextPageToken')
        if not page:
            break
    return deleted

def delete_old_events(service,calendar_id,group):
    return _delete_matching(service,calendar_id,[f'group={group}'])

def delete_all_app_events(service,calendar_id):
    return _delete_matching(service,calendar_id,[])

def sync_to_calendar(service,data):
    calendar_id=data['calendar_id']
    group=data['student_group'].strip().lower()
    semester_start=to_date(data['semester_start'])
    semester_end=to_date(data['semester_end'])
    holiday_start=to_date(data['holiday_start'])
    holiday_end=to_date(data['holiday_end'])

    delete_old_events(service,calendar_id,group)

    created=0
    seen=set()
    for c in data['classes']:
        ev=build_event(c,group,semester_start,semester_end,holiday_start,holiday_end)
        key=ev['extendedProperties']['private']['key']
        if key in seen:
            continue
        seen.add(key)
        service.events().insert(calendarId=calendar_id,body=ev).execute()
        created+=1
    return created
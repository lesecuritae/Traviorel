"""Source-pinned station notices. Missing notices never establish normal service.

National raw GTFS feeds are deliberately not joined by names: their identifiers
need a matching static-feed index. Existing Transitous performs that join.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
import re
import sys
from urllib.parse import urlsplit

import httpx

from .cache import cached_call
from .config import TZ, TRANSITOUS_URL, TRANSITOUS_USER_AGENT

_PDF_SLOTS = asyncio.Semaphore(2)

_MONTHS = {'Jan':1,'Feb':2,'Mär':3,'Apr':4,'Mai':5,'Jun':6,'Jul':7,'Aug':8,'Sep':9,'Okt':10,'Nov':11,'Dez':12}
_DAYS = {'Mo':0,'Di':1,'Mi':2,'Do':3,'Fr':4,'Sa':5,'So':6}
_EFFECT = r'Fahrtausfall|Ersatzverkehr mit\s+Bus|Geändertes Fahrtziel|Geänderte Unterwegshalte|Fahrplanänderungen|Gleisänderung'


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.hidden = max(0,self.hidden-1)
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def clean_text(value):
    parser = _Plain()
    parser.feed(str(value or '')[:20000])
    return ' '.join(' '.join(parser.parts).split())[:4000]


def safe_link(value):
    if not isinstance(value,str) or len(value)>2000: return None
    parts = urlsplit(value)
    return value if parts.scheme=='https' and parts.hostname and not parts.username and not parts.password else None


def station_pdf_path(html, eva):
    class Metadata(HTMLParser):
        def __init__(self): super().__init__(); self.meta = {}
        def handle_starttag(self,tag,attrs):
            a = dict(attrs)
            if tag=='meta' and a.get('name') in {'bf:id','bf:evaNumbers'}: self.meta[a['name']] = a.get('content','')
    parsed = Metadata(); parsed.feed(html)
    expected = str(eva).lstrip('0')
    offered = {x.lstrip('0') for x in re.findall(r'\d+',parsed.meta.get('bf:evaNumbers',''))}
    station_id = parsed.meta.get('bf:id','')
    path = f'/downloads/schedule/Aenderungstafel_{station_id}.pdf'
    if expected not in offered or not station_id.isdigit() or path not in html: raise ValueError('station identity mismatch')
    return path


async def _download(url, maximum):
    # Only our own constants/source-verified paths reach this function. No redirects
    # or model/message-provided URLs; no cookies, keys or authenticated client.
    async with httpx.AsyncClient(timeout=5,follow_redirects=False,headers={'User-Agent':TRANSITOUS_USER_AGENT}) as client:
        async with client.stream('GET',url) as response:
            response.raise_for_status()
            chunks=[]; size=0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size>maximum: raise ValueError('notice source too large')
                chunks.append(chunk)
            return b''.join(chunks)


async def _extract_pdf(data):
    async with _PDF_SLOTS:
        process = await asyncio.create_subprocess_exec(sys.executable,'-m','reisevergleich.notice_pdf',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            output,_ = await asyncio.wait_for(process.communicate(data),7)
            if process.returncode or len(output)>500000: raise ValueError('PDF extraction refused')
            return json.loads(output)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def fetch_station_document(station):
    eva = station.id_for('db')
    if not eva or not str(eva).isdigit(): raise ValueError('no verified railway identifier')
    # A candidate URL is never accepted as an identity match by its spelling.
    name = station.name.casefold().translate(str.maketrans({'ä':'ae','ö':'oe','ü':'ue','ß':'ss'}))
    slug = re.sub(r'[^a-z0-9]+','-',name).strip('-')[:120]
    async def load():
        html = (await _download('https://www.bahnhof.de/'+slug+'/fahrplan',600000)).decode('utf-8')
        path = station_pdf_path(html,eva)
        data = await _download('https://www.bahnhof.de'+path,2_000_000)
        if not data.startswith(b'%PDF-'): raise ValueError('not a PDF')
        pages = await _extract_pdf(data)
        return {'pages':pages,'url':'https://www.bahnhof.de'+path,'retrieved_at':datetime.now(TZ).isoformat()}
    return await cached_call('official-notice-document-v1',{'eva':eva,'slug':slug},1800,load)


async def fetch_transitous(station,when):
    sid = station.id_for('transitous')
    if not sid: raise ValueError('no verified Transitous identifier')
    async def load():
        url = httpx.URL(TRANSITOUS_URL+'/api/v6/stoptimes',params={'stopId':sid,'time':when.isoformat(),'n':20,'withAlerts':'true'})
        return json.loads(await _download(str(url),1_000_000))
    return await cached_call('transitous-notices-v1',{'stop':sid,'when':when.isoformat(timespec='minutes')},60,load)


def _date_condition(value, day, reference):
    """Conservative German date grammar; unknown syntax is never a match."""
    value = re.sub(r'\s+',' ',value.strip()).strip(' .')
    value = re.sub(r'^(?:vom|am)\s+','',value)
    if not value: return False
    if ', nicht ' in value:
        main,exception = value.split(', nicht ',1)
        return _date_condition(main,day,reference) and not _date_condition(exception,day,reference)
    if value=='täglich': return day>=reference
    weekday = re.fullmatch(r'(Mo|Di|Mi|Do|Fr|Sa|So)(?:\s*-\s*(Mo|Di|Mi|Do|Fr|Sa|So))?(?:\s+vom\s+(.+))?',value)
    if weekday:
        start = _DAYS[weekday[1]]; end = _DAYS[weekday[2]] if weekday[2] else start
        allowed = set(range(start,end+1)) if end>=start else set(range(start,7))|set(range(end+1))
        return day.weekday() in allowed and day>=reference and (not weekday[3] or _date_condition(weekday[3],day,reference))
    # Lists can omit the month until the last item: "14., 15. Nov".
    groups = re.findall(r'([0-9., bis\-]+)\s+(Jan|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)\.?',value)
    residue = re.sub(r'([0-9., bis\-]+)\s+(Jan|Feb|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)\.?','',value)
    if not groups or residue.strip(' ,'): return False
    for days,month in groups:
        year = reference.year + (1 if reference.month>=11 and _MONTHS[month]<=2 else 0)
        if day.year!=year or day.month!=_MONTHS[month]: continue
        for part in days.split(','):
            part=part.strip(' .')
            span=re.fullmatch(r'(\d{1,2})\.?\s*(?:bis|-)\s*(\d{1,2})',part)
            if span and 1<=int(span[1])<=day.day<=int(span[2])<=31: return day>=reference
            if re.fullmatch(r'\d{1,2}',part) and int(part)==day.day: return day>=reference
    return False


def parse_change_pages(pages,when,end,mode):
    if mode=='bus': return []
    notices=[]
    for number,page in enumerate(pages,1):
        lines=[x.rstrip() for x in page.splitlines() if x.strip()]
        title=next((i for i,x in enumerate(lines) if 'Fahrplanänderungen' in x and i+1<len(lines) and 'Gültig ab:' in lines[i+1]),None)
        # The official PDFs currently use an inverted vertical transform.
        if title is None:
            reverse=list(reversed(lines))
            if any('Fahrplanänderungen' in x for x in reverse[:4]): lines=reverse
        text='\n'.join(lines)
        ref=re.search(r'Gültig ab:\s*(\d{2}\.\d{2}\.\d{4})',text)
        if not ref: continue
        reference=datetime.strptime(ref[1],'%d.%m.%Y').date()
        if not 0 <= (when.date()-reference).days <= 62: continue
        blocks=re.split(r'(?m)^\s*('+_EFFECT+r')\s*$',text)
        for i in range(1,len(blocks)-1,2):
            effect,body=blocks[i:i+2]
            body=body.split('Zeichenerklärung')[0]
            line=re.search(r'(?m)^\s*(S|e|RE|RB|IRE|IC|ICE)\s+(\d+)\s*$',body)
            if not line: continue
            prefix='S' if line[1]=='e' else line[1]
            if mode=='suburban' and prefix!='S': continue
            if mode=='local_train' and prefix not in {'S','RE','RB','IRE'}: continue
            # Only recurring, explicitly timed rows are promoted to matched notices.
            after=body[line.end():].splitlines()
            direction=next((x.strip() for x in after if x.strip() and not re.fullmatch(r'[0-9:—– .-]+',x.strip())), '')
            if not direction or direction=='Abfahrtzeiten:': continue
            for times,condition in re.findall(r'Abfahrtzeiten:\s*([0-9:\s]+)>\s*([^>]+?)(?=Abfahrtzeiten:|$)',body):
                # Unknown instructions, other effects and weekday exceptions fail closed.
                if 'fällt die Fahrt aus.' not in condition: continue
                condition=condition.split('fällt die Fahrt aus.')[0].strip()
                affected=[]; stamps=[]; day=when.date()
                while day<=end.date():
                    if _date_condition(condition,day,reference):
                        for clock in re.findall(r'\b\d{1,2}:\d{2}\b',times):
                            try:stamp=datetime.combine(day,datetime.strptime(clock,'%H:%M').time(),tzinfo=TZ)
                            except ValueError:continue
                            if when<=stamp<=end:
                                affected.append(stamp.strftime('%H:%M')); stamps.append(stamp.isoformat())
                    day+=timedelta(days=1)
                if affected:
                    notices.append({'effect':'cancelled','line':prefix+line[2],'direction':direction,'affected_departures':sorted(set(affected)),
                        'affected_departure_times':stamps,'text':f'{effect}: {prefix}{line[2]} Richtung {direction} am {when.date().isoformat()}, betroffene Abfahrten '+', '.join(sorted(set(affected)))+'.',
                        'applicability':'station_time','requested_window':{'from':when.isoformat(),'to':end.isoformat()},'page':number,'date_rule':condition})
    return notices


def _stamp(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result if result.tzinfo else None
    except ValueError:return None


def _period_matches(period,when,end):
    start=_stamp(period.get('start')) if period.get('start') else None
    stop=_stamp(period.get('end')) if period.get('end') else None
    if (period.get('start') and not start) or (period.get('end') and not stop): return False
    if not start and not stop: return False
    return (not start or start<=end) and (not stop or stop>when)


def transitous_notices(data,sid,when,end,mode):
    notices=[]
    allowed={'suburban':{'SUBURBAN'},'bus':{'BUS','COACH'},'local_train':{'SUBURBAN','REGIONAL_RAIL'}}
    for trip in (data.get('stopTimes') or [])[:100]:
        place=trip.get('place') or {}
        stamp=_stamp(place.get('scheduledDeparture') or place.get('departure'))
        if place.get('stopId')!=sid or not stamp or not when<=stamp<=end: continue
        if mode!='all' and trip.get('mode') not in allowed.get(mode,set()): continue
        for alert in (trip.get('alerts') or [])+(place.get('alerts') or []):
            periods=alert.get('impactPeriod') or []
            if periods and not any(_period_matches(p,when,end) for p in periods): continue
            header=clean_text(alert.get('headerText'));description=clean_text(alert.get('descriptionText'))
            if not header and not description: continue
            notices.append({'source':'transitous','effect':alert.get('effect') or 'information','line':trip.get('routeShortName'),
                'text':(header+' '+description).strip(),'source_url':safe_link(alert.get('url')) or 'https://transitous.org/sources/',
                'applicability':'source_attached_departure','impact_periods':periods})
    return notices


def board_notices(board):
    notices=[]
    for cancelled,key in [(False,'departures'),(True,'cancelled_departures')]:
        for row in (board.get(key) or [])[:40]:
            if cancelled or row.get("cancelled"):
                notices.append({'source':'db','effect':'cancelled','line':row.get('line'),'direction':row.get('direction'),
                    'text':f"Ausfall: {row.get('line') or 'Fahrt'} Richtung {row.get('direction') or 'unbekannt'}, Plan {row.get('planned_departure') or 'unbekannt'}.",
                    'applicability':'source_attached_departure','source_url':'https://www.bahn.de/'})
            for remark in (row.get('remarks') or [])[:20]:
                text=clean_text(remark.get('text') or remark.get('summary'))
                if text: notices.append({'source':'db','effect':remark.get('type') or 'information','line':row.get('line'),
                    'text':text,'applicability':'source_attached_departure','source_url':safe_link(remark.get('url')) or 'https://www.bahn.de/'})
    return notices


async def collect_service_notices(station,when,duration,mode,*,board=None):
    now=datetime.now(TZ).isoformat();end=(when.astimezone(timezone.utc)+timedelta(minutes=duration)).astimezone(TZ)
    notices=board_notices(board or {});sources=[{'source':'db','status':'messages_received' if notices else 'no_messages_received','coverage':'partial','retrieved_at':now}]
    if os.getenv('SERVICE_NOTICES_ENABLED','1')=='0':
        return {'service_notices':notices,'notice_scope':'origin_station_and_returned_legs','notice_sources':sources+[{'source':'supplementary','status':'disabled','coverage':'partial'}]}
    async def official():
        if mode=='bus': return [],{'source':'official_station_document','status':'not_applicable','coverage':'rail_stations_only'}
        try:
            async with asyncio.timeout(18):doc=await fetch_station_document(station)
            rows=parse_change_pages(doc['pages'],when,end,mode)
            for row in rows:row.update(source='official_station_document',source_url=doc['url'],retrieved_at=doc['retrieved_at'])
            return rows,{'source':'official_station_document','status':'messages_received' if rows else 'no_verified_matching_message',
                'coverage':'partial_recurring_cancellations','source_url':doc['url'],'retrieved_at':doc['retrieved_at'],
                'note':'Dokument kann weitere Änderungen enthalten; unbekannte Datumsregeln werden nicht als Ausfall behauptet.'}
        except Exception:return [],{'source':'official_station_document','status':'unavailable','coverage':'partial','retrieved_at':now}
    async def transit():
        try:
            async with asyncio.timeout(7):data=await fetch_transitous(station,when)
            rows=transitous_notices(data,station.id_for('transitous'),when,end,mode)
            return rows,{'source':'transitous','status':'messages_received' if rows else 'no_messages_received','coverage':'partial','retrieved_at':now}
        except Exception:return [],{'source':'transitous','status':'unavailable','coverage':'partial','retrieved_at':now}
    for rows,status in await asyncio.gather(official(),transit()):notices.extend(rows);sources.append(status)
    unique={}
    for notice in notices:
        notice.setdefault('station_name',station.name)
        notice.setdefault('station_ids',dict(station.provider_ids) or {station.provider:station.provider_id})
        notice.setdefault('retrieved_at',now)
        key=json.dumps({k:notice.get(k) for k in ('source','line','direction','text','source_url')},sort_keys=True)
        unique.setdefault(key,notice)
    return {'service_notices':list(unique.values())[:30],'notice_sources':sources,'notice_scope':'origin_station_and_returned_legs'}


def render_notice_lines(data):
    lines=[]
    for row in (data.get('service_notices') or [])[:8]:
        lines.append('Verkehrsmeldung: '+clean_text(row.get('text'))[:650]+(' Quelle: '+row['source_url'] if row.get('source_url') else ''))
    sources=data.get('notice_sources') or []
    if any(s.get('status')=='unavailable' for s in sources):lines.append('Zusätzliche Störungsinformationen teilweise nicht verfügbar; fehlende Meldungen belegen keinen störungsfreien Betrieb.')
    elif not lines:lines.append('Keine passende Verkehrsmeldung aus den geprüften Quellen erhalten; die Abdeckung ist unvollständig.')
    for source in sources:
        if source.get('source')=='official_station_document' and source.get('source_url'):
            lines.append('Amtlicher Änderungsfahrplan (weitere Hinweise möglicherweise nicht automatisch zugeordnet): '+source['source_url'])
    return lines

"""Synthetic nationwide notice fixtures; no live source or train availability claims."""
import asyncio
from datetime import datetime, timedelta

import pytest

from reisevergleich import service_notices as notices
from reisevergleich.models import StationSelection

WHEN = datetime.fromisoformat('2030-11-16T09:00:00+01:00')
STATION = StationSelection(name='Musterstadt West', provider='db', provider_id='8000001',
                           provider_ids={'db':'8000001','transitous':'fixture:west'})
PAGE = '''Fahrplanänderungen Musterstadt West
Gültig ab: 09.11.2030
Wiederkehrende Änderungen
Fahrtausfall
S 1
Richtung Ost
Abfahrtzeiten:
09:10 09:40
> 16., 17. Nov fällt die Fahrt aus.
Abfahrtzeiten:
22:10
> 18. Nov fällt die Fahrt aus.
Fahrtausfall
RE 1
Richtung Nord
Abfahrtzeiten:
09:20
> 16. Nov fällt die Fahrt aus.
Zeichenerklärung
'''


@pytest.mark.parametrize('day,count', [(16,1),(17,1),(18,0)])
def test_planned_cancellation_filters_day_mode_and_window(day,count):
    result = notices.parse_change_pages([PAGE],WHEN.replace(day=day),WHEN.replace(day=day,hour=11),'suburban')
    assert len(result)==count
    if result:
        assert result[0]['line']=='S1'
        assert result[0]['affected_departures']==['09:10','09:40']
        assert result[0]['direction']=='Richtung Ost'


def test_weekday_range_and_exception():
    page=PAGE.replace('16., 17. Nov','Mo - Fr, nicht 18. Nov')
    # These dates/weekday values are computed rather than assumed.
    for day in range(16,23):
        when=WHEN.replace(day=day)
        rows=notices.parse_change_pages([page],when,when+timedelta(hours=2),'suburban')
        assert bool(rows)==(when.weekday()<5 and day!=18)


def test_unknown_date_syntax_never_claims_applicability():
    page=PAGE.replace('16., 17. Nov','an ausgewählten Tagen')
    assert notices.parse_change_pages([page],WHEN,WHEN+timedelta(hours=2),'suburban')==[]


def test_reversed_layout_and_bus_do_not_mislabel_rail():
    reverse='\n'.join(reversed(PAGE.splitlines()))
    assert len(notices.parse_change_pages([reverse],WHEN,WHEN+timedelta(hours=2),'suburban'))==1
    assert notices.parse_change_pages([PAGE],WHEN,WHEN+timedelta(hours=2),'bus')==[]


def test_source_html_metadata_must_match_selected_eva():
    html='<meta name="bf:id" content="42"><meta name="bf:evaNumbers" content="8000001"><a href="/downloads/schedule/Aenderungstafel_42.pdf">PDF</a>'
    assert notices.station_pdf_path(html,'8000001').endswith('_42.pdf')
    with pytest.raises(ValueError):notices.station_pdf_path(html,'8009999')
    with pytest.raises(ValueError):notices.station_pdf_path(html.replace('_42.pdf','_77.pdf'),'8000001')


def test_notice_links_are_not_executable_and_html_is_plain():
    assert notices.clean_text('<b>Gleiswechsel</b><script>run()</script>')=='Gleiswechsel'
    assert notices.safe_link('javascript:alert(1)') is None


def test_transitous_future_departure_cannot_prove_notice_for_today():
    data={'stopTimes':[{'place':{'stopId':'fixture:west','scheduledDeparture':'2030-11-18T09:10:00+01:00'},
                        'mode':'SUBURBAN','routeShortName':'S1','alerts':[{'headerText':'Ausfall'}]}]}
    assert notices.transitous_notices(data,'fixture:west',WHEN,WHEN+timedelta(hours=2),'suburban')==[]


def test_transitous_alert_requires_actual_selected_stop_and_impact_period():
    item={'place':{'stopId':'fixture:west','scheduledDeparture':'2030-11-16T09:10:00+01:00'},'mode':'SUBURBAN',
          'routeShortName':'S1','alerts':[{'headerText':'Bauarbeiten','impactPeriod':[{'start':'2030-11-18T00:00:00Z'}]}]}
    assert notices.transitous_notices({'stopTimes':[item]},'fixture:west',WHEN,WHEN+timedelta(hours=2),'suburban')==[]
    item['alerts'][0].pop('impactPeriod')
    assert len(notices.transitous_notices({'stopTimes':[item]},'fixture:west',WHEN,WHEN+timedelta(hours=2),'suburban'))==1
    assert notices.transitous_notices({'stopTimes':[item]},'other',WHEN,WHEN+timedelta(hours=2),'suburban')==[]


def test_empty_board_still_receives_planned_notice(monkeypatch):
    monkeypatch.setenv('SERVICE_NOTICES_ENABLED','1')
    async def document(*args):return {'pages':[PAGE],'url':'https://www.bahnhof.de/downloads/schedule/Aenderungstafel_42.pdf','retrieved_at':WHEN.isoformat()}
    async def transit(*args):return {'stopTimes':[]}
    monkeypatch.setattr(notices,'fetch_station_document',document)
    monkeypatch.setattr(notices,'fetch_transitous',transit)
    result=asyncio.run(notices.collect_service_notices(STATION,WHEN,120,'suburban',board={'departures':[]}))
    assert result['service_notices']
    assert result['notice_sources'][0]['coverage']=='partial'
    assert any('Fahrtausfall' in x for x in notices.render_notice_lines(result))


def test_notice_source_failure_is_honest_and_does_not_destroy_board(monkeypatch):
    monkeypatch.setenv('SERVICE_NOTICES_ENABLED','1')
    async def fail(*args):raise TimeoutError()
    monkeypatch.setattr(notices,'fetch_station_document',fail);monkeypatch.setattr(notices,'fetch_transitous',fail)
    board={'departures':[{'line':'S1'}]}
    result=asyncio.run(notices.collect_service_notices(STATION,WHEN,90,'suburban',board=board))
    assert result['service_notices']==[] and board=={'departures':[{'line':'S1'}]}
    assert all(s['status']=='unavailable' for s in result['notice_sources'] if s['source']!='db')
    assert 'nicht verfügbar' in ' '.join(notices.render_notice_lines(result))


def test_source_remark_and_cancelled_details_survive(monkeypatch):
    monkeypatch.setenv('SERVICE_NOTICES_ENABLED','0')
    board={'departures':[], 'cancelled_departures':[{'line':'S1','direction':'Ost','planned_departure':WHEN.isoformat(),
        'remarks':[{'type':'warning','text':'<b>Brückenarbeiten</b>'}]}]}
    result=asyncio.run(notices.collect_service_notices(STATION,WHEN,90,'suburban',board=board))
    assert any(x['effect']=='cancelled' for x in result['service_notices'])
    assert any('Brückenarbeiten' in x['text'] for x in result['service_notices'])


def test_normal_mcp_empty_board_contains_planned_notice(monkeypatch):
    from reisevergleich import mcp_server as mcp
    monkeypatch.setenv('SERVICE_NOTICES_ENABLED','1')
    monkeypatch.setattr(mcp,'_server_now',lambda:WHEN)
    async def board(payload):return {'status':'empty','departures':[]}
    async def collect(station,when,duration,mode,**kwargs):
        assert kwargs['board']['departures']==[]
        return {'service_notices':[{'text':'Fixture Fahrtausfall S1','source_url':'https://www.bahnhof.de/'}],
                'notice_sources':[{'source':'fixture','status':'messages_received'}]}
    monkeypatch.setattr(mcp,'db_departures',board)
    monkeypatch.setattr(mcp,'collect_service_notices',collect,raising=False)
    result=asyncio.run(mcp.next_departures(STATION.name,transport_mode='suburban',origin_station=STATION))
    assert result.structured_content['service_notices'][0]['text']=='Fixture Fahrtausfall S1'
    assert 'Fixture Fahrtausfall' in result.content[0].text


def test_normal_ground_search_without_connections_still_checks_messages(monkeypatch):
    from reisevergleich import mcp_server as mcp
    monkeypatch.setenv('SERVICE_NOTICES_ENABLED','1')
    monkeypatch.setattr(mcp,'_server_now',lambda:WHEN)
    async def search(request):return {'connections':[]}
    async def resolve(*args):return STATION
    async def collect(*args,**kwargs):return {'service_notices':[{'text':'Fixture Bauarbeiten'}],'notice_sources':[]}
    monkeypatch.setattr(mcp,'search',search)
    monkeypatch.setattr(mcp,'_departure_station',resolve)
    monkeypatch.setattr(mcp,'collect_service_notices',collect,raising=False)
    result=asyncio.run(mcp.search_ground(STATION.name,'Musterstadt Ost',date=WHEN.date().isoformat(),departure_after='09:00'))
    assert result.structured_content['service_notices']
    assert 'Fixture Bauarbeiten' in result.content[0].text


def test_stale_document_cannot_assert_a_current_cancellation():
    stale=PAGE.replace('09.11.2030','09.11.2029')
    assert notices.parse_change_pages([stale],WHEN,WHEN+timedelta(hours=2),'suburban')==[]


def test_unknown_impact_timestamp_is_not_treated_as_unbounded():
    item={'place':{'stopId':'fixture:west','scheduledDeparture':WHEN.isoformat()},'mode':'SUBURBAN',
          'alerts':[{'headerText':'Bauarbeiten','impactPeriod':[{'start':'malformed'}]}]}
    assert notices.transitous_notices({'stopTimes':[item]},'fixture:west',WHEN,WHEN+timedelta(hours=2),'suburban')==[]


def test_http_redirect_and_oversize_response_fail_closed(monkeypatch):
    import httpx
    original=httpx.AsyncClient
    def response(request):return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    monkeypatch.setattr(notices.httpx,'AsyncClient',lambda **kw: original(**kw,transport=httpx.MockTransport(response)))
    with pytest.raises(httpx.HTTPStatusError):asyncio.run(notices._download('https://www.bahnhof.de/fixture',100))
    def response(request):return httpx.Response(200,content=b'x'*101)
    with pytest.raises(ValueError):asyncio.run(notices._download('https://www.bahnhof.de/fixture',100))


def test_corrupt_pdf_is_refused_by_bounded_child():
    with pytest.raises(ValueError):asyncio.run(notices._extract_pdf(b'%PDF-1.1\ninvalid'))


def test_cross_midnight_message_keeps_calendar_date():
    when=WHEN.replace(hour=23,minute=30)
    page=PAGE.replace('09:10 09:40','00:10')
    rows=notices.parse_change_pages([page],when,when+timedelta(hours=2),'suburban')
    assert rows[0]['affected_departure_times']==['2030-11-17T00:10:00+01:00']

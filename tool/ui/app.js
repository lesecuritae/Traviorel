const $ = (id) => document.getElementById(id);
const state = {
  travelMode: 'ground',
  journeyType: 'round_trip',
  durationNights: 7,
  dticket: (localStorage.getItem('traviorel-dticket') ?? localStorage.getItem('reisevergleich-dticket')) === 'true',
  hotelType: 'hotel',
  stations: {origin: null, destination: null},
};

function localIso(value) {
  return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;
}

let calendarMonth = new Date();
let calendarTarget = 'departureDate';
let activeSearch = null;
let coverageQueue = [];
let coverageGeneration = 0;
let activeCalendarSearch = null;
const progressLabels = {db:'Bahnverbindungen', transitous:'Weitere Verbindungen', gtfs:'Fernbus- und Zugfahrplan', flixbus:'Busverbindungen', flixtrain:'Zusätzliche Zugverbindungen', merge:'Ergebnisaufbereitung'};
const statusLabels = {waiting:'wartet', loading:'wird geladen', processing:'wird verarbeitet', completed:'abgeschlossen', empty:'keine Ergebnisse', failed:'fehlgeschlagen', cancelled:'abgebrochen'};

function renderProgress(data={}) {
  const steps = data.steps || {};
  $('progressSteps').innerHTML = Object.entries(progressLabels).map(([key,label]) => {
    const step = steps[key] || {status:'waiting'};
    return `<div class="progress-step ${esc(step.status)}"><span class="progress-dot" aria-hidden="true"></span><strong>${esc(label)}</strong><span>${esc(statusLabels[step.status] || step.status)}</span>${step.detail ? `<small>${esc(step.detail)}</small>` : ''}</div>`;
  }).join('');
  $('progressSummary').textContent = data.status === 'completed' ? 'Abgeschlossen' : data.status === 'failed' ? 'Mit Fehlern beendet' : 'Suche läuft';
}

async function pollProgress(search) {
  while (activeSearch === search && !search.done) {
    try {
      const response = await fetch(`/api/search-status/${encodeURIComponent(search.id)}`, {signal: search.controller.signal});
      if (response.ok && activeSearch === search) renderProgress(await response.json());
    } catch (error) { if (error.name === 'AbortError') return; }
    await new Promise(resolve => setTimeout(resolve, 350));
  }
}

function renderCalendar() {
  const field = $(calendarTarget);
  const selected = field.value;
  const minimum = field.min;
  $('calendarMonth').textContent = new Intl.DateTimeFormat('de-DE', {month:'long', year:'numeric'}).format(calendarMonth);
  const first = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  const days = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 0).getDate();
  const buttons = [];
  for (let empty = 0; empty < offset; empty++) buttons.push('<span></span>');
  for (let day = 1; day <= days; day++) {
    const value = localIso(new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), day));
    buttons.push(`<button type="button" data-calendar-date="${value}" class="${value===selected?'selected':''}" ${value<minimum?'disabled':''} aria-pressed="${value===selected}">${day}</button>`);
  }
  $('calendarDays').innerHTML = buttons.join('');
  $('calendarDays').querySelectorAll('[data-calendar-date]').forEach(button => button.addEventListener('click', () => {
    field.value = button.dataset.calendarDate;
    syncJourneyDates(); renderCalendar(); closeCalendar();
  }));
  const current = new Date();
  $('calendarPrevious').disabled = calendarMonth.getFullYear() === current.getFullYear() && calendarMonth.getMonth() === current.getMonth();
}

function closeCalendar() {
  $('calendarPanel').classList.add('hidden');
  ['departureCalendarToggle', 'returnCalendarToggle'].forEach(id => $(id).setAttribute('aria-expanded', 'false'));
}

function openCalendar(target) {
  if ($(target).disabled) return;
  calendarTarget = target;
  const selected = $(target).value || $(target).min || $('departureDate').value;
  const [year, month] = selected.split('-').map(Number);
  calendarMonth = new Date(year, month - 1, 1);
  $('calendarPanel').classList.remove('hidden');
  ['departureCalendarToggle', 'returnCalendarToggle'].forEach(id => $(id).setAttribute('aria-expanded', String(id.startsWith(target === 'departureDate' ? 'departure' : 'return'))));
  renderCalendar();
}

function setTodayDefaults() {
  const now = new Date();
  const departure = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 35);
  const iso = localIso(departure);
  $('departureDate').value = iso;
  $('departureDate').min = localIso(now);
  const ret = new Date(departure.getFullYear(), departure.getMonth(), departure.getDate() + 7);
  $('returnDate').value = localIso(ret);
  $('returnDate').min = iso;
  calendarMonth = new Date(departure.getFullYear(), departure.getMonth(), 1);
  renderCalendar();
}

function dateDifferenceInDays(start, end) {
  if (!start || !end) return null;
  const [sy, sm, sd] = start.split('-').map(Number);
  const [ey, em, ed] = end.split('-').map(Number);
  return Math.round((Date.UTC(ey, em - 1, ed) - Date.UTC(sy, sm - 1, sd)) / 86400000);
}

function syncJourneyDates() {
  const departure = $('departureDate').value;
  const returnDate = $('returnDate').value;
  $('returnDate').min = departure;
  const nights = dateDifferenceInDays(departure, returnDate);
  state.journeyType = returnDate ? 'round_trip' : 'one_way';
  if (nights !== null && nights >= 0) state.durationNights = nights;
  $('oneWayButton').classList.toggle('active', state.journeyType === 'one_way');
  $('oneWayButton').setAttribute('aria-pressed', String(state.journeyType === 'one_way'));
  $('oneWayButton').textContent = state.journeyType === 'one_way' ? 'Hin- & Rückfahrt' : 'Nur Hinfahrt';
  $('returnDate').disabled = state.journeyType === 'one_way';
  $('returnCalendarToggle').disabled = state.journeyType === 'one_way';
  $('returnDisabledHint').classList.toggle('hidden', state.journeyType !== 'one_way');
  $('returnDate').closest('.return-field').classList.toggle('disabled', state.journeyType === 'one_way');
  if (state.journeyType === 'one_way' && calendarTarget === 'returnDate') closeCalendar();
  $('returnDate').setCustomValidity(nights !== null && nights < 0 ? 'Die Rückreise darf nicht vor der Abreise liegen.' : '');
  syncTravelMode();
}

function syncTravelMode() {
  const isGround = state.travelMode === 'ground';
  document.querySelectorAll('.ground-only').forEach(element => element.classList.toggle('hidden', !isGround));
  document.getElementById("lodgingPanel").classList.toggle("hidden", isGround || state.journeyType === 'one_way');
  document.getElementById('ticketPanel').classList.toggle('hidden', state.travelMode === 'flight_stay');
  document.querySelectorAll('.flight-option').forEach(option => {
    option.classList.toggle('hidden', isGround);
  });
  document.querySelectorAll('.feeder-option').forEach(option => {
    option.classList.toggle("hidden", state.travelMode === 'flight_stay' || (isGround && option.classList.contains('flight-option')));
  });
  document.querySelectorAll('[data-dticket]').forEach(button => { button.disabled = state.travelMode === 'flight_stay'; });
  document.getElementById('destinationTransfer').disabled = isGround;
  syncDticket();
}

function selectedCalendarDays() {
  const preset = $('priceCalendarPreset').value;
  return preset === 'custom' ? Number($('priceCalendarCustom').value) : Number(preset);
}

function setDepartureDate(value) {
  const nights = dateDifferenceInDays($('departureDate').value, $('returnDate').value);
  $('departureDate').value = value;
  if (nights !== null && nights > 0) {
    const nextReturn = new Date(`${value}T12:00:00`);
    nextReturn.setDate(nextReturn.getDate() + nights);
    $('returnDate').value = localIso(nextReturn);
  }
  syncJourneyDates();
  renderCalendar();
}

function renderPriceCalendar(data) {
  const rows = (data.days || []).map(day => {
    const price = day.price_available ? money(day.price, day.currency || 'EUR') : 'Preis offen';
    const stateLabel = day.status === 'failed' ? 'Fehlgeschlagen' : day.connection_count ? `${day.connection_count} Verbindungen` : 'Keine Verbindung';
    return `<button type="button" class="price-calendar-day ${day.cheapest ? 'cheapest' : ''}" data-price-date="${esc(day.date)}"><span>${dateText(day.date)}</span><strong>${esc(price)}</strong><small>${esc(stateLabel)}</small>${day.cheapest ? '<b>Günstigster Tag</b>' : ''}</button>`;
  }).join('');
  $('priceCalendarResults').innerHTML = `<div class="price-calendar-heading"><div><span class="eyebrow">Flexible Preissuche</span><h2>${esc(data.origin)} → ${esc(data.destination)}</h2></div><span>${esc(data.calendar_days)} Tage</span></div><div class="price-calendar-days">${rows}</div><p class="meta">Aktuelle Preise aus den bestehenden DB-, Transitous- und Flix-Adaptern. „Preis offen“ bedeutet, dass nur Fahrplandaten vorliegen.</p>`;
  $('priceCalendarResults').classList.remove('hidden');
  $('priceCalendarResults').querySelectorAll('[data-price-date]').forEach(button => button.addEventListener('click', () => {
    setDepartureDate(button.dataset.priceDate);
    $('searchForm').requestSubmit();
  }));
}

async function loadPriceCalendar() {
  if (state.travelMode !== 'ground' || !state.stations.origin || !state.stations.destination) {
    showMessage('Bitte Start und Ziel aus der Stationsliste auswählen.', 'error');
    return;
  }
  const days = selectedCalendarDays();
  if (!Number.isInteger(days) || days < 1 || days > 14) {
    showMessage('Der Preisvergleich ist auf 1 bis 14 Tage begrenzt.', 'error');
    return;
  }
  if (activeCalendarSearch) activeCalendarSearch.abort();
  const controller = new AbortController();
  activeCalendarSearch = controller;
  const button = $('priceCalendarButton');
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>Preise werden geladen';
  hideMessage();
  try {
    const response = await fetch('/api/price-calendar', {
      method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
      body:JSON.stringify({...getPayload(), calendar_days:days}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : `HTTP ${response.status}`);
    if (activeCalendarSearch === controller) renderPriceCalendar(data);
  } catch (error) {
    if (error.name !== 'AbortError') showMessage(`Flexible Preissuche fehlgeschlagen: ${error.message}`, 'error');
  } finally {
    if (activeCalendarSearch === controller) {
      activeCalendarSearch = null;
      button.disabled = false;
      button.textContent = 'Preise vergleichen';
    }
  }
}

function syncDticket() {
  document.querySelectorAll('[data-dticket]').forEach(btn => {
    btn.classList.toggle('active', String(state.dticket) === btn.dataset.dticket);
    document.getElementById('dticketOnlyRow').classList.toggle('hidden', !state.dticket || state.travelMode !== 'ground');
    if (!state.dticket) document.getElementById('dticketOnly').checked = false;
  });
}

function syncHotelType() {
  document.querySelectorAll('[data-hotel-type]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.hotelType === state.hotelType);
    btn.setAttribute('aria-checked', String(btn.dataset.hotelType === state.hotelType));
  });
  const hotelUsesStars = state.hotelType === 'hotel' || state.hotelType === 'resort';
  document.getElementById('starsField').classList.toggle('hidden', !hotelUsesStars);
  document.getElementById('lodgingHelp').textContent = state.hotelType === 'hotel'
    ? 'Hotel ist voreingestellt und beginnt bei 3 Sternen. Hostels erscheinen nur im eigenen Hostel-Reiter.'
    : state.hotelType === 'hostel' ? 'Hostels werden ausdrücklich gesucht; eine Hotel-Sterneklasse wird nicht vorausgesetzt.'
    : state.hotelType === 'apartment' ? 'Apartments werden separat gesucht; Hotelsterne werden nicht angewendet.'
    : state.hotelType === 'resort' ? 'Resorts werden mit der gewählten Mindestklassifizierung gesucht.'
    : 'Die Reise wird ohne Unterkunft verglichen.';
}

function showMessage(text, type='') {
  const box = $('message');
  box.textContent = text;
  box.className = `message ${type}`.trim();
}

function hideMessage() { $('message').className = 'message hidden'; }
function money(v, c='EUR') {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return 'Preis offen';
  return new Intl.NumberFormat('de-DE', {style:'currency', currency:c || 'EUR'}).format(Number(v));
}
function hm(value) {
  if (!value) return '–';
  const text = String(value);
  const parsed = new Date(text);
  if (text.includes('T') && !Number.isNaN(parsed.getTime())) {
    return new Intl.DateTimeFormat('de-DE', {hour:'2-digit', minute:'2-digit', hour12:false, timeZone: 'Europe/Berlin'}).format(parsed);
  }
  return text.slice(0, 5);
}
function dateText(value) {
  if (!value) return '–';
  const d = new Date(`${value}T12:00:00`);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat('de-DE', {day:'2-digit', month:'2-digit', year:'numeric'}).format(d);
}
function durationText(mins) {
  if (!mins) return '';
  const h = Math.floor(mins / 60), m = mins % 60;
  return h ? `${h} h ${m ? `${m} min` : ''}`.trim() : `${m} min`;
}
function esc(s='') {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
}

function getPayload() {
  const originAirports = $('originAirports').value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean);
  const hotelUsesStars = state.hotelType === 'hotel' || state.hotelType === 'resort';
  return {
    journey_type: state.journeyType,
    travel_mode: state.travelMode === 'ground' ? 'ground' : 'flight',
    origin: $('origin').value.trim(),
    destination: $('destination').value.trim(),
    origin_station: state.travelMode === 'ground' ? state.stations.origin : null,
    destination_station: state.travelMode === 'ground' ? state.stations.destination : null,
    departure_date: $('departureDate').value,
    departure_after: $('departureAfter').value,
    return_mode: 'date',
    duration_value: state.journeyType === 'one_way' ? 0 : state.durationNights,
    duration_unit: 'nights',
    return_date: state.journeyType === 'round_trip' ? $('returnDate').value : null,
    deutschlandticket: state.travelMode !== 'flight_stay' && state.dticket,
    include_feeder: state.travelMode === 'flight',
    deutschlandticket_only: state.travelMode === 'ground' && state.dticket && document.getElementById('dticketOnly').checked,
    include_train: state.travelMode !== 'flight_stay' && $('includeTrain').checked,
    include_bus: state.travelMode !== 'flight_stay' && $('includeBus').checked,
    include_flixtrain: state.travelMode !== 'flight_stay' && $('includeTrain').checked,
    include_flixbus: state.travelMode !== 'flight_stay' && $('includeBus').checked,
    flix_origin_stop_id: null,
    flix_destination_stop_id: null,
    split_ticket_check: state.travelMode !== 'flight_stay' && $('includeTrain').checked && $('splitTicket').checked,
    include_destination_transfer: state.travelMode !== 'ground' && $('destinationTransfer').checked,
    origin_airports: originAirports,
    destination_airport: $('destinationAirport').value.trim().toUpperCase() || null,
    include_hotel: state.journeyType === 'round_trip' && state.travelMode !== 'ground' && state.hotelType !== 'none',
    hotel_property_type: state.hotelType,
    hotel_min_stars: hotelUsesStars ? Number(document.getElementById('hotelStars').value) : 0,
    airport_buffer_minutes: Number($('airportBuffer').value),
    stops: $('stops').value,
    max_results: 24,
    refresh_cache: $('refreshCache').checked,
  };
}

const stationTimers = {};
const stationRequestVersions = {origin:0, destination:0};
function renderStationSuggestions(field, stations) {
  const panel = $(`${field}Suggestions`);
  panel.innerHTML = (stations || []).map((station, index) => {
    const place = [station.region, station.country].filter(Boolean).join(', ');
    return `<button type="button" role="option" data-station-index="${index}"><strong>${esc(station.name)}</strong>${place ? `<span>${esc(place)}</span>` : ''}</button>`;
  }).join('');
  panel.classList.toggle('hidden', !stations?.length);
  panel.querySelectorAll('button').forEach(button => button.addEventListener('click', () => {
    selectStation(field, stations[Number(button.dataset.stationIndex)]);
  }));
}

function selectStation(field, station) {
    if (station) {
    state.stations[field] = {name:station.name, provider:station.provider, provider_id:station.provider_id, provider_ids:station.provider_ids || {[station.provider]:station.provider_id}, provider_alias_ids:station.provider_alias_ids || {}, latitude:station.latitude, longitude:station.longitude};
    $(field).value = station.name;
    $(field).dataset.selected = 'true';
    $(`${field}Suggestions`).classList.add('hidden');
  }
}

async function findStations(field) {
  const query = $(field).value.trim();
  if (query.length < 2) return renderStationSuggestions(field, []);
  const version = ++stationRequestVersions[field];
  try {
    const response = await fetch(`/api/stations?q=${encodeURIComponent(query)}&limit=12`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (version !== stationRequestVersions[field] || $(field).value.trim() !== query) return;
    if (data.auto_selection && !data.requires_selection) selectStation(field, data.auto_selection);
    else renderStationSuggestions(field, data.stations);
  } catch (_) { renderStationSuggestions(field, []); }
}

function bindStationInput(field) {
  $(field).addEventListener('input', () => {
    stationRequestVersions[field] += 1;
    state.stations[field] = null;
    delete $(field).dataset.selected;
    clearTimeout(stationTimers[field]);
    stationTimers[field] = setTimeout(() => findStations(field), 250);
  });
  $(field).addEventListener('focus', () => { if ($(field).value.trim().length >= 2) findStations(field); });
}

function actionLinks(offerUrl, manualUrl) {
  if (offerUrl) return `<p class="action-links"><a class="link action-link" href="${esc(offerUrl)}" target="_blank" rel="noreferrer">Angebot prüfen/buchen</a></p>`;
  if (manualUrl) return `<p class="action-links"><a class="link action-link" href="${esc(manualUrl)}" target="_blank" rel="noreferrer">Weitere Angebote öffnen</a></p>`;
  return '';
}

function feederVariantHtml(v) {
  if (!v) return '<p class="muted">Keine passende Variante gefunden.</p>';
  const badges = [];
  if (v.requires_deutschlandticket) badges.push('<span class="badge ticket">Deutschlandticket</span>');
  (v.transport_modes || []).forEach(m => badges.push(`<span class="badge">${esc(m)}</span>`));
  const segs = (v.segments || []).map(seg => {
    const label = seg.line || seg.type || seg.provider || 'Verbindung';
    return `<div class="timeline-row"><div class="time">${hm(seg.departure)} → ${hm(seg.arrival)}</div><div><strong>${esc(label)}</strong><div class="meta">${esc(seg.origin || '')} → ${esc(seg.destination || '')}</div></div><div>${seg.price !== undefined ? money(seg.price, seg.currency) : ''}</div></div>`;
  }).join('');
  return `<div>${badges.join('')}</div><div class="timeline">${segs || `<div class="timeline-row"><div class="time">${hm(v.departure)} → ${hm(v.arrival)}</div><div><strong>${esc(v.label || v.type || 'Verbindung')}</strong><div class="meta">${durationText(v.duration_minutes)}</div></div><div>${money(v.additional_ticket_cost, v.currency)}</div></div>`}</div>${actionLinks(v.offer_url, v.manual_url)}`;
}

function feederCard(title, component) {
  if (!component) return '';
  if (component.manual_required) return `<article class="result-card"><div class="card-head"><div><span class="eyebrow">Zubringer</span><h3>${esc(title)}</h3></div><span class="price-pill">manuell</span></div><p class="muted">Keine automatisch passende Verbindung gefunden.</p></article>`;
  const views = component.views || {};
  const names = [
    ['deutschlandticket','D-Ticket'],
    ['cheapest', views.deutschlandticket ? 'Günstigste Alternative' : 'Günstigste'],
    ['fastest','Schnellste'],
  ].filter(([key]) => views[key]);
  const initial = state.dticket && views.deutschlandticket ? 'deutschlandticket' : (views.cheapest ? 'cheapest' : names[0]?.[0]);
  const tabs = names.map(([key,label]) => `<button type="button" data-view="${key}" class="${key===initial?'active':''}">${label}</button>`).join('');
  const selected = views[initial] || component.selected;
  const price = selected?.price_known ? money(selected.additional_ticket_cost, selected.currency) : 'Preis offen';
  return `<article class="result-card feeder-card" data-feeder='${esc(JSON.stringify(views))}'><div class="card-head"><div><span class="eyebrow">Zubringer</span><h3>${esc(title)}</h3></div><span class="price-pill">${price}</span></div><div class="variant-tabs">${tabs}</div><div class="variant-content">${feederVariantHtml(selected)}</div></article>`;
}

function flightCard(f) {
  if (!f) return '';
  const p = f.price ? money(f.price.value, f.price.currency) : 'Preis offen';
  const o = f.outbound || {}, r = f.return || {};
  return `<article class="result-card"><div class="card-head"><div><span class="eyebrow">Flug</span><h3>${esc(o.from || '')} → ${esc(o.to || '')}</h3></div><span class="price-pill">${p}</span></div><div class="timeline"><div class="timeline-row"><div class="time">${hm(o.departure)} → ${hm(o.arrival)}</div><div><strong>Hinflug</strong><div class="meta">${esc(o.from||'')} → ${esc(o.to||'')} · ${o.stops ?? 0} Stopp(s)</div></div><div></div></div>${r.departure ? `<div class="timeline-row"><div class="time">${hm(r.departure)} → ${hm(r.arrival)}</div><div><strong>Rückflug</strong><div class="meta">${esc(r.from||'')} → ${esc(r.to||'')} · ${r.stops ?? 0} Stopp(s)</div></div><div></div></div>`:''}</div>${actionLinks(f.offer_url, f.manual_url)}</article>`;
}

function transferCard(title, t) {
  if (!t) return '';
  const options = (t.options || []).map(o => `<div class="timeline-row"><div class="time">${hm(o.departure)} → ${hm(o.arrival)}</div><div><strong>${esc(o.type || o.provider || 'Transfer')}</strong><div class="meta">${esc(o.departure_station || '')} → ${esc(o.arrival_station || '')} · ${durationText(o.duration_minutes)}</div></div><div>${o.price !== undefined ? money(o.price,o.currency) : ''}</div></div>`).join('');
  return `<article class="result-card"><div class="card-head"><div><span class="eyebrow">Transfer</span><h3>${esc(title)}</h3></div><span class="price-pill">${t.manual_required?'manuell':'Optionen'}</span></div>${options ? `<div class="timeline">${options}</div>` : '<p class="muted">Keine verifizierte Transferoption gefunden.</p>'}</article>`;
}

function hotelCard(h) {
  if (!h) return '';
  const opts = (h.options || []).slice(0,3);
  const html = opts.map(x => {
    const price = x.price || {};
    const basis = price.basis === 'verified_total_stay' ? 'Gesamtpreis verifiziert' : 'Preis vor Buchung prüfen';
    const classification = x.stars ? `${x.stars} Sterne` : (x.provider_min_stars ? `mindestens ${x.provider_min_stars} Sterne laut Providerfilter` : '');
    const source = x.provider ? ` · Quelle: ${String(x.provider)}` : '';
    return `<div class="hotel"><strong>${esc(x.name || 'Unterkunft')}</strong><div class="muted">${esc(classification)}${x.rating ? ` · ${x.rating}/10` : ''}${esc(source)}</div><div class="hotel-price">${price.value !== undefined ? money(price.value,price.currency) : 'Preis offen'}</div><div class="meta">${esc(basis)}</div>${actionLinks(x.offer_url, null)}</div>`;
  }).join('');
  return `<article class="result-card"><div class="card-head"><div><span class="eyebrow">Unterkunft</span><h3>${dateText(h.checkin)} – ${dateText(h.checkout)}</h3></div></div><div class="hotel-list">${html || '<p class="muted">Keine passende Unterkunft gefunden.</p>'}</div>${actionLinks(null, h.manual_url)}</article>`;
}

function groundAlternativesHtml(title, component) {
  const mixed = (component?.mixed_ticket_options || []).slice(0, 4);
  const split = component?.split_ticket?.status === "success" ? (component.split_ticket.split_options || []).slice(0, 3) : [];
  if (!mixed.length && !split.length) return "";
  const mixedRows = mixed.map(option => {
    const segments = (option.segments || []).map(segment => `<div class="timeline-row"><div class="time">${hm(segment.departure?.time || segment.departure)} → ${hm(segment.arrival?.time || segment.arrival)}</div><div><strong>${esc(segment.ticket || readableLegTitle(segment))}</strong><div class="meta">${esc(readablePlace(segment.origin || segment.departure))} → ${esc(readablePlace(segment.destination || segment.arrival))}</div></div><div>${segment.paid_price > 0 ? money(segment.paid_price, segment.currency || "EUR") : ""}</div></div>`).join("");
    return `<section class="alternative"><div class="alternative-head"><div><span class="badge ticket">Mischverbindung</span><strong>${esc(option.label || option.type || "Getrennte Fahrkarten")}</strong></div><b>${option.price_known ? money(option.total_price, option.currency || "EUR") : "Preis offen"}</b></div><div class="timeline">${segments}</div><p class="meta">${esc(option.price_note || "Die Teilpreise gelten für getrennte Fahrkarten.")}</p></section>`;
  }).join("");
  const splitRows = split.map(option => {
    const segments = (option.segments || []).map(segment => `<div class="timeline-row"><div class="time">${hm(segment.departure)} → ${hm(segment.arrival)}</div><div><strong>${esc(segment.origin || "")} → ${esc(segment.destination || "")}</strong><div class="meta">Separates DB-Ticket</div></div><div>${money(segment.price, segment.currency || "EUR")}</div></div>`).join("");
    const station = option.split_station?.name || option.split_station || "Umstieg";
    return `<section class="alternative"><div class="alternative-head"><div><span class="badge">Split-Ticket</span><strong>Trennung in ${esc(station)}</strong></div><b>${money(option.total_price, option.currency || "EUR")}</b></div><div class="timeline">${segments}</div><p class="meta">Ersparnis gegenüber dem geprüften Direktticket: ${money(option.savings, option.currency || "EUR")}</p></section>`;
  }).join("");
  return `<article class="result-card alternatives-card"><div class="card-head"><div><span class="eyebrow">${esc(title)}</span><h3>Misch- & Split-Tickets</h3></div><span class="price-pill">${mixed.length + split.length} Optionen</span></div><div class="alternative-list">${mixedRows}${splitRows}</div></article>`;
}

function reliabilityHtml(connection) {
  const reliability = connection?.reliability;
  if (!reliability) return '';
  if (reliability.status !== 'ok' || reliability.percent === undefined) {
    return `<div class="reliability-summary reliability-empty">Keine ausreichenden historischen Daten</div>`;
  }
  const approximate = reliability.approximate ? 'ca. ' : '';
  const confidence = reliability.approximate ? '<span class="reliability-quality">geringe Datenbasis</span>' : '';
  const legDetails = (connection.segments?.length ? connection.segments.flatMap(segment => segment.legs?.length ? segment.legs : [segment]) : (connection.legs || [])).map(leg => {
    const stats = leg.reliability;
    if (!stats || !stats.sample_count) return '';
    const median = stats.median_arrival_delay_minutes;
    const p90 = stats.p90_arrival_delay_minutes;
    const cancellation = stats.cancellation_rate;
    const quality = stats.quality === 'good' ? 'gut' : stats.quality === 'limited' ? 'begrenzt' : 'gering';
    return `<div class="reliability-leg"><strong>${esc(readableLegTitle(leg))}</strong><span>${stats.sample_count} historische Fahrten${median !== undefined ? ` · Median ${median >= 0 ? '+' : ''}${esc(median)} min` : ''}${p90 !== undefined ? ` · P90 ${p90 >= 0 ? '+' : ''}${esc(p90)} min` : ''}${cancellation !== undefined ? ` · Ausfälle ${(Number(cancellation) * 100).toFixed(1)} %` : ''} · Datenqualität ${quality}</span></div>`;
  }).join('');
  const connectionDetails = (reliability.connections || []).map(item => item.status === 'ok'
    ? `<div class="reliability-leg"><strong>${esc(item.station || 'Anschluss')}</strong><span>${item.scheduled_transfer_minutes} min Umstieg · ${item.approximate ? 'ca. ' : ''}${item.percent} %</span></div>`
    : '').join('');
  const details = legDetails || connectionDetails ? `<details class="reliability-details"><summary>Historische Details</summary>${connectionDetails}${legDetails}</details>` : '';
  return `<div class="reliability-summary"><strong>${esc(reliability.label)} · ${approximate}${esc(reliability.percent)} %</strong>${confidence}</div>${details}`;
}

function coverageResultHtml(data) {
  if (!data || data.status !== 'ok') {
    const message = data?.message || 'Mobilfunkanalyse fehlgeschlagen';
    return `<p class="coverage-unavailable">${esc(message)}</p>`;
  }
  const networks = (data.operator_networks || []).map(network => {
    const percent = network.coverage_percent;
    return `<div class="coverage-network"><div><strong>${esc(network.name)}</strong><span>${percent === null || percent === undefined ? 'keine Daten' : `${esc(percent)} %`}</span></div><div class="coverage-meter" role="meter" aria-label="${esc(network.name)} Mobilfunkabdeckung" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${esc(percent ?? 0)}"><i style="width:${Math.max(0, Math.min(100, Number(percent) || 0))}%"></i></div></div>`;
  }).join('');
  if (!networks) return '<p class="coverage-unavailable">Betreiberdaten nicht verfügbar</p>';
  return networks;
}

function readablePlace(value) {
  if (!value) return '';
  const text = typeof value === 'string' ? value : (value.name || value.station || value.city || '');
  return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(String(text)) ? '' : text;
}

function readableMode(value) {
  const mode = String(value || '').toLowerCase();
  if (mode.includes('bus') || mode.includes('coach')) return 'Bus';
  if (mode.includes('regional')) return 'Regionalzug';
  if (mode.includes('train') || mode.includes('rail')) return 'Zug';
  if (mode.includes('tram')) return 'Straßenbahn';
  if (mode.includes('walk')) return 'Fußweg';
  return '';
}

function readableLegTitle(leg) {
  const line = typeof leg.line === 'string' ? leg.line : (leg.line?.name || leg.line?.label || '');
  const operator = typeof leg.operator === 'string' ? leg.operator : (leg.operator?.name || '');
  const provider = leg.segment_provider || (typeof leg.provider === 'string' ? leg.provider : leg.provider?.name) || '';
  const normalizedProvider = /^flixbus$/i.test(provider) && leg.segment_provider ? leg.segment_provider : provider;
  if (line) return operator && operator !== line ? `${line} · ${operator}` : line;
  if (normalizedProvider) return normalizedProvider;
  return readableMode(leg.mode || leg.type) || 'Unbekannte Teilstrecke';
}

function displayLegs(connection) {
  if (connection.segments?.length) {
    return connection.segments.flatMap(segment => (segment.legs?.length ? segment.legs : [segment]).map(leg => ({...leg, segment_provider:segment.provider})));
  }
  return connection.legs || [];
}

async function loadCoverage() {
  const queue = [...coverageQueue];
  let next = 0;
  async function worker() {
    while (next < queue.length) {
      const {id, route} = queue[next++];
      const target = document.querySelector(`[data-coverage-id="${id}"] .coverage-content`);
      if (!target) continue;
      try {
        const response = await fetch('/api/coverage', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({route}),
        });
        const data = await response.json();
        target.innerHTML = coverageResultHtml(response.ok ? data : null);
      } catch (_) {
        target.innerHTML = coverageResultHtml(null);
      }
    }
  }
  await Promise.all(Array.from({length: Math.min(3, queue.length)}, worker));
}

function warningRoutes(context) {
  const routes = [];
  for (const direction of [context?.outbound, context?.return]) {
    routes.push(...(direction?.connections || []).slice(0, 3));
  }
  for (const feeder of [context?.outbound_feeder, context?.return_feeder]) {
    const selected = feeder?.selected;
    if (selected?.segments?.length) routes.push({segments:selected.segments});
  }
  const flight = context?.flight;
  for (const direction of [flight?.outbound, flight?.return]) {
    if (!direction?.from || !direction?.to) continue;
    routes.push({
      warning_geocode: true,
      origin: {name:`Flughafen ${direction.from}`},
      destination: {name:`Flughafen ${direction.to}`},
    });
  }
  return routes.slice(0, 8);
}

function warningResultHtml(data) {
  const warnings = data?.warnings || [];
  if (!warnings.length) return '';
  const items = warnings.map(warning => {
    const affected = (warning.affected_stops || []).length ? ` · ${warning.affected_stops.join(', ')}` : '';
    const location = warning.location ? `<div class="warning-location">${esc(warning.location)}${esc(affected)}</div>` : (affected ? `<div class="warning-location">${esc(affected.slice(3))}</div>` : '');
    return `<article class="travel-warning"><div class="warning-icon" aria-hidden="true">⚠</div><div><strong>${esc(warning.title)}</strong>${warning.description ? `<p>${esc(warning.description)}</p>` : ''}${location}<div class="meta">Quelle: NINA/BBK${warning.issuer ? ` · ${esc(warning.issuer)}` : ''}</div></div></article>`;
  }).join('');
  return `<section class="warning-panel"><div class="warning-heading"><span aria-hidden="true">⚠</span><h2>Aktuelle Warnungen entlang der Reise</h2></div>${items}</section>`;
}

async function loadWarnings(context) {
  const target = $('travelWarnings');
  if (!target) return;
  const routes = warningRoutes(context);
  if (!routes.length) return;
  try {
    const response = await fetch('/api/warnings', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({routes}),
    });
    if (!response.ok) return;
    const content = warningResultHtml(await response.json());
    if (content) {
      target.innerHTML = content;
      target.classList.remove('hidden');
    }
  } catch (_) {
    // Warnungen sind additive Informationen und dürfen die Reise nie stören.
  }
}

function groundConnectionsHtml(title, component) {
  const connections = component?.connections || [];
  if (!connections.length) {
    const technicalFailure = (component?.provider_statuses || []).some(item => item.outcome === 'technical_error');
    const message = technicalFailure ? 'Ein Teil der Verbindungen konnte technisch nicht geprüft werden.' : 'Keine Verbindung verfügbar.';
    return `<section class="direction-group" data-direction="${esc(title)}"><div class="direction-heading"><div><span class="eyebrow">Bahn & Bus</span><h2>${esc(title)}</h2></div><span class="muted">0 Verbindungen</span></div><article class="result-card"><p class="muted">${message}</p></article></section>`;
  }
  const cards = connections.map(connection => {
    const coverageId = `coverage-${coverageGeneration}-${coverageQueue.length}`;
    coverageQueue.push({id: coverageId, route: connection});
    const labels = (connection.labels || []).map(label => `<span class="badge ${label === 'D-Ticket' ? 'ticket' : ''}">${esc(label)}</span>`).join('');
    const price = connection.deutschlandticket_covered
      ? '0 € zusätzlich'
      : (connection.price !== undefined ? money(connection.price, connection.currency || 'EUR') : 'Preis offen');
    const display = displayLegs(connection);
    const legs = display.map((leg, index) => {
      const origin = readablePlace(leg.origin || leg.departure) || (index === 0 ? readablePlace(connection.origin || connection.coverage_origin) : 'Zwischenhalt');
      const destination = readablePlace(leg.destination || leg.arrival) || (index === display.length - 1 ? readablePlace(connection.destination || connection.coverage_destination) : 'Zwischenhalt');
      return `<div class="timeline-row connection-leg"><div class="time">${hm(leg.departure?.time || leg.departure)} → ${hm(leg.arrival?.time || leg.arrival)}</div><div><strong>${esc(readableLegTitle(leg))}</strong><div class="meta">${esc(origin)} → ${esc(destination)}</div></div></div>`;
    }).join('');
    const transfers = connection.transfers !== undefined
      ? `${connection.transfers} ${connection.transfers === 1 ? 'Umstieg' : 'Umstiege'}`
      : '';
    const earlyDeparture = connection.early_departure_minutes
      ? `<p class="departure-tolerance">${esc(connection.early_departure_minutes)} Minuten vor gewünschter Zeit</p>`
      : '';
    return `<article class="result-card connection-card"><div class="card-head"><div><div class="connection-labels">${labels}</div><h3>${hm(connection.departure?.time || connection.departure)} → ${hm(connection.arrival?.time || connection.arrival)}</h3><p class="connection-summary">${durationText(connection.duration_minutes)}${transfers ? ` · ${transfers}` : ''}</p>${earlyDeparture}${reliabilityHtml(connection)}</div><span class="price-pill">${price}</span></div><div class="timeline">${legs || '<p class="muted">Keine Teilstrecken verfügbar.</p>'}</div>${actionLinks(connection.offer_url, connection.manual_url)}<section class="coverage-panel" data-coverage-id="${coverageId}"><div class="coverage-heading"><strong>Mobilfunk entlang der Strecke</strong><span>Outdoor · 4G/5G</span></div><div class="coverage-content"><span class="coverage-loading"><i class="spinner"></i>Abdeckung wird unabhängig geladen …</span></div></section></article>`;
  }).join('');
  return `<section class="direction-group" data-direction="${esc(title)}"><div class="direction-heading"><div><span class="eyebrow">Bahn & Bus</span><h2>${esc(title)}</h2></div><span class="direction-count">${connections.length} ${connections.length === 1 ? 'Verbindung' : 'Verbindungen'}</span></div><div class="connection-list">${cards}</div></section>`;
}

function render(data) {
  coverageGeneration += 1;
  coverageQueue = [];
  const r = data.response_context || {};
  if (data.status === 'missing_fields') {
    showMessage(data.error || `Fehlende Angaben: ${(data.missing_fields || []).join(', ')}`, 'error');
    $('results').className = 'results hidden';
    return;
  }
  hideMessage();
  const route = r.route || {};
  const cost = r.cost_summary || r.price_summary || {};
  const subtotal = cost.known_subtotal ?? cost.known_total_price ?? cost.round_trip_live_price;
  const complete = cost.complete ?? cost.total_price_complete;
  let html = `<article class="summary-card"><div><h2>${esc(route.origin || $('origin').value)} → ${esc(route.destination || $('destination').value)}</h2><p>${dateText(route.outbound_date)}${route.return_date ? ` bis ${dateText(route.return_date)}` : ''}${route.stay_nights ? ` · ${route.stay_nights} Nächte` : ''}${state.dticket ? ' · Deutschlandticket: Ja' : ' · Deutschlandticket: Nein'}</p></div><div class="total"><strong>${subtotal !== undefined ? money(subtotal, cost.currency || 'EUR') : '–'}</strong><span>${complete ? 'bekannte Gesamtkosten' : 'bekannte Teilsumme'}</span></div></article><div id="travelWarnings" class="hidden"></div>`;

  if (data.search_mode === 'ground_trip') {
    const out = r.outbound || {}, back = r.return || {}, dt = r.deutschlandticket || {};
    html += groundConnectionsHtml('Hinfahrt', out);
    html += groundAlternativesHtml('Hinfahrt', out);
    if (r.return) {
      html += groundConnectionsHtml('Rückfahrt', back);
      html += groundAlternativesHtml('Rückfahrt', back);
    }
  } else {
    html += feederCard(`${route.origin || ''} → ${route.origin_airport || 'Flughafen'}`, r.outbound_feeder);
    html += flightCard(r.flight);
    html += transferCard('Flughafen → Ziel', r.outbound_transfer);
    html += hotelCard(r.hotel);
    html += transferCard('Ziel → Flughafen', r.return_transfer);
    html += feederCard(`${route.origin_airport || 'Flughafen'} → ${route.origin || ''}`, r.return_feeder);
  }

  $('results').innerHTML = html;
  $('results').className = 'results';
  if (data.search_mode === 'ground_trip') loadCoverage();
  loadWarnings(r);

  document.querySelectorAll('.feeder-card').forEach(card => {
    const views = JSON.parse(card.dataset.feeder || '{}');
    card.querySelectorAll('[data-view]').forEach(btn => {
      btn.addEventListener('click', () => {
        card.querySelectorAll('[data-view]').forEach(x => x.classList.remove('active'));
        btn.classList.add('active');
        const view = views[btn.dataset.view];
        card.querySelector('.variant-content').innerHTML = feederVariantHtml(view);
        card.querySelector('.price-pill').textContent = view?.price_known ? money(view.additional_ticket_cost, view.currency) : 'Preis offen';
      });
    });
  });
}

async function submit(ev) {
  ev.preventDefault();
  if (state.travelMode === 'ground' && (!state.stations.origin || !state.stations.destination)) {
    showMessage('Bitte Start und Ziel aus der Stationsliste auswählen.', 'error');
    return;
  }
  if (activeSearch) activeSearch.controller.abort();
  const search = {id: `fw_${Date.now()}_${crypto.getRandomValues(new Uint32Array(1))[0].toString(36)}`, controller: new AbortController(), done:false};
  activeSearch = search;
  hideMessage();
  $('results').className = 'results hidden';
  const btn = $('searchButton');
  btn.innerHTML = '<span class="spinner"></span>Suche aktualisieren';
  $('statusBadge').textContent = 'Suche läuft';
  $('searchProgress').classList.remove('hidden');
  renderProgress();
  pollProgress(search);
  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'X-Search-ID':search.id},
      body: JSON.stringify(getPayload()),
      signal: search.controller.signal,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : `HTTP ${res.status}`);
    if (activeSearch !== search) return;
    render(data);
    $('statusBadge').textContent = data.cache?.journey_hit ? 'Cache' : 'Live';
  } catch (err) {
    if (err.name === 'AbortError') return;
    showMessage(`Suche fehlgeschlagen: ${err.message}`, 'error');
    $('statusBadge').textContent = 'Fehler';
  } finally {
    search.done = true;
    if (activeSearch !== search) return;
    btn.disabled = false;
    btn.textContent = 'Verbindungen finden';
    $('searchProgress').classList.add('hidden');
  }
}

function bind() {
  setTodayDefaults();
  syncJourneyDates();
  syncHotelType();
  bindStationInput('origin');
  bindStationInput('destination');
  $('priceCalendarPreset').addEventListener('change', () => $('priceCalendarCustom').classList.toggle('hidden', $('priceCalendarPreset').value !== 'custom'));
  $('priceCalendarButton').addEventListener('click', loadPriceCalendar);
  document.querySelectorAll('[data-mode]').forEach(btn => btn.addEventListener('click', () => {
    state.travelMode = btn.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('active', x===btn));
    syncTravelMode();
  }));
  ['includeTrain', 'includeBus'].forEach(id => $(id).addEventListener('change', () => {
    if (!$('includeTrain').checked && !$('includeBus').checked) $(id).checked = true;
    $('splitTicket').disabled = !$('includeTrain').checked;
    $('splitTicket').closest('.split-ticket-choice').classList.toggle('disabled', !$('includeTrain').checked);
  }));
  document.querySelectorAll('[data-dticket]').forEach(btn => btn.addEventListener('click', () => {
    state.dticket = btn.dataset.dticket === 'true';
    localStorage.setItem('traviorel-dticket', String(state.dticket));
    syncDticket();
  }));
  document.querySelectorAll("[data-hotel-type]").forEach(btn => btn.addEventListener("click", () => {
    if (state.travelMode === "ground") return;
    state.hotelType = btn.dataset.hotelType;
    syncHotelType();
  }));
  $('departureCalendarToggle').addEventListener('click', () => openCalendar('departureDate'));
  $('returnCalendarToggle').addEventListener('click', () => openCalendar('returnDate'));
  $('calendarPrevious').addEventListener('click', () => { calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth()-1, 1); renderCalendar(); });
  $('calendarNext').addEventListener('click', () => { calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth()+1, 1); renderCalendar(); });
  $('oneWayButton').addEventListener('click', () => {
    if (state.journeyType === 'one_way') {
      const departure = new Date(`${$('departureDate').value}T12:00:00`);
      departure.setDate(departure.getDate() + Math.max(1, state.durationNights || 7));
      $('returnDate').value = localIso(departure);
    } else {
      const previousNights = dateDifferenceInDays($('departureDate').value, $('returnDate').value);
      state.durationNights = previousNights !== null && previousNights > 0 ? previousNights : 7;
      $('returnDate').value = '';
    }
    syncJourneyDates();
  });
  $('searchForm').addEventListener('submit', submit);
}

document.addEventListener('DOMContentLoaded', bind);

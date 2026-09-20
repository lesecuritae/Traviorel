import crypto from 'node:crypto';
import http from 'node:http';
import {AsyncLocalStorage} from 'node:async_hooks';
import {readFileSync} from 'node:fs';
import {createClient} from './vendor/index.js';
import {profile as dbProfile} from './vendor/p/db/index.js';
import {profile as dbnavProfile} from './vendor/p/dbnav/index.js';

const PORT = Number.parseInt(process.env.PORT || '3001', 10);
const USER_AGENT = process.env.USER_AGENT || 'traviorel/0.3.0';
const REQUEST_TIMEOUT_MS = Number.parseInt(process.env.DB_REQUEST_TIMEOUT_MS || '20000', 10);
const SPLIT_TIMEOUT_MS = Number.parseInt(process.env.DB_SPLIT_TIMEOUT_MS || '75000', 10);
const SPLIT_REQUEST_TIMEOUT_MS = Number.parseInt(process.env.DB_SPLIT_REQUEST_TIMEOUT_MS || '15000', 10);
const CACHE_TTL_MS = 15 * 60 * 1000;
const CFFI_BRIDGE_URL = process.env.DB_CFFI_BRIDGE_URL || 'http://app:8000/internal/db-cffi/request';
function readCffiToken() {
  const explicit = process.env.DB_CFFI_TOKEN || '';
  if (explicit) { if (explicit.trim() !== explicit || /\s/.test(explicit)) throw new Error('DB_CFFI_TOKEN is invalid'); return explicit; }
  const filename = (process.env.DB_CFFI_TOKEN_FILE || '').trim();
  if (!filename) throw new Error('DB_CFFI_TOKEN or DB_CFFI_TOKEN_FILE is required');
  let token;
  try { token = readFileSync(filename, 'utf8').trim(); } catch (error) { throw new Error('DB_CFFI_TOKEN_FILE cannot be read', {cause: error}); }
  if (!/^[0-9a-fA-F]{64}$/.test(token)) throw new Error('DB_CFFI_TOKEN_FILE contains an invalid token');
  return token;
}
const CFFI_TOKEN = readCffiToken();
const cffiSessionStorage = new AsyncLocalStorage();
const DBNAV_BLOCK_TTL_MS = Number.parseInt(process.env.DBNAV_BLOCK_TTL_MS || '30000', 10);
const DBNAV_MAX_CONCURRENCY = Math.max(1, Number.parseInt(process.env.DBNAV_MAX_CONCURRENCY || '2', 10));
let dbnavBlockedUntil = 0;
let dbnavActive = 0;
const dbnavWaiters = [];

async function acquireDbnavSlot() {
  if (dbnavActive < DBNAV_MAX_CONCURRENCY) {
    dbnavActive += 1;
    return;
  }
  await new Promise((resolve) => dbnavWaiters.push(resolve));
  dbnavActive += 1;
}

function releaseDbnavSlot() {
  dbnavActive = Math.max(0, dbnavActive - 1);
  const next = dbnavWaiters.shift();
  if (next) next();
}

async function withDbnavSlot(task) {
  await acquireDbnavSlot();
  try {
    if (dbnavCircuitOpen()) {
      const error = new Error('dbnav temporarily blocked');
      error.code = 'DBNAV_CIRCUIT_OPEN';
      throw error;
    }
    return await task();
  } finally {
    releaseDbnavSlot();
  }
}

function currentCffiSessionKey() {
  return cffiSessionStorage.getStore()?.sessionKey || null;
}

function dbnavCircuitOpen() {
  return Date.now() < dbnavBlockedUntil;
}

function markDbnavBlocked() {
  dbnavBlockedUntil = Date.now() + Math.max(1000, DBNAV_BLOCK_TTL_MS);
}

function cffiHeadersObject(headers) {
  if (!headers) return {};
  if (typeof headers.entries === 'function') return Object.fromEntries(headers.entries());
  return {...headers};
}

function cffiAppendQuery(url, query) {
  if (!query) return url;
  const target = new URL(url);
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) target.searchParams.append(`${key}[]`, String(item));
    } else {
      target.searchParams.set(key, String(value));
    }
  }
  return target.toString();
}

async function cffiDbnavRequest(ctx, userAgent, reqData) {
  if (!CFFI_TOKEN) throw new Error('DB_CFFI_TOKEN fehlt');

  const {profile, opt} = ctx;
  const rawBody = profile.transformReqBody(ctx, reqData.body);
  const transformed = profile.transformReq(ctx, {
    method: reqData.method,
    body: rawBody === undefined ? undefined : JSON.stringify(rawBody),
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'Accept-Language': opt.language || profile.defaultLanguage || 'de',
      'user-agent': userAgent,
      ...cffiHeadersObject(reqData.headers),
    },
    query: reqData.query,
  });

  const url = cffiAppendQuery(reqData.endpoint + (reqData.path || ''), transformed.query);
  const bridgeResponse = await fetch(CFFI_BRIDGE_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-db-cffi-token': CFFI_TOKEN,
    },
    body: JSON.stringify({
      method: transformed.method || reqData.method || 'GET',
      url,
      headers: cffiHeadersObject(transformed.headers),
      body: transformed.body,
      session_key: currentCffiSessionKey(),
    }),
    signal: AbortSignal.timeout(Math.max(REQUEST_TIMEOUT_MS + 5000, 30000)),
  });

  if (!bridgeResponse.ok) {
    const details = await bridgeResponse.text();
    throw new Error(`curl_cffi bridge HTTP ${bridgeResponse.status}: ${details.slice(0, 500)}`);
  }

  const result = await bridgeResponse.json();
  if (result.status < 200 || result.status >= 300) {
    const error = new Error(`curl_cffi HTTP ${result.status} für ${url}`);
    error.response = {
      status: result.status,
      statusText: String(result.body || '').slice(0, 500),
    };
    throw error;
  }

  let body;
  try {
    body = JSON.parse(result.body);
  } catch (error) {
    throw new Error(`Ungültiges JSON von ${url}: ${String(error?.message || error)}`);
  }
  if (body?.code === 'OPS_BLOCKED' || body?.status === 'OPS_BLOCKED' || (body?.status === 'ERROR' && body?.code)) {
    if (body?.code === 'OPS_BLOCKED') markDbnavBlocked();
    const error = new Error(`DBnav ${body?.code || body?.status || 'ERROR'} für ${url}`);
    error.code = body?.code || body?.status || 'DBNAV_ERROR';
    error.details = body;
    throw error;
  }
  if (body?.fehlerNachricht || body?.errors) {
    throw new Error(`DB-API Fehler von ${url}: ${JSON.stringify(body).slice(0, 800)}`);
  }
  return {res: body, common: {}};
}

const dbnavCffiProfile = {...dbnavProfile, request: cffiDbnavRequest};
const clients = {
  db: createClient(dbProfile, USER_AGENT, {enrichStations: false}),
  dbnav: createClient(dbnavCffiProfile, USER_AGENT, {enrichStations: false}),
};
const PROFILE_ORDER = ['dbnav', 'db'];
const journeyCache = new Map();

const allProducts = {
  suburban: true,
  subway: true,
  tram: true,
  bus: true,
  ferry: true,
  nationalExpress: true,
  national: true,
  regional: true,
  regionalExpress: true,
  taxi: true,
};

const deutschlandticketProducts = {
  suburban: true,
  subway: true,
  tram: true,
  bus: true,
  ferry: true,
  nationalExpress: false,
  national: false,
  regional: true,
  regionalExpress: true,
  taxi: false,
};

class HttpError extends Error {
  constructor(status, message, details = undefined) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
    this.details = details;
  }
}

function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  });
  res.end(body);
}

async function readJson(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1_000_000) throw new HttpError(413, 'request body too large');
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new HttpError(400, 'invalid JSON body', String(error?.message || error));
  }
}

function errorText(error) {
  const parts = [];
  const add = (value) => {
    if (value === undefined || value === null || value === '') return;
    let text;
    try {
      text = typeof value === 'string' ? value : JSON.stringify(value);
    } catch {
      text = String(value);
    }
    if (text && !parts.includes(text)) parts.push(text);
  };
  add(error?.message);
  add(error?.cause?.message);
  add(error?.response?.status);
  add(error?.response?.statusText);
  add(error?.response?.data);
  add(error?.body);
  add(error?.details);
  if (!parts.length) add(error || 'unknown error');
  const text = parts.join(' | ');
  return text.length > 1600 ? `${text.slice(0, 1600)}…` : text;
}

function withTimeout(promise, timeoutMs, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label}: timeout after ${timeoutMs} ms`)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function cleanupCache() {
  const now = Date.now();
  for (const [token, value] of journeyCache) {
    if (value.expiresAt <= now) journeyCache.delete(token);
  }
  while (journeyCache.size > 200) {
    const oldest = journeyCache.keys().next().value;
    journeyCache.delete(oldest);
  }
}

function cacheJourney(journey, context) {
  cleanupCache();
  const token = crypto.randomUUID();
  journeyCache.set(token, {
    journey,
    context,
    expiresAt: Date.now() + CACHE_TTL_MS,
  });
  return token;
}

function normalizeText(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/\bhauptbahnhof\b/g, 'hbf')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

const AIRPORT_PROVIDER_IDENTITIES = {
  BER: {aliases: ['ber', 'berlin', 'berlin brandenburg', 'brandenburg', 'schonefeld'], lookup: 'Berlin Brandenburg Flughafen BER Terminal 1-2'},
  LEJ: {aliases: ['lej', 'leipzig', 'halle', 'leipzig halle'], lookup: 'Leipzig Halle Flughafen'},
  DUS: {aliases: ['dus', 'dusseldorf'], lookup: 'Dusseldorf Flughafen'},
  FRA: {aliases: ['fra', 'frankfurt'], lookup: 'Frankfurt Flughafen'},
  MUC: {aliases: ['muc', 'munchen'], lookup: 'Munchen Flughafen'},
  HAM: {aliases: ['ham', 'hamburg'], lookup: 'Hamburg Flughafen Airport'},
  CGN: {aliases: ['cgn', 'koln', 'bonn', 'koln bonn'], lookup: 'Koln Bonn Flughafen'},
  HAJ: {aliases: ['haj', 'hannover'], lookup: 'Hannover Flughafen'},
  NUE: {aliases: ['nue', 'nurnberg'], lookup: 'Nurnberg Flughafen'},
  STR: {aliases: ['str', 'stuttgart', 'messe'], lookup: 'Stuttgart Flughafen Messe'},
};

function phraseInNormalizedText(text, phrase) {
  const haystack = ` ${normalizeText(text)} `;
  const needle = ` ${normalizeText(phrase)} `;
  return needle.trim().length > 0 && haystack.includes(needle);
}

function airportIntent(value) {
  const raw = String(value || '');
  const normalized = normalizeText(raw);
  if (/\b[A-Z]{3}\b/.test(raw)) return true;
  return /\b(flughafen|airport|terminal|messe)\b/.test(normalized);
}

function airportCodeFromText(value) {
  const raw = String(value || '');
  if (!airportIntent(raw)) return null;
  const explicit = raw.match(/\b[A-Z]{3}\b/g) || [];
  for (const code of explicit) {
    if (AIRPORT_PROVIDER_IDENTITIES[code]) return code;
  }
  for (const [code, config] of Object.entries(AIRPORT_PROVIDER_IDENTITIES)) {
    if (config.aliases.some((alias) => phraseInNormalizedText(raw, alias))) return code;
  }
  return null;
}

function airportIdentityCompatible(query, candidate) {
  const requestedCode = airportCodeFromText(query);
  if (!requestedCode) return true;
  const candidateCode = airportCodeFromText(candidate);
  if (candidateCode) return candidateCode === requestedCode;
  const aliases = AIRPORT_PROVIDER_IDENTITIES[requestedCode]?.aliases || [];
  return aliases.some((alias) => phraseInNormalizedText(candidate, alias));
}

function airportLookupQuery(query) {
  const code = airportCodeFromText(query);
  return code ? (AIRPORT_PROVIDER_IDENTITIES[code]?.lookup || query) : query;
}

function terminalNumbers(value) {
  const numbers = new Set();
  const text = String(value || '');
  const regex = /\b(?:terminal|t)\s*([0-9]+)(?:\s*[-–/]\s*([0-9]+))?/gi;
  let match;
  while ((match = regex.exec(text)) !== null) {
    const a = Number(match[1]);
    const b = Number(match[2] || match[1]);
    if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b < 1 || a > 20 || b > 20) continue;
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    for (let n = lo; n <= hi; n += 1) numbers.add(n);
  }
  return numbers;
}

function terminalCompatibility(query, candidate) {
  const requested = terminalNumbers(query);
  const actual = terminalNumbers(candidate);
  if (!requested.size || !actual.size) return 0;
  for (const value of requested) {
    if (actual.has(value)) return 500;
  }
  return -1000;
}

async function resolveLocation(client, query, profileName, directId = null) {
  if (!query || typeof query !== 'string') throw new HttpError(400, 'origin/destination missing');
  const cleaned = query.trim();
  if (directId && typeof directId === 'string') return {id: directId.trim(), name: cleaned};
  if (/^\d{6,}$/.test(cleaned)) return {id: cleaned, name: cleaned};

  const lookupQueries = [cleaned];
  const airportLookup = airportLookupQuery(cleaned);
  if (airportLookup !== cleaned) lookupQueries.push(airportLookup);

  const locationLists = await Promise.all(lookupQueries.map((lookup) => withTimeout(client.locations(lookup, {
    results: 20,
    stops: true,
    addresses: false,
    poi: false,
    language: 'de',
  }), REQUEST_TIMEOUT_MS, `${profileName} location search ${lookup}`)));

  const locations = [];
  const seen = new Set();
  for (const list of locationLists) {
    for (const item of (Array.isArray(list) ? list : [])) {
      const key = item?.id ? String(item.id) : `${item?.type || ''}|${item?.name || ''}`;
      if (!seen.has(key)) {
        seen.add(key);
        locations.push(item);
      }
    }
  }

  const wanted = normalizeText(cleaned);
  const requestedAirport = airportCodeFromText(cleaned);
  const candidates = locations
    .filter((item) => (item?.type === 'station' || item?.type === 'stop') && item.id)
    .filter((item) => !requestedAirport || airportIdentityCompatible(cleaned, item.name))
    .map((item) => {
      const terminalScore = terminalCompatibility(cleaned, item.name);
      if (terminalScore < 0) return null;
      const name = normalizeText(item.name);
      let score = item.type === 'station' ? 20 : 10;
      if (name === wanted) score += 200;
      else if (name.startsWith(wanted) || wanted.startsWith(name)) score += 100;
      else if (name.includes(wanted) || wanted.includes(name)) score += 50;
      if (/\bhbf\b/.test(wanted) && /\bhbf\b/.test(name)) score += 30;
      if (requestedAirport && airportCodeFromText(item.name) === requestedAirport) score += 600;
      score += terminalScore;
      return {item, score};
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  const stop = candidates[0]?.item;
  if (!stop) {
    const names = locations.slice(0, 12).map((item) => item?.name).filter(Boolean);
    throw new HttpError(404, `DB-Ort nicht sicher gefunden: ${cleaned}`, {
      requested_airport: requestedAirport,
      lookup_queries: lookupQueries,
      candidates: names,
    });
  }
  return {
    id: String(stop.id),
    name: stop.name || cleaned,
    location: stop.location || null,
  };
}

function toNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value.replace(',', '.'));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function collectMoney(node, path = '', currency = 'EUR', out = []) {
  if (node === null || node === undefined) return out;
  if (Array.isArray(node)) {
    for (let index = 0; index < node.length; index += 1) {
      collectMoney(node[index], `${path}[${index}]`, currency, out);
    }
    return out;
  }
  if (typeof node !== 'object') return out;

  let localCurrency = currency;
  for (const [key, value] of Object.entries(node)) {
    if (/currency|waehrung|währung/i.test(key) && typeof value === 'string') {
      localCurrency = value.toUpperCase();
    }
  }

  for (const [key, value] of Object.entries(node)) {
    const nextPath = path ? `${path}.${key}` : key;
    if (value && typeof value === 'object') {
      collectMoney(value, nextPath, localCurrency, out);
      continue;
    }
    const number = toNumber(value);
    if (number === null || number <= 0) continue;
    if (!/(price|amount|betrag|total|gesamt|centamount|abpreis)/i.test(key)) continue;
    let amount = number;
    if (/cent/i.test(key)) amount /= 100;
    if (amount < 1 || amount > 10_000) continue;
    out.push({amount: Math.round(amount * 100) / 100, currency: localCurrency, path: nextPath});
  }
  return out;
}

function isRegularAdultFareName(value) {
  const name = String(value || "").normalize("NFKD").replace(/\p{Diacritic}/gu, "").toLowerCase();
  if (!name || name === "from") return false;
  if (/\b(kind|kinder|jugend|young|senior|ermassigt|gruppe|kleingruppe)\b/.test(name)) return false;
  if (/\b(brandenburg[- ]berlin|bayern|baden[- ]wurttemberg|sachsen|sachsen[- ]anhalt|thuringen|hessen|niedersachsen|schleswig[- ]holstein|rheinland[- ]pfalz|saarland|mecklenburg[- ]vorpommern)[- ]ticket\b/.test(name)) return false;
  if (/\bquer[- ]durchs[- ]land[- ]ticket\b|\bticket nacht\b/.test(name)) return false;
  return true;
}

function bestMoney(journey) {
  const directAmount = toNumber(journey?.price?.amount ?? journey?.price);
  if (directAmount !== null && directAmount > 0 && !Array.isArray(journey?.tickets)) {
    return {
      price: Math.round(directAmount * 100) / 100,
      currency: String(journey?.price?.currency || 'EUR').toUpperCase(),
      source: 'journey.price.amount',
      eligibilityKnown: false,
      partialFare: Boolean(journey?.partialFare || journey?.price?.partialFare),
      priceCandidates: [],
    };
  }

  // db-vendo-client v6 stores ticket.priceObj.amount in cents.
  // Treating this field as euros would turn 54.99 EUR into 5499 EUR.
  const ticketCandidates = [];
  for (let index = 0; index < (journey?.tickets || []).length; index += 1) {
    const ticket = journey.tickets[index];
    const cents = toNumber(ticket?.priceObj?.amount);
    if (cents === null || cents <= 0) continue;
    const amount = Math.round(cents) / 100;
    if (amount < 1 || amount > 10_000) continue;
    ticketCandidates.push({
      amount: Math.round(amount * 100) / 100,
      currency: String(ticket?.priceObj?.currency || 'EUR').toUpperCase(),
      path: `tickets[${index}].priceObj.amount_cents`,
      fareName: typeof ticket?.name === "string" ? ticket.name : null,
      firstClass: Boolean(ticket?.firstClass),
      eligibilityKnown: isRegularAdultFareName(ticket?.name),
      partialFare: Boolean(ticket?.partialFare || ticket?.priceObj?.partialFare),
    });
  }

  const valid = [...ticketCandidates]
    .filter((candidate) => candidate.amount >= 1 && candidate.amount <= 10_000)
    .sort((a, b) => a.amount - b.amount);
  if (!valid.length) {
    return {
      price: null,
      currency: 'EUR',
      source: null,
      partialFare: false,
      priceCandidates: [],
    };
  }
  const eligible = valid.filter((candidate) => candidate.eligibilityKnown && !candidate.firstClass);
  const fullFares = eligible.filter((candidate) => !candidate.partialFare);
  const selected = (fullFares.length ? fullFares : eligible)[0];
  if (!selected) {
    return {price: null, currency: 'EUR', source: null, partialFare: false, priceCandidates: valid.slice(0, 12)};
  }
  return {
    price: selected.amount,
    currency: selected.currency,
    source: `fallback:${selected.path}`,
    partialFare: Boolean(selected.partialFare),
    priceCandidates: valid.slice(0, 12),
    fareName: selected.fareName,
    eligibilityKnown: true,
  };
}

function dateValue(value) {
  if (value instanceof Date) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isoValue(value) {
  const date = dateValue(value);
  return date ? date.toISOString() : null;
}

function plannedDeparture(item) {
  return item?.plannedDeparture || item?.departure || null;
}

function plannedArrival(item) {
  return item?.plannedArrival || item?.arrival || null;
}

function minutesBetween(start, end) {
  const a = dateValue(start)?.getTime();
  const b = dateValue(end)?.getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null;
  return Math.round((b - a) / 60000);
}

function buildDbUrl(from, to, departure, bestPrice = false) {
  const dt = dateValue(departure);
  const local = dt ? new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(dt).replace(' ', 'T') : String(departure);
  const soid = `A=1@O=${from.name}@L=${from.id}@`;
  const zoid = `A=1@O=${to.name}@L=${to.id}@`;
  const params = new URLSearchParams({
    sts: 'true', so: from.name, zo: to.name, soid, zoid,
    hd: local, hza: 'D', ar: 'false', s: 'true', d: 'false',
    fm: 'false', bp: bestPrice ? 'true' : 'false', dlt: 'false', dltv: 'false',
  });
  return `https://www.bahn.de/buchung/fahrplan/suche#${params.toString()}`;
}

function normalizeStopover(stopover) {
  const stop = stopover?.stop || {};
  return {
    id: stop.id ? String(stop.id) : null,
    name: stop.name || null,
    arrival: isoValue(plannedArrival(stopover)),
    departure: isoValue(plannedDeparture(stopover)),
    platform: stopover?.plannedArrivalPlatform || stopover?.arrivalPlatform || stopover?.plannedDeparturePlatform || stopover?.departurePlatform || null,
    latitude: Number.isFinite(Number(stop?.location?.latitude)) ? Number(stop.location.latitude) : null,
    longitude: Number.isFinite(Number(stop?.location?.longitude)) ? Number(stop.location.longitude) : null,
  };
}

// Verspätung in Minuten: aus den Sekunden der Auskunft, sonst aus Ist- gegen Plan-Zeit.
function delayMinutes(seconds, actual, planned) {
  const direct = Number(seconds);
  if (seconds != null && Number.isFinite(direct)) return Math.round(direct / 60);
  const a = dateValue(actual)?.getTime();
  const b = dateValue(planned)?.getTime();
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((a - b) / 60000) : null;
}

function normalizeLeg(leg) {
  return {
    trip_id: leg?.tripId ? String(leg.tripId) : null,
    line: leg?.line?.name || leg?.line?.productName || null,
    line_name: leg?.line?.name || leg?.line?.productName || null,
    train_number: leg?.line?.fahrtNr != null ? String(leg.line.fahrtNr) : null,
    train_type: leg?.line?.productName || leg?.line?.product || null,
    product: leg?.line?.product || null,
    operator: leg?.line?.operator?.name || leg?.line?.operator || null,
    mode: leg?.line?.mode || null,
    origin: {
      id: leg?.origin?.id ? String(leg.origin.id) : null,
      name: leg?.origin?.name || null,
    },
    destination: {
      id: leg?.destination?.id ? String(leg.destination.id) : null,
      name: leg?.destination?.name || null,
    },
    departure: isoValue(plannedDeparture(leg)),
    arrival: isoValue(plannedArrival(leg)),
    cancelled: Boolean(leg?.cancelled),
    departure_delay_minutes: delayMinutes(leg?.departureDelay, leg?.departure, leg?.plannedDeparture),
    arrival_delay_minutes: delayMinutes(leg?.arrivalDelay, leg?.arrival, leg?.plannedArrival),
    platform: leg?.departurePlatform || null,
    planned_platform: leg?.plannedDeparturePlatform || null,
    walking: Boolean(leg?.walking),
    stopovers: Array.isArray(leg?.stopovers) ? leg.stopovers.map(normalizeStopover) : [],
  };
}

function normalizeJourney(journey, from, to, mode, index, profileName) {
  const legs = (journey?.legs || []).filter((leg) => !leg?.walking);
  if (!legs.length) return null;
  const first = legs[0];
  const last = legs.at(-1);
  const departure = plannedDeparture(first);
  const arrival = plannedArrival(last);
  if (!departure || !arrival) return null;
  const money = bestMoney(journey);
  const usablePrice = money.partialFare || money.eligibilityKnown !== true ? null : money.price;
  const analysisToken = cacheJourney(journey, {
    from,
    to,
    mode,
    profileName,
    cffiSessionKey: currentCffiSessionKey(),
  });
  const normalSearchUrl = buildDbUrl(from, to, departure, false);
  const bestPriceSearchUrl = buildDbUrl(from, to, departure, true);
  return {
    id: `${profileName}-${index}`,
    provider: 'Deutsche Bahn',
    provider_code: profileName,
    db_source: profileName,
    analysis_profile: profileName,
    type: 'train',
    mode,
    origin: from.name,
    destination: to.name,
    origin_id: from.id,
    destination_id: to.id,
    departure: isoValue(departure),
    arrival: isoValue(arrival),
    duration_minutes: minutesBetween(departure, arrival),
    transfers: Math.max(0, legs.length - 1),
    price: mode === 'deutschlandticket' ? 0 : usablePrice,
    listed_price: money.price,
    currency: money.currency,
    price_source: money.source,
    fare_name: money.fareName || null,
    price_is_partial: Boolean(money.partialFare),
    price_candidates: money.priceCandidates,
    deutschlandticket_coverage: mode === 'deutschlandticket' ? 'voraussichtlich_abgedeckt' : null,
    booking_url: normalSearchUrl,
    db_search_links: {
      normal: {
        label: 'Normale DB-Suche',
        url: normalSearchUrl,
        purpose: 'Konkrete Verbindung auf bahn.de öffnen und den aktuellen Endpreis prüfen.',
      },
      best_price: {
        label: 'DB-Bestpreissuche',
        url: bestPriceSearchUrl,
        purpose: 'Günstigste DB-Angebote für einen zukünftigen Reisetag über verschiedene Abfahrtszeiten prüfen.',
      },
    },
    analysis_token: analysisToken,
    legs: legs.map(normalizeLeg),
  };
}

function lineLabel(leg) {
  return String(leg?.line?.name || leg?.line?.productName || '').trim().toUpperCase();
}

function isDeutschlandticketJourney(journey) {
  for (const leg of journey?.legs || []) {
    if (leg?.walking) continue;
    const product = String(leg?.line?.product || '').toLowerCase();
    const label = lineLabel(leg);
    if (product === 'national' || product === 'nationalexpress') return false;
    if (/^(ICE|IC|EC|ECE|TGV|RJ|RJX|NJ|FLX)\b/.test(label)) return false;
  }
  return true;
}

async function refreshJourneySafely(client, profileName, journey, body, timeoutMs = REQUEST_TIMEOUT_MS) {
  if (!journey?.refreshToken || body.with_prices === false) return journey;
  if (profileName === 'dbnav' && dbnavCircuitOpen()) {
    return {...journey, price_refresh_error: 'dbnav temporarily blocked'};
  }
  try {
    const update = await withTimeout(client.refreshJourney(journey.refreshToken, {
      stopovers: true,
      remarks: true,
      tickets: true,
      firstClass: Boolean(body.first_class),
      age: Number.isInteger(body.age) ? body.age : undefined,
      deutschlandTicketDiscount: Boolean(body.has_deutschlandticket),
      language: 'de',
    }), timeoutMs, `${profileName} refresh journey`);
    const refreshed = update?.journey || update;
    return refreshed?.legs ? refreshed : journey;
  } catch (error) {
    if (profileName === 'dbnav' && (error?.code === 'OPS_BLOCKED' || errorText(error).includes('OPS_BLOCKED'))) {
      markDbnavBlocked();
    }
    return {...journey, price_refresh_error: errorText(error)};
  }
}

async function mapWithConcurrency(items, concurrency, worker) {
  const results = new Array(items.length);
  let next = 0;
  async function run() {
    while (true) {
      const index = next;
      next += 1;
      if (index >= items.length) return;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(Array.from({length: Math.min(concurrency, items.length || 1)}, run));
  return results;
}

async function searchWithProfile(profileName, body) {
  const client = clients[profileName];
  if (!client) throw new Error(`unknown DB profile: ${profileName}`);
  const [origin, destination] = await Promise.all([
    resolveLocation(client, body.origin, profileName, body.origin_id),
    resolveLocation(client, body.destination, profileName, body.destination_id),
  ]);
  const mode = body.mode === 'deutschlandticket' ? 'deutschlandticket' : 'all';
  const departure = body.departure ? new Date(body.departure) : null;
  const arrival = body.arrival ? new Date(body.arrival) : null;
  const arrivalBefore = body.arrival_before ? new Date(body.arrival_before) : null;
  if (!departure && !arrival) throw new HttpError(400, 'departure or arrival ISO timestamp required');
  if (departure && Number.isNaN(departure.getTime())) throw new HttpError(400, 'invalid departure ISO timestamp');
  if (arrival && Number.isNaN(arrival.getTime())) throw new HttpError(400, 'invalid arrival ISO timestamp');
  if (arrivalBefore && Number.isNaN(arrivalBefore.getTime())) throw new HttpError(400, 'invalid arrival_before ISO timestamp');
  const maxResults = Math.min(Math.max(Number(body.results || 8), 1), 48);
  const options = {
    results: maxResults,
    stopovers: true,
    remarks: true,
    products: mode === 'deutschlandticket' ? deutschlandticketProducts : allProducts,
    notOnlyFastRoutes: mode === 'deutschlandticket' || body.not_only_fast_routes === true,
    language: 'de',
  };
  if (arrival) options.arrival = arrival;
  else options.departure = departure;
  if (mode === 'all' && body.bestprice === true) options.bestprice = true;
  if (Number.isInteger(body.max_transfers) && body.max_transfers >= 0) {
    options.transfers = body.max_transfers;
  }

  const response = await withTimeout(
    client.journeys(origin.id, destination.id, options),
    REQUEST_TIMEOUT_MS,
    `${profileName} journey search`,
  );
  let journeys = Array.isArray(response?.journeys) ? response.journeys : [];
  if (mode === 'deutschlandticket') journeys = journeys.filter(isDeutschlandticketJourney);

  const refreshLimit = mode === 'all' && body.with_prices !== false ? Math.min(journeys.length, 4) : 0;
  const head = journeys.slice(0, refreshLimit);
  const refreshedHead = await mapWithConcurrency(head, 2, (journey) => (
    refreshJourneySafely(client, profileName, journey, body)
  ));
  journeys = [...refreshedHead, ...journeys.slice(refreshLimit)];

  const normalized = journeys
    .map((journey, index) => normalizeJourney(journey, origin, destination, mode, index, profileName))
    .filter(Boolean)
    .filter((journey) => !arrivalBefore || (journey.arrival && new Date(journey.arrival) <= arrivalBefore))
    .slice(0, maxResults);

  const priced = normalized.filter((journey) => Number(journey.price) > 0).length;
  return {
    status: normalized.length ? 'ok' : 'empty',
    source: profileName,
    mode,
    origin,
    destination,
    journeys: normalized,
    earlier_ref: response?.earlierRef || null,
    later_ref: response?.laterRef || null,
    diagnostics: {
      returned: normalized.length,
      priced,
      price_refreshes: refreshLimit,
      bestprice: mode === 'all' && body.bestprice === true,
      not_only_fast_routes: options.notOnlyFastRoutes === true,
      time_mode: arrival ? 'arrival' : 'departure',
      arrival_before: arrivalBefore ? arrivalBefore.toISOString() : null,
    },
  };
}

async function searchJourneys(body) {
  const attempts = [];
  let scheduleFallback = null;
  const wantsPrices = body.mode !== 'deutschlandticket' && body.with_prices !== false;

  for (const profileName of PROFILE_ORDER) {
    if (profileName === 'dbnav' && dbnavCircuitOpen()) {
      attempts.push({profile: profileName, ok: false, skipped: true, error: 'dbnav temporarily blocked'});
      continue;
    }
    try {
      const result = profileName === 'dbnav'
        ? await withDbnavSlot(() => searchWithProfile(profileName, body))
        : await searchWithProfile(profileName, body);
      if (profileName === 'dbnav') dbnavBlockedUntil = 0;
      const priced = Number(result?.diagnostics?.priced || 0);
      attempts.push({
        profile: profileName,
        ok: true,
        routes: result.journeys.length,
        priced,
      });
      if (!result.journeys.length) continue;
      if (!wantsPrices || priced > 0) return {...result, attempts};
      if (!scheduleFallback) scheduleFallback = result;
    } catch (error) {
      if (profileName === 'dbnav' && error?.code === 'DBNAV_CIRCUIT_OPEN') {
        attempts.push({profile: profileName, ok: false, skipped: true, error: 'dbnav temporarily blocked'});
        continue;
      }
      if (profileName === 'dbnav' && (error?.code === 'OPS_BLOCKED' || errorText(error).includes('OPS_BLOCKED'))) {
        markDbnavBlocked();
      }
      attempts.push({profile: profileName, ok: false, error: errorText(error)});
    }
  }

  if (scheduleFallback) {
    return {...scheduleFallback, attempts, status: 'ok_without_price'};
  }
  return {
    status: 'empty',
    source: null,
    mode: body.mode === 'deutschlandticket' ? 'deutschlandticket' : 'all',
    journeys: [],
    attempts,
  };
}

function stationMatches(left, right) {
  if (!left || !right) return false;
  if (left.id && right.id && String(left.id) === String(right.id)) return true;
  const leftName = normalizeText(left.name);
  const rightName = normalizeText(right.name);
  return Boolean(leftName && rightName && leftName === rightName);
}

function compactSequence(values) {
  const compact = [];
  for (const value of values) {
    if (!value) continue;
    if (compact.at(-1) !== value) compact.push(value);
  }
  return compact;
}

function journeySignatureFromLegs(legs) {
  const transitLegs = (legs || []).filter((leg) => !leg?.walking);
  return {
    tripIds: compactSequence(transitLegs.map((leg) => leg?.tripId ? String(leg.tripId) : null)),
    lines: compactSequence(transitLegs.map((leg) => lineLabel(leg)).filter(Boolean)),
  };
}

function expectedSignatureForPoint(journey, point, side) {
  const legs = (journey?.legs || []).filter((leg) => !leg?.walking);
  let selected;
  if (side === 'first') {
    selected = legs.slice(0, point.legIndex + 1);
  } else {
    selected = legs.slice(point.withinLeg ? point.legIndex : point.nextLegIndex);
  }
  return journeySignatureFromLegs(selected);
}

function sameSequence(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function candidateMatchesExpected(candidate, expected) {
  const actual = journeySignatureFromLegs(candidate?.legs || []);
  if (expected.tripIds.length && actual.tripIds.length
      && sameSequence(actual.tripIds, expected.tripIds)) {
    return true;
  }
  if (expected.lines.length && actual.lines.length
      && sameSequence(actual.lines, expected.lines)) {
    return true;
  }
  return false;
}

function extractSplitPoints(journey) {
  const transitLegs = (journey?.legs || []).filter((leg) => !leg?.walking);
  const points = [];
  const seen = new Set();

  const addPoint = (point) => {
    if (!point?.station?.id || !point.arrival || !point.departure) return;
    if (point.arrival > point.departure) return;
    const key = `${point.station.id}|${point.arrival.getTime()}|${point.departure.getTime()}`;
    if (seen.has(key)) return;
    seen.add(key);
    points.push(point);
  };

  // Stops within one train are valid split points when both timestamps exist.
  transitLegs.forEach((leg, legIndex) => {
    const stopovers = Array.isArray(leg?.stopovers) ? leg.stopovers : [];
    stopovers.forEach((stopover, stopIndex) => {
      if (stopIndex === 0 || stopIndex === stopovers.length - 1) return;
      const stop = stopover?.stop;
      addPoint({
        station: stop?.id ? {id: String(stop.id), name: stop.name || String(stop.id)} : null,
        arrival: dateValue(plannedArrival(stopover)),
        departure: dateValue(plannedDeparture(stopover)),
        legIndex,
        nextLegIndex: legIndex,
        stopIndex,
        withinLeg: true,
        transferPoint: false,
        line: lineLabel(leg),
      });
    });
  });

  // At a transfer the incoming leg often has only arrival and the outgoing leg
  // only departure. Combine both legs instead of silently losing the station.
  for (let legIndex = 0; legIndex < transitLegs.length - 1; legIndex += 1) {
    const incoming = transitLegs[legIndex];
    const outgoing = transitLegs[legIndex + 1];
    if (!stationMatches(incoming?.destination, outgoing?.origin)) continue;
    const station = incoming.destination?.id ? incoming.destination : outgoing.origin;
    addPoint({
      station: {
        id: String(station.id),
        name: station.name || String(station.id),
      },
      arrival: dateValue(plannedArrival(incoming)),
      departure: dateValue(plannedDeparture(outgoing)),
      legIndex,
      nextLegIndex: legIndex + 1,
      stopIndex: null,
      withinLeg: false,
      transferPoint: true,
      line: `${lineLabel(incoming)} -> ${lineLabel(outgoing)}`,
    });
  }

  return points.sort((a, b) => a.departure - b.departure);
}

function selectSplitPoints(points, maxPoints) {
  if (points.length <= maxPoints) return points;
  const selected = [];
  const used = new Set();
  const add = (point) => {
    const key = `${point.station.id}|${point.departure.getTime()}`;
    if (!used.has(key) && selected.length < maxPoints) {
      used.add(key);
      selected.push(point);
    }
  };
  points.filter((point) => point.transferPoint).forEach(add);
  const remaining = points.filter((point) => !used.has(`${point.station.id}|${point.departure.getTime()}`));
  const slots = maxPoints - selected.length;
  for (let index = 0; index < slots && remaining.length; index += 1) {
    const position = Math.min(remaining.length - 1, Math.floor(((index + 1) * remaining.length) / (slots + 1)));
    add(remaining[position]);
  }
  for (const point of remaining) add(point);
  return selected.sort((a, b) => a.departure - b.departure);
}

function journeyTimeScore(journey, expectedDeparture, expectedArrival) {
  const legs = (journey?.legs || []).filter((leg) => !leg?.walking);
  if (!legs.length) return Number.POSITIVE_INFINITY;
  const dep = dateValue(plannedDeparture(legs[0]));
  const arr = dateValue(plannedArrival(legs.at(-1)));
  if (!dep || !arr) return Number.POSITIVE_INFINITY;
  const depDiff = Math.abs(dep - expectedDeparture);
  const arrDiff = expectedArrival ? Math.abs(arr - expectedArrival) : 0;
  if (depDiff > 120_000 || arrDiff > 15 * 60_000) return Number.POSITIVE_INFINITY;
  return depDiff + arrDiff * 0.35;
}

async function ensurePricedJourney(client, profileName, journey, body, timeoutMs = REQUEST_TIMEOUT_MS) {
  if (bestMoney(journey).price !== null) return journey;
  return refreshJourneySafely(client, profileName, journey, {...body, with_prices: true}, timeoutMs);
}

async function findExactJourney(client, profileName, from, to, targetDeparture, expectedArrival, expectedSignature, body) {
  const response = await withTimeout(client.journeys(from, to, {
    departure: targetDeparture,
    results: 6,
    stopovers: true,
    remarks: true,
    notOnlyFastRoutes: true,
    transfers: 8,
    firstClass: Boolean(body.first_class),
    age: Number.isInteger(body.age) ? body.age : undefined,
    language: 'de',
    products: allProducts,
  }), SPLIT_REQUEST_TIMEOUT_MS, `${profileName} split journey ${from} -> ${to}`);

  const candidates = (response?.journeys || [])
    .filter((journey) => candidateMatchesExpected(journey, expectedSignature))
    .map((journey) => ({journey, score: journeyTimeScore(journey, targetDeparture, expectedArrival)}))
    .filter((entry) => Number.isFinite(entry.score))
    .sort((a, b) => a.score - b.score);
  if (!candidates.length) return null;
  return ensurePricedJourney(client, profileName, candidates[0].journey, body, SPLIT_REQUEST_TIMEOUT_MS);
}

function normalizeSplitSegment(journey, index) {
  const legs = (journey?.legs || []).filter((leg) => !leg?.walking);
  const first = legs[0];
  const last = legs.at(-1);
  const money = bestMoney(journey);
  return {
    id: `split-segment-${index}`,
    origin: first?.origin?.name || null,
    origin_id: first?.origin?.id ? String(first.origin.id) : null,
    destination: last?.destination?.name || null,
    destination_id: last?.destination?.id ? String(last.destination.id) : null,
    departure: isoValue(plannedDeparture(first)),
    arrival: isoValue(plannedArrival(last)),
    price: money.price,
    currency: money.currency,
    price_source: money.source,
    fare_name: money.fareName || null,
    price_is_partial: Boolean(money.partialFare),
    legs: legs.map(normalizeLeg),
  };
}

async function analyzeSplitPoint(client, profileName, point, journey, body) {
  const legs = (journey?.legs || []).filter((leg) => !leg?.walking);
  const origin = legs[0]?.origin;
  const destination = legs.at(-1)?.destination;
  const originalDeparture = dateValue(plannedDeparture(legs[0]));
  const originalArrival = dateValue(plannedArrival(legs.at(-1)));
  if (!origin?.id || !destination?.id || !originalDeparture || !originalArrival) {
    throw new Error('original journey lacks station IDs or timestamps');
  }

  const firstSignature = expectedSignatureForPoint(journey, point, 'first');
  const secondSignature = expectedSignatureForPoint(journey, point, 'second');
  const [first, second] = await Promise.all([
    findExactJourney(client, profileName, String(origin.id), point.station.id, originalDeparture, point.arrival, firstSignature, body),
    findExactJourney(client, profileName, point.station.id, String(destination.id), point.departure, originalArrival, secondSignature, body),
  ]);
  if (!first || !second) return {status: 'no_exact_match'};

  const firstMoney = bestMoney(first);
  const secondMoney = bestMoney(second);
  if (firstMoney.price === null || secondMoney.price === null) return {status: 'price_missing'};
  if (firstMoney.partialFare || secondMoney.partialFare) return {status: 'partial_price'};

  const firstLegs = (first.legs || []).filter((leg) => !leg?.walking);
  const secondLegs = (second.legs || []).filter((leg) => !leg?.walking);
  const firstArrival = dateValue(plannedArrival(firstLegs.at(-1)));
  const secondDeparture = dateValue(plannedDeparture(secondLegs[0]));
  if (!firstArrival || !secondDeparture || firstArrival > secondDeparture) {
    return {status: 'invalid_connection'};
  }

  return {
    status: 'ok',
    profile: profileName,
    split_station: point.station,
    split_arrival: point.arrival.toISOString(),
    split_departure: point.departure.toISOString(),
    total_price: Math.round((firstMoney.price + secondMoney.price) * 100) / 100,
    segments: [normalizeSplitSegment(first, 1), normalizeSplitSegment(second, 2)],
  };
}

async function splitAnalyze(body) {
  cleanupCache();
  const token = String(body.analysis_token || '');
  const cached = journeyCache.get(token);
  if (!cached || cached.expiresAt <= Date.now()) {
    throw new HttpError(410, 'analysis token expired or unknown; repeat the journey search');
  }

  const profileName = cached.context?.profileName || 'dbnav';
  const client = clients[profileName];
  if (!client) throw new HttpError(500, `unknown cached DB profile: ${profileName}`);
  let journey = cached.journey;
  journey = await ensurePricedJourney(client, profileName, journey, body);
  const originalMoney = bestMoney(journey);
  if (originalMoney.price === null || originalMoney.partialFare) {
    return {
      status: 'unavailable',
      method: 'db-vendo-direct-two-ticket',
      profile: profileName,
      reason: 'Für die Gesamtverbindung wurde kein vollständiger, belastbarer DB-Preis geliefert.',
      split_options: [],
    };
  }

  const allPoints = extractSplitPoints(journey);
  if (!allPoints.length) {
    return {
      status: 'not_applicable',
      method: 'db-vendo-direct-two-ticket',
      profile: profileName,
      reason: 'Die Verbindung enthält keine nutzbaren Zwischenhalte für eine Teilung.',
      original_price: originalMoney.price,
      currency: originalMoney.currency,
      split_options: [],
    };
  }

  const maxPoints = Math.min(Math.max(Number(body.max_split_points || 4), 1), 10);
  const points = selectSplitPoints(allPoints, maxPoints);
  const diagnostics = [];
  const started = Date.now();

  let results;
  try {
    results = await withTimeout(mapWithConcurrency(points, 2, async (point) => {
      if (Date.now() - started > SPLIT_TIMEOUT_MS) {
        return {status: 'timeout', split_station: point.station};
      }
      try {
        return await analyzeSplitPoint(client, profileName, point, journey, body);
      } catch (error) {
        return {status: 'failed', split_station: point.station, error: errorText(error)};
      }
    }), SPLIT_TIMEOUT_MS, 'complete split analysis');
  } catch (error) {
    return {
      status: 'failed',
      method: 'db-vendo-direct-two-ticket',
      profile: profileName,
      reason: errorText(error),
      original_price: originalMoney.price,
      currency: originalMoney.currency,
      checked_split_points: points.length,
      available_split_points: allPoints.length,
      split_options: [],
      diagnostics: [{status: 'timeout', error: errorText(error)}],
    };
  }

  const splitOptions = [];
  for (const result of results) {
    if (result?.status === 'ok') {
      const savings = Math.round((originalMoney.price - result.total_price) * 100) / 100;
      if (savings > 0) splitOptions.push({...result, savings});
      else diagnostics.push({status: 'not_cheaper', split_station: result.split_station, total_price: result.total_price});
    } else {
      diagnostics.push(result);
    }
  }
  splitOptions.sort((a, b) => b.savings - a.savings || a.total_price - b.total_price);

  return {
    status: splitOptions.length ? 'success' : 'no_saving_found',
    method: 'db-vendo-direct-two-ticket',
    profile: profileName,
    original_price: originalMoney.price,
    currency: originalMoney.currency,
    checked_split_points: points.length,
    available_split_points: allPoints.length,
    limited: allPoints.length > points.length,
    split_options: splitOptions.slice(0, 5),
    diagnostics,
  };
}

async function runSelfTests() {
  if (PROFILE_ORDER.join(',') !== 'dbnav,db') {
    throw new Error(`self-test profile order failed: ${PROFILE_ORDER.join(',')}`);
  }
  if (terminalCompatibility('Flughafen BER - Terminal 1-2', 'Flughafen BER - Terminal 5 [Bus Terminal]') >= 0) {
    throw new Error('self-test terminal mismatch was not rejected');
  }
  if (terminalCompatibility('Flughafen BER - Terminal 1-2', 'Flughafen BER - Terminal 1') <= 0) {
    throw new Error('self-test terminal overlap was not preferred');
  }
  if (airportIdentityCompatible('Flughafen BER - Terminal 1-2', 'Flughafen Köln/Bonn Terminal 1, Köln')) {
    throw new Error('self-test BER was confused with Köln/Bonn');
  }
  if (airportIdentityCompatible('Flughafen BER - Terminal 1-2', 'Frankfurt (Main) Flughafen Terminal 2')) {
    throw new Error('self-test BER was confused with Frankfurt');
  }
  if (!airportIdentityCompatible('Flughafen BER - Terminal 1-2', 'Flughafen BER - Terminal 1-2')) {
    throw new Error('self-test BER exact airport identity was rejected');
  }
  const airportResolverClient = {
    locations: async () => [
      {type: 'station', id: 'cgn-t1', name: 'Flughafen Köln/Bonn Terminal 1, Köln'},
      {type: 'station', id: 'fra-t2', name: 'Frankfurt (Main) Flughafen Terminal 2'},
      {type: 'station', id: 'ber-t12', name: 'Flughafen BER - Terminal 1-2'},
    ],
  };
  const resolvedBer = await resolveLocation(airportResolverClient, 'Flughafen BER - Terminal 1-2', 'self-test');
  if (resolvedBer.id !== 'ber-t12') {
    throw new Error(`self-test BER resolver selected wrong airport: ${JSON.stringify(resolvedBer)}`);
  }
  const wrongAirportOnlyClient = {
    locations: async () => [
      {type: 'station', id: 'cgn-t1', name: 'Flughafen Köln/Bonn Terminal 1, Köln'},
      {type: 'station', id: 'fra-t2', name: 'Frankfurt (Main) Flughafen Terminal 2'},
    ],
  };
  let wrongAirportRejected = false;
  try {
    await resolveLocation(wrongAirportOnlyClient, 'Flughafen BER - Terminal 1-2', 'self-test');
  } catch (error) {
    wrongAirportRejected = error?.status === 404;
  }
  if (!wrongAirportRejected) {
    throw new Error('self-test wrong-airport-only resolver did not fail closed');
  }
  const ticketMoney = bestMoney({
    tickets: [{name: "Erwachsenenpreis", priceObj: {amount: 5499, currency: 'EUR'}}],
  });
  if (ticketMoney.price !== 54.99) {
    throw new Error(`self-test ticket cents failed: ${ticketMoney.price}`);
  }
  const mixedTicketMoney = bestMoney({
    tickets: [
      {name: "Teilpreis", priceObj: {amount: 999, currency: 'EUR'}, partialFare: true},
      {name: "Erwachsenenpreis", priceObj: {amount: 5499, currency: 'EUR'}, partialFare: false},
    ],
  });
  if (mixedTicketMoney.price !== 54.99 || mixedTicketMoney.partialFare) {
    throw new Error(`self-test full fare preference failed: ${JSON.stringify(mixedTicketMoney)}`);
  }
  const classifiedFare = bestMoney({
    price: {amount: 3.50, currency: "EUR"},
    tickets: [{name: "Brandenburg-Berlin-Ticket Nacht", priceObj: {amount: 2700, currency: "EUR"}, firstClass: false}],
  });
  if (classifiedFare.price !== null) {
    throw new Error(`self-test regional group fare was exposed as regular adult fare: ${JSON.stringify(classifiedFare)}`);
  }
  const unclassifiedFare = bestMoney({tickets: [{name: "from", priceObj: {amount: 350, currency: "EUR"}}]});
  if (unclassifiedFare.price !== null) {
    throw new Error(`self-test unclassified reduced fare was exposed: ${JSON.stringify(unclassifiedFare)}`);
  }


  const journey = {
    legs: [
      {
        tripId: 'trip-a',
        line: {name: 'ICE 100'},
        origin: {id: 'start', name: 'Start'},
        destination: {id: 'split', name: 'Split'},
        plannedDeparture: '2030-08-15T06:00:00+02:00',
        plannedArrival: '2030-08-15T07:00:00+02:00',
        stopovers: [],
      },
      {
        tripId: 'trip-b',
        line: {name: 'RE 200'},
        origin: {id: 'split', name: 'Split'},
        destination: {id: 'end', name: 'End'},
        plannedDeparture: '2030-08-15T07:10:00+02:00',
        plannedArrival: '2030-08-15T08:00:00+02:00',
        stopovers: [],
      },
    ],
  };
  const points = extractSplitPoints(journey);
  if (points.length !== 1 || !points[0].transferPoint || points[0].station.id !== 'split') {
    throw new Error(`self-test transfer point failed: ${JSON.stringify(points)}`);
  }
  const expected = expectedSignatureForPoint(journey, points[0], 'second');
  if (!candidateMatchesExpected({legs: [journey.legs[1]]}, expected)) {
    throw new Error('self-test journey signature failed');
  }
  if (candidateMatchesExpected({legs: [journey.legs[0]]}, expected)) {
    throw new Error('self-test false journey match failed');
  }

  const originalDb = clients.db;
  const originalDbnav = clients.dbnav;
  const calls = [];
  const makeClient = (profileName, {failJourneys = false, priceCents = 5499} = {}) => ({
    locations: async (query) => [{type: 'station', id: query === 'Start' ? 'start' : 'end', name: query}],
    journeys: async () => {
      calls.push(`${profileName}:journeys`);
      if (failJourneys) throw new Error(`${profileName} intentionally failed`);
      return {
        journeys: [{
          refreshToken: `${profileName}-refresh`,
          legs: [{
            tripId: `${profileName}-trip`,
            line: {name: 'ICE 100', product: 'national'},
            origin: {id: 'start', name: 'Start'},
            destination: {id: 'end', name: 'End'},
            plannedDeparture: '2030-08-15T06:00:00+02:00',
            plannedArrival: '2030-08-15T08:00:00+02:00',
            stopovers: [],
          }],
        }],
      };
    },
    refreshJourney: async () => ({
      journey: {
        refreshToken: `${profileName}-refresh`,
        tickets: [{name: "Erwachsenenpreis", priceObj: {amount: priceCents, currency: 'EUR'}, partialFare: false}],
        legs: [{
          tripId: `${profileName}-trip`,
          line: {name: 'ICE 100', product: 'national'},
          origin: {id: 'start', name: 'Start'},
          destination: {id: 'end', name: 'End'},
          plannedDeparture: '2030-08-15T06:00:00+02:00',
          plannedArrival: '2030-08-15T08:00:00+02:00',
          stopovers: [],
        }],
      },
    }),
  });

  try {
    calls.length = 0;
    clients.dbnav = {
      locations: async (query) => [{type: 'station', id: query === 'Start' ? 'start' : 'end', name: query}],
      journeys: async (_origin, _destination, options) => {
        if (!options.departure || options.arrival) throw new Error('window self-test lost departure-based query');
        return {journeys: [
          {legs: [{
            tripId: 'regional-ok', line: {name: 'RE 1', product: 'regional'},
            origin: {id: 'start', name: 'Start'}, destination: {id: 'end', name: 'End'},
            plannedDeparture: '2030-08-15T06:30:00+02:00', plannedArrival: '2030-08-15T07:30:00+02:00', stopovers: [],
          }]},
          {legs: [{
            tripId: 'regional-late', line: {name: 'RE 2', product: 'regional'},
            origin: {id: 'start', name: 'Start'}, destination: {id: 'end', name: 'End'},
            plannedDeparture: '2030-08-15T10:30:00+02:00', plannedArrival: '2030-08-15T12:30:00+02:00', stopovers: [],
          }]},
        ]};
      },
      refreshJourney: async (journey) => ({journey}),
    };
    clients.db = makeClient('db', {priceCents: 9999});
    const bounded = await searchJourneys({
      origin: 'Start', destination: 'End', departure: '2030-08-15T06:00:00+02:00',
      arrival_before: '2030-08-15T11:45:00+02:00', mode: 'deutschlandticket', results: 8, with_prices: false,
    });
    if (bounded.journeys.length !== 1 || bounded.journeys[0]?.id?.includes('regional-late')) {
      throw new Error(`self-test bounded departure window failed: ${JSON.stringify(bounded)}`);
    }

    calls.length = 0;
    clients.dbnav = makeClient('dbnav', {priceCents: 4299});
    clients.db = makeClient('db', {priceCents: 9999});
    const primary = await searchJourneys({
      origin: 'Start', destination: 'End', departure: '2030-08-15T06:00:00+02:00',
      mode: 'all', results: 2, with_prices: true,
    });
    if (primary.source !== 'dbnav' || primary.journeys[0]?.price !== 42.99) {
      throw new Error(`self-test dbnav primary failed: ${JSON.stringify(primary)}`);
    }
    if (calls.some((call) => call.startsWith('db:'))) {
      throw new Error(`self-test db was called despite priced dbnav result: ${JSON.stringify(calls)}`);
    }
    if (primary.journeys[0]?.analysis_profile !== 'dbnav') {
      throw new Error(`self-test primary profile token failed: ${JSON.stringify(primary.journeys[0])}`);
    }

    calls.length = 0;
    clients.dbnav = makeClient('dbnav', {failJourneys: true});
    clients.db = makeClient('db', {priceCents: 5499});
    const fallback = await searchJourneys({
      origin: 'Start', destination: 'End', departure: '2030-08-15T06:00:00+02:00',
      mode: 'all', results: 2, with_prices: true,
    });
    if (fallback.source !== 'db' || fallback.journeys[0]?.price !== 54.99) {
      throw new Error(`self-test db fallback failed: ${JSON.stringify(fallback)}`);
    }
    if (fallback.attempts.map((attempt) => attempt.profile).join(',') !== 'dbnav,db') {
      throw new Error(`self-test fallback order failed: ${JSON.stringify(fallback.attempts)}`);
    }
    if (fallback.journeys[0]?.analysis_profile !== 'db') {
      throw new Error(`self-test fallback profile token failed: ${JSON.stringify(fallback.journeys[0])}`);
    }

    calls.length = 0;
    dbnavBlockedUntil = Date.now() + 5000;
    clients.dbnav = makeClient('dbnav', {priceCents: 4299});
    clients.db = makeClient('db', {priceCents: 5499});
    const circuitFallback = await searchJourneys({
      origin: 'Start', destination: 'End', departure: '2030-08-15T06:00:00+02:00',
      mode: 'all', results: 2, with_prices: true,
    });
    if (circuitFallback.source !== 'db' || circuitFallback.attempts[0]?.skipped !== true) {
      throw new Error(`self-test dbnav circuit fallback failed: ${JSON.stringify(circuitFallback)}`);
    }
    if (calls.some((call) => call.startsWith('dbnav:'))) {
      throw new Error(`self-test dbnav circuit still called dbnav: ${JSON.stringify(calls)}`);
    }
    dbnavBlockedUntil = 0;

    const blockedRefreshClient = {
      refreshJourney: async () => {
        const error = new Error('OPS_BLOCKED');
        error.code = 'OPS_BLOCKED';
        throw error;
      },
    };
    await refreshJourneySafely(
      blockedRefreshClient,
      'dbnav',
      {refreshToken: 'blocked-refresh', legs: [{plannedDeparture: '2030-08-15T06:00:00+02:00'}]},
      {with_prices: true},
    );
    if (!dbnavCircuitOpen()) {
      throw new Error('self-test refresh OPS_BLOCKED did not open dbnav circuit');
    }
    dbnavBlockedUntil = 0;
  } finally {
    clients.db = originalDb;
    clients.dbnav = originalDbnav;
  }

  console.log('db-api self-tests: OK (dbnav -> db, refresh prices, block circuit, curl_cffi session context)');
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url === '/health') {
      cleanupCache();
      return send(res, 200, {
        status: 'ok',
        service: 'dbnav-db-search-and-split',
        cache_entries: journeyCache.size,
        profile_order: PROFILE_ORDER,
        dbnav_circuit_open: dbnavCircuitOpen(),
        dbnav_active_requests: dbnavActive,
        dbnav_max_concurrency: DBNAV_MAX_CONCURRENCY,
        capabilities: ['dbnav-first', 'db-fallback', 'prices-via-refreshJourney', 'split-ticket', 'deutschlandticket', 'arrival-search', 'bestprice', 'non-optimal-routes', 'curl-cffi-firefox-pool', 'fingerprint-session-sticky', 'dbnav-block-circuit-breaker', 'dbnav-concurrency-gate'],
      });
    }
    if (req.method === 'POST' && req.url === '/search') {
      const body = await readJson(req);
      const sessionKey = crypto.randomUUID();
      const result = await cffiSessionStorage.run(
        {sessionKey},
        () => searchJourneys(body),
      );
      return send(res, 200, result);
    }
    if (req.method === 'GET' && req.url.startsWith('/locations?')) {
      const query = new URL(req.url, 'http://localhost').searchParams.get('q')?.trim();
      if (!query || query.length < 2) throw new HttpError(400, 'q must contain at least two characters');
      const lists = await Promise.allSettled(PROFILE_ORDER.map(async (profileName) => {
        const client = clients[profileName];
        const items = await withTimeout(client.locations(query, {
          results: 20, stops: true, addresses: false, poi: false, language: 'de',
        }), REQUEST_TIMEOUT_MS, `${profileName} location suggestions`);
        return (Array.isArray(items) ? items : []).filter((item) =>
          (item?.type === 'station' || item?.type === 'stop') && item.id
        ).map((item) => ({
          provider: 'db', provider_id: String(item.id), name: item.name || String(item.id),
          latitude: item.location?.latitude ?? null, longitude: item.location?.longitude ?? null,
          is_station: item.type === 'station',
        }));
      }));
      const seen = new Set();
      const locations = [];
      for (const settled of lists) {
        if (settled.status !== 'fulfilled') continue;
        for (const item of settled.value) {
          if (seen.has(item.provider_id)) continue;
          seen.add(item.provider_id); locations.push(item);
        }
      }
      return send(res, 200, {locations: locations.slice(0, 20)});
    }
    if (req.method === 'POST' && req.url === '/split') {
      const body = await readJson(req);
      const cached = journeyCache.get(String(body.analysis_token || ''));
      const sessionKey = cached?.context?.cffiSessionKey || crypto.randomUUID();
      const result = await cffiSessionStorage.run(
        {sessionKey},
        () => splitAnalyze(body),
      );
      return send(res, 200, result);
    }
    return send(res, 404, {status: 'error', error: 'not found'});
  } catch (error) {
    console.error(error);
    const status = Number.isInteger(error?.status) ? error.status : 502;
    return send(res, status, {
      status: 'error',
      error: errorText(error),
      details: error?.details,
    });
  }
});

if (process.env.REISEVERGLEICH_SELF_TEST === '1') {
  await runSelfTests();
} else {
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`db-api listening on :${PORT}`);
  });
}

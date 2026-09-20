from reisevergleich import trvl
from reisevergleich.airports import AIRPORT_PROVIDER_QUERIES, provider_location_query

# Suchtexte für deutsche Flughäfen: vorher wurde für BER „<Stadt> Airport BER“ gesucht (nie gefunden).
assert trvl._airport_query("BER", "Berlin Hbf") == "Berlin Brandenburg Airport"
assert trvl._airport_query("fra", "Frankfurt") == "Frankfurt Flughafen"
assert trvl._airport_query("STR", "Stuttgart Hbf") == "Flughafen/Messe"
assert trvl._airport_query("FCO", "Rom") == "Rome Fiumicino Airport FCO", "ausländische Flughäfen bleiben"
assert trvl._airport_query("XYZ", "Irgendwo") == "Irgendwo Airport XYZ"
assert AIRPORT_PROVIDER_QUERIES["BER"] == "Berlin Brandenburg Airport" and AIRPORT_PROVIDER_QUERIES["STR"] == "Flughafen/Messe"
assert provider_location_query("BER") == "Berlin Brandenburg Airport" and provider_location_query("Leipzig Hbf") == "Leipzig Hbf"

# DB-Schreibweise mit Klammern heißt bei Transitous „Frankfurt(Main) Hbf“ (Leerzeichen nach der Klammer).
assert trvl._transitous_station_query("Frankfurt(Main)Hbf") == "Frankfurt(Main) Hbf"
assert trvl._transitous_station_query("Frankfurt (Main) Hbf") == "Frankfurt (Main) Hbf"
assert trvl._transitous_station_query("Berlin Hbf") == "Berlin Hbf" and trvl._transitous_station_query("Halle (Saale) Hbf") == "Halle (Saale) Hbf"
assert trvl._transitous_station_query("") == ""
print("Transitous-Suchtexte für Flughäfen und DB-Bahnhöfe: OK")

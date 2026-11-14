import os

# Die Tests dürfen weder den Verspätungsindex aus dem Netz aufbauen noch Preise in den Datenordner schreiben;
# die Tests dafür schalten beides selbst und mit eigenen Dateien ein.
os.environ.setdefault("DELAY_INDEX", "0")
os.environ.setdefault("PRICE_HISTORY", "0")

# Supplemental notice HTTP/PDF sources are exercised with controlled fixtures.
os.environ.setdefault("SERVICE_NOTICES_ENABLED", "0")

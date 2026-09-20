import os

# Die Tests dürfen weder den Verspätungsindex aus dem Netz aufbauen noch Preise in den Datenordner schreiben;
# die Tests dafür schalten beides selbst und mit eigenen Dateien ein.
os.environ.setdefault("DELAY_INDEX", "0")
os.environ.setdefault("PRICE_HISTORY", "0")

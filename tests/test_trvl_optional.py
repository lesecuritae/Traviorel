import asyncio

from reisevergleich import trvl, utils

# 1) Abgeschaltetes trvl: kein Prozess wird gestartet.
utils.TRVL_ENABLED = False
blocked = utils.run_command([utils.TRVL_BIN, "flights", "BER", "FCO"], 5)
assert blocked["ok"] is False and blocked["code"] == 127 and "abgeschaltet" in blocked["stderr"]
assert utils.run_command(["true"], 5)["ok"] is True, "andere Programme laufen weiter"

# 2) Anbieter mit eigenem Weg laufen weiter, Anbieter nur über trvl werden übersprungen.
trvl.TRVL_ENABLED = False
calls = []


async def native_flix():
    calls.append("flix")
    return {"ok": True, "data": {"routes": [{"provider": "flixbus", "type": "bus", "price": 9.99, "currency": "EUR",
                                             "duration_minutes": 200, "departure": {"time": "2030-01-01T08:00:00+01:00"},
                                             "arrival": {"time": "2030-01-01T11:20:00+01:00"}}]}}


def command(provider):
    raise AssertionError("ohne trvl darf kein trvl-Befehl gebaut werden")


routes, statuses = asyncio.run(trvl._provider_ground_commands(
    ("transitous", "taxi", "flixbus"), command, timeout=5, concurrency=2, compact_limit=5, native={"flixbus": native_flix},
))
assert calls == ["flix"] and [r["provider"] for r in routes] == ["flixbus"]
by_provider = {status["provider"]: status for status in statuses}
assert by_provider["flixbus"]["ok"] is True and by_provider["flixbus"]["result_count"] == 1
assert by_provider["taxi"]["skipped"] is True and by_provider["taxi"]["ok"] is False

# 3) Fällt der eigene Weg aus, greift bei aktivem trvl der trvl-Befehl.
trvl.TRVL_ENABLED = True
utils.TRVL_ENABLED = True
ran = []


async def native_broken():
    return {"ok": False, "error": "Netz weg"}


async def fake_run(provider, cmd, timeout, semaphore):
    ran.append(provider)
    return provider, {"ok": False, "error": "trvl-Test"}, 0.0


original = trvl._run_provider_json
trvl._run_provider_json = fake_run
routes, statuses = asyncio.run(trvl._provider_ground_commands(
    ("flixbus",), lambda provider: ["trvl", provider], timeout=5, concurrency=1, compact_limit=5, native={"flixbus": native_broken},
))
trvl._run_provider_json = original
assert ran == ["flixbus"] and routes == []

# 4) Fähigkeitsbericht ohne trvl.
trvl.TRVL_ENABLED = False
assert asyncio.run(trvl.capability_report())["enabled"] is False
print("trvl ist optional: Direktwege laufen, trvl-only-Anbieter werden übersprungen: OK")

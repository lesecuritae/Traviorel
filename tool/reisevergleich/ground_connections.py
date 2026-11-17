from __future__ import annotations

from typing import Any


def _signature_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _signature_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_signature_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_signature_value(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def connection_signature(route: dict[str, Any]) -> tuple[Any, ...]:
    """Identify one complete connection independently of provider result IDs."""
    legs = route.get("legs") if isinstance(route.get("legs"), list) else []
    leg_signature = tuple(
        (
            _signature_value(leg.get("departure")),
            _signature_value(leg.get("arrival")),
            _signature_value(leg.get("origin")),
            _signature_value(leg.get("destination")),
            _signature_value(leg.get("mode")),
            _signature_value(leg.get("line")),
        )
        for leg in legs
        if isinstance(leg, dict)
    )
    modes = tuple(
        (_signature_value(leg.get("mode")), _signature_value(leg.get("line")))
        for leg in legs
        if isinstance(leg, dict)
    )
    return (_signature_value(route.get("departure")), _signature_value(route.get("arrival")), modes, leg_signature)


def complete_connections(
    component: dict[str, Any],
    deutschlandticket_routes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge complete provider journeys and attach criteria without duplication."""
    recommendation = component.get("recommendation") if isinstance(component.get("recommendation"), dict) else {}
    cheapest = recommendation.get("cheapest_with_live_price")
    fastest = recommendation.get("fastest")
    cheapest_signature = connection_signature(cheapest) if isinstance(cheapest, dict) else None
    fastest_signature = connection_signature(fastest) if isinstance(fastest, dict) else None

    visible = component.get("visible_options") or []
    if visible:
        candidates: list[tuple[dict[str, Any], bool]] = [
            (route, False) for route in visible if isinstance(route, dict)
        ]
    else:
        candidates = []
        for key in ("db_options", "flix_options"):
            candidates.extend((route, False) for route in component.get(key) or [] if isinstance(route, dict))
    visible_signatures = {connection_signature(route) for route in visible if isinstance(route, dict)}
    candidates.extend(
        (route, True) for route in deutschlandticket_routes or []
        if isinstance(route, dict) and (not visible or connection_signature(route) in visible_signatures)
    )
    if not visible:
        for route in (cheapest, fastest):
            if isinstance(route, dict):
                candidates.append((route, False))

    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for route, covered in candidates:
        signature = connection_signature(route)
        if signature not in merged:
            merged[signature] = {**route, "labels": []}
            order.append(signature)
        item = merged[signature]
        for key, value in route.items():
            if item.get(key) in (None, "", []) and value not in (None, "", []):
                item[key] = value
        labels = item["labels"]
        provider = str(item.get("provider") or "").strip()
        if provider and provider not in labels:
            labels.append(provider)
        if covered:
            item["deutschlandticket_covered"] = True
            item["additional_ticket_cost"] = 0.0
            if "D-Ticket" not in labels:
                labels.append("D-Ticket")
        if signature == cheapest_signature and "Günstigste" not in labels:
            labels.append("Günstigste")
        if signature == fastest_signature and "Schnellste" not in labels:
            labels.append("Schnellste")

    return [merged[signature] for signature in order]

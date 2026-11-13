"""Provider-independent checks for explicit ground transport selections."""
from typing import Any, Literal

GroundTransportMode = Literal["all", "suburban", "bus", "local_train"]


def route_matches_transport(route: dict[str, Any], transport_mode: GroundTransportMode) -> bool:
    """Accept selected rides and walking transfers, never mixed or unknown rides."""
    if transport_mode == "all":
        return True
    accepted = {"suburban": {"suburban"}, "bus": {"bus", "coach"},
                "local_train": {"suburban", "regional", "regionalexpress", "regional_rail"}}[transport_mode]
    rides = []
    for leg in route.get("legs") or []:
        if not isinstance(leg, dict):
            return False
        mode = str(leg.get("mode") or leg.get("type") or "").casefold()
        if leg.get("walking") or mode in {"walk", "walking", "foot"}:
            continue
        rides.append(leg)
        line = leg.get("line") if isinstance(leg.get("line"), dict) else {}
        product = str(leg.get("product") or line.get("product") or "").casefold()
        if (product or mode) not in accepted:
            return False
    return bool(rides)


def is_suburban_route(route: dict[str, Any]) -> bool:
    return route_matches_transport(route, "suburban")


def apply_transport_selection(request: Any) -> Any:
    if request.transport_mode in {"suburban", "local_train"}:
        request.include_train, request.include_bus = True, False
        request.include_flixtrain = request.include_flixbus = False
    elif request.transport_mode == "bus":
        request.include_train, request.include_bus = False, True
        request.include_flixtrain = False
    return request

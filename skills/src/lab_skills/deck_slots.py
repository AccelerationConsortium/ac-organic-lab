"""Deck-slot vocabulary resolver — one shelf, three names, one key on the wire.

An OT-2 deck slot is named three ways in this lab and every writer used to be
left to guess which one a given argument wanted:

* the location registry (``locations.yaml``) says ``ot2_hte/slot_2``;
* the OT-2 gateway's arguments want the bare key ``"2"`` — ``tips.reset`` /
  ``tips.mark`` ``slot``, ``move_labware`` ``new_location``, ``setup``
  ``labware[].location``, the keys of ``deck.declare`` ``slots``;
* the xArm graph calls the same shelf ``opentrons_2_low`` / ``opentrons_2_high``.

A string in the wrong vocabulary passes the catalog's Pydantic schema (it is a
``str``) and is refused by the gateway, which latches the robot into ``error``
until an operator clears it. So the translation belongs in the SDK, where
every writer passes through (ARCHITECTURE.md decision #1: workflows and
agents through ``lab-skills``, the dashboard assistant through ``lab-control``
which imports this module), not in any one caller's prompt.

What this module does — and does not — do:

* :func:`canonicalize_slot_args` rewrites every slot-carrying argument of a
  liquid-handler skill to the device's own key, accepting ``2``, ``"slot 2"``,
  ``slot_2``, ``ot2_hte/slot_2`` or another device's alias for the same place.
  A token naming a place on a *different* device is refused
  (``wrong_device_location``); a registry place with several keys on this
  device is refused (``ambiguous_location``); a token the registry does not
  know passes through **unchanged** — the resolver never invents a slot, the
  schema and the device stay the authority.
* It is vocabulary translation on an already-authored step, not custody
  inference. ``docs/PLATE_TRACKING.md``'s rule that aliases are observation-only
  and never infer a move is untouched: nothing here decides *whether*
  something moves, only how the author's spelling of a place is written into
  the argument the device already expects.
* :func:`location_vocabulary` is the map itself, for showing to an agent.
* :func:`touched_slots` names the slots a (canonical) step reaches, for a
  reader that wants to show the operator the deck before they authorize.

``validate_plan`` and ``execute_plan`` call the resolver on every step
(UI_DESIGN.md §5 Step 1m). Direct ``EquipmentClient.command()`` calls carry
no skill name and are **not** canonicalised — write the key there.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .exceptions import LabError
from .locations import LocationEntry, LocationsConfig, load_locations
from .registry import EquipmentEntry

logger = logging.getLogger(__name__)

__all__ = [
    "DECK_TOUCHING_SKILLS",
    "OFF_DECK",
    "SLOT_ARG_SKILLS",
    "SlotResolutionError",
    "canonical_slot",
    "canonicalize_slot_args",
    "default_locations",
    "find_location",
    "location_vocabulary",
    "set_default_locations",
    "touched_slots",
]


class SlotResolutionError(LabError):
    """A slot argument could not be written in the device's vocabulary.

    ``code`` is stable and machine-readable so callers can map it onto their
    own refusal taxonomy (``validate_plan`` emits it as the Violation code,
    lab-control as the ``ProposalRefused`` code):

    * ``invalid_args`` — not a slot at all (``None``, a bool, an empty string);
    * ``wrong_device_location`` — names a place on a different device;
    * ``ambiguous_location`` — a registry place that maps to several keys on
      this device, so no single key can be chosen for the author.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


OFF_DECK = "OFF_DECK"

#: Liquid-handler skills whose arguments carry a deck slot. Every other verb
#: on the surface names labware by nickname (run-engine ids), never by place.
SLOT_ARG_SKILLS = frozenset({"tips.reset", "tips.mark", "move_labware", "setup", "deck.declare"})

#: Liquid-handler skills that use or change the deck — the ones a reader
#: should show the operator the deck for. Lifecycle, lights, plate/well
#: bookkeeping and the temperature module do not touch a slot.
DECK_TOUCHING_SKILLS = SLOT_ARG_SKILLS | frozenset(
    {"pick_up_tip", "aspirate", "dispense", "drop_tip", "move_to", "home"}
)

# ``2`` / ``slot 2`` / ``slot_2`` / ``ot2_hte/slot_2`` — the spellings a person
# (or a model paraphrasing one) writes for a deck slot.
_SLOT_RE = re.compile(r"^(?:(?P<eq>[a-z0-9_]+)/)?slot[ _-]?(?P<n>\d{1,2})$")
_BARE_SLOT_RE = re.compile(r"^\d{1,2}$")


# -- the registry the resolver reads ------------------------------------------

_default: LocationsConfig | None = None
_default_loaded = False


def set_default_locations(config: LocationsConfig | None) -> None:
    """Install the registry :func:`default_locations` returns.

    ``None`` resets to the lazy loader (``locations.yaml`` is read again on
    the next call). Hosts that already hold a ``LocationsConfig`` — the
    dashboard's lifespan, lab-control's ``run()`` — install it once; tests
    install a fixture and reset with ``None``.
    """
    global _default, _default_loaded
    _default = config
    _default_loaded = config is not None


def default_locations() -> LocationsConfig:
    """The location registry, loaded lazily once via :func:`load_locations`.

    Without ``locations.yaml`` the resolver still runs syntax-only
    (``slot_2`` -> ``"2"``); it just cannot label places or catch a
    wrong-device *registry* name (the ``<equipment>/slot_N`` form is still
    caught by shape).
    """
    global _default, _default_loaded
    if not _default_loaded:
        _default_loaded = True
        try:
            _default = load_locations()
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "locations.yaml unavailable; deck-slot resolver runs syntax-only: %s", exc
            )
            _default = None
    return _default or LocationsConfig(locations=[])


# -- resolution ---------------------------------------------------------------


def find_location(locations: LocationsConfig, token: str) -> LocationEntry | None:
    """A registry place by canonical name, or by ANY device's alias for it
    (an xArm node id names the same shelf the OT-2 calls slot 2)."""
    loc = locations.by_name(token)
    if loc is not None:
        return loc
    for candidate in locations.locations:
        for eq_id in candidate.aliases:
            if token in candidate.alias_tokens(eq_id):
                return candidate
    return None


def canonical_slot(
    entry: EquipmentEntry, raw: Any, locations: LocationsConfig | None = None
) -> tuple[str, LocationEntry | None]:
    """Translate one slot-ish token into ``entry``'s own deck key.

    Returns ``(key, registry_place_or_None)``. Raises
    :class:`SlotResolutionError` for a token that names a place on a
    different device or a registry place with several keys on this one. A
    token the registry does not know is returned unchanged with ``None``.
    ``move_labware``'s ``OFF_DECK`` is preserved (case-insensitively).
    """
    if raw is None or isinstance(raw, bool):
        raise SlotResolutionError("invalid_args", 'a deck slot must be a string like "2"')
    if isinstance(raw, int):
        token = str(raw)
    elif isinstance(raw, float) and raw.is_integer():
        token = str(int(raw))
    elif isinstance(raw, str):
        token = raw.strip()
    else:
        raise SlotResolutionError(
            "invalid_args", f'a deck slot must be a string like "2", got {type(raw).__name__}'
        )
    if not token:
        raise SlotResolutionError("invalid_args", "a deck slot must not be empty")
    if token.upper() == OFF_DECK:
        return OFF_DECK, None

    locs = locations if locations is not None else default_locations()
    low = token.lower()
    m = _SLOT_RE.match(low)
    if m and m.group("eq") and m.group("eq") != entry.id:
        raise SlotResolutionError(
            "wrong_device_location",
            f"{token!r} is a place on {m.group('eq')!r}, not on {entry.id!r}; "
            f"name one of {entry.id!r}'s own slots",
        )
    if m or _BARE_SLOT_RE.match(low):
        key = str(int(m.group("n") if m else low))
        place = next(
            (loc for loc in locs.for_equipment(entry.id) if key in loc.alias_tokens(entry.id)),
            None,
        )
        return key, place

    place = find_location(locs, low) or find_location(locs, token)
    if place is None:
        return token, None
    if place.equipment != entry.id:
        raise SlotResolutionError(
            "wrong_device_location",
            f"{token!r} is {place.name!r}, a place on {place.equipment!r}, not on "
            f"{entry.id!r}; name one of {entry.id!r}'s own slots",
        )
    keys = place.alias_tokens(entry.id)
    if len(keys) != 1:
        raise SlotResolutionError(
            "ambiguous_location",
            f"{place.name!r} maps to {keys!r} on {entry.id!r}; give the deck slot key directly",
        )
    return keys[0], place


def canonicalize_slot_args(
    entry: EquipmentEntry,
    skill: str,
    args: dict[str, Any] | None,
    locations: LocationsConfig | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rewrite every slot-carrying argument of a liquid-handler skill to the
    device's own key, and report each place the registry knows.

    Returns ``(args, resolved)``. ``resolved`` lists
    ``{"field", "value", "location", "label", "given"?}`` — one per argument
    the registry could name — so a reader can print "OT-2 HTE · slot 2" next to
    ``slot=2``. Only ``liquid_handler`` entries and the skills in
    :data:`SLOT_ARG_SKILLS` are touched; anything else returns its args
    unchanged. Raises :class:`SlotResolutionError` (see :func:`canonical_slot`)
    and, for ``deck.declare``, when two spellings name the same slot.
    """
    out = dict(args or {})
    if entry.kind != "liquid_handler" or not out or skill not in SLOT_ARG_SKILLS:
        return out, []
    locs = locations if locations is not None else default_locations()
    resolved: list[dict[str, Any]] = []

    def note(field: str, given: Any, key: str, place: LocationEntry | None) -> None:
        if place is None:
            return
        item: dict[str, Any] = {
            "field": field,
            "value": key,
            "location": place.name,
            "label": place.label,
        }
        if str(given) != key:
            item["given"] = str(given)
        resolved.append(item)

    if skill in ("tips.reset", "tips.mark") and out.get("slot") is not None:
        key, place = canonical_slot(entry, out["slot"], locs)
        note("slot", out["slot"], key, place)
        out["slot"] = key
    elif skill == "move_labware" and out.get("new_location") is not None:
        key, place = canonical_slot(entry, out["new_location"], locs)
        note("new_location", out["new_location"], key, place)
        out["new_location"] = key
    elif skill == "setup" and isinstance(out.get("labware"), list):
        labware: list[Any] = []
        for i, item in enumerate(out["labware"]):
            if isinstance(item, dict) and item.get("location") is not None:
                key, place = canonical_slot(entry, item["location"], locs)
                note(f"labware[{i}].location", item["location"], key, place)
                item = {**item, "location": key}
            labware.append(item)
        out["labware"] = labware
    elif skill == "deck.declare" and isinstance(out.get("slots"), dict):
        slots: dict[str, Any] = {}
        for raw_key, value in out["slots"].items():
            key, place = canonical_slot(entry, raw_key, locs)
            if key in slots:
                raise SlotResolutionError(
                    "invalid_args",
                    f"deck.declare names slot {key!r} twice ({raw_key!r} and an earlier "
                    "spelling of the same slot); give each slot once",
                )
            note(f"slots.{key}", raw_key, key, place)
            slots[key] = value
        out["slots"] = slots
    return out, resolved


def touched_slots(skill: str, args: dict[str, Any]) -> list[str]:
    """Every deck slot a liquid-handler skill's (already canonicalised)
    arguments name. Read from the arguments, not from registry hits, so a
    slot the registry does not list is still reported."""
    touched: list[str] = []
    if skill in ("tips.reset", "tips.mark") and isinstance(args.get("slot"), str):
        touched.append(args["slot"])
    elif skill == "move_labware" and isinstance(args.get("new_location"), str):
        if args["new_location"] != OFF_DECK:
            touched.append(args["new_location"])
    elif skill == "setup" and isinstance(args.get("labware"), list):
        touched.extend(
            item["location"]
            for item in args["labware"]
            if isinstance(item, dict) and isinstance(item.get("location"), str)
        )
    elif skill == "deck.declare" and isinstance(args.get("slots"), dict):
        touched.extend(str(k) for k in args["slots"])
    return touched


def location_vocabulary(
    entry: EquipmentEntry, locations: LocationsConfig | None = None
) -> list[dict[str, Any]]:
    """The registry's places on ``entry`` (or, for a device that reaches other
    devices' places — an arm — every place it can reach), each with the token
    every device uses for it. The map an agent was never shown."""
    locs = locations if locations is not None else default_locations()
    out: list[dict[str, Any]] = []
    for loc in locs.locations:
        if not loc.active:
            continue
        if loc.equipment == entry.id:
            item: dict[str, Any] = {"name": loc.name, "label": loc.label}
            own = loc.alias_tokens(entry.id)
            if own:
                item["slot"] = own[0] if len(own) == 1 else own
            others = {eq: loc.alias_tokens(eq) for eq in loc.aliases if eq != entry.id}
            if others:
                item["also_known_as"] = others
            out.append(item)
        elif entry.id in loc.aliases:
            out.append(
                {
                    "name": loc.name,
                    "label": loc.label,
                    "on": loc.equipment,
                    "nodes": loc.alias_tokens(entry.id),
                }
            )
    return out

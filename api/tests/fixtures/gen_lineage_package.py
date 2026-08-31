"""Rebuild ``lineage_package_compiler_0_6_0.json`` with bitácora's own compiler.

**This script does not run under this repo's test suite** — it imports
``bitacora``, which lives in another repo and is deliberately not a dependency
here (a runner that could import the issuer's compiler could not honestly claim
to verify its output). It is vendored beside the fixture so regenerating that
file is a command rather than an archaeology exercise::

    uv --directory ../bitacora run --no-sync python \\
        api/tests/fixtures/gen_lineage_package.py \\
        > api/tests/fixtures/lineage_package_compiler_<version>.json

Update ``COMMIT`` / ``DATE`` (and the filename) to match what was run, and
expect ``test_lineage_fixture.py`` to need updating if the shape moved — that
noticing is the entire reason the fixture exists.

Prints the exact object the authorization route publishes —
``{**package.digest_payload(), "warnings": package.warnings}`` — wrapped with
its provenance and the digest bitácora computed. Reads nothing, writes nothing.
"""

import json

from bitacora.compile import COMPILER_VERSION, compile_protocol

COMMIT = "b060cd5"
DATE = "2026-08-31"


def plate(role, labware, wells):
    return {"labware": labware, "rows": 8, "columns": 12, "role": role, "wells": wells}


DOC = {
    "protocol": "lineage_fixture",
    "description": "Two feedstock wells into a reaction plate, well for well.",
    "parameters": {
        "volume_ul": {"type": "number", "default": 50.0,
                      "description": "Transfer volume per well."},
    },
    "substances": {
        "acid_a": {"name": "acetic acid", "cas": "64-19-7", "smiles": "CC(=O)O",
                   "inchikey": "QTBSBXVTEAMEQO-UHFFFAOYSA-N"},
        "acid_b": {"name": "benzoic acid", "cas": "65-85-0", "smiles": "OC(=O)c1ccccc1",
                   "inchikey": "WPYMKLBDIGXBTP-UHFFFAOYSA-N"},
    },
    "plates": {
        "acid_stock": plate("feedstock", "agilent_96_2ml_deep_square",
                            {"A1": {"contents": "acid_a"}, "B1": {"contents": "acid_b"}}),
        "reaction": plate("conditions", "corning_96_wellplate_360ul_flat",
                          {"A1": {"conditions": {"acid": "acid_a"}},
                           "B1": {"conditions": {"acid": "acid_b"}}}),
    },
    "steps": [
        {"step_id": "transfer_acid", "action": "transfer_wells",
         "source": "acid_stock", "dest": "reaction", "mapping": "identity"},
    ],
}

ACTIONS = {
    "transfer_wells": {
        "role": "liquid_handler",
        "for_each_well": True,
        "steps": [
            {"id": "tip", "skill": "pick_up_tip",
             "args": {"pipette": "p300", "labware_nickname": "tips"}},
            {"id": "aspirate", "skill": "aspirate",
             "args": {"pipette": "p300", "volume_ul": "{volume_ul}",
                      "location": {"labware_nickname": "acid_stock", "position": "{well}"}}},
            {"id": "dispense", "skill": "dispense",
             "args": {"pipette": "p300", "volume_ul": "{volume_ul}",
                      "location": {"labware_nickname": "reaction", "position": "{well}"}}},
            {"id": "drop", "skill": "drop_tip", "args": {"pipette": "p300"}},
        ],
    },
}

PARAMETERS = {"volume_ul": 50.0}
BINDINGS = {"acid_stock": "PLT-0007", "reaction": "PLT-0042"}

pkg = compile_protocol(DOC, ACTIONS, PARAMETERS, plate_bindings=BINDINGS)
published = {**pkg.digest_payload(), "warnings": pkg.warnings}
print(json.dumps(
    {
        "_provenance": {
            "generated_by": "bitacora compile_protocol",
            "compiler_version": COMPILER_VERSION,
            "commit": COMMIT,
            "date": DATE,
            "package_digest": pkg.digest,
            "plate_bindings": BINDINGS,
        },
        "package": published,
    },
    indent=2, sort_keys=True))

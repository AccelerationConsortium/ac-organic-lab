# Repo-committed custom labware definitions

Opentrons **schema-2** labware definition JSON files, one per file, named
`<loadName>.json`. This is the **reviewed tier** of the lab's custom-labware
store: definitions here are merged with admin-uploaded ones
(`<data-dir>/labware/` on the dashboard host) and served read-only at
`GET /api/labware`; a repo-committed definition **wins** over an uploaded one
with the same `loadName`, and the API refuses to upload/delete over a
repo-committed name (change these via PR only).

Why review matters: a wrong well depth or footprint crashes a pipette into a
plate. Build candidates with the dashboard's labware builder (`/utils/labware_builder`),
test them, then commit the JSON here once trusted.

Requirements (enforced by `api/app/labware.py::validate_definition` and the
builder UI):

- `schemaVersion: 2`; `parameters.loadName` matching `^[a-z0-9._]+$` and
  containing at least one `_` (the OT-2 gateway parses bare deck-declare
  strings with `_` as load_names).
- `metadata.displayName`; `dimensions` within the OT-2 slot envelope
  (127 × 85.5 × 200 mm); every well inside the footprint; `ordering`
  consistent with `wells`; `parameters.tipLength` when `isTiprack`.
- Manufacturer metadata uses Opentrons schema-2's standard `brand` object:
  `brand.brand` is the vendor/manufacturer, `brand.brandId[]` holds OEM
  part/product numbers, and `brand.links[]` holds HTTP(S) manufacturer
  product pages. Do not add custom top-level metadata keys.

Consumers: the OT-2 control page's deck picker ("Custom" group — declares
intent only) and workflow `setup` plans via lab-skills (the definition rides
as the labware `config` for `protocol.load_labware_from_definition`).

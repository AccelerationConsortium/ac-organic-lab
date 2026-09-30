# Reader results and repeat runs

Absorbance and fluorescence reads from an authorized run are captured in a
separate durable outbox, `reader_measurements.sqlite3`, beside the dashboard
audit database. BitacoraDB remains the scientific record. No device command is
repeated to recover a recording failure.

## Identity before acquisition

Each read must follow a compiled `plate.load` on its reader role. That load's
physical plate HID must match exactly one authorized plate binding. The runner
resolves the existing experiment's samples before any equipment action. Each
requested well needs exactly one sample with `meta.well` and either
`meta.plate_hid`, `meta.physical_plate_hid`, or `meta.container_id` naming the
registered parent plate. A `meta.plate` equal to the actual physical HID is also
accepted. A nominal name such as `reader_plate` alone is insufficient.

This path does not register new plates or invent samples, collection times,
matrices, or chemical contents. Missing or ambiguous sample links refuse before
acquisition. Existing imported samples with explicit physical identity can be
reused without changing their IDs or historical measurements. A plate whose
contents have changed needs the appropriate new sample records; reusing a
barcode alone does not establish that the material is unchanged.

## Capture and publication

- Each successful device response is committed locally, with the compiled step
  and capture time, before proceeding. Saturated wells retain `null` plus their
  over-range flag; malformed responses are retained and visibly refused for
  scientific publication.
- At completion, results from each authored scan step are grouped by reader,
  physical plate, well, technique, excitation, and focal height. Wavelengths
  are ordered; incomplete scans carry `complete: false` and both acquired and
  expected step IDs. Missing points are never filled with zeros.
- Measurements are appended to the existing sample. Their metadata contains
  the physical plate, nominal protocol label, Plan/run/authorization IDs,
  source commit, digest, step IDs, and the original responses and arguments.
- A deterministic measurement HID includes the run identity. A new run creates
  new measurements; a recording retry uses the same frozen IDs and payloads.
  The record layer's `(sample_id, hid)` uniqueness and exact readback handle a
  response lost after a committed POST. No PATCH is used on old science rows.
- A local capture failure stops later steps and remains distinct from a device
  failure. A remote publication failure leaves raw data and frozen payloads
  pending. Publication state is separate from physical run success.

After restart, an acquiring journal entry becomes `interrupted`. Recovery can
publish the retained partial data but cannot resume or replay acquisition.
Back up and restrict this journal like the existing run audit database.

## UI and endpoints

Utilities → Runs shows reader recording state and **Retry saving measurements**.
The retry is available only once execution has stopped. Both routes require
verified identity and project membership; the dashboard requires a browser
session for these operations.

- `GET /api/workflow/runs/{run_id}/measurements`: durable publication status.
- `POST /api/workflow/runs/{run_id}/measurements/retry`: publish retained results
  only; no equipment access.
- `GET /api/workflow/capabilities`: advertises `reader_measurements_v1`.

Bitácora checks the capability before issuing authorizations containing these
reads. Its live Guide endpoint reference reflects the deployed OpenAPI.

The Samples map retains measurement history instead of replacing rows sharing a
nominal well label. Registered container identity resolves imported plate labels
onto the protocol's physical bindings. Measurement selection displays plate and
run identity; missing legacy identity is explicit. Different physical plates,
fluorescence excitation settings, or focal heights are not overlaid as
comparable spectra. Selecting an older measurement actually selects its curve.

## Deployment and verification

Deploy the runner before the Bitácora changes. Preserve the existing registered
samples and measurements; there is no data migration or automatic relabelling.
Preflight the actual sample metadata before a commissioned run. Offline tests
exercise repeat runs, sample ambiguity, saturation, partial scans, transport
failure, restart, authenticated recovery, and the real workflow/record-view API
connections. No equipment is operated by these tests.

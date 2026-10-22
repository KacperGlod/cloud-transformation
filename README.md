# Cloud Transformation

A Python web application for managing and processing point clouds, with a
Potree 1.8 viewer. FastAPI serves the library, accounts, REST API, and viewer
from the same address. SQLAlchemy models store metadata in SQLite; a separate
worker executes the durable processing queue.

## Getting started

Requires Python 3.11 or later and Node.js/npm for building Potree assets.
Create a virtual environment and install the Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install/build the viewer dependencies if `build/potree/potree.js` is missing:

```powershell
npm.cmd install
```

Start the local server:

```powershell
npm.cmd start
```

`npm start` is a compatibility launcher that selects `.venv` and starts
`run.py`. You can also start Python directly:

```powershell
.\.venv\Scripts\python.exe run.py
```

The library and viewer will be available at:

    http://localhost:1234/

If your PowerShell execution policy allows scripts, you can use the standard
`npm install` and `npm start` commands.

Open the application and create your account. The first account adopts existing
directories in `pointclouds/`, without copying or moving their files. Further
accounts start with an empty library. Legacy point clouds default to `private`
unless their `cloud-info.json` explicitly declares `public`.

## Library, access, and database

The library lists the signed-in user's uploads and processing results. It
supports LAS/LAZ/PLY/PTS/PTX uploads, renaming, changing visibility, viewing job logs, and
deleting a cloud's files while retaining its metadata. Cloud names need not be unique.
New clouds have UUID identifiers and directories independent of their names.

Cloud collections are optional, private groups in the library, independent of
saved viewer projects. A cloud may belong to several collections or none.
Use **Nowa kolekcja** to create an empty collection, then select clouds and choose
**Dodaj wybrane**. The **Kolekcja** filter switches between all clouds, clouds
without a collection, or a named collection. **Zarządzaj kolekcją** opens its
membership editor, name field, and delete action. Search and status filters
continue to apply within the selected collection.

The editor suggests other clouds connected to any current member through source
and result relationships, including ancestors, descendants, and siblings in the
same processing family. Suggestions show which member they relate to. They are
restricted to the owner's library, exclude existing members and deleted clouds,
and never add anything automatically. Retained deleted source records can still
connect surviving relatives. New processing results become suggestions as the
library refreshes. An empty collection has no suggestions; any cloud can be added
manually, including pending uploads and unrelated clouds.

**Odłącz** removes only the membership. Deleting a collection leaves cloud files,
jobs, and saved viewer projects intact. Deleted clouds disappear from membership
lists and counts. Migration `0005` creates `cloud_collections` and
`collection_clouds`, with a composite membership primary key and cascading
membership cleanup. Existing clouds are not assigned to any collection.

Click a cloud's name to open **Informacje o chmurze**, a drawer on the right of
the library. The viewer opens the same drawer with **ⓘ** beside the cloud
selector; it describes the currently loaded cloud. The panel shows file and
view formats, size, dates, point count, XYZ bounds, available viewer attributes,
and declared CRS/units. Owners also see the original file name, immediate source
cloud (including a retained record of a deleted source), and saved operation
parameters/results. Public access does not expose private provenance or operation
parameters. **↻** refreshes the information; Escape or **×** closes the panel.
The drawer has **Informacje** and **Kolekcje** tabs. **Kolekcje** lists the
selected cloud's current collection memberships, with a count in the tab and an
explicit empty state for unassigned clouds. Memberships are visible only to the
owner, including for public clouds. Refresh preserves the selected tab and reads
current names and memberships. Arrow keys, Home, and End navigate between tabs.
Click a collection name to close the drawer and filter the library to that
collection, clearing the cloud search and status filter. In the viewer, the same
link opens the filtered library. Collection links use `/?collection={id}` and
can also be opened in a new tab; the selected collection survives a reload.
`/?collection=unassigned` selects clouds without membership. An unavailable
collection falls back to all clouds with an explanatory message.
The info API returns `collections` as `{id, name}` entries for the owner, an
empty list for no membership, or `null` for other viewers. Collections are also
available before point processing finishes. This does not require a new migration.

`GET /api/clouds/{id}/info` reads bounded LAS/LAZ headers and Potree 1.x/2.x JSON
metadata on demand, without decoding points or changing the database schema.
LAS/LAZ bounds take precedence over Potree's padded bounding box. Normalized
PLY/PTS/PTX imports use the working LAS/LAZ for geometry and retain their original
format in the panel. Geometry is unavailable until processing finishes. Missing
or malformed metadata leaves available fields visible with an explanatory notice.
CRS decoding uses pyproj for LAS WKT/GeoTIFF records, EPSG identifiers, and basic
legacy Potree PROJ definitions. Units are never inferred from coordinate values;
a 2D CRS does not define Z units. Unknown CRS/units remain explicitly unknown.

Deletion permanently removes the cloud's original file, working LAS/LAZ, Potree files, and temporary
job directories from the server. Its database record remains with status
`deleted` and a UTC `deleted_at` timestamp, along with job history and links
from derived clouds. Deleted clouds are excluded from the library and cannot
be viewed, downloaded, edited, or processed, including through old public links.
This does not provide an undo or trash feature. Interrupted deletion is finished
on the next application startup; failed file cleanup can be retried.

Use **Pobierz** on a ready cloud to save its preserved LAS/LAZ/PLY/PTS/PTX file to your
computer. Downloads use the cloud's current library name with the file's actual
`.las`, `.laz`, `.ply`, `.pts`, or `.ptx` extension, including after renaming the cloud. Characters that
cannot be used in file names are replaced with underscores. The metadata field
`downloadName` matches the name sent in the download response. Cloud directories
keep their stable identifiers, so duplicate names do not
overwrite other clouds. New processing results also use the assigned name for
their LAS/LAZ file on disk; renaming a cloud later changes the download name
without moving its stored files. Existing results with generated file names
also download under the current library name. Downloads require the owner's
login, including for public clouds. For processing results, this downloads the result's LAS/LAZ
file. The action is disabled while processing or when no source file is
available. Files stream directly from the server with HTTP Range support for
resuming downloads; the browser does not buffer the entire file in JavaScript.

Click a column heading to sort by name, status, visibility, source file size,
or upload date; click again to reverse the order. Names use Polish alphabetical
and natural numeric ordering, and statuses follow their displayed Polish labels.
File sizes use bytes and dates use their full timestamps. The default is newest
first. Sorting is preserved while searching, filtering, and polling for updates.

Active clouds also show a progress bar in the library table's Status column,
with the current job's percentage when available. Queued tasks and stages that
have not reported progress show an indeterminate bar. It updates automatically
and disappears when processing finishes, fails, or is cancelled.

Library row actions have fixed positions, from left to right: **Porównaj**,
**Otwórz**, **Pobierz**, **Edytuj**, **Usuń**. An omitted optional action
leaves its position empty, so the remaining controls stay aligned across rows.

- `private` (default): metadata and files are accessible only to the owner.
- `public`: anyone with the viewer URL can view the converted cloud. Editing,
  processing, job logs, and original-file downloads still require ownership.

The database is `data/app.sqlite3` by default. It contains `User`, `Cloud`,
`Project`, `ProcessingJob`, and login-session records. LAS/LAZ/PLY/PTS/PTX and Potree binaries stay
outside the database. Only dedicated asset directories are served statically;
database files, Python source, tools, and temporary uploads are not web assets.
Converted data is served after authorization with HTTP Range support.

SQLite uses WAL, foreign keys, and a 30-second busy timeout. Database
transactions remain short and never encompass conversion or file uploads.
Alembic migrations run automatically at startup. To run them manually:

```powershell
.\.venv\Scripts\python.exe -m backend.migrate
```

To create a future schema migration after changing the models:

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
```

Review the generated migration before applying it. Models use portable UUID,
JSON, and date types. A future PostgreSQL deployment will additionally require
a PostgreSQL driver, a new `DATABASE_URL`, and migration of existing records;
changing the URL alone does not transfer data. PostgreSQL operation has not
been validated by the SQLite test suite.

Back up both the database and point-cloud files. For a running SQLite database,
use SQLite's backup API rather than copying only the `.sqlite3` file while its
WAL is active. Keep `data/` persistent across deployments and out of Git.

## Worker and configuration

`run.py` starts a separate Python worker automatically. A filesystem lock allows
one processing worker per runtime directory on the same machine. Queued tasks
survive restarts. Interrupted processing restarts from its saved input; a
partial upload is marked failed and must be uploaded again. Deletion is refused
while the cloud is being processed or used as another job's source.

Use **Usuń z kolejki** on a queued job or **Zatrzymaj operację** on a running
job, in the library's task list or the viewer's operation panel. Queued jobs
are cancelled immediately. Running jobs enter `cancelling`; the worker stops
the process it launched, removes incomplete output, and records `cancelled`.
The source cloud is preserved. Pending cancellation survives a worker restart
and is finished instead of requeuing the job. A cancellation that arrives after
successful completion returns 409 and keeps the completed result.

**Ponów po błędzie** (or **Uruchom ponownie** after cancellation) restarts the
same job from the beginning, with its saved parameters, cloud ID and job ID.
It goes to the end of the queue, clears the last error/progress/result, and
appends the previous error and retry timestamp to the retained log. Derived
operations require an accessible, ready source. A complete uploaded original
is retained privately in the job's runtime directory after failure/cancellation;
generated working files and partial Potree output are discarded. Successful
completion or explicit cloud deletion removes that runtime directory. Include
these retained uploads in backups. Incomplete uploads, or older failed imports
whose input was already removed, require uploading again; the UI explains why
retry is unavailable. No database schema migration is needed for these states.

To run the API and worker in separate terminals:

```powershell
.\.venv\Scripts\python.exe run.py --no-worker
.\.venv\Scripts\python.exe -m backend.worker
```

Initialize the database before starting a standalone worker. The current
deployment uses one API instance and one worker on a single machine.

Configuration is read from environment variables:

| Variable | Default / purpose |
| --- | --- |
| `DATABASE_URL` | SQLite at `data/app.sqlite3` |
| `DATA_DIR` | Private database and lock directory: `data/` |
| `POINTCLOUDS_DIR` | Original and converted point clouds: `pointclouds/` |
| `RUNTIME_DIR` | Uploads, worker logs, temporary data: `.runtime/python/` |
| `MAX_UPLOAD_BYTES` | 100 GiB per source file |
| `POTREE_CONVERTER` | Bundled converter executable |
| `LASZIP_LIBRARY` | Bundled `laszip.dll` |
| `PDAL_PATH` | PDAL executable for PLY/PTS/PTX imports; detects `.runtime/pdal/Library/bin/pdal.exe` on Windows, `.runtime/pdal/bin/pdal` elsewhere, then `pdal` on PATH |
| `START_WORKER` | `1`; set `0` for an independently managed worker |
| `SECURE_COOKIES` | `0` for localhost HTTP; use `1` with HTTPS |
| `HOST` / `PORT` | `127.0.0.1` / `1234`; also available as CLI arguments |

Accounts use scrypt password hashes and expiring HttpOnly session cookies.
Signed-in users can choose **Zmień hasło** in the library header. Changing a
password requires the current password and a different new password of 10–256
characters. The browser also asks to repeat the new password. A successful change
revokes all existing sessions and issues a new cookie for the current browser.
The password update and replacement session are committed together; other accounts
are unaffected. Password changes do not require a database migration.
Mutating browser requests must come from the application's own origin.
Keep HTTPS/reverse-proxy configuration aligned with the application's URL.
The bundled processing tools are Windows binaries; other operating systems need
compatible PotreeConverter and LASzip builds before processing is enabled.

## REST API

Interactive API documentation is available at `/docs`; OpenAPI is `/openapi.json`.
Authenticate with `/api/auth/register` or `/api/auth/login`, then retain the
session cookie for subsequent calls.

| Method and path | Purpose |
| --- | --- |
| `GET /api/auth/me`, `POST /api/auth/logout` | Current account and logout |
| `POST /api/auth/password` | Change the signed-in user's password with JSON `currentPassword` and `newPassword`; returns 204 and rotates the session cookie |
| `GET /api/clouds` | Signed-in user's clouds, including pending/failed uploads |
| `POST /api/clouds` | Multipart upload: `file`, `name`, optional `visibility` |
| `GET /api/clouds/{id}` | Cloud metadata (owner or public access) |
| `GET /api/clouds/{id}/info` | Bounded geometry, CRS, viewer attributes, and owner-only source/operation details |
| `GET /api/clouds/{id}/comparison` | Ready result and its immediate source; requires view access to both |
| `GET /api/collections`, `POST /api/collections` | List private collections or create one with `name` and optional `cloudIds` |
| `GET /api/collections/{id}`, `PATCH /api/collections/{id}` | Read membership or rename the owner's collection with `name` |
| `DELETE /api/collections/{id}` | Delete the collection and its memberships while preserving all clouds |
| `POST /api/collections/{id}/clouds` | Atomically add `cloudIds` (1–1000 UUIDs per request); repeat adds are idempotent |
| `DELETE /api/collections/{id}/clouds/{cloudId}` | Idempotently detach one cloud; returns updated collection |
| `GET /api/collections/{id}/suggestions` | Related candidates with `cloudId`, `relatedCloudId`, `relatedCloudName`, and graph `distance` |
| `GET /api/projects`, `POST /api/projects` | List the owner's saved viewer projects (optional `cloudId` filter), or create one with `name`, `cloudId`, and `sceneState` |
| `GET /api/projects/{id}`, `PUT /api/projects/{id}` | Open or update a private project; updates require `name`, `sceneState`, and the current `revision` |
| `DELETE /api/projects/{id}` | Delete a saved project while keeping the cloud and its files |
| `PATCH /api/clouds/{id}` | Owner changes `name` and/or `visibility` |
| `DELETE /api/clouds/{id}` | Owner permanently removes files and marks the retained cloud record deleted |
| `GET /api/clouds/{id}/download` | Owner downloads the preserved LAS/LAZ/PLY/PTS/PTX as an attachment; supports HEAD and Range |
| `GET /api/clouds/{id}/files/{path}` | Authorized files, including Range requests |
| `GET /api/jobs`, `GET /api/jobs/{id}` | Owner's persistent job states and logs |
| `POST /api/jobs/{id}/cancel` | Owner cancels a queued job or requests running-job cancellation; returns the current job (200) |
| `POST /api/jobs/{id}/retry` | Owner restarts a failed/cancelled job with its saved parameters (202); missing input or an incompatible state returns 409 |
| `POST /api/segmentations` | Queue a clipping-box operation |
| `POST /api/transformations` | Queue a coordinate transformation |
| `POST /api/point-removals` | Queue spherical point removal |

`/viewer?cloud={id}` opens a specific cloud. The library and viewer stream large
uploads through `/api/conversions?filename=...&name=...&visibility=private`
using an `application/octet-stream` body, avoiding a second temporary copy
created by multipart parsing. Multipart `/api/clouds` is also supported.
The viewer's existing raw upload and polling endpoints remain compatible. Ready legacy
file URLs under `/pointclouds/` also pass through authorization.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_jobs.py tests/test_projects.py tests/test_ply.py tests/test_scan_text.py tests/test_migrations.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_info.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_collections.py -q
npm.cmd run test:flight
npm.cmd run test:library
npm.cmd run test:comparison
```

API tests use isolated temporary databases and point clouds. They cover account
sessions, ownership, visibility, Range requests, uploads, stable identifiers,
soft deletion, permanent file cleanup, retained job history, legacy imports,
and restart recovery. Migration tests verify upgrades from the previous schema,
metadata consistency, and downgrades without losing existing records.
Collection tests cover optional and shared membership, private access, atomic
batch validation, source/result suggestions, deleted links and cycles, restart
persistence, and preservation of files and viewer projects when removing a collection.
`tests/test_jobs.py` also checks real process termination (including silent tools
with closed stdout), cancellation recovery, owner/origin checks, retained inputs,
queue ordering, retries, concurrent requests, and cancellation during stage changes.
The integration test uses the
bundled converter and LASzip for real import, transformation, segmentation,
and point-removal jobs; it skips when those tools are unavailable.

## WASD free flight

Click the **airplane icon** in the bottom-left corner of the viewer to start
flying immediately. The mouse pointer will be locked, and the camera will switch
to perspective view. The control instructions remain visible throughout the
flight. Movement uses game-style controls:

- **W / S** — fly forward / backward along the viewing direction;
- **A / D** — fly left / right;
- **mouse** — look around without holding a button;
- **Space or E** — fly up;
- **Ctrl or Q** — fly down;
- **Shift** — fly three times faster;
- **mouse wheel** — increase / decrease speed;
- **Esc** — stop flying and restore the previous controls.

Speed is measured in point cloud coordinate units per second. You can move
freely through space without gravity or collisions. Flight also stops when you
switch windows or tabs; click the airplane icon to resume. The instructions
disappear when flight ends. Clicking inside the viewer during flight keeps the
instructions visible; press **Esc** to stop flying and restore normal controls.
Leaving flight keeps the final camera position, viewing direction, and perspective
projection, including when flight stops because the window loses focus. The
previous projection is restored only if flight could not start or was cancelled
before the mouse was locked.
When orbit controls resume, their pivot distance is set from the cloud at the
center of the view, with the active cloud's bounds as a fallback. Looking away
from the cloud retains the preflight orbit distance. Flight speed does not
determine the orbit radius, so rotation and panning work normally immediately
after leaving flight, including after changing flight speed or repeated flights.

## LAS/LAZ/PLY/PTS/PTX import

PotreeConverter should be located at:

```text
tools/PotreeConverter/PotreeConverter.exe
```

After starting the project:

1. Click **Dodaj chmurę** in the library (or open the viewer's import panel).
2. Select a `.las`, `.laz`, `.ply`, `.pts`, or `.ptx` file.
3. Enter a point cloud name and choose its visibility in the library.
4. Click the button to upload and convert the file.

The file is uploaded only to the local server on your computer.
PotreeConverter saves new output in `pointclouds/<uuid>/`. Once ready, open the
cloud from the library. Imports initiated inside the viewer load automatically.
Conversions run one at a time to avoid overloading the computer.
When switching clouds, the camera frames the selected cloud; previously loaded
hidden clouds do not expand its framing bounds.

After a successful conversion, the point cloud directory also contains the
original LAS/LAZ/PLY/PTS/PTX file under its original name and the complete PotreeConverter
output in `conversion.log`. The `cloud-info.json` file stores local information
about the point cloud's origin. The SQL database is authoritative for current
names, ownership, visibility, and processing status.

### PLY, PTS, PTX and PDAL

PLY, PTS, and PTX imports use the PDAL command-line application, tested with PDAL 2.10.2.
The Python PDAL bindings are not required. Install the native runtime once per
machine, for example with [Conda](https://pdal.io/en/stable/download.html#conda)
or micromamba, from the repository root:

```powershell
conda create --yes --prefix .runtime/pdal --override-channels --channel conda-forge libpdal-core=2.10.2
```

The same command works with `micromamba create` in place of `conda create`.
The application detects this local installation without activating it. For an
installation elsewhere, set `PDAL_PATH` to the full path of `pdal.exe` (or `pdal`).
Keep its DLLs/libraries alongside the executable. PDAL, its environment, and
package caches are local dependencies and must not be committed. A server
deployment must provision PDAL as well as PotreeConverter and LASzip.
`GET /api/health` exposes `plyImportAvailable`, `ptsImportAvailable`, and
`ptxImportAvailable`; missing PDAL rejects these imports
with HTTP 503 before a job is created. LAS/LAZ do not require PDAL.

Supported PLY 1.0 encodings are ASCII, binary little endian, and binary big
endian. A nonempty `vertex` element must have scalar `x`, `y`, and `z` fields.
Optional `red`, `green`, and `blue` fields must form a complete RGB set of
`uchar`/`uint8` or `ushort`/`uint16` values. Eight-bit colors are expanded to the
LAS 16-bit range. `intensity` and other standard LAS fields recognized by PDAL
are copied. Vertex list properties are rejected. Mesh faces are ignored: the
viewer displays their vertices as points, not a surface.

The worker streams PLY into a LAS 1.4 LAZ file named `working.laz`, then runs
PotreeConverter. Coordinates use a scale of 0.001 in the input coordinate units
and offsets based on the first point; no reprojection or unit conversion is
performed. Very large extents exceeding the LAS integer range fail explicitly.
Normals and custom properties are retained in the unchanged original PLY but
are not copied to the working LAZ or processing results: the bundled converter
cannot safely handle PDAL extra-byte attributes. PLY metadata is not used to
infer a coordinate reference system.

`Cloud.source_file` identifies the downloadable original, while the nullable
`processing_file` identifies the normalized LAZ. Existing LAS/LAZ records use
their source directly, without rewriting data. Migration `0003` adds this column
automatically on startup. API field `sourceAvailable` controls original downloads;
`processingAvailable` controls viewer processing tools. Both files require the
owner's authorization, even when the converted viewer assets are public.
Processing results remain LAZ, and deleting a cloud permanently removes both
its original and its working file. Interrupted jobs recover both files before
restarting conversion. The working filename is also recorded in `cloud-info.json`
for importing an existing directory.

### PTS and PTX details

PTS is accepted as ASCII text with a positive point count on the first line.
Each subsequent point must use one consistent layout: `X Y Z`, `X Y Z I`,
`X Y Z R G B`, or `X Y Z I R G B`. Whitespace, CRLF, and a UTF-8 BOM on the first
line are supported. Intensity follows the signed PTS range -2048 through 2047;
the working LAZ stores the rounded value plus 2048 (0 through 4095). Colors are
integer values from 0 through 255, expanded to 16-bit LAS RGB. Extra or missing
points, inconsistent layouts, invalid numbers, and out-of-range attributes fail
the import rather than silently dropping data. The original file is unchanged.

PTX supports one or more scan blocks in a file. Each block has column and row
counts, a scanner position and three axes, a 4x4 affine matrix, and exactly
`columns * rows` point records. Layouts are `X Y Z I` or `X Y Z I R G B`, consistent
across all scans in the file. The 4x4 matrix is authoritative and is applied
once to that block's coordinates, using PTX's row-vector convention (translation
in the final matrix row). Singular/non-affine matrices and non-finite values are
rejected. Local `0 0 0` measurements are omitted **before** transforming them,
as missing samples, matching PDAL's default PTX behavior. An import containing
only missing samples fails. Intensity must be between 0 and 1 and is mapped to
rounded values from 0 through 4096, following PDAL's PTX convention. RGB uses the
same 8-to-16-bit mapping as PTS. Skipping missing samples removes their grid
positions from the working cloud; the downloadable PTX retains the entire scan.

The native PDAL 2.10.2 PTS/PTX readers do not support streaming. To bound memory,
`python -m backend.scan_text` validates records incrementally and writes a
temporary binary PLY in batches of 8192 points. PDAL then streams that file to
`working.laz`, using the same normalization as PLY. This requires additional
temporary disk space but does not load the complete cloud into RAM. Disk-space
checks run before normalization and during batch writes. Temporary PLY files
are never published as viewer assets and are removed with the job directory.
No additional database migration or native dependency is required beyond the
PLY setup. The `normalizing` job phase appears as **Przygotowanie** in the library;
recovery restarts preparation from the retained original. Import statistics
(input/output counts, skipped samples, number of scan blocks) are recorded in
the processing job and `cloud-info.json`. A PTX containing multiple stations
becomes one cloud in the library, with the transformed points combined.

## Point cloud segmentation

Segmentation runs locally and requires a point cloud with its original LAS/LAZ
or normalized working LAZ preserved. Points are read and written using
`tools/PotreeConverter/laszip.dll`, and the segmentation script runs with the
application's Python installation.

1. Load the source point cloud.
2. Open the cloud processing panel.
3. Add a clipping box, then adjust its position, rotation, and scale in Potree.
4. Choose whether to keep points inside or outside the clipping box.
5. Enter an output name and start segmentation and conversion.

The server selects points from the original LAS/LAZ file, saves the result as
`pointclouds/<uuid>/<source-file>.laz`, runs PotreeConverter, and automatically loads
the new point cloud. Segmentation also supports rotated clipping boxes.

The output directory contains:

- the resulting LAZ file;
- Potree data (`metadata.json`, `octree.bin`, `hierarchy.bin`);
- `segmentation.log` and `conversion.log`;
- `cloud-info.json` with the source point cloud name, the `inside`/`outside`
  mode, the clipping box transformation, and the number of selected points.

Point clouds without an original LAS/LAZ or normalized working LAZ can still be
viewed, but segmentation is disabled for them.

## Saved viewer projects

Open **Chmury i przetwarzanie → Projekty** in the viewer of an owned, ready cloud.
Enter a project name and choose **Zapisz projekt**. The saved project appears in
**Moje projekty** in the library and in that cloud's project selector. Choose
**Otwórz projekt** to restore it, **Zapisz zmiany** to update it, or **Zapisz kopię**
to keep a separate version. Multiple projects can refer to the same cloud.
Projects also have stable links: `/viewer?project={id}`.

A project saves the camera position, direction, orbit distance, field of view,
and perspective/orthographic mode; display and clipping settings; active-cloud
transform, point coloring, size, shape, opacity and classification visibility;
return-number, source-ID and GPS-time filters;
measurements, box/sphere volumes, profiles, and nested plain-text annotations.
Opening replaces the current viewer tools and reuses the existing cloud cache.
The interface asks before replacing unsaved changes. Save explicitly to persist
work; changing the camera does not automatically overwrite a project.

Projects belong to their creator and are available only on that account, even
when their cloud is public. Creating and updating require an owned, ready cloud.
Renaming a cloud keeps its project links working. Deleting a cloud retains its
projects in the library as unavailable; deleting a project removes only its saved
state. Source and converted point-cloud files are not copied into the project.

Migration `0004` creates `projects` with UUID `id`, `owner_id`, `cloud_id`, `name`,
JSON `scene_state`, UTC timestamps, and a positive integer `revision`. It runs
automatically on startup, or through `python -m backend.migrate`. Back up the
database before deployment as described above. Existing cloud and account data
remain in their current tables.

`POST /api/projects` accepts `name`, UUID `cloudId`, and `sceneState`. The scene
uses `schemaVersion: 1` and a camera with `position`, `yaw`, `pitch`, and positive
`radius`; other scene fields have documented defaults in `/docs`. Responses
include `viewerUrl` and `revision`. `PUT /api/projects/{id}` requires `name`,
`sceneState`, and the last read `revision`; a stale save returns HTTP 409 and
leaves the newer state intact. Owner and cloud cannot be reassigned by an update.

Scene validation rejects non-finite coordinates, unsupported object types and
unknown fields, duplicate object IDs, and cyclic or deeper-than-16 annotation
hierarchies. Scenes are limited to 2 MiB, 1,000 objects per tool collection and
50,000 measurement/profile vertices in total. Viewer restoration uses fixed
constructors and plain text; saved scenes cannot supply URLs or executable HTML.

## Comparing a result with its source

Choose **Porównaj** on a ready processing result in the library, or open the
result in the viewer and choose **Porównaj z oryginałem** in the cloud panel.
`/compare?cloud={result-id}` opens the source on the left and the result on the
right. The source is the immediate input to that operation; for a chain of
operations it may itself be an earlier result.

Rotating, panning, and zooming either panel synchronizes the other camera.
Turn off **Synchronizuj kamery** to navigate independently. **Dopasuj widok**
frames the combined bounds when synchronized, or each cloud separately when
independent. Real coordinates are preserved, so transformations remain visible
instead of being automatically aligned. On narrow screens the panels are
stacked vertically. Each panel has a 500,000-point rendering budget.

Both clouds must be ready and accessible. Publishing a result does not grant
access to its private source. Deleted sources cannot be compared; the result
remains available on its own. Comparison is read-only and does not create or
modify point-cloud files. Two isolated viewer frames reuse Potree without
changing its rendering library; camera messages are restricted to those frames
on the same origin.

## Manual point removal with a brush

The brush tool removes small, irregular regions without a clipping box.
Each click picks an actual point in the cloud and creates a spherical
selection around it with the specified radius.

1. Load a point cloud with its source LAS/LAZ or normalized working LAZ preserved.
2. Set the brush radius in the point removal section.
3. Activate point picking in the viewer, then click the unwanted points.
   Dragging still rotates the view, and `Esc` ends point picking.
4. Reopen the panel. If needed, undo the last selection or clear all selections.
5. Enter an output name and start removal and conversion.

The red spheres are a preview. When saving, the server removes every point
inside at least one selected sphere from the source LAS/LAZ data, writes a new
LAZ file, and runs PotreeConverter. The original file remains unchanged.
`cloud-info.json` records the selection centers and radii, statistics, and
the link to the source point cloud. Full logs are saved in
`point-removal.log` and `conversion.log`.

## Coordinate transformations

Transformations run locally without PDAL. The
`tools/transform_pointcloud.py` script reads and writes points through the
bundled `laszip.dll` library, preserving point attributes and supporting
both LAS and LAZ files.

1. Load a point cloud with its source LAS/LAZ or normalized working LAZ preserved.
2. Open the cloud processing panel.
3. In the transformation section, enter the XYZ translation, XYZ rotation
   in degrees, and XYZ scale.
4. Choose a pivot: the center of the cloud's bounding box, the coordinate
   origin, or custom coordinates.
5. Enter an output name and start transformation and conversion.

Rotations are applied around the X, Y, and Z axes in that order. The server
makes two passes over the points: first, it calculates the new bounds and a
safe LAS header offset; then, it writes the transformed coordinates to a new
LAZ file. The original point cloud remains unchanged.

The output directory contains the resulting LAZ file, Potree data,
`transformation.log`, `conversion.log`, and `cloud-info.json`. The metadata
records the parameters, the actual pivot, input and output bounds, the
coordinate storage scale and offset, and the link to the source point cloud.

This operation transforms coordinates within the current coordinate system.
It does not reproject data between coordinate reference systems, such as
PL-2000 and WGS84.

## Manually replacing a point cloud

Place a converted cloud in its own directory under `pointclouds/` and restart
the application. Newly discovered directories are registered to the first
account. PotreeConverter 1.x output needs `cloud.js`; PotreeConverter 2.x output
needs `metadata.json`, `hierarchy.bin`, and `octree.bin`. Open the registered
cloud from the library. New uploads should normally use the API or upload form.

## Commands

- `npm.cmd start` — starts the Python application, library, API, viewer,
  and separate processing worker.
- `npm.cmd run build` — rebuilds the Potree library.
- `npm.cmd run test:flight` — checks flight controls and mouse wheel zoom
  (requires Node.js 22 or later).
- `npm.cmd run test:library` — checks cloud library column sorting
  (requires Node.js 22 or later).
- `npm.cmd run test:comparison` — checks camera synchronization, independent
  navigation, framing, and invalid comparison messages (requires Node.js 22 or later).
- `npm.cmd run dev` — rebuilds the library and watches its source files;
  start the server separately with `npm.cmd start`.

The server listens only on localhost by default. Temporary uploads and worker
logs are stored in `.runtime/python/`, which is ignored by Git.

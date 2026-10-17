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
supports LAS/LAZ uploads, renaming, changing visibility, viewing job logs, and
deleting a cloud together with its files. Cloud names need not be unique.
New clouds have UUID identifiers and directories independent of their names.

Use **Pobierz** on a ready cloud to save its preserved LAS/LAZ file to your
computer. Downloads keep the original file name and require the owner's login,
including for public clouds. For processing results, this downloads the result's
LAS/LAZ file. The action is disabled while processing or when no source file is
available. Files stream directly from the server with HTTP Range support for
resuming downloads; the browser does not buffer the entire file in JavaScript.

Click a column heading to sort by name, status, visibility, source file size,
or upload date; click again to reverse the order. Names use Polish alphabetical
and natural numeric ordering, and statuses follow their displayed Polish labels.
File sizes use bytes and dates use their full timestamps. The default is newest
first. Sorting is preserved while searching, filtering, and polling for updates.

- `private` (default): metadata and files are accessible only to the owner.
- `public`: anyone with the viewer URL can view the converted cloud. Editing,
  processing, job logs, and original-file downloads still require ownership.

The database is `data/app.sqlite3` by default. It contains `User`, `Cloud`,
`ProcessingJob`, and login-session records. LAS/LAZ and Potree binaries stay
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
| `START_WORKER` | `1`; set `0` for an independently managed worker |
| `SECURE_COOKIES` | `0` for localhost HTTP; use `1` with HTTPS |
| `HOST` / `PORT` | `127.0.0.1` / `1234`; also available as CLI arguments |

Accounts use scrypt password hashes and expiring HttpOnly session cookies.
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
| `GET /api/clouds` | Signed-in user's clouds, including pending/failed uploads |
| `POST /api/clouds` | Multipart upload: `file`, `name`, optional `visibility` |
| `GET /api/clouds/{id}` | Cloud metadata (owner or public access) |
| `PATCH /api/clouds/{id}` | Owner changes `name` and/or `visibility` |
| `DELETE /api/clouds/{id}` | Owner deletes the cloud and its files |
| `GET /api/clouds/{id}/download` | Owner downloads the preserved LAS/LAZ as an attachment; supports HEAD and Range |
| `GET /api/clouds/{id}/files/{path}` | Authorized files, including Range requests |
| `GET /api/jobs`, `GET /api/jobs/{id}` | Owner's persistent job states and logs |
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
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q
npm.cmd run test:flight
npm.cmd run test:library
```

API tests use isolated temporary databases and point clouds. They cover account
sessions, ownership, visibility, Range requests, uploads, stable identifiers,
deletion, legacy imports, and restart recovery. The integration test uses the
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

## LAS/LAZ import

PotreeConverter should be located at:

```text
tools/PotreeConverter/PotreeConverter.exe
```

After starting the project:

1. Click **Dodaj chmurę** in the library (or open the viewer's import panel).
2. Select a `.las` or `.laz` file.
3. Enter a point cloud name and choose its visibility in the library.
4. Click the button to upload and convert the file.

The file is uploaded only to the local server on your computer.
PotreeConverter saves new output in `pointclouds/<uuid>/`. Once ready, open the
cloud from the library. Imports initiated inside the viewer load automatically.
Conversions run one at a time to avoid overloading the computer.

After a successful conversion, the point cloud directory also contains the
original LAS/LAZ file under its original name and the complete PotreeConverter
output in `conversion.log`. The `cloud-info.json` file stores local information
about the point cloud's origin. The SQL database is authoritative for current
names, ownership, visibility, and processing status.

## Point cloud segmentation

Segmentation runs locally and requires a point cloud with its original LAS or
LAZ file preserved. Points are read and written using
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

Point clouds without an original LAS/LAZ file can still be viewed, but
segmentation is disabled for them.

## Manual point removal with a brush

The brush tool removes small, irregular regions without a clipping box.
Each click picks an actual point in the cloud and creates a spherical
selection around it with the specified radius.

1. Load a point cloud with its source LAS/LAZ file preserved.
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

1. Load a point cloud with its source LAS/LAZ file preserved.
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
- `npm.cmd run dev` — rebuilds the library and watches its source files;
  start the server separately with `npm.cmd start`.

The server listens only on localhost by default. Temporary uploads and worker
logs are stored in `.runtime/python/`, which is ignored by Git.

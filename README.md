# Cloud Transformation

A simple, full-screen point cloud viewer based on Potree 1.8. The project
uses `index.html` as its main view, supports LAS/LAZ uploads, and converts
files locally using PotreeConverter.

## Getting started

Install the dependencies:

```powershell
npm.cmd install
```

Start the local server:

```powershell
npm.cmd start
```

The viewer and local conversion API will be available at:

    http://localhost:1234/

If your PowerShell execution policy allows scripts, you can use the standard
`npm install` and `npm start` commands.

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

1. Open the LAS/LAZ upload dialog in the top-right corner.
2. Select a `.las` or `.laz` file.
3. Enter a unique point cloud name.
4. Click the button to upload and convert the file.

The file is uploaded only to the local server on your computer.
PotreeConverter saves the output in `pointclouds/<name>/`, and the viewer
automatically loads the generated `metadata.json` file. Conversions run one
at a time to avoid overloading the computer.

After a successful conversion, the point cloud directory also contains the
original LAS/LAZ file under its original name and the complete PotreeConverter
output in `conversion.log`. The `cloud-info.json` file stores local information
about the point cloud's origin and provides a foundation for future account
and database integration.

## Point cloud segmentation

Segmentation runs locally and requires a point cloud with its original LAS or
LAZ file preserved. Points are read and written using
`tools/PotreeConverter/laszip.dll`, and the segmentation script runs with the
system's Python 3 installation.

1. Load the source point cloud.
2. Open the cloud processing panel.
3. Add a clipping box, then adjust its position, rotation, and scale in Potree.
4. Choose whether to keep points inside or outside the clipping box.
5. Enter a unique output name and start segmentation and conversion.

The server selects points from the original LAS/LAZ file, saves the result as
`pointclouds/<name>/<name>.laz`, runs PotreeConverter, and automatically loads
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
5. Enter a unique output name and start transformation and conversion.

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

1. Place the converted point cloud in the `pointclouds` directory.
2. Open `index.html`.
3. Change the value of `POINT_CLOUD_PATH`:

```js
const POINT_CLOUD_PATH = "./pointclouds/cloud-name/metadata.json";
```

For PotreeConverter 1.x output, point to `cloud.js`. For PotreeConverter 2.x
output, use `metadata.json`.

## Commands

- `npm.cmd start` — starts the local viewer, upload API, segmentation,
  transformations, and PotreeConverter queue.
- `npm.cmd run build` — rebuilds the Potree library.
- `npm.cmd run test:flight` — checks flight controls and mouse wheel zoom
  (requires Node.js 22 or later).
- `npm.cmd run dev` — rebuilds the library and watches its source files;
  start the server separately with `npm.cmd start`.

The server listens only on localhost. Temporary upload files are stored in
the `.runtime` directory, which is ignored by Git.

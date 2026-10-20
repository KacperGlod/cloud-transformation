import * as THREE from "../../libs/three.js/build/three.module.js";

const vector = value => value.toArray();
const rotation = object => [object.rotation.x, object.rotation.y, object.rotation.z];
const measurementFlags = ["showDistances", "showCoordinates", "showArea", "closed", "showAngles",
  "showHeight", "showCircle", "showAzimuth", "showEdges"];
const materialValues = ["size", "minSize", "maxSize", "pointSizeType", "shape", "opacity",
  "rgbGamma", "rgbBrightness", "rgbContrast", "intensityGamma", "intensityBrightness", "intensityContrast"];
const finitePair = (value, fallback) => value?.length === 2 && value.every(Number.isFinite) ? [...value] : fallback;
const transform = object => ({position: vector(object.position), rotation: rotation(object),
  rotationOrder: object.rotation.order, scale: vector(object.scale)});

export function captureProjectScene(viewer, pointcloud) {
  const {scene} = viewer;
  const view = scene.view;
  const material = pointcloud.material;
  const annotations = [];
  scene.annotations.traverseDescendants(annotation => {
    annotations.push({
      id: annotation.uuid, parentId: annotation.parent === scene.annotations ? null : annotation.parent.uuid,
      title: annotation.elTitle.text(), description: annotation.elDescription.find(".annotation-description-content").text(),
      position: vector(annotation.position || annotation.boundingBox.getCenter(new THREE.Vector3())),
      offset: vector(annotation.offset), cameraPosition: annotation.cameraPosition ? vector(annotation.cameraPosition) : null,
      cameraTarget: annotation.cameraTarget ? vector(annotation.cameraTarget) : null,
      radius: Number.isFinite(annotation.radius) && annotation.radius > 0 ? annotation.radius : null,
      visible: annotation.visible,
    });
  });
  const gradient = Object.entries(Potree.Gradients).find(([, value]) => value === material.gradient)?.[0] || "SPECTRAL";
  return {
    schemaVersion: 1,
    camera: {position: vector(view.position), yaw: view.yaw, pitch: view.pitch, radius: view.radius,
      fov: viewer.getFOV(), projection: scene.cameraMode},
    settings: {pointBudget: viewer.getPointBudget(), edlEnabled: viewer.getEDLEnabled(),
      edlRadius: viewer.getEDLRadius(), edlStrength: viewer.getEDLStrength(), background: viewer.getBackground(),
      minNodeSize: viewer.getMinNodeSize(), showBoundingBoxes: viewer.getShowBoundingBox(),
      clipTask: viewer.getClipTask(), clipMethod: viewer.getClipMethod(),
      returnNumberRange: [...viewer.filterReturnNumberRange], numberOfReturnsRange: [...viewer.filterNumberOfReturnsRange],
      pointSourceIDRange: [...viewer.filterPointSourceIDRange], gpsTimeRange: finitePair(viewer.filterGPSTimeRange, null)},
    material: {...Object.fromEntries(materialValues.map(key => [key, material[key]])),
      activeAttributeName: material.activeAttributeName, color: material.color.toArray(), gradient,
      elevationRange: finitePair(material.elevationRange, [0, 1]), intensityRange: finitePair(material.intensityRange, [0, 65535])},
    transform: transform(pointcloud),
    classification: JSON.parse(JSON.stringify(viewer.classifications)),
    measurements: scene.measurements.map(measure => ({id: measure.uuid, name: measure.name,
      points: measure.points.map(point => vector(point.position)), color: measure.color.toArray(), visible: measure.visible,
      ...Object.fromEntries(measurementFlags.map(key => [key, Boolean(measure[key])]))})),
    volumes: scene.volumes.map(volume => ({id: volume.uuid, name: volume.name, type: volume.constructor.name,
      ...transform(volume), visible: volume.visible, clip: volume.clip})),
    profiles: scene.profiles.map(profile => ({id: profile.uuid, name: profile.name, points: profile.points.map(vector),
      width: profile.width, visible: profile.visible})),
    annotations,
  };
}

function restoreTransform(object, data) {
  object.position.fromArray(data.position);
  object.rotation.set(...data.rotation, data.rotationOrder);
  object.scale.fromArray(data.scale);
  object.updateMatrixWorld(true);
}

export function clearProjectTools(viewer) {
  viewer.inputHandler.deselectAll();
  viewer.inputHandler.drag = null;
  viewer.inputHandler.pressedKeys = {};
  viewer.dispatchEvent({type: "cancel_insertions"});
  viewer.scene.removeAllMeasurements();
  for (const volume of [...viewer.scene.polygonClipVolumes]) viewer.scene.removePolygonClipVolume(volume);
  for (const annotation of [...viewer.scene.annotations.children]) viewer.scene.removeAnnotation(annotation);
}

export function restoreProjectScene(viewer, pointcloud, state) {
  if (state.schemaVersion !== 1) throw new Error("Nieobsługiwana wersja zapisu projektu.");
  const tree = window.jQuery?.("#jstree_scene").jstree(true);
  if (tree) {
    // Saved labels are plain text, including when Potree adds them to jsTree.
    for (const node of tree.get_json("#", {flat: true}).filter(node => node.parent === "#")) {
      tree.rename_node(node.id, node.text.replace(/<\/?b>/g, ""));
    }
    tree.settings.core.force_text = true;
  }
  clearProjectTools(viewer);
  viewer.controls.stop?.();
  viewer.controls.stopTweens?.();
  if (state.transform) restoreTransform(pointcloud, state.transform);
  const settings = state.settings;
  viewer.setPointBudget(settings.pointBudget);
  viewer.setEDLEnabled(settings.edlEnabled);
  viewer.setEDLRadius(settings.edlRadius);
  viewer.setEDLStrength(settings.edlStrength);
  viewer.setBackground(settings.background);
  viewer.setMinNodeSize(settings.minNodeSize);
  viewer.setShowBoundingBox(settings.showBoundingBoxes);
  viewer.setClipTask(settings.clipTask);
  viewer.setClipMethod(settings.clipMethod);
  viewer.setFilterReturnNumberRange(...settings.returnNumberRange);
  viewer.setFilterNumberOfReturnsRange(...settings.numberOfReturnsRange);
  viewer.setFilterPointSourceIDRange(...settings.pointSourceIDRange);
  viewer.setFilterGPSTimeRange(...(settings.gpsTimeRange || [-Infinity, Infinity]));
  viewer.setClassifications({...structuredClone(Potree.ClassificationScheme.DEFAULT), ...structuredClone(state.classification)});
  const material = pointcloud.material;
  for (const key of materialValues) material[key] = state.material[key];
  material.activeAttributeName = state.material.activeAttributeName;
  material.color.setRGB(...state.material.color);
  material.elevationRange = [...state.material.elevationRange];
  material.intensityRange = [...state.material.intensityRange];
  if (Object.hasOwn(Potree.Gradients, state.material.gradient)) material.gradient = Potree.Gradients[state.material.gradient];
  for (const item of state.measurements) {
    const measure = new Potree.Measure();
    measure.uuid = item.id;
    measure.name = item.name;
    measure.visible = item.visible;
    measure.color.setRGB(...item.color);
    for (const key of measurementFlags) measure[key] = item[key];
    for (const point of item.points) measure.addMarker(new THREE.Vector3(...point));
    viewer.scene.addMeasurement(measure);
  }
  for (const item of state.volumes) {
    const volume = item.type === "SphereVolume" ? new Potree.SphereVolume() : new Potree.BoxVolume();
    volume.uuid = item.id;
    volume.name = item.name;
    restoreTransform(volume, item);
    volume.clip = item.clip;
    volume.visible = item.visible;
    viewer.scene.addVolume(volume);
  }
  for (const item of state.profiles) {
    const profile = new Potree.Profile();
    profile.uuid = item.id;
    profile.name = item.name;
    profile.visible = item.visible;
    profile.setWidth(item.width);
    for (const point of item.points) profile.addMarker(new THREE.Vector3(...point));
    viewer.scene.addProfile(profile);
  }
  const annotations = new Map();
  for (const item of state.annotations) {
    const annotation = new Potree.Annotation({position: item.position,
      cameraPosition: item.cameraPosition || [...state.camera.position],
      cameraTarget: item.cameraTarget || vector(new THREE.Vector3(...state.camera.position).add(viewer.scene.view.direction))});
    annotation.uuid = item.id;
    // Potree accepts rich HTML. This application stores and renders plain text.
    annotation.title = window.jQuery("<span>").text(item.title);
    annotation.description = window.jQuery("<span>").text(item.description);
    annotation._title = item.title;
    annotation._description = item.description;
    annotation.offset.fromArray(item.offset);
    if (item.radius !== null) annotation.radius = item.radius;
    annotation.visible = item.visible;
    annotations.set(item.id, annotation);
  }
  // Build children before adding roots, so Potree sees the complete hierarchy.
  for (const item of state.annotations) {
    if (item.parentId) annotations.get(item.parentId).add(annotations.get(item.id));
  }
  for (const item of state.annotations) {
    if (!item.parentId) viewer.scene.annotations.add(annotations.get(item.id));
  }
  const camera = state.camera;
  viewer.scene.view.position.fromArray(camera.position);
  viewer.scene.view.yaw = camera.yaw;
  viewer.scene.view.pitch = camera.pitch;
  viewer.scene.view.radius = camera.radius;
  viewer.setFOV(camera.fov);
  viewer.setCameraMode(camera.projection);
  viewer.setMoveSpeed(camera.radius);
}

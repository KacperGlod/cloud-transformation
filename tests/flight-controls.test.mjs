import test from "node:test";
import assert from "node:assert/strict";
import {FlightControls} from "../src/navigation/FlightControls.js";
import {View} from "../src/viewer/View.js";
import {OrbitControls} from "../src/navigation/OrbitControls.js";
import {resumeAfterFlight} from "../web/assets/flight-navigation.js";

function setup(t, {immediateLock = true} = {}) {
	const document = new EventTarget();
	const window = new EventTarget();
	const canvas = new EventTarget();
	document.defaultView = window;
	document.pointerLockElement = null;
	canvas.ownerDocument = document;
	canvas.focus = () => {};
	canvas.requestPointerLock = () => {
		if (immediateLock) {
			document.pointerLockElement = canvas;
			document.dispatchEvent(new Event("pointerlockchange"));
		}
	};
	document.exitPointerLock = () => {
		document.pointerLockElement = null;
		document.dispatchEvent(new Event("pointerlockchange"));
	};
	const view = new View();
	view.yaw = 0;
	view.pitch = 0;
	const viewer = {
		renderer: {domElement: canvas},
		scene: {view},
		inputHandler: {pressedKeys: {}},
		speed: 10,
		getMoveSpeed() { return this.speed; },
		setMoveSpeed(speed) { this.speed = speed; },
	};
	const controls = new FlightControls(viewer);
	controls.enabled = true;
	t.after(() => controls.dispose());
	controls.start();
	return {controls, view, viewer, document, window, canvas};
}

function input(target, type, properties = {}) {
	const event = new Event(type, {cancelable: true});
	for (const [key, value] of Object.entries(properties)) {
		Object.defineProperty(event, key, {value});
	}
	target.dispatchEvent(event);
	return event;
}

function fly(controls, seconds = 1, fps = 60) {
	for (let frame = 0; frame < seconds * fps; frame++) controls.update(1 / fps);
}

function close(actual, expected, tolerance = 1e-8) {
	assert.ok(Math.abs(actual - expected) < tolerance, `${actual} should equal ${expected}`);
}

test("W follows the camera's full direction, including pitch; S reverses it", t => {
	const {controls, view, document} = setup(t);
	view.yaw = Math.PI / 2;
	view.pitch = Math.PI / 6;
	input(document, "keydown", {code: "KeyW"});
	fly(controls);
	close(view.position.x, -10 * Math.cos(Math.PI / 6));
	close(view.position.z, 5);
	input(document, "keyup", {code: "KeyW"});
	input(document, "keydown", {code: "KeyS"});
	fly(controls);
	close(view.position.length(), 0);
});

test("A/D strafe relative to yaw and simultaneous opposite keys cancel", t => {
	const {controls, view, document} = setup(t);
	view.yaw = Math.PI / 2;
	input(document, "keydown", {code: "KeyD"});
	fly(controls);
	close(view.position.y, 10);
	input(document, "keydown", {code: "KeyA"});
	fly(controls);
	close(view.position.y, 10);
	input(document, "keyup", {code: "KeyD"});
	fly(controls);
	close(view.position.length(), 0);
});

test("diagonal flight has the same speed as forward flight", t => {
	const {controls, view, document} = setup(t);
	input(document, "keydown", {code: "KeyW"});
	input(document, "keydown", {code: "KeyD"});
	input(document, "keydown", {code: "Space"});
	fly(controls);
	close(view.position.length(), 10);
	close(view.position.x, view.position.y);
	close(view.position.y, view.position.z);
});

test("Space/E and Ctrl/Q change world height regardless of camera pitch", t => {
	const {controls, view, document} = setup(t);
	view.pitch = Math.PI / 3;
	for (const [code, expectedHeight] of [["Space", 10], ["ControlLeft", 0], ["KeyE", 10], ["KeyQ", 0]]) {
		input(document, "keydown", {code});
		fly(controls);
		input(document, "keyup", {code});
		close(view.position.z, expectedHeight);
		close(view.position.x, 0);
		close(view.position.y, 0);
	}
});

test("Shift triples speed without changing the configured base speed", t => {
	const {controls, view, document, viewer} = setup(t);
	input(document, "keydown", {code: "KeyW"});
	input(document, "keydown", {code: "ShiftLeft"});
	fly(controls);
	close(view.position.y, 30);
	assert.equal(viewer.getMoveSpeed(), 10);
	input(document, "keyup", {code: "ShiftLeft"});
	fly(controls);
	close(view.position.y, 40);
});

test("releasing a key stops movement immediately", t => {
	const {controls, view, document} = setup(t);
	input(document, "keydown", {code: "KeyW"});
	fly(controls);
	input(document, "keyup", {code: "KeyW"});
	const position = view.position.clone();
	fly(controls);
	assert.deepEqual(view.position, position);
});

test("flight is independent of frame rate and long frames cannot teleport the camera", t => {
	const low = setup(t);
	const high = setup(t);
	input(low.document, "keydown", {code: "KeyW"});
	input(high.document, "keydown", {code: "KeyW"});
	fly(low.controls, 1, 30);
	fly(high.controls, 1, 120);
	close(low.view.position.y, high.view.position.y);
	high.controls.update(5);
	close(high.view.position.y, 10.5);
});

test("relative mouse movement rotates the camera without crossing the vertical limit", t => {
	const {controls, view, document} = setup(t);
	input(document, "mousemove", {movementX: 100, movementY: -50});
	close(view.yaw, -0.2);
	close(view.pitch, 0.1);
	input(document, "mousemove", {movementX: 0, movementY: -100_000});
	assert.ok(view.pitch < Math.PI / 2);
	input(document, "mousemove", {movementX: 0, movementY: 100_000});
	assert.ok(view.pitch > -Math.PI / 2);
});

test("wheel adjusts base speed and consumes scrolling only during flight", t => {
	const {controls, viewer, canvas} = setup(t);
	assert.ok(input(canvas, "wheel", {deltaY: -100}).defaultPrevented);
	close(viewer.speed, 12);
	input(canvas, "wheel", {deltaY: 100});
	close(viewer.speed, 10);
	viewer.speed = 0.1;
	input(canvas, "wheel", {deltaY: 100});
	close(viewer.speed, 0.1);
	controls.stop();
	assert.equal(input(canvas, "wheel", {deltaY: -100}).defaultPrevented, false);
	close(viewer.speed, 0.1);
});

test("Escape, focus loss, hidden tabs and pointer unlock stop flight and clear held keys", t => {
	for (const reason of ["escape", "blur", "hidden", "unlock"]) {
		const {controls, view, document, window} = setup(t);
		let ended = 0;
		controls.addEventListener("end", () => ended++);
		input(document, "keydown", {code: "KeyW"});
		if (reason === "escape") input(document, "keydown", {code: "Escape"});
		if (reason === "blur") input(window, "blur");
		if (reason === "hidden") {
			document.hidden = true;
			input(document, "visibilitychange");
		}
		if (reason === "unlock") document.exitPointerLock();
		fly(controls);
		assert.equal(controls.active, false, reason);
		assert.equal(controls.pressedKeys.size, 0, reason);
		assert.equal(ended, 1, reason);
		close(view.position.length(), 0);
		controls.start();
		fly(controls);
		close(view.position.length(), 0);
		controls.dispose();
	}
});

test("typing in a form leaves flight and never consumes text input", t => {
	const {controls, view, document} = setup(t);
	input(document, "keydown", {code: "KeyW"});
	const event = input(document, "keydown", {code: "KeyA", target: {closest: () => ({tagName: "INPUT"})}});
	fly(controls);
	assert.equal(event.defaultPrevented, false);
	assert.equal(controls.active, false);
	close(view.position.length(), 0);
});

test("a denied pointer lock reports one error and never starts movement", t => {
	const {controls, view, document} = setup(t, {immediateLock: false});
	let errors = 0;
	controls.addEventListener("error", () => errors++);
	input(document, "pointerlockerror");
	input(document, "pointerlockerror");
	input(document, "keydown", {code: "KeyW"});
	fly(controls);
	assert.equal(errors, 1);
	assert.equal(controls.pending, false);
	assert.equal(controls.active, false);
	close(view.position.length(), 0);
});

test("a late pointer lock after cancellation is released", t => {
	const {controls, document, canvas} = setup(t, {immediateLock: false});
	controls.stop();
	document.pointerLockElement = canvas;
	input(document, "pointerlockchange");
	assert.equal(document.pointerLockElement, null);
	assert.equal(controls.active, false);
});

test("leaving flight keeps the final pose and perspective across subsequent orbit updates", t => {
	for (const reason of ["escape", "blur", "hidden", "unlock", "stop"]) {
		const {controls, view, viewer, document, window} = setup(t);
		viewer.scene.cameraMode = 1;
		viewer.controls = controls;
		viewer.setCameraMode = mode => { viewer.scene.cameraMode = mode; };
		viewer.setControls = next => {
			viewer.controls.enabled = false;
			next.enabled = true;
			viewer.controls = next;
		};
		const orbit = new OrbitControls(viewer);
		orbit.setScene(viewer.scene);
		let cancelledTween = false;
		orbit.yawDelta = 0.8;
		orbit.pitchDelta = 0.3;
		orbit.panDelta.set(0.5, 0.5);
		orbit.radiusDelta = 100;
		orbit.tweens.push({stop() { cancelledTween = true; }});
		controls.addEventListener("end", () => resumeAfterFlight(viewer, orbit, 0, true));
		view.position.set(500000, 6000000, 100);
		input(document, "keydown", {code: "KeyW"});
		input(document, "mousemove", {movementX: 150, movementY: 75});
		fly(controls);
		const position = view.position.clone();
		const {yaw, pitch, radius} = view;
		if (reason === "escape") input(document, "keydown", {code: "Escape"});
		if (reason === "blur") input(window, "blur");
		if (reason === "hidden") {
			document.hidden = true;
			input(document, "visibilitychange");
		}
		if (reason === "unlock") document.exitPointerLock();
		if (reason === "stop") controls.stop();
		for (let frame = 0; frame < 120; frame++) orbit.update(1 / 60);
		close(view.position.distanceTo(position), 0, 1e-7);
		assert.equal(view.yaw, yaw, reason);
		assert.equal(view.pitch, pitch, reason);
		assert.equal(view.radius, radius, reason);
		assert.equal(viewer.scene.cameraMode, 1, reason);
		assert.equal(viewer.controls, orbit, reason);
		assert.equal(cancelledTween, true, reason);
		assert.equal(viewer.inputHandler.drag, null, reason);
		assert.deepEqual(viewer.inputHandler.pressedKeys, {}, reason);
	}
});

test("failed and cancelled flight entry restore the previous projection without moving the view", t => {
	for (const reason of ["error", "cancel"]) {
		const {controls, viewer, view, document} = setup(t, {immediateLock: false});
		viewer.scene.cameraMode = 1;
		viewer.setCameraMode = mode => { viewer.scene.cameraMode = mode; };
		const orbit = new OrbitControls(viewer);
		viewer.setControls = next => { viewer.controls = next; };
		controls.addEventListener(reason === "error" ? "error" : "end", () => resumeAfterFlight(viewer, orbit, 0, false));
		view.position.set(10, 20, 30);
		const position = view.position.clone();
		if (reason === "error") input(document, "pointerlockerror");
		else controls.stop();
		assert.deepEqual(view.position, position, reason);
		assert.equal(viewer.scene.cameraMode, 0, reason);
		assert.equal(viewer.controls, orbit, reason);
	}
});

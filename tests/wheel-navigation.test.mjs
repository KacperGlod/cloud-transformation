import test from "node:test";
import assert from "node:assert/strict";
import {InputHandler} from "../src/navigation/InputHandler.js";
import {OrbitControls} from "../src/navigation/OrbitControls.js";
import {FlightControls} from "../src/navigation/FlightControls.js";
import {View} from "../src/viewer/View.js";

function setup(t) {
	const document = new EventTarget();
	const canvas = new EventTarget();
	document.defaultView = new EventTarget();
	document.pointerLockElement = null;
	canvas.ownerDocument = document;
	canvas.tabIndex = -1;
	canvas.focus = () => {};
	canvas.requestPointerLock = () => {
		document.pointerLockElement = canvas;
		document.dispatchEvent(new Event("pointerlockchange"));
	};
	document.exitPointerLock = () => {
		document.pointerLockElement = null;
		document.dispatchEvent(new Event("pointerlockchange"));
	};
	const view = new View();
	view.radius = 100;
	const viewer = {
		renderer: {domElement: canvas},
		scene: {view},
		speed: 10,
		getMoveSpeed() { return this.speed; },
		setMoveSpeed(speed) { this.speed = speed; },
	};
	// Node's EventTarget has no DOM capture phase. Register flight's capture
	// listeners first to preserve the event order used by the browser.
	const flight = new FlightControls(viewer);
	const handler = new InputHandler(viewer);
	viewer.inputHandler = handler;
	handler.setScene(viewer.scene);
	const orbit = new OrbitControls(viewer);
	orbit.setScene(viewer.scene);
	handler.addInputListener(orbit);
	flight.addEventListener("end", () => {
		flight.enabled = false;
		handler.removeInputListener(flight);
		handler.addInputListener(orbit);
	});
	t.after(() => flight.dispose());
	function enterFlight() {
		handler.removeInputListener(orbit);
		handler.addInputListener(flight);
		flight.enabled = true;
		flight.start();
	}
	return {canvas, document, viewer, view, orbit, flight, handler, enterFlight};
}

function input(target, type, properties) {
	const event = new Event(type, {cancelable: true});
	for (const [key, value] of Object.entries(properties)) {
		Object.defineProperty(event, key, {value});
	}
	target.dispatchEvent(event);
	return event;
}

test("standard wheel zooms in and out before entering flight", t => {
	const {canvas, view, orbit} = setup(t);
	const scrollIn = input(canvas, "wheel", {deltaY: -120});
	orbit.update(0.05);
	assert.ok(view.radius < 100, "scrolling up should zoom in");
	assert.equal(scrollIn.defaultPrevented, true);
	const radius = view.radius;
	input(canvas, "wheel", {deltaY: 120});
	orbit.update(0.05);
	assert.ok(view.radius > radius, "scrolling down should zoom out");
});

test("wheel direction works for pixel, line and page deltas", t => {
	for (const [deltaMode, deltaY] of [[0, -0.5], [1, -3], [2, -1]]) {
		const {canvas, view, orbit} = setup(t);
		input(canvas, "wheel", {deltaY, deltaMode});
		orbit.update(0.05);
		assert.equal(view.radius, 90);
	}
});

test("wheel changes flight speed without zooming while pointer lock is active", t => {
	const {canvas, view, viewer, orbit, flight, enterFlight} = setup(t);
	enterFlight();
	assert.equal(flight.active, true);
	const scroll = input(canvas, "wheel", {deltaY: -120});
	assert.equal(scroll.defaultPrevented, true);
	assert.equal(viewer.speed, 12);
	assert.equal(view.radius, 100);
	assert.equal(orbit.radiusDelta, 0);
});

test("Escape restores wheel zoom and leaves flight speed unchanged", t => {
	const {canvas, document, view, viewer, orbit, flight, enterFlight} = setup(t);
	enterFlight();
	input(document, "keydown", {code: "Escape"});
	assert.equal(flight.active, false);
	const speed = viewer.speed;
	input(canvas, "wheel", {deltaY: -120});
	assert.equal(viewer.speed, speed, "scrolling outside flight must not adjust flight speed");
	orbit.update(0.05);
	assert.equal(view.radius, 90);
});

test("horizontal scrolling does not zoom or cancel a camera animation", t => {
	const {canvas, view, orbit} = setup(t);
	let cancelled = false;
	orbit.tweens.push({stop() { cancelled = true; }});
	input(canvas, "wheel", {deltaX: 120, deltaY: 0});
	assert.equal(cancelled, false);
	assert.equal(view.radius, 100);
	assert.equal(orbit.radiusDelta, 0);
});

test("deprecated mousewheel events do not cause a second zoom step", t => {
	const {canvas, view, orbit} = setup(t);
	input(canvas, "wheel", {deltaY: -120});
	input(canvas, "mousewheel", {wheelDelta: 120});
	orbit.update(0.05);
	assert.equal(view.radius, 90);
});

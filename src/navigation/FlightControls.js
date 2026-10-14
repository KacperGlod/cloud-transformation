import * as THREE from "../../libs/three.js/build/three.module.js";
import {EventDispatcher} from "../EventDispatcher.js";

const FLIGHT_KEYS = new Set([
	"KeyW", "KeyS", "KeyA", "KeyD", "KeyQ", "KeyE", "Space",
	"ControlLeft", "ControlRight", "ShiftLeft", "ShiftRight",
]);

function isEditable(target) {
	return Boolean(target && target.closest && target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])"));
}

export class FlightControls extends EventDispatcher {
	constructor(viewer) {
		super();
		this.viewer = viewer;
		this.domElement = viewer.renderer.domElement;
		this.document = this.domElement.ownerDocument;
		this.window = this.document.defaultView;
		this.scene = viewer.scene;
		this.sceneControls = new THREE.Scene();
		this.enabled = false;
		this.sensitivity = 0.002;
		this.boostMultiplier = 3;
		this.pressedKeys = new Set();
		this.locked = false;
		this.pending = false;
		this.movement = new THREE.Vector3();

		this.listeners = [
			[this.document, "pointerlockchange", () => this.onPointerLockChange()],
			[this.document, "pointerlockerror", () => this.onPointerLockError()],
			[this.document, "keydown", event => this.onKeyDown(event), true],
			[this.document, "keyup", event => this.onKeyUp(event), true],
			[this.document, "mousemove", event => this.onMouseMove(event), true],
			[this.domElement, "wheel", event => this.onWheel(event), {capture: true, passive: false}],
			[this.window, "blur", () => this.stop()],
			[this.document, "visibilitychange", () => {
				if (this.document.hidden) this.stop();
			}],
			[this.document, "focusin", event => {
				if (isEditable(event.target)) this.stop();
			}],
		];

		// Keep editing tools from selecting or dragging objects under the locked cursor.
		for (const type of ["mousedown", "mouseup", "click", "dblclick", "contextmenu", "mousewheel", "DOMMouseScroll"]) {
			this.listeners.push([this.domElement, type, event => {
				if (this.active) {
					event.preventDefault();
					event.stopImmediatePropagation();
				}
			}, true]);
		}
		for (const [target, type, listener, options] of this.listeners) {
			target.addEventListener(type, listener, options);
		}
	}

	get active() {
		return this.enabled && this.locked && this.document.pointerLockElement === this.domElement;
	}

	setScene(scene) {
		this.scene = scene;
	}

	start() {
		if (!this.enabled || this.pending || this.active) return;
		this.pending = true;
		this.pressedKeys.clear();
		this.domElement.focus({preventScroll: true});
		try {
			if (!this.domElement.requestPointerLock) {
				throw new Error("Przeglądarka nie obsługuje przechwytywania myszy.");
			}
			const request = this.domElement.requestPointerLock();
			if (request && request.catch) request.catch(() => this.onPointerLockError());
		} catch (error) {
			this.onPointerLockError(error.message);
		}
	}

	onPointerLockChange() {
		if (this.document.pointerLockElement === this.domElement) {
			// A request may complete after the user has already left the viewer.
			if (!this.enabled || (!this.pending && !this.locked)) {
				this.document.exitPointerLock();
				return;
			}
			if (!this.locked) {
				this.pending = false;
				this.locked = true;
				this.pressedKeys.clear();
				this.viewer.inputHandler.pressedKeys = {};
				this.domElement.focus({preventScroll: true});
				this.dispatchEvent({type: "start"});
			}
		} else if (this.locked) {
			this.stop();
		}
	}

	onPointerLockError(message) {
		if (!this.pending) return;
		this.pending = false;
		this.pressedKeys.clear();
		this.dispatchEvent({
			type: "error",
			message: message || "Nie udało się przechwycić myszy. Kliknij „Lot WSAD”, aby spróbować ponownie.",
		});
	}

	stop() {
		const wasFlying = this.locked || this.pending;
		this.locked = false;
		this.pending = false;
		this.pressedKeys.clear();
		if (this.document.pointerLockElement === this.domElement) {
			this.document.exitPointerLock();
		}
		if (wasFlying) {
			this.viewer.inputHandler.pressedKeys = {};
			this.dispatchEvent({type: "end"});
		}
	}

	onKeyDown(event) {
		if (!this.active) return;
		if (isEditable(event.target)) {
			this.stop();
			return;
		}
		if (event.code === "Escape") {
			this.stop();
			return;
		}
		if (event.altKey || event.metaKey) {
			this.pressedKeys.clear();
			return;
		}
		if (FLIGHT_KEYS.has(event.code)) {
			this.pressedKeys.add(event.code);
			event.preventDefault();
		}
		// Potree's shortcuts (e.g. Delete) must not edit the scene during flight.
		event.stopImmediatePropagation();
	}

	onKeyUp(event) {
		this.pressedKeys.delete(event.code);
		if (this.active && FLIGHT_KEYS.has(event.code) && !isEditable(event.target)) {
			event.preventDefault();
			event.stopImmediatePropagation();
		}
	}

	onMouseMove(event) {
		if (!this.active) return;
		const view = this.scene.view;
		view.yaw -= event.movementX * this.sensitivity;
		const pitchLimit = Math.PI / 2 - 0.01;
		view.pitch = Math.max(-pitchLimit, Math.min(pitchLimit, view.pitch - event.movementY * this.sensitivity));
		event.stopImmediatePropagation();
	}

	onWheel(event) {
		if (!this.active) return;
		event.preventDefault();
		event.stopImmediatePropagation();
		if (event.deltaY === 0) return;
		const speed = this.viewer.getMoveSpeed() * Math.pow(1.2, -Math.sign(event.deltaY));
		this.viewer.setMoveSpeed(Math.max(0.1, Math.min(1000000, speed)));
	}

	update(delta) {
		if (!this.active) return;
		const keys = this.pressedKeys;
		const view = this.scene.view;
		const forward = Number(keys.has("KeyW")) - Number(keys.has("KeyS"));
		const side = Number(keys.has("KeyD")) - Number(keys.has("KeyA"));
		const up = Number(keys.has("Space") || keys.has("KeyE"))
			- Number(keys.has("ControlLeft") || keys.has("ControlRight") || keys.has("KeyQ"));
		this.movement.copy(view.direction).multiplyScalar(forward)
			.addScaledVector(view.getSide(), side);
		this.movement.z += up;
		const boost = keys.has("ShiftLeft") || keys.has("ShiftRight") ? this.boostMultiplier : 1;
		// Normalize the final direction so diagonal flight has the same speed.
		// Cap long frames to prevent jumps when a tab or window regains focus.
		const distance = this.viewer.getMoveSpeed() * boost * Math.max(0, Math.min(delta, 0.05));
		view.position.addScaledVector(this.movement.normalize(), distance);
		view.radius = Math.max(0.1, 3 * this.viewer.getMoveSpeed());
	}

	dispose() {
		this.stop();
		for (const [target, type, listener, options] of this.listeners) {
			target.removeEventListener(type, listener, options);
		}
	}
}

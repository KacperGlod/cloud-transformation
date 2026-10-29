import * as THREE from "../../libs/three.js/build/three.module.js";
import {PotreeRenderer} from "./PotreeRenderer.js";
import {Utils} from "../utils.js";

// Render each cloud as a complete opaque image with its own depth buffer.
// Applying opacity once to that image avoids per-point overdraw and preserves
// the same geometry, EDL and HQ shading at both 100% and 99% layer visibility.
export class LayerRenderer {

	constructor(viewer) {
		this.viewer = viewer;
		this.regularRenderer = new PotreeRenderer(viewer);
		this.target = new THREE.WebGLRenderTarget(1, 1, {
			minFilter: THREE.NearestFilter,
			magFilter: THREE.NearestFilter,
			format: THREE.RGBAFormat,
			depthTexture: new THREE.DepthTexture(1, 1, THREE.UnsignedIntType),
		});
		this.composite = new THREE.RawShaderMaterial({
			uniforms: {
				layerColor: {value: this.target.texture},
				layerDepth: {value: this.target.depthTexture},
				opacity: {value: 1},
			},
			vertexShader: `
				precision highp float;
				attribute vec3 position;
				attribute vec2 uv;
				varying vec2 vUv;
				void main() {
					vUv = uv;
					gl_Position = vec4(position, 1.0);
				}`,
			fragmentShader: `
				#extension GL_EXT_frag_depth : enable
				precision highp float;
				uniform sampler2D layerColor;
				uniform sampler2D layerDepth;
				uniform float opacity;
				varying vec2 vUv;
				void main() {
					vec4 color = texture2D(layerColor, vUv);
					if (color.a == 0.0 || opacity == 0.0) discard;
					gl_FragColor = vec4(color.rgb, color.a * opacity);
					gl_FragDepthEXT = texture2D(layerDepth, vUv).r;
				}`,
			transparent: true,
			depthTest: true,
			depthFunc: THREE.AlwaysDepth,
			depthWrite: true,
			blending: THREE.CustomBlending,
			blendSrc: THREE.SrcAlphaFactor,
			blendDst: THREE.OneMinusSrcAlphaFactor,
			blendSrcAlpha: THREE.OneFactor,
			blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
		});
	}

	clear() {
		this.regularRenderer.clear();
	}

	render(params) {
		const {viewer} = this;
		const {renderer} = viewer;
		const camera = params.camera || viewer.scene.getActiveCamera();
		const size = renderer.getSize(new THREE.Vector2());
		// The renderer uses a shared working target, not one full-size texture
		// per cloud. Resize and disposal therefore have bounded GPU memory cost.
		this.target.setSize(size.width, size.height);
		const base = viewer.getPRenderer({compositeLayers: false});
		const clouds = viewer.scene.pointclouds.filter(cloud => cloud.visible && cloud.material.opacity > 0);
		// The first row is the top layer, independent of opacity and load order.
		clouds.sort((a, b) => (b.userData.layerOrder ?? 0) - (a.userData.layerOrder ?? 0));
		const clearColor = renderer.getClearColor(new THREE.Color()).clone();
		const clearAlpha = renderer.getClearAlpha();
		viewer.dispatchEvent({type: "render.pass.begin", viewer});
		this.regularRenderer.renderBackground();
		try {
			for (const pointcloud of clouds) {
				renderer.setRenderTarget(this.target);
				renderer.setClearColor(0x000000, 0);
				renderer.clear(true, true, true);
				const material = pointcloud.material;
				try {
					base.render({camera, layerTarget: this.target, pointclouds: [pointcloud]});
				} finally {
					// HQ temporarily substitutes depth and attribute materials.
					pointcloud.material = material;
				}
				renderer.setRenderTarget(null);
				this.composite.uniforms.opacity.value = material.opacity;
				Utils.screenPass.render(renderer, this.composite);
			}
		} finally {
			renderer.setRenderTarget(null);
			renderer.setClearColor(clearColor, clearAlpha);
		}
		this.regularRenderer.renderScene(camera);
	}

	dispose() {
		this.target.dispose();
		this.composite.dispose();
	}
}

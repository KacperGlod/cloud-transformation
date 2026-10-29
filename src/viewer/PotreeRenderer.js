
import * as THREE from "../../libs/three.js/build/three.module.js";


export class PotreeRenderer {

	constructor (viewer) {
		this.viewer = viewer;
		this.renderer = viewer.renderer;

		{
			let dummyScene = new THREE.Scene();
			let geometry = new THREE.SphereGeometry(0.001, 2, 2);
			let mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
			mesh.position.set(36453, 35163, 764712);
			dummyScene.add(mesh);

			this.dummyMesh = mesh;
			this.dummyScene = dummyScene;
		}
	}

	clearTargets(){

	}

	clear(){
		let {viewer, renderer} = this;


		// render skybox
		if(viewer.background === "skybox"){
			renderer.setClearColor(0xff0000, 1);
		}else if(viewer.background === "gradient"){
			renderer.setClearColor(0x00ff00, 1);
		}else if(viewer.background === "black"){
			renderer.setClearColor(0x000000, 1);
		}else if(viewer.background === "white"){
			renderer.setClearColor(0xFFFFFF, 1);
		}else{
			renderer.setClearColor(0x000000, 0);
		}

		renderer.clear();
	}
 
	renderBackground(){
		let {viewer, renderer} = this;

		// render skybox
		if(viewer.background === "skybox"){
			viewer.skybox.camera.rotation.copy(viewer.scene.cameraP.rotation);
			viewer.skybox.camera.fov = viewer.scene.cameraP.fov;
			viewer.skybox.camera.aspect = viewer.scene.cameraP.aspect;
			
			viewer.skybox.parent.rotation.x = 0;
			viewer.skybox.parent.updateMatrixWorld();

			viewer.skybox.camera.updateProjectionMatrix();
			renderer.render(viewer.skybox.scene, viewer.skybox.camera);
		}else if(viewer.background === "gradient"){
			renderer.render(viewer.scene.sceneBG, viewer.scene.cameraBG);
		}
	}

	render(params){
		const {viewer} = this;
		const camera = params.camera || viewer.scene.getActiveCamera();
		const isolated = Boolean(params.layerTarget);
		if (!isolated) {
			viewer.dispatchEvent({type: "render.pass.begin", viewer});
			this.renderBackground();
		}
		
		for(let pointcloud of params.pointclouds || viewer.scene.pointclouds){
			const {material} = pointcloud;
			material.useEDL = false;
		}
		
		viewer.pRenderer.render(viewer.scene.scenePointCloud, camera, params.layerTarget || null, {
			clipSpheres: viewer.scene.volumes.filter(v => (v instanceof Potree.SphereVolume)),
			pointclouds: params.pointclouds,
			...(isolated ? {transparent: false, opacity: 1} : {}),
		});
		if (!isolated) this.renderScene(camera);
	}

	renderScene(camera){
		const {viewer, renderer} = this;
		
		// render scene
		renderer.render(viewer.scene.scene, camera);

		viewer.dispatchEvent({type: "render.pass.scene",viewer: viewer});
		
		viewer.clippingTool.update();
		renderer.render(viewer.clippingTool.sceneMarker, viewer.scene.cameraScreenSpace); //viewer.scene.cameraScreenSpace);
		renderer.render(viewer.clippingTool.sceneVolume, camera);

		renderer.render(viewer.controls.sceneControls, camera);
		
		renderer.clearDepth();
		
		viewer.transformationTool.update();
		
		viewer.dispatchEvent({type: "render.pass.perspective_overlay",viewer: viewer});

		// renderer.render(viewer.controls.sceneControls, camera);
		// renderer.render(viewer.clippingTool.sceneVolume, camera);
		// renderer.render(viewer.transformationTool.scene, camera);
		
		if (viewer.useHQ) {
			const {width, height} = renderer.getSize(new THREE.Vector2());
			renderer.setViewport(width - viewer.navigationCube.width, height - viewer.navigationCube.width,
				viewer.navigationCube.width, viewer.navigationCube.width);
			renderer.render(viewer.navigationCube, viewer.navigationCube.camera);
			renderer.setViewport(0, 0, width, height);
		}
		
		viewer.dispatchEvent({type: "render.pass.end",viewer: viewer});
	}

}

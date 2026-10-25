import * as T from './source/vendor/three.module.min.js';
import {OrbitControls} from './source/vendor/OrbitControls.js';
import {DuckView} from './source/duck.js';
import {parseDuck} from './source/pose.js';
import {referencePose,REFERENCE_IDS} from './source/calibration-reference.js';
export async function createView(){
 const element=document.getElementById('scene'),marker=document.getElementById('joint-marker');
 const response=await fetch('./source/assets/duck-visual.bin');if(!response.ok)throw Error('模型资源不可用');
 const model=parseDuck(await response.arrayBuffer());
 const geometry=model.meshes.map(m=>{const g=new T.BufferGeometry();g.setAttribute('position',new T.BufferAttribute(m.vertices,3));g.setAttribute('normal',new T.BufferAttribute(m.normals,3));g.setIndex(new T.BufferAttribute(m.indices,1));g.computeBoundingBox();return g;});
 const scene=new T.Scene();scene.background=new T.Color('#f7faf8');scene.add(new T.HemisphereLight(0xffffff,0x899e98,2.4));
 const key=new T.DirectionalLight(0xfff6e8,3);key.position.set(.5,-.5,.9);scene.add(key);
 const fill=new T.DirectionalLight(0xddefff,1.4);fill.position.set(-.4,.55,.3);scene.add(fill);
 const v=new DuckView();v.model=model;v.geometry=geometry;v.actual=v.build(false);scene.add(v.actual.root);
 const camera=new T.OrthographicCamera(-.3,.3,.3,-.3,.001,5);camera.up.set(0,0,1);
 const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(2,devicePixelRatio));renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;element.prepend(renderer.domElement);
 const controls=new OrbitControls(camera,renderer.domElement);controls.enablePan=false;controls.minZoom=.7;controls.maxZoom=4;
 const grid=new T.GridHelper(.7,28,0xc5d4cc,0xe2ebe5);grid.rotation.x=Math.PI/2;grid.position.z=-.001;scene.add(grid);
 const originals=v.actual.servos.map(s=>s.material);v.actual.servos.forEach(s=>s.material=s.material.clone());
 let selected=13,target=new T.Vector3(0,0,.19),height=.48,viewname='three';
 function draw(){
  renderer.render(scene,camera);const mesh=v.actual.servos[REFERENCE_IDS.indexOf(selected)];
  const p=new T.Box3().setFromObject(mesh).getCenter(new T.Vector3()).project(camera);
  marker.textContent='ID'+selected;marker.style.left=Math.max(4,Math.min(element.clientWidth-65,(p.x+1)*element.clientWidth/2+12))+'px';marker.style.top=Math.max(4,Math.min(element.clientHeight-30,(1-p.y)*element.clientHeight/2-10))+'px';
 }
 function resize(){const w=element.clientWidth,h=element.clientHeight,a=w/h;renderer.setSize(w,h);camera.left=-height*a/2;camera.right=height*a/2;camera.top=height/2;camera.bottom=-height/2;camera.updateProjectionMatrix();draw();}
 function setCamera(name){viewname=name;const dir={three:[.48,-.66,.2],right:[0,-.7,0],left:[0,.7,0],front:[.7,0,0]}[name];camera.position.copy(target).add(new T.Vector3(...dir));controls.target.copy(target);controls.update();draw();}
 function set(id,pose){
  selected=id;v.pose(v.actual,referencePose(pose,REFERENCE_IDS.indexOf(id)),[0,0,-1],0);
  const box=new T.Box3().setFromObject(v.actual.root);target=box.getCenter(new T.Vector3());height=Math.max(.18,box.max.z-box.min.z)*1.30;
  v.actual.servos.forEach((m,i)=>{m.material.copy(originals[i]);m.renderOrder=0;if(REFERENCE_IDS[i]===id){m.material.color.set('#dfae38');m.material.emissive?.set('#684900');m.material.emissiveIntensity=.35;m.material.depthTest=false;m.material.depthWrite=false;m.renderOrder=10;}});
  controls.target.copy(target);resize();setCamera(viewname);
 }
 controls.addEventListener('change',draw);new ResizeObserver(resize).observe(element);set(13,'zero');setCamera('three');
 return {set,camera:setCamera};
}

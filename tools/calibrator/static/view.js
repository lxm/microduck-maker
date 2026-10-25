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
 let selected=13,target=new T.Vector3(0,0,.19),height=.48,viewname='auto',focusBox=new T.Box3();
 const directions={three:[.48,-.66,.2],right:[0,-.7,0],left:[0,.7,0],front:[.7,0,0]};
 function preset(){
  const side=selected<20?-1:1;
  if([10,20,32].includes(selected))return {direction:[.38,side*.28,.8],label:'斜上方 · 观察转向'};
  if([11,21,33].includes(selected))return {direction:[.8,side*.08,.08],label:'正面 · 观察侧摆'};
  return {direction:[.08,(selected<30?side:-1)*.8,.08],label:selected<30?'侧面 · 观察俯仰与连杆':'侧面 · 观察头颈与嘴部'};
 }
 function draw(){
  renderer.render(scene,camera);const mesh=v.actual.servos[REFERENCE_IDS.indexOf(selected)];
  const p=new T.Box3().setFromObject(mesh).getCenter(new T.Vector3()).project(camera);
  marker.textContent='ID'+selected;marker.style.left=Math.max(4,Math.min(element.clientWidth-65,(p.x+1)*element.clientWidth/2+12))+'px';marker.style.top=Math.max(4,Math.min(element.clientHeight-30,(1-p.y)*element.clientHeight/2-10))+'px';
 }
 function resize(){
  const w=element.clientWidth,h=element.clientHeight;if(!w||!h)return;
  const a=w/h;renderer.setSize(w,h);camera.updateMatrixWorld();
  const right=new T.Vector3().setFromMatrixColumn(camera.matrixWorld,0),up=new T.Vector3().setFromMatrixColumn(camera.matrixWorld,1);
  let halfWidth=0,halfHeight=0;
  for(const x of [focusBox.min.x,focusBox.max.x])for(const y of [focusBox.min.y,focusBox.max.y])for(const z of [focusBox.min.z,focusBox.max.z]){
   const point=new T.Vector3(x,y,z).sub(target);halfWidth=Math.max(halfWidth,Math.abs(point.dot(right)));halfHeight=Math.max(halfHeight,Math.abs(point.dot(up)));
  }
  height=Math.max(.16,halfHeight*2.25,halfWidth*2.25/a);
  camera.left=-height*a/2;camera.right=height*a/2;camera.top=height/2;camera.bottom=-height/2;camera.updateProjectionMatrix();draw();
 }
 function setCamera(name){
  if(name!=='auto'&&!directions[name])return;
  viewname=name;
  focusBox.setFromObject(v.actual.root);
  if(name==='auto'){
   focusBox.makeEmpty();const group=selected<20?10:selected<30?20:30;
   v.actual.servos.forEach((mesh,i)=>{if(Math.floor(REFERENCE_IDS[i]/10)*10===group)focusBox.union(new T.Box3().setFromObject(mesh));});
   focusBox.expandByScalar(.035);
  }
  target=focusBox.getCenter(new T.Vector3());camera.zoom=1;
  camera.position.copy(target).add(new T.Vector3(...(name==='auto'?preset().direction:directions[name])));
  controls.target.copy(target);controls.update();resize();
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===name)));
  document.getElementById('camera-note').textContent=name==='auto'?'自动视角 · '+preset().label+' · 选择下一颗时自动切换':'手动视角 · 选择下一颗时恢复自动视角';
 }
 function set(id,pose,automatic=false){
  selected=id;v.pose(v.actual,referencePose(pose,REFERENCE_IDS.indexOf(id)),[0,0,-1],0);
  v.actual.servos.forEach((m,i)=>{m.material.copy(originals[i]);m.renderOrder=0;if(REFERENCE_IDS[i]===id){m.material.color.set('#dfae38');m.material.emissive?.set('#684900');m.material.emissiveIntensity=.35;m.material.depthTest=false;m.material.depthWrite=false;m.renderOrder=10;}});
  setCamera(automatic?'auto':viewname);
 }
 controls.addEventListener('change',draw);new ResizeObserver(resize).observe(element);set(13,'zero',true);
 return {set,camera:setCamera};
}

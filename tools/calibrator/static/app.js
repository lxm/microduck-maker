import {createView} from './view.js';
const $=id=>document.getElementById(id);
const ids=[10,11,12,13,14,20,21,22,23,24,30,31,32,33,34];
const names=['右髋转向','右髋侧摆','右髋俯仰','右膝','右踝','左髋转向','左髋侧摆','左髋俯仰','左膝','左踝','颈部俯仰','头部俯仰','头部转向','头部侧摆','嘴部'];
let selected=13,pose='zero',state=null,busy=false,active=false,sessionStarted=false,view,requestSequence=0;
const info=await (await fetch('/api/info')).json();
let preparation=info.preparation,preparing=false,setupMessage='';
$('connection').hidden=info.mode!=='hardware';
if(info.auto_install===false)$('dependency-note').textContent='自动安装已关闭。检查主板已有依赖并发现串口；此步骤不停止控制服务、不打开串口。';
$('mode').textContent=info.mode==='demo'?'模拟演示 · 未连接实机':'实机模式 · '+info.host;
$('enter').textContent=info.mode==='demo'?'开始模拟标定':'连接并进入标定';
$('notice').textContent=info.mode==='demo'?'模拟模式：所有读数与使能均为模拟，不连接小鸭。':(info.eeprom_only?'手动标定：零位 → 2048，保存到舵机 EEPROM。保留备份日志，不读写 TOML。':'')+'进入后会核验断力、备份零位，并暂停手柄与 robotd；请在支架上操作。';
if(info.active&&info.state?.servos){state=info.state;active=true;sessionStarted=true;}
function current(){return state?.servos?.find(x=>x.id===selected);}
function renderConnection(){
 if(info.mode!=='hardware')return;
 const chosen=$('serial-port').value||info.serial_port||'auto';
 $('prepare').disabled=busy||active||sessionStarted;
 $('prepare').textContent=preparing?'正在检查与准备…':'检查环境与发现串口';
 const choices=$('serial-port');choices.replaceChildren();
 const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择舵机总线串口';choices.append(placeholder);
 const auto=document.createElement('option');auto.value='auto';auto.textContent='自动选择（优先使用 robotd 串口）';
 auto.disabled=Boolean(preparation?.ready&&(!preparation.ports.length||(preparation.ports.length>1&&!preparation.runtime_port)));
 choices.append(auto);
 for(const p of preparation?.ports||[]){const o=document.createElement('option');o.value=p.device;o.textContent=p.device+(p.runtime?' · robotd 当前串口':'')+(p.description?' · '+p.description:'');choices.append(o);}
 if(chosen!=='auto'&&!Array.from(choices.options).some(o=>o.value===chosen)){
  const alias=(preparation?.ports||[]).find(p=>p.aliases.includes(chosen));
  choices.value=alias?.device||'';
 }else choices.value=chosen==='auto'&&auto.disabled?'':chosen;
 choices.disabled=busy||active||sessionStarted;
 $('connection-status').textContent=preparing?'正在通过 SSH 检查环境；首次安装可能需要几分钟，控制服务继续运行。':setupMessage||
  (preparation?.ready?`板端依赖就绪 · 发现 ${preparation.ports.length} 个串口${preparation.installed?.length?' · 已安装 '+preparation.installed.join('、'):''}。`+
   (preparation.ports.length>1&&!preparation.runtime_port?(choices.value?'已选择 '+choices.value+'。':'请明确选择舵机总线串口。'):'进入标定前会再次核对串口。'):'尚未检查主板环境。');
}
let setupLogTimer=null,setupLogRequest=null;
async function readSetupLog(){
 if(setupLogRequest)return setupLogRequest;
 setupLogRequest=(async()=>{
  try{
   const response=await fetch('/api/setup-log');if(!response.ok)throw Error('HTTP '+response.status);
   const log=await response.json(),element=$('setup-log');
   const follow=element.scrollHeight-element.scrollTop-element.clientHeight<35;
   const content=log.lines.join('\n');
   if(content&&element.textContent!==content){element.textContent=content;if(follow)element.scrollTop=element.scrollHeight;}
   $('setup-log-state').textContent=log.running?'进行中…':log.run?'已结束':'';
   if(log.running&& !setupLogTimer){$('setup-log-panel').open=true;setupLogTimer=setInterval(readSetupLog,600);}
   if(!log.running&&!busy&&setupLogTimer){clearInterval(setupLogTimer);setupLogTimer=null;}
  }catch(e){$('setup-log-state').textContent='日志读取失败：'+e.message;}
 })();
 try{await setupLogRequest;}finally{setupLogRequest=null;}
}
function startSetupLog(){
 $('setup-log-panel').open=true;$('setup-log-state').textContent='正在连接…';
 if(!setupLogTimer)setupLogTimer=setInterval(readSetupLog,600);
}
async function finishSetupLog(){
 if(setupLogTimer)clearInterval(setupLogTimer);setupLogTimer=null;
 if(setupLogRequest)await setupLogRequest;
 await readSetupLog();
}
async function prepareBoard(){
 if(busy||active)return;
 busy=preparing=true;setupMessage='';preparation=null;render();$('error').hidden=true;startSetupLog();
 try{
  const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':info.csrf},body:JSON.stringify({action:'prepare'})});
  const data=await response.json();if(!data.ok)throw Error(data.error);
  preparation=data.result;
 }catch(e){setupMessage='环境准备失败；控制服务未停止。'+e.message;$('error').textContent=e.message;$('error').hidden=false;}
 finally{await finishSetupLog();busy=preparing=false;render();}
}
function zeroRunning(){return ['arming','moving'].includes(state?.zero_check?.phase);}
function selectJoint(id){selected=id;$('clear').checked=false;if(id===34)pose='zero';render();view?.set(selected,pose,true);}
function render(){
 renderConnection();
 $('joints').replaceChildren();
 ids.forEach((id,i)=>{
  if([10,20,30].includes(id)){const h=document.createElement('div');h.className='group-title';h.textContent=id===10?'右腿 / RIGHT':id===20?'左腿 / LEFT':'头颈 / HEAD';$('joints').append(h);}
  const b=document.createElement('button');b.className=id===selected?'selected':'';b.dataset.id=id;b.setAttribute('aria-current',String(id===selected));
  const label=document.createElement('span');label.textContent=`ID${id} · ${names[i]}`;
  const st=document.createElement('span');st.className='joint-state';st.textContent=state?.held?.includes(id)?'● 保持':state?.completed?.[id]?'✓':'○';if(state?.held?.includes(id))st.classList.add('amber');
  b.append(label,st);b.onclick=()=>selectJoint(id);$('joints').append(b);
 });
 const r=current(),held=state?.held?.includes(selected),saved=Boolean(state?.completed?.[selected]),pending=state?.pending?.id===selected?state.pending:null;
 $('joint-title').textContent=`ID${selected} · ${names[ids.indexOf(selected)]}`;
 $('position').textContent=r?r.position:'—';$('torque').textContent=active?(held?'保持中':'断力'):'未连接';
 $('progress').textContent=`${Object.keys(state?.completed||{}).length} / 15`;
 $('instruction').textContent=selected===34?'嘴部单独记录自然闭合与舒适张开。不要挤压连杆或顶住机械终点；这里没有嘴部转轴动画。':`把${names[ids.indexOf(selected)]}按装配基准摆到机械 0°。只核对关节两侧结构，其他断力关节不用同时摆正。`;
 $('body-sample').hidden=selected===34;$('mouth-sample').hidden=selected!==34;
 $('sample').disabled=$('closed').disabled=busy||!active||held;
 $('open').disabled=busy||!active||held||!state?.mouth_closed;
 $('apply').disabled=busy||!active||!pending||pose!=='zero';
 $('hold').disabled=busy||!active||held||!saved||!$('clear').checked;
 $('release').disabled=busy||!active||!held;$('undo').disabled=busy||!active||held||!saved;
 $('enter').disabled=busy||active||!$('supported').checked||(info.mode==='hardware'&&(!preparation?.ready||!$('serial-port').value));
 $('finish').disabled=busy||!sessionStarted;$('relax').disabled=!sessionStarted;
 $('zero-stop').disabled=!sessionStarted;
 $('zero-all').disabled=busy||!active||!state?.zero_inspection_supported||zeroRunning()||!$('zero-clear').checked;
 const z=state?.zero_check;
 $('zero-status').textContent=!active?'进入标定后可用；无需在本次会话重新标定所有关节。':!state?.zero_inspection_supported?'请先结束旧会话，再重新连接以使用全部回零功能。':!z?(info.eeprom_only&&!state?.completed?.['34']?'仅 EEPROM 模式：身体可逐颗标定；全部回零前需在本次会话完成嘴部闭合、张开两端点标定。':'就绪 · 归零只使用舵机已保存的零位，不改写标定。'):
 z.phase==='arming'?`正在逐颗接入保持 ${z.armed} / ${z.ids?.length||15} · 可随时全部断力`:
 z.phase==='moving'?`正在缓慢归零 ${z.progress}% · 距零位最大 ${z.max_error_deg.toFixed(2)}°`:
 z.phase==='holding'?`ID ${(z.ids||ids).join("、")} 已回零并保持 · 实测最大误差 ${z.max_error_deg.toFixed(2)}°`:
 z.phase==='failed'?`归零未完成：${z.error} · 请核对断力状态`:'归零已停止 · 再次开始需要重新勾选确认';
 if(zeroRunning())for(const id of ['sample','closed','open','apply','hold','release','undo'])$(id).disabled=true;
 $('offsets').textContent=r?`EEPROM 原始值 ${r.initial_offset_raw} → 当前 ${r.offset_raw}。身体 2048 = 机械零位，方向 −1；程序不再叠加此偏移。方向、限位与 IMU 安装矩阵不在此工具内改动。`:'尚未读取零位';
 $('preview').className='preview'+(pending?' ready':'');
 $('preview').textContent=pending?`已稳定采样：${pending.position} 计数\n零位偏移调整 ${(pending.correction_counts*360/4096).toFixed(2)}° · 30 秒内保持不动并保存${pending.mouth?'\n嘴部开度 '+((2048-pending.mouth.open_raw)*360/4096).toFixed(1)+'°':''}`:saved?'本次零位已保存。保持在刚标定的位置，可单独使能。':selected===34&&state?.mouth_closed?'闭嘴已采样，请手动张开后记录第二个端点。':'采样后显示零位改变量。';
 document.querySelectorAll('[data-pose]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.pose===pose));b.disabled=selected===34&&['positive','negative'].includes(b.dataset.pose);});
 $('ref-tag').textContent={zero:'机械零位 · 0°',home:'HOME · 仅供对照',positive:'当前关节 · +10°',negative:'当前关节 · −10°'}[pose];
 $('reference-note').textContent=pose==='home'?'HOME 是站立目标，不是机械零位。请切回“机械零位 0°”再采样保存。':'只需对齐选中关节两侧的结构关系；其余真实关节不用同时摆成图中的姿势。';
 if(pose!=='zero')$('sample').disabled=$('closed').disabled=$('open').disabled=true;
 if(state?.backup)$('backup').textContent='备份：'+state.backup;
 if(active&&state?.verified_at)$('live').textContent=`最近核验 ${new Date(state.verified_at*1000).toLocaleTimeString()} · ${state.held?.length||0} 颗保持 · ${zeroRunning()?'正在连续读取归零反馈':'页面每 3 秒读取'}`;
 $('readings').replaceChildren();
 for(const row of state?.servos||[]){
  const tr=document.createElement('tr');
  const values=[`ID${row.id} · ${row.name}`,state.completed?.[row.id]?'已保存':'未修改',active?(state.held?.includes(row.id)?'● 保持中':'断力'):'非实时记录',Number.isFinite(row.angle_deg)?row.angle_deg.toFixed(2)+'°':'—',row.hold_position??'—',row.voltage?.toFixed(1)+' V',row.temperature+' °C'];
  values.forEach((value,i)=>{const td=document.createElement('td');td.textContent=value;if(i===2&&state.held?.includes(row.id))td.className='amber';tr.append(td);});$('readings').append(tr);
 }
 $('overview-state').textContent=active?`${Object.keys(state?.completed||{}).length} 颗已保存 · ${state?.held?.length||0} 颗保持`:'没有实时会话 · 请勿把历史读数当作当前状态';
}
async function action(name,extra={},quiet=false){
 let recovered=false;
 if(busy&&name!=='relax')return;const sequence=++requestSequence;busy=true;render();if(!quiet)$('error').hidden=true;
 if(name==='enter'&&info.mode==='hardware')startSetupLog();
 if(name==='zero'||name==='relax')$('zero-clear').checked=false;
 try{
  const resp=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':info.csrf},body:JSON.stringify({action:name,motion_token:info.motion_token,...extra})});
  const data=await resp.json();if(sequence!==requestSequence)return;if(data.motion_token)info.motion_token=data.motion_token;
  if(Object.hasOwn(data,'preparation'))preparation=data.preparation;
  if(!data.ok){if(data.state?.servos&&data.active){state=data.state;active=true;sessionStarted=true;recovered=true;}throw Error(data.error);}
  info.motion_token=data.result.motion_token;
  if(data.result.closed){active=false;sessionStarted=false;state={...state,held:data.result.all_torque_off?[]:state?.held,pending:null};$('live').textContent=data.result.all_torque_off?'已结束 · 全部断力已核验':'已结束 · 断力未核验';if(data.result.error)throw Error(data.result.error);}
  else{state=data.result;active=true;sessionStarted=true;}
  if(name==='apply'||name==='hold')$('clear').checked=false;
 }catch(e){if(sequence===requestSequence){$('error').textContent=e.message;$('error').hidden=false;if(quiet&&!recovered){active=false;$('live').textContent='连接/核验中断，状态未知；请托稳。';}}}
 finally{if(name==='enter'&&info.mode==='hardware')await finishSetupLog();if(sequence===requestSequence){busy=false;render();}}
}
$('supported').onchange=$('clear').onchange=$('zero-clear').onchange=render;
$('prepare').onclick=prepareBoard;
$('serial-port').onchange=render;
$('enter').onclick=()=>action('enter',{supported:$('supported').checked,serial_port:$('serial-port').value||info.serial_port});
$('sample').onclick=()=>action('sample',{id:selected});
$('closed').onclick=()=>action('sample',{id:34,endpoint:'closed'});
$('open').onclick=()=>action('sample',{id:34,endpoint:'open'});
$('apply').onclick=()=>action('apply',{token:state.pending.token});
$('hold').onclick=()=>action('hold',{id:selected,clear:$('clear').checked});
$('release').onclick=()=>action('release',{id:selected});
$('undo').onclick=()=>action('undo',{id:selected});
$('relax').onclick=()=>action('relax');$('finish').onclick=()=>action('finish');
$('zero-all').onclick=()=>action('zero',{confirmed:$('zero-clear').checked});
$('zero-stop').onclick=()=>action('relax');
$('next').onclick=()=>{pose='zero';selectJoint(ids[(ids.indexOf(selected)+1)%ids.length]);};
document.querySelectorAll('[data-pose]').forEach(b=>b.onclick=()=>{pose=b.dataset.pose;render();view?.set(selected,pose);});
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view?.camera(b.dataset.view));
if(info.mode==='hardware')readSetupLog();
render();let lastPoll=0;setInterval(()=>{if(active&&!busy&&performance.now()-lastPoll>(zeroRunning()?200:3000)){lastPoll=performance.now();action('status',{},true);}},200);
try{view=await createView();view.set(selected,pose,true);$('model-loading').hidden=true;window.calibratorReady=true;}
catch(e){$('model-loading').textContent='三维模型未加载：'+e.message;}

const $ = id => document.getElementById(id);
let token='', state=null, watch=null, updating=false, polling=false, lastSent=-Infinity, candidateKey='';
async function api(path,body,method='POST') {
  const response=await fetch('/api/navigation/'+path,{method,headers:{'Content-Type':'application/json','X-Navigation-Token':token},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const result=await response.json();
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'输入无效，请检查坐标、时间和格式');
  return result;
}
async function run(fn){$('nav-error').textContent='';try{await fn();await refresh();}catch(e){$('nav-error').textContent=e.message;await refresh();}}
async function refresh(){if(polling||!token)return;polling=true;try{render(await api('state',undefined,'GET'));}catch(e){$('nav-error').textContent=e.message;state=null;$('nav-instruction').textContent='';updateSpeech(null);}finally{polling=false;}}
function stopWatch(){if(watch!==null){navigator.geolocation.clearWatch(watch);watch=null;}}
function render(s){
 state=s;$('map-mode').value=s.provider;$('nav-status').textContent=s.status;
 $('map-notice').textContent=s.simulated?'模拟模式：位置、目的地与路线为虚构演示，不能用于真实出行。':s.configured?'高德模式：搜索及定位转换/规划会按操作向高德发送相关位置和搜索词。实际路线、定位精度与覆盖尚需现场验证。':'高德或地图未配置：不会自动用模拟路线代替。请设置 AMAP_API_KEY 和 ALLOW_EXTERNAL_MAPS。';
 $('nav-message').textContent=s.message;$('nav-instruction').textContent=s.instruction||'';
 const l=s.location;
 $('location-info').textContent=l?l.source+' / '+l.point.coordinate_system+'（输入 '+l.original_coordinate_system+'） · 精度 '+l.accuracy_m+'m · '+l.age_s+'s 前 · '+(s.location_fault||l.status):'尚无位置'+(s.location_fault?' · '+s.location_fault:'');
 $('mock-start').disabled=!s.simulated;document.querySelectorAll('[data-fix]').forEach(b=>b.disabled=!s.simulated);
 $('browser-start').disabled=s.provider!=='amap';
 renderCandidates(s);
 const r=s.route;
 $('route-info').textContent=r?(r.source==='mock'?'虚构模拟路线':'高德返回路线')+' · '+Math.round(r.distance_m)+' 米 · 目的地：'+r.destination.label:'';
 const mapLink = $('route-map-link');
 mapLink.hidden = r?.source !== 'amap';
 if (!mapLink.hidden) mapLink.href = walkingLink(r.destination, r.origin, false);
 $('nav-speech-test').disabled = !s.speech_enabled;
 const list=$('route-steps');list.replaceChildren();if(r)r.steps.forEach((v,i)=>{const li=document.createElement('li');li.textContent=v.instruction+'（'+Math.round(v.distance_m)+'m）';if(i===s.step_index)li.className='current-step';list.append(li);});
 $('nav-progress').textContent='路线进度约 '+s.progress_m+'m'+(s.distance_to_route_m===null?'':' · 距路线约 '+s.distance_to_route_m+'m');
 updateSpeech(s);
 draw(s);
}
let speechKey = '', queuedSpeech = '', speechURL = null;
function instructionKey(s) {
  return s?.speech_enabled && s.instruction && !s.recording_active
    ? s.route.route_id + ':' + s.step_index + ':' + s.instruction : '';
}
function clearNavAudio() {
  const player = $('nav-audio');
  player.pause();
  player.removeAttribute('src');
  player.load();
  player.hidden = true;
  if (speechURL) URL.revokeObjectURL(speechURL);
  speechURL = null;
}
function updateSpeech(s) {
  const key = instructionKey(s);
  $('nav-speak').disabled = !key;
  if (key === speechKey) return;
  speechKey = key;
  if (queuedSpeech !== 'test') clearNavAudio();
  $('nav-speech-info').textContent = '';
  if (key && $('nav-auto').checked) playInstruction();
}
async function playInstruction(test = false) {
  const key = test ? 'test' : instructionKey(state);
  if (!key || voiceActive || queuedSpeech === key || queuedSpeech === 'test') return;
  const player = $('nav-audio');
  if (!test && player.dataset.instruction === key && player.src) {
    player.play().catch(() => {});
    return;
  }
  queuedSpeech = key;
  try {
    const endpoint = test ? 'speech-test' : 'speech';
    const response = await fetch('/api/navigation/' + endpoint, {
      method: 'POST', headers: {'X-Navigation-Token': token}
    });
    if (!response.ok) throw new Error((await response.json()).detail);
    const clip = await response.blob();
    if (voiceActive || (!test && key !== instructionKey(state))) return;
    clearNavAudio();
    speechURL = URL.createObjectURL(clip);
    player.src = speechURL;
    player.dataset.instruction = key;
    player.hidden = false;
    await player.play();
    $('nav-speech-info').textContent = '';
  } catch (error) {
    if (test || key === instructionKey(state)) {
      $('nav-speech-info').textContent = error.name === 'NotAllowedError'
        ? '语音已生成，请点下方播放器的播放键。' : error.message;
    }
  } finally {
    if (queuedSpeech === key) queuedSpeech = '';
  }
}
$('nav-speak').addEventListener('click', () => playInstruction());
$('nav-speech-test').addEventListener('click', () => playInstruction(true));
$('nav-auto').addEventListener('change', () => {
  if ($('nav-auto').checked) playInstruction();
});
function draw(s){const c=$('route-canvas'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);const points=s.route?s.route.steps.flatMap(v=>v.geometry):[];if(!points.length){ctx.fillStyle='#9cacc0';ctx.font='18px sans-serif';ctx.fillText('规划后显示路线几何示意',30,60);return;}const l=s.location?.point;if(l)points.push(l);const xs=points.map(p=>p.longitude),ys=points.map(p=>p.latitude),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),scale=Math.min(520/Math.max(maxX-minX,0.0001),270/Math.max(maxY-minY,0.0001)),xy=p=>[40+(p.longitude-minX)*scale,310-(p.latitude-minY)*scale];ctx.strokeStyle='#75d9be';ctx.lineWidth=4;ctx.beginPath();s.route.steps.flatMap(v=>v.geometry).forEach((p,i)=>{const [x,y]=xy(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();for(const [p,color] of [[s.route.origin,'#75d9be'],[s.route.destination.point,'#ffcf7d'],[l,'#79aeed']]){if(!p)continue;const [x,y]=xy(p);ctx.fillStyle=color;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill();}}
$('map-mode').addEventListener('change',()=>run(async()=>{stopWatch();await api('mode',{value:$('map-mode').value});}));
$('manual-position').addEventListener('click',()=>run(()=>{if(!$('lon').value||!$('lat').value)throw new Error('请填写经纬度');return api('position',{longitude:Number($('lon').value),latitude:Number($('lat').value),coordinate_system:$('coord').value,source:'manual',accuracy_m:Number($('accuracy').value),captured_at:new Date().toISOString(),age_s:0});}));
$('mock-start').addEventListener('click',()=>run(()=>api('mock-position',{point:'start'})));
for(const b of document.querySelectorAll('[data-fix]'))b.addEventListener('click',()=>run(()=>api('mock-position',{point:b.dataset.fix})));
let submittedSearch = null;
async function searchPage(query, region, page) {
  const result = await api('search', {query, region, page});
  submittedSearch = {query, region, page};
  render(result);
}
$('search-form').addEventListener('submit', event => {
  event.preventDefault();
  run(() => searchPage($('destination-query').value, $('region').value, 1));
});
$('next-candidates').addEventListener('click', () => {
  if (submittedSearch) run(() => searchPage(
    submittedSearch.query, submittedSearch.region, submittedSearch.page + 1));
});
function amapLink(path, params) {
  const url = new URL('https://uri.amap.com/' + path);
  url.search = new URLSearchParams({...params, src: 'AIAA3800'}).toString();
  return url.href;
}
function walkingLink(candidate, origin = null, openApp = true) {
  const params = {to: [candidate.point.longitude, candidate.point.latitude, candidate.label.replace(/,/g, '，')].join(','),
    mode: 'walk', callnative: openApp ? '1' : '0'};
  if (origin) params.from = [origin.longitude, origin.latitude].join(',');
  return amapLink('navigation', params);
}
function renderCandidates(s) {
  const key = s.search_id || '';
  $('next-candidates').hidden = s.provider !== 'amap' || !submittedSearch
    || !s.candidates.length || submittedSearch.page >= 5;
  if (candidateKey === key) return;
  candidateKey = key;
  const box = $('candidates');
  box.replaceChildren();
  for (const candidate of s.candidates) {
    const label = document.createElement('p');
    label.textContent = [candidate.label, candidate.address].filter(Boolean).join(' · ');
    const plan = document.createElement('button');
    plan.textContent = '确认此目的地并规划';
    plan.className = 'secondary';
    plan.addEventListener('click', () => run(() => api('plan', {search_id: key, candidate_id: candidate.candidate_id})));
    box.append(label, plan);
    if (candidate.source === 'amap') {
      const link = document.createElement('a');
      link.href = walkingLink(candidate);
      link.textContent = '用高德步行导航';
      box.append(link);
    }
  }
  if (key && !s.candidates.length) box.textContent = '这一页暂无候选，可修改地点或在高德地图搜索。';
}
function syncAmapSearch(copyToWeb = true) {
  const input = $('amap-destination');
  const query = input.value.trim().replace(/^(?:(?:请\s*)?(?:带我去|导航到|导航去|我想去|我要去|去)\s*)/, '')
    .replace(/[。！？!?]+$/, '').trim();
  const link = $('amap-search');
  link.hidden = !query;
  if (!query) {
    link.removeAttribute('href');
    return;
  }
  const params = {keyword: query, view: 'map', callnative: '1'};
  const city = $('region').value.trim();
  if (city) params.city = city;
  link.href = amapLink('search', params);
  if (copyToWeb) $('destination-query').value = query;
}
$('amap-destination').addEventListener('input', syncAmapSearch);
$('region').addEventListener('input', () => syncAmapSearch(false));

let stopVoice = null, voiceActive = false;
$('nav-voice').addEventListener('click', async () => {
  if (stopVoice) {
    stopVoice();
    return;
  }
  if (!token) return;
  voiceActive = true;
  clearNavAudio();
  const button = $('nav-voice');
  const info = $('nav-voice-status');
  let context, stream, timer;
  button.disabled = true;
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      throw new Error('此浏览器无法录音，请在可信 HTTPS 页面打开，或输入地点。');
    }
    context = new AudioContext();
    await context.resume();
    stream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true
    }});
    await context.audioWorklet.addModule('/static/nav-pcm.js');
    const recorder = new AudioWorkletNode(context, 'short-recording');
    const source = context.createMediaStreamSource(stream);
    const input = new Float32Array(Math.floor(context.sampleRate * 10));
    let length = 0;
    recorder.port.onmessage = ({data}) => {
      const count = Math.min(data.length, input.length - length);
      input.set(data.subarray(0, count), length);
      length += count;
    };
    source.connect(recorder).connect(context.destination);
    button.disabled = false;
    button.textContent = '结束并识别';
    info.textContent = '正在录音。说完后点「结束并识别」。';
    await new Promise(resolve => {
      stopVoice = resolve;
      timer = setTimeout(resolve, 10000);
    });
    stopVoice = null;
    button.disabled = true;
    stream.getTracks().forEach(track => track.stop());
    await context.close();
    info.textContent = '电脑正在识别目的地…';
    const ratio = input.length / 160000;
    const pcm = new DataView(new ArrayBuffer(Math.floor(length / ratio) * 2));
    for (let i = 0; i < pcm.byteLength / 2; i++) {
      const begin = Math.floor(i * ratio);
      const end = Math.max(begin + 1, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = begin; j < end; j++) sum += input[j];
      pcm.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, sum / (end - begin))) * 32767), true);
    }
    const response = await fetch('/api/navigation/voice-destination', {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream', 'X-Navigation-Token': token},
      body: pcm.buffer
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail);
    $('amap-destination').value = result.text.slice(0, 80);
    syncAmapSearch();
    info.textContent = '请核对识别结果，再点击「在高德地图搜索」。';
  } catch (error) {
    info.textContent = error.name === 'NotAllowedError' ? '请允许浏览器使用麦克风，或输入目的地。' : error.message;
  } finally {
    clearTimeout(timer);
    voiceActive = false;
    stopVoice = null;
    stream?.getTracks().forEach(track => track.stop());
    if (context && context.state !== 'closed') await context.close();
    button.disabled = false;
    button.textContent = '说出目的地';
  }
});
for(const [id,endpoint] of [['nav-start','start'],['nav-pause','pause'],['nav-stop','stop']])$(id).addEventListener('click',()=>run(()=>api(endpoint,{})));
$('browser-stop').addEventListener('click',()=>run(async()=>{stopWatch();$('browser-info').textContent='定位已停止';await api('location-fault',{status:'stopped'});}));
$('browser-start').addEventListener('click', () => run(async () => {
  if (!window.isSecureContext || !navigator.geolocation) {
    throw new Error('请在可信 HTTPS 页面开启定位，或由高德 App 获取当前位置。');
  }
  stopWatch();
  lastSent = -Infinity;
  let latestTimestamp = -Infinity;
  const info = $('browser-info');
  info.textContent = '等待手机定位，请保持网页在前台。';
  const id = navigator.geolocation.watchPosition(async fix => {
    if (watch !== id || updating || fix.timestamp <= latestTimestamp
        || performance.now() - lastSent < 3000) return;
    updating = true;
    lastSent = performance.now();
    try {
      await api('position', {
        longitude: fix.coords.longitude, latitude: fix.coords.latitude,
        accuracy_m: fix.coords.accuracy, coordinate_system: 'WGS84', source: 'browser',
        captured_at: new Date(fix.timestamp).toISOString(),
        age_s: Math.max(0, (Date.now() - fix.timestamp) / 1000)
      });
      latestTimestamp = fix.timestamp;
      if (watch === id) info.textContent = '正在接收浏览器位置，请保持网页在前台。';
    } catch (error) {
      if (watch === id) info.textContent = error.message;
    } finally {
      updating = false;
      await refresh();
    }
  }, async error => {
    if (watch !== id) return;
    if (error.code !== 1) {
      info.textContent = '暂未收到新位置，正在等待手机更新。也可用高德 App 定位。';
      return;
    }
    stopWatch();
    info.textContent = '定位权限被拒绝，可在浏览器设置中开启。';
    try {
      await api('location-fault', {status: 'denied'});
      await refresh();
    } catch (failure) {
      info.textContent = failure.message;
    }
  }, {enableHighAccuracy: true, maximumAge: 0, timeout: 20000});
  watch = id;
}));
async function init(){try{token=(await api('session',undefined,'GET')).token;await refresh();setInterval(refresh,1000);}catch(e){$('nav-error').textContent=e.message;}}
const destinationDraft = sessionStorage.getItem('destination-draft');
if (destinationDraft) {
  $('amap-destination').value = destinationDraft;
  syncAmapSearch();
  sessionStorage.removeItem('destination-draft');
}
init();

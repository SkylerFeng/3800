const $ = id => document.getElementById(id);
let state = null, currentFrame = null, busy = false, shownAudio = null;
let audioContext = null, toneLevel = null, lastToneAt = -Infinity;
let livePreview = null;
let previewURL = null;

function clearPreview() {
  $('preview').removeAttribute('src');
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
}

async function receivePreview(controller) {
  const {signal} = controller;
  let after = '';
  while (!signal.aborted) {
    try {
      const response = await fetch('/api/camera/live-frame?after=' + encodeURIComponent(after), {signal});
      if (response.status === 200) {
        const image = await response.blob();
        if (signal.aborted) break;
        after = response.headers.get('X-Frame-ID');
        const oldURL = previewURL;
        previewURL = URL.createObjectURL(image);
        currentFrame = after;
        $('preview').src = previewURL;
        $('preview').hidden = false;
        $('no-image').hidden = true;
        if (oldURL) URL.revokeObjectURL(oldURL);
        continue;
      }
    } catch (error) {
      if (signal.aborted) break;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

function renderPreview(camera) {
  const frame = camera.frame;
  const live = camera.mode === 'esp32';
  const visible = Boolean(frame && camera.status === 'valid');
  $('preview').hidden = !visible;
  $('no-image').hidden = visible;
  if (!visible) $('no-image').textContent = camera.status === 'stale' ? '实时画面已过期' : camera.message;
  if (live) {
    if (!livePreview) {
      currentFrame = null;
      clearPreview();
      livePreview = new AbortController();
      receivePreview(livePreview);
    }
    if (!visible) clearPreview();
    return;
  }
  if (livePreview) {
    livePreview.abort();
    livePreview = null;
    currentFrame = null;
    clearPreview();
  }
  if ((frame?.frame_id || null) !== currentFrame) {
    currentFrame = frame?.frame_id || null;
    if (frame) $('preview').src = '/api/frame?id=' + encodeURIComponent(frame.frame_id);
    else clearPreview();
  }
}
async function api(path, body, method='POST') {
  const response = await fetch(path, {method, headers: body instanceof File ? {'Content-Type':body.type} : {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body: body instanceof File ? body : JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '输入校验失败，请检查格式和范围');
  return data;
}
async function action(fn) {
  $('error').textContent = '';
  try {await fn(); await refresh();} catch(e) {$('error').textContent = e.message;}
}
function render(s) {
  state = s;
  $('connection').textContent = '本机服务已连接';
  $('camera-mode').value = s.camera.mode;
  $('vision-mode').value = s.vision.provider;
  $('telemetry-mode').value = s.telemetry.source;
  $('vision-info').textContent = s.vision.message + (s.vision.model ? ' · 模型：' + s.vision.model : '');
  $('vision-notice').textContent = s.vision.provider === 'mock' ? '当前视觉模型返回固定模拟内容。' : s.vision.message + '。真实模型回答仍可能出错；测距、触觉与硬件状态独立显示。';
  const f = s.camera.frame;
  renderPreview(s.camera);
  $('image-label').textContent = !f ? '等待图像' : f.simulated ? '模拟 · 静态素材' : f.static ? '本地上传 · 静态素材' : 'ESP32 · 实时画面';
  $('frame-info').textContent = f ? `${f.width} × ${f.height} · frame ${f.frame_id.slice(0,8)} · 接收于 ${new Date(f.received_at).toLocaleTimeString()} · ${f.age_s}s 前 · ${f.static ? '静态素材' : s.camera.status}` : s.camera.message;
  $('mock-distance').hidden = s.telemetry.source !== 'mock';
  $('distance-value').textContent = s.distance.status === 'valid' ? s.distance.distance_m.toFixed(2) : '—';
  const distanceLabels = {
    valid: '有效距离', no_target: '无有效回波', out_of_range: '超出设定量程',
    too_close: '低于测距下限', interference: '回波受干扰', stale: '数据已过期',
    fault: '测距异常', waiting: '等待数据', not_installed: '声纳未就绪', unconfigured: '声纳未配置'
  };
  $('warning-level').textContent = s.distance.level;
  $('warning-level').className = 'badge ' + s.distance.level;
  updateObstacleTone(s.distance.level);
  $('distance-info').textContent = `${distanceLabels[s.distance.status]} (${s.distance.status}) · ${s.distance.age_s}s 前 · ${s.distance.message}`;
  $('thresholds').textContent = `紧急 ≤ ${s.thresholds.critical_m}m；提醒 ≤ ${s.thresholds.warning_m}m；退出迟滞 ${s.thresholds.hysteresis_m}m；有效期 ${s.thresholds.ttl_s}s`;
  $('haptic-event').textContent = s.haptic.last_event ? JSON.stringify(s.haptic.last_event, null, 2) : '尚无触觉请求';
  const t = s.current_task;
  $('task-status').textContent = t ? `${t.status} · ${t.simulated ? '模拟模型' : t.provider}` : '等待提问';
  $('answer').textContent = t ? t.answer || t.error || '正在处理问题…' : '选择图片，然后输入问题。';
  $('task-info').textContent = t ? `request ${t.request_id.slice(0,8)} · frame ${t.frame_id.slice(0,8)} · ${t.frame_source} / ${t.frame_static ? '静态素材' : '实时快照'}${t.elapsed_s === undefined ? '' : ' · '+t.elapsed_s+'s'}` : '';
  $('audio-info').textContent = s.audio.message;
  const recording = s.recording;
  const listening = recording.status === 'recording';
  const review = recording.status === 'ready';
  $('recording-info').textContent = recording.error || (listening ? '正在录音，再短按 BOOT 结束' :
    review ? `完整录音 ${recording.duration_s}s，识别后可核对并提交` :
    s.telemetry.source === 'esp32' ? '短按眼镜 BOOT 开始录音，再按一次结束。' : '');
  $('transcribe').hidden = s.telemetry.source !== 'esp32';
  $('transcribe').disabled = !review || recording.transcribing || recording.provider === 'unconfigured';
  const player = $('answer-audio');
  const warn = ['critical', 'warning'].includes(s.distance.level);
  const clip = !listening && !($('obstacle-tone').checked && warn) && s.audio.status === 'ready' ? s.audio.request_id : null;
  if (clip !== shownAudio) {
    shownAudio = clip;
    player.pause();
    if (clip) {
      player.src = '/api/audio/' + encodeURIComponent(clip);
      if ($('auto-answer').checked) player.play().catch(() => {});
    } else {
      player.removeAttribute('src');
      player.load();
    }
  }
  player.hidden = !clip;
  $('live-consent-label').hidden = !(s.camera.mode === 'esp32' && s.vision.external_upload_enabled);
  $('ask').disabled = !f || s.camera.status !== 'valid';
  const imu = s.imu.rpy_deg ? `横滚 / 俯仰 / 偏航 ${s.imu.rpy_deg.map(v => v.toFixed(1) + '°').join(' / ')} · ${s.imu.age_s}s 前` : s.imu.status;
  const health = s.telemetry.health;
  const entries = [
    ['图像', `${s.camera.mode} / ${s.camera.status}`],
    ['设备连接', s.telemetry.message + (health ? ` · ${health.rssi_dbm} dBm` : '')],
    ['测距', `${s.distance.source} / ${s.distance.status} / ${s.distance.age_s}s`],
    ['IMU', imu], ['麦克风', health ? (health.microphone_ready ? '已就绪' : '设备报告不可用') : '等待设备状态'],
    ['触觉', s.haptic.status], ['模型', `${s.vision.provider} / ${s.vision.status}`],
    ['语音', s.audio.status], ['定位 / 地图', s.location.status + ' / ' + s.map.provider],
    ['最近错误', s.last_error || '暂无']
  ];
  $('modules').replaceChildren(...entries.flatMap(([key,value])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=value;return [dt,dd];}));
  $('events').textContent = s.events.slice().reverse().map(e=>JSON.stringify(e)).join('\n') || '等待事件';
}
function updateObstacleTone(level) {
  if (!$('obstacle-tone').checked || !audioContext || audioContext.state !== 'running') return;
  if (level !== 'critical' && level !== 'warning') {
    toneLevel = null;
    return;
  }
  const now = audioContext.currentTime;
  if (toneLevel === level && now - lastToneAt < 1.5) return;
  toneLevel = level;
  lastToneAt = now;
  const oscillator = audioContext.createOscillator();
  const volume = audioContext.createGain();
  oscillator.frequency.value = level === 'critical' ? 880 : 560;
  volume.gain.setValueAtTime(.12, now);
  volume.gain.exponentialRampToValueAtTime(.001, now + .2);
  oscillator.connect(volume).connect(audioContext.destination);
  oscillator.start(now);
  oscillator.stop(now + .22);
}
$('obstacle-tone').addEventListener('change', async () => {
  if (!$('obstacle-tone').checked) {
    toneLevel = null;
    $('tone-info').textContent = '';
    return;
  }
  try {
    audioContext ||= new AudioContext();
    await audioContext.resume();
    updateObstacleTone(state?.distance.level);
  } catch {
    $('obstacle-tone').checked = false;
    $('tone-info').textContent = '浏览器无法播放提示音。';
  }
});
async function refresh() {
  if (busy) return;
  busy = true;
  try {render(await api('/api/state', undefined, 'GET'));} catch(e) {$('connection').textContent='服务连接失败'; $('error').textContent=e.message;} finally {busy=false;}
}
for (const [id,module] of [['camera-mode','camera'],['vision-mode','vision'],['telemetry-mode','telemetry']]) {
  $(id).addEventListener('change',()=> action(async()=>{if(module==='telemetry') $('continuous').checked=false; await api('/api/mode/'+module,{value:$(id).value});}));
}
$('image-file').addEventListener('change',()=>action(async()=>{const file=$('image-file').files[0];if(!file)return;if(file.size>5*1024*1024)throw new Error('图片不能超过 5 MiB');await api('/api/image',file);$('image-file').value='';}));
for (const button of document.querySelectorAll('[data-question]')) {
  button.addEventListener('click', () => {
    $('question').value = button.dataset.question;
    $('question').focus();
  });
}
$('question-form').addEventListener('submit', event => {
  event.preventDefault();
  const allowLiveUpload = !$('live-consent-label').hidden && $('live-consent').checked;
  $('live-consent').checked = false;
  action(() => api('/api/questions', {
    question: $('question').value,
    frame_id: state.camera.mode === 'esp32' ? null : currentFrame,
    allow_live_upload: allowLiveUpload
  }));
});
$('use-destination').addEventListener('click', () => {
  const query = $('question').value.trim().replace(/^(?:请\s*)?(?:带我去|导航到|我想去|我要去|去)\s*/, '').replace(/[。！？!?]$/, '').trim();
  if (!query || query.length > 80) {
    $('error').textContent = '请输入 1～80 个字的目的地名称。';
    $('question').focus();
    return;
  }
  sessionStorage.setItem('destination-draft', query);
  window.location.assign('/navigation');
});
$('transcribe').addEventListener('click', () => action(async () => {
  $('transcribe').disabled = true;
  const result = await api('/api/recording/transcribe', {stream_id: state.recording.stream_id});
  $('question').value = result.text;
  if (result.simulated) $('error').textContent = '当前识别结果为固定演示文本。';
  $('question').focus();
}));
$('cancel').addEventListener('click',()=>action(()=>api('/api/cancel',{})));
$('distance').addEventListener('input',()=>{$('slider-value').textContent=Number($('distance').value).toFixed(2)+' m';});
function sendDistance(){return api('/api/distance',{status:'valid',distance_m:Number($('distance').value)});}
$('apply-distance').addEventListener('click',()=>action(sendDistance));
for (const button of document.querySelectorAll('[data-scenario]')) button.addEventListener('click',()=>action(async()=>{ $('continuous').checked=false;const s=button.dataset.scenario; if(s==='near'||s==='normal') { $('distance').value=s==='near'?.3:2; $('distance').dispatchEvent(new Event('input')); await sendDistance(); }else await api('/api/distance',{status:s,distance_m:null}); }));
let sending=false;
setInterval(async()=>{if($('continuous').checked&&!sending){sending=true;try{await sendDistance();}catch(e){$('error').textContent=e.message;$('continuous').checked=false;}finally{sending=false;}}await refresh();},500);
refresh();

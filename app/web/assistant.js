const $ = id => document.getElementById(id);
const player = $('audio');
let session = null;
let microphone = null;
let busy = false;
let playing = false;
let clipURL = null;
let audioSource = null;
let lastRouteKey = '';
let sessionVersion = 0;
let starting = null;
let turnController = null;
let arrivalRoute = null;
let mapLinkAvailable = false;
let handoffPending = false;
let handoffTimer = null;
const HANDOFF_SESSION_KEY = '3800.assistant.handoff.v1';
const SETTINGS_KEY = '3800.assistant.preferences.v1';
const phoneMode = /\b3800Mobile\b/.test(navigator.userAgent);
const nativeMic = /\b3800NativeMic\b/.test(navigator.userAgent);
let nativeMicPort = null;
window.addEventListener('message', event => {
  if (nativeMic && event.data === '3800 microphone' && event.ports.length === 1) {
    if (nativeMicPort) nativeMicPort.close();
    nativeMicPort = event.ports[0];
  }
});
$('phone-mode').hidden = !phoneMode;

function saveSettings() {
  try {
    if ($('remember-settings').checked) {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(permissions()));
      $('settings-status').textContent = '已保存到当前浏览器。';
    } else {
      localStorage.removeItem(SETTINGS_KEY);
      $('settings-status').textContent = '设置仅在当前页面有效。';
    }
  } catch (error) {
    $('settings-status').textContent = '浏览器无法保存设置。';
  }
}

try {
  const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
  if (saved && typeof saved === 'object') {
    $('allow-frame').checked = saved.allow_frame === true;
    $('allow-location').checked = saved.allow_location === true;
    $('region').value = typeof saved.region === 'string' ? saved.region.slice(0, 40) : '';
    $('remember-settings').checked = true;
    $('settings-status').textContent = '已恢复此浏览器的设置。';
  }
} catch (error) {
  // Browser storage is optional.
}
$('settings').open = !$('remember-settings').checked;
for (const id of ['allow-frame', 'allow-location', 'region', 'remember-settings']) {
  $(id).addEventListener('change', saveSettings);
}

function updateMapLink() {
  const destination = $('destination').value.trim();
  const link = $('open-amap');
  link.hidden = !(mapLinkAvailable && $('allow-location').checked && destination);
  if (link.hidden) {
    link.removeAttribute('href');
    return;
  }
  const url = new URL('https://uri.amap.com/search');
  url.search = new URLSearchParams({keyword: destination, view: 'map', callnative: '1', src: 'AIAA3800'});
  if ($('region').value.trim()) url.searchParams.set('city', $('region').value.trim());
  link.href = url.href;
}

function keepHandoffSession() {
  if (!session) return;
  handoffPending = true;
  try {
    sessionStorage.setItem(HANDOFF_SESSION_KEY, JSON.stringify({
      token: session.token, navToken: session.navToken, ...permissions()
    }));
  } catch (error) {
    // The current page can still resume if it stays in memory.
  }
  clearTimeout(handoffTimer);
  handoffTimer = setTimeout(() => {
    if (!document.hidden) report('如果高德没有打开，请点「打开高德地图 App」。');
  }, 1800);
}

function showHandoff(action) {
  const isWalk = action.kind === 'walk';
  const url = new URL('https://uri.amap.com/' + (isWalk ? 'navigation' : 'search'));
  url.search = new URLSearchParams(isWalk
    ? {to: [action.longitude, action.latitude, action.label.replace(/,/g, '，')].join(','),
       mode: 'walk', callnative: '1', src: 'AIAA3800'}
    : {keyword: action.label, view: 'map', callnative: '1', src: 'AIAA3800'});
  if (!isWalk && action.region) url.searchParams.set('city', action.region);
  const androidWalk = isWalk && /Android/i.test(navigator.userAgent);
  const params = new URLSearchParams({
    featureName: 'OnFootNavi', sourceApplication: 'AIAA3800',
    lat: action.latitude, lon: action.longitude, dev: '0'
  });
  const intent = 'intent://openFeature?' + params + '#Intent;scheme=amapuri;package=com.autonavi.minimap;';
  $('handoff-app').href = androidWalk
    ? intent + 'S.browser_fallback_url=' + encodeURIComponent(url.href) + ';end' : url.href;
  url.searchParams.set('callnative', '0');
  $('handoff-web').href = url.href;
  $('handoff-panel').hidden = false;
  keepHandoffSession();
  report('正在尝试打开高德。');
  try {
    if (androidWalk) window.location.assign(intent + 'end');
    else if (/Android|iPhone|iPad/i.test(navigator.userAgent)) window.location.assign($('handoff-app').href);
  } catch (error) {
    report('浏览器阻止了自动跳转，请点「打开高德地图 App」。');
  }
}

for (const id of ['handoff-app', 'handoff-web']) {
  $(id).addEventListener('click', keepHandoffSession);
}

function clearHandoff() {
  handoffPending = false;
  clearTimeout(handoffTimer);
  try { sessionStorage.removeItem(HANDOFF_SESSION_KEY); } catch (error) {}
}

$('destination').addEventListener('input', updateMapLink);
$('region').addEventListener('change', updateMapLink);
$('search-destination').addEventListener('click', () => {
  const destination = $('destination').value.trim();
  if (destination) sendText('带我去' + destination);
});
$('forget-settings').addEventListener('click', async () => {
  $('remember-settings').checked = $('allow-frame').checked = $('allow-location').checked = false;
  $('region').value = '';
  saveSettings();
  $('settings-status').textContent = '已清除授权偏好。麦克风和定位的系统权限可在浏览器设置中撤销。';
  await stopSession();
  report('已结束会话。');
});

async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : '助手请求失败，请重试。');
  }
  return response;
}

function assistantHeaders(headers = {}) {
  return {'X-Assistant-Token': session.token, ...headers};
}

function navigationHeaders() {
  return {'X-Navigation-Token': session.navToken, 'Content-Type': 'application/json'};
}

function report(message) {
  $('status').textContent = message;
}

function clearAudio() {
  if (audioSource) {
    audioSource.stop();
    audioSource = null;
  }
  player.pause();
  player.removeAttribute('src');
  player.load();
  player.hidden = true;
  if (clipURL) URL.revokeObjectURL(clipURL);
  clipURL = null;
}

async function startSession() {
  if (session) return;
  if (starting) return starting;
  starting = openSession();
  try { await starting; } finally { starting = null; }
}

async function openSession() {
  const {token: navToken} = await (await api('/api/navigation/session')).json();
  const {token} = await (await api('/api/assistant/session', {
    method: 'POST', headers: {'X-Navigation-Token': navToken}
  })).json();
  session = {token, navToken, watch: null};
  sessionVersion++;
  lastRouteKey = '';
  arrivalRoute = null;
  $('stop').disabled = false;
  if ($('allow-location').checked) await startLocation();
}

async function startLocation() {
  if (!session || session.watch !== null) return;
  const active = session;
  const nav = await (await api('/api/assistant/state', {headers: assistantHeaders()})).json();
  if (session !== active || active.watch !== null || !$('allow-location').checked) return;
  if (nav.mode !== 'amap') {
    $('location-status').textContent = nav.mode === 'mock' ? '正在使用模拟路线。' : '地图尚未配置。';
    return;
  }
  if (!navigator.geolocation || !window.isSecureContext) {
    $('location-status').textContent = '手机定位需要可信 HTTPS 和位置权限。';
    return;
  }
  let timestamp = -Infinity;
  let sent = -Infinity;
  let pending = false;
  $('location-status').textContent = '等待手机位置…';
  active.watch = navigator.geolocation.watchPosition(async fix => {
    if (session !== active || active.watch === null || pending || fix.timestamp <= timestamp || performance.now() - sent < 3000) return;
    pending = true;
    sent = performance.now();
    try {
      const response = await api('/api/navigation/position', {
        method: 'POST', headers: navigationHeaders(),
        body: JSON.stringify({
          longitude: fix.coords.longitude, latitude: fix.coords.latitude,
          accuracy_m: fix.coords.accuracy, coordinate_system: 'WGS84', source: 'browser',
          captured_at: new Date(fix.timestamp).toISOString(),
          age_s: Math.max(0, (Date.now() - fix.timestamp) / 1000)
        })
      });
      const position = await response.json();
      timestamp = fix.timestamp;
      if (session === active) $('location-status').textContent = position.location && position.location.status === 'valid'
        ? '手机定位已就绪。' : '正在等待精度足够的新位置。';
    } catch (error) {
      if (session === active) $('location-status').textContent = error.message;
    } finally {
      pending = false;
    }
  }, error => {
    if (session !== active || active.watch === null) return;
    $('location-status').textContent = error.code === 1 ? '手机位置权限被拒绝。' : '正在等待手机更新位置。';
    if (error.code === 1) invalidateLocation(active);
  }, {enableHighAccuracy: true, maximumAge: 0, timeout: 20000});
}

function invalidateLocation(active) {
  return fetch('/api/navigation/location-fault', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'X-Navigation-Token': active.navToken, 'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'stopped'}), keepalive: true
  }).catch(() => {});
}

function stopLocation(active) {
  if (!active || active.watch === null) return;
  navigator.geolocation.clearWatch(active.watch);
  active.watch = null;
  invalidateLocation(active);
}

async function stopSession({remote = true} = {}) {
  if (starting) await starting.catch(() => {});
  const active = session;
  session = null;
  sessionVersion++;
  if (turnController) turnController.abort();
  turnController = null;
  busy = false;
  lastRouteKey = '';
  arrivalRoute = null;
  mapLinkAvailable = false;
  clearHandoff();
  $('handoff-panel').hidden = true;
  $('destination').value = '';
  $('destination-panel').hidden = true;
  updateMapLink();
  stopLocation(active);
  await stopMicrophone();
  clearAudio();
  $('stop').disabled = true;
  $('start-voice').disabled = false;
  if (remote && active) {
    await api('/api/assistant/session', {
      method: 'DELETE', headers: {'X-Assistant-Token': active.token}, keepalive: true
    }).catch(() => {});
  }
}

async function playResponse(response, version, kind = 'reply') {
  const blob = await response.blob();
  if (sessionVersion !== version) return;
  clearAudio();
  clipURL = URL.createObjectURL(blob);
  player.dataset.kind = kind;
  player.src = clipURL;
  player.hidden = false;
  playing = true;
  try {
    const context = microphone && microphone.context;
    if (context && context.state === 'running') {
      const buffer = await context.decodeAudioData(await blob.arrayBuffer());
      if (sessionVersion !== version || player.src !== clipURL || !clipURL) return;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      audioSource = source;
      player.hidden = true;
      await new Promise(resolve => {
        source.onended = resolve;
        source.start();
      });
      if (audioSource === source) audioSource = null;
      return;
    }
    await player.play();
    await new Promise(resolve => {
      const done = () => {
        for (const event of events) player.removeEventListener(event, done);
        resolve();
      };
      const events = ['ended', 'pause', 'error', 'emptied'];
      for (const event of events) player.addEventListener(event, done);
      if (player.paused || player.ended) done();
    });
  } catch (error) {
    player.hidden = false;
    report('回复已生成。请点播放，播完后继续说。');
  } finally {
    playing = false;
  }
}

function permissions() {
  return {
    region: $('region').value,
    allow_frame: $('allow-frame').checked,
    allow_location: $('allow-location').checked
  };
}

async function handleTurn(path, options) {
  if (busy) return;
  busy = true;
  const controller = new AbortController();
  turnController = controller;
  try {
    await startSession();
    if (controller.signal.aborted) return;
    const version = sessionVersion;
    clearAudio();
    report('正在处理…');
    const headers = assistantHeaders(options.headers);
    if (phoneMode) headers['X-3800-Camera'] = 'phone';
    let result = await (await api(path, {
      ...options, headers, signal: controller.signal
    })).json();
    if (result.snapshot_id) {
      report('正在从眼镜取图…');
      const local = await fetch('/_3800/phone-snapshot', {credentials: 'same-origin', signal: controller.signal});
      const cameraError = local.ok ? '' : await local.text();
      result = await (await api('/api/assistant/snapshot/' + encodeURIComponent(result.snapshot_id), {
        method: 'POST', headers: assistantHeaders({'Content-Type': 'image/jpeg'}),
        signal: controller.signal, body: local.ok ? await local.blob() : null
      })).json();
      if (cameraError) result.reply += ' ' + cameraError;
    }
    if (sessionVersion !== version) return;
    $('heard').textContent = result.heard.trim() ? '你说：' + result.heard : '';
    $('reply').textContent = result.reply;
    if (result.destination) {
      $('destination').value = result.destination;
      $('destination-panel').hidden = false;
      mapLinkAvailable = result.amap_available === true;
      updateMapLink();
    }
    if (result.ended) {
      await stopSession({remote: false});
      report(result.reply);
      return;
    }
    if (result.repeat_route) lastRouteKey = '';
    if (result.handoff && $('allow-location').checked) {
      showHandoff(result.handoff);
      if (!result.audio_id) return;
      await new Promise(resolve => setTimeout(resolve, 1800));
      if (document.hidden || !handoffPending || controller.signal.aborted) return;
    } else {
      report(microphone ? '正在听。' : '可继续输入或开启语音。');
    }
    if (result.audio_id) {
      const response = await api('/api/assistant/audio/' + encodeURIComponent(result.audio_id), {
        headers: assistantHeaders(), signal: controller.signal
      });
      if (!document.hidden) await playResponse(response, version);
    }
  } catch (error) {
    if (!controller.signal.aborted) report(error.message);
  } finally {
    if (turnController === controller) {
      turnController = null;
      busy = false;
    }
  }
}

function sendText(text) {
  handleTurn('/api/assistant/turn', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, ...permissions()})
  });
}
$('text-form').addEventListener('submit', event => {
  event.preventDefault();
  if (busy) return;
  const text = $('text').value.trim();
  if (!text) return;
  $('text').value = '';
  sendText(text);
});

function pcm16(chunks, length, sampleRate) {
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    samples.set(chunk, offset);
    offset += chunk.length;
  }
  const ratio = sampleRate / 16000;
  const pcm = new DataView(new ArrayBuffer(Math.min(160000, Math.floor(length / ratio)) * 2));
  for (let i = 0; i < pcm.byteLength / 2; i++) {
    const begin = Math.floor(i * ratio);
    const end = Math.max(begin + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = begin; j < end; j++) sum += samples[j];
    pcm.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, sum / (end - begin))) * 32767), true);
  }
  return pcm.buffer;
}

function listen(rate) {
  let chunks = [];
  let length = 0;
  let silence = 0;
  let audible = 0;
  let lead = [];
  function reset() {
    if (microphone) microphone.capturing = false;
    chunks = [];
    length = silence = audible = 0;
    lead = [];
  }
  return data => {
    if (!microphone || busy || playing || !player.paused || document.hidden) {
      reset();
      return;
    }
    let power = 0;
    for (const sample of data) power += sample * sample;
    const voiced = Math.sqrt(power / data.length) >= .018;
    if (!length && !voiced) {
      lead.push(data);
      if (lead.length > Math.ceil(.18 * rate / data.length)) lead.shift();
      return;
    }
    if (!length) {
      microphone.capturing = true;
      chunks = lead;
      length = lead.reduce((sum, chunk) => sum + chunk.length, 0);
      report('正在听你说话…');
    }
    chunks.push(data);
    length += data.length;
    if (voiced) {
      audible += data.length;
      silence = 0;
    } else {
      silence += data.length;
    }
    if (silence < .75 * rate && length < 9.7 * rate) return;
    if (audible >= .16 * rate) {
      const allowed = permissions();
      const params = new URLSearchParams({
        region: allowed.region,
        allow_location: allowed.allow_location ? '1' : '0',
        allow_frame: allowed.allow_frame ? '1' : '0'
      });
      handleTurn('/api/assistant/voice?' + params, {
        method: 'POST', headers: {'Content-Type': 'application/octet-stream'},
        body: pcm16(chunks, length, rate)
      });
    } else {
      report('正在听。');
    }
    reset();
  };
}

async function stopMicrophone() {
  const input = microphone;
  microphone = null;
  if (!input) return;
  if (input.port) {
    input.port.onmessage = null;
    input.port.postMessage('stop');
  }
  if (input.stream) input.stream.getTracks().forEach(track => track.stop());
  if (input.context && input.context.state !== 'closed') await input.context.close().catch(() => {});
}

function startNativeMic(button, context) {
  const port = nativeMicPort;
  microphone = {context, port};
  const feed = listen(16000);
  port.onmessage = async ({data}) => {
    if (!microphone || microphone.port !== port) return;
    if (data === 'started') {
      report('正在听。请让页面保持在前台。');
    } else if (['paused', 'permission', 'error'].includes(data)) {
      await stopMicrophone();
      button.disabled = false;
      report(data === 'paused' ? '录音已暂停。回到助手后可重新开启。'
        : data === 'permission' ? '请在手机设置中允许 3800 随身助手使用麦克风。'
          : 'Android 录音启动失败。请检查系统麦克风开关或关闭其他录音应用后重试。');
    } else {
      const bytes = atob(data);
      const samples = new Float32Array(Math.floor(bytes.length / 2));
      for (let i = 0; i < samples.length; i++) {
        const value = bytes.charCodeAt(i * 2) | (bytes.charCodeAt(i * 2 + 1) << 8);
        samples[i] = (value << 16 >> 16) / 32768;
      }
      if (samples.length) feed(samples);
    }
  };
  port.postMessage('start');
}

$('start-voice').addEventListener('click', async () => {
  const button = $('start-voice');
  button.disabled = true;
  let context, stream;
  try {
    if (nativeMic) {
      if (!nativeMicPort) throw new Error('手机录音正在初始化，请稍后重试。');
      if (window.AudioContext) {
        context = new AudioContext();
        context.resume().catch(() => {});
      }
      await startSession();
      startNativeMic(button, context);
      return;
    }
    if (!window.isSecureContext || !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) || !window.AudioContext) {
      throw new Error('请用可信 HTTPS 打开助手，或使用文字输入。');
    }
    context = new AudioContext();
    if (!context.audioWorklet || !window.AudioWorkletNode) {
      throw new Error('当前系统 WebView 不支持连续录音。请更新 Android System WebView，或使用文字指令。');
    }
    await context.resume();
    stream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true
    }});
    await context.audioWorklet.addModule('/static/nav-pcm.js');
    await startSession();
    const source = context.createMediaStreamSource(stream);
    const recorder = new AudioWorkletNode(context, 'short-recording');
    microphone = {context, stream, source, recorder};
    source.connect(recorder).connect(context.destination);
    const feed = listen(context.sampleRate);
    recorder.port.onmessage = ({data}) => feed(data);
    report('正在听。请让页面保持在前台。');
  } catch (error) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (context) await context.close().catch(() => {});
    report(error.name === 'NotAllowedError' ? '请允许麦克风权限，或改用文字输入。'
      : error.name === 'NotFoundError' ? '系统找不到可用麦克风。'
        : error.name === 'NotReadableError' ? '浏览器无法启动麦克风。请检查系统录音设置，或使用随身助手 App。' : error.message);
    button.disabled = false;
  }
});

$('stop').addEventListener('click', async () => {
  await stopSession();
  report('语音助手已结束。');
});
$('allow-location').addEventListener('change', () => {
  updateMapLink();
  if ($('allow-location').checked) startLocation().catch(error => report(error.message));
  else {
    clearHandoff();
    $('handoff-panel').hidden = true;
    stopLocation(session);
    $('location-status').textContent = '';
    if (['route', 'arrival'].includes(player.dataset.kind)) clearAudio();
  }
});
window.addEventListener('pagehide', () => {
  if (!handoffPending) stopSession();
});

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (handoffPending) clearAudio();
    return;
  }
  if (handoffPending) {
    clearHandoff();
    if (microphone && microphone.context) microphone.context.resume().catch(() => {});
    report(microphone ? '已回到助手，可以继续说话。' : '已回到助手，可以继续输入或开启语音。');
  }
});

async function restoreHandoffSession() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(HANDOFF_SESSION_KEY) || 'null'); } catch (error) {}
  if (!saved || !saved.token || !saved.navToken) return;
  clearHandoff();
  try {
    await api('/api/assistant/state', {headers: {'X-Assistant-Token': saved.token}});
    if (session) return;
    session = {token: saved.token, navToken: saved.navToken, watch: null};
    sessionVersion++;
    $('stop').disabled = false;
    $('allow-frame').checked = saved.allow_frame === true;
    $('allow-location').checked = saved.allow_location === true;
    $('region').value = typeof saved.region === 'string' ? saved.region.slice(0, 40) : '';
    if ($('allow-location').checked) await startLocation();
    report('已恢复会话。可以继续输入或开启语音。');
  } catch (error) {
    report(error.message);
  }
}

starting = restoreHandoffSession().finally(() => { starting = null; });

let polling = false;
setInterval(async () => {
  if (!session || polling || document.hidden) return;
  polling = true;
  const version = sessionVersion;
  try {
    const response = await api('/api/assistant/state', {headers: assistantHeaders()});
    const {navigation} = await response.json();
    if (version !== sessionVersion) return;
    $('route-status').textContent = navigation.route_id ? navigation.instruction || navigation.message : '';
    const key = navigation.instruction && [navigation.route_id, navigation.step_index, navigation.instruction].join(':');
    if (!key) {
      if (lastRouteKey && player.dataset.kind === 'route') clearAudio();
      lastRouteKey = '';
      if (navigation.near_destination && arrivalRoute !== navigation.route_id
          && !busy && !playing && player.paused && !(microphone && microphone.capturing)) {
        const audio = await api('/api/assistant/arrival-speech', {
          method: 'POST', headers: assistantHeaders()
        });
        if (version === sessionVersion && !busy && !(microphone && microphone.capturing)) {
          arrivalRoute = navigation.route_id;
          playResponse(audio, version, 'arrival').catch(error => report(error.message));
        }
      }
    } else if (key !== lastRouteKey && !busy && !playing && player.paused) {
      if ((microphone && microphone.capturing)) return;
      const audio = await api('/api/navigation/speech', {method: 'POST', headers: navigationHeaders()});
      if (version !== sessionVersion || busy || (microphone && microphone.capturing)) return;
      lastRouteKey = key;
      playResponse(audio, version, 'route').catch(error => report(error.message));
    }
  } catch (error) {
    if (version === sessionVersion) {
      report(error.message);
      if (error.message === '请重新开始助手会话') await stopSession({remote: false});
    }
  } finally {
    polling = false;
  }
}, 1000);

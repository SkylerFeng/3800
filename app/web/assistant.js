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

async function api(path, options = {}) {
  const response = await fetch(path, options);
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
      if (session === active) $('location-status').textContent = position.location?.status === 'valid'
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
    headers: {'X-Navigation-Token': active.navToken, 'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'stopped'}), keepalive: true
  }).catch(() => {});
}

function stopLocation(active) {
  if (active?.watch === null || active?.watch === undefined) return;
  navigator.geolocation.clearWatch(active.watch);
  active.watch = null;
  invalidateLocation(active);
}

async function stopSession({remote = true} = {}) {
  if (starting) await starting.catch(() => {});
  const active = session;
  session = null;
  sessionVersion++;
  turnController?.abort();
  turnController = null;
  busy = false;
  lastRouteKey = '';
  stopLocation(active);
  const input = microphone;
  microphone = null;
  input?.stream.getTracks().forEach(track => track.stop());
  if (input?.context.state !== 'closed') await input?.context.close().catch(() => {});
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
    const context = microphone?.context;
    if (context?.state === 'running') {
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
  } catch {
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
    const result = await (await api(path, {
      ...options, headers: assistantHeaders(options.headers), signal: controller.signal
    })).json();
    if (sessionVersion !== version) return;
    $('heard').textContent = '你说：' + result.heard;
    $('reply').textContent = result.reply;
    if (result.ended) {
      await stopSession({remote: false});
      report(result.reply);
      return;
    }
    if (result.repeat_route) lastRouteKey = '';
    report(microphone ? '正在听。' : '可继续输入或开启语音。');
    if (result.audio_id) {
      const response = await api('/api/assistant/audio/' + encodeURIComponent(result.audio_id), {
        headers: assistantHeaders(), signal: controller.signal
      });
      await playResponse(response, version);
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

$('text-form').addEventListener('submit', event => {
  event.preventDefault();
  if (busy) return;
  const text = $('text').value.trim();
  if (!text) return;
  $('text').value = '';
  handleTurn('/api/assistant/turn', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, ...permissions()})
  });
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

function listen(port, context) {
  const rate = context.sampleRate;
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
  port.onmessage = ({data}) => {
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

$('start-voice').addEventListener('click', async () => {
  const button = $('start-voice');
  button.disabled = true;
  let context, stream;
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      throw new Error('请用可信 HTTPS 打开助手，或使用文字输入。');
    }
    context = new AudioContext();
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
    listen(recorder.port, context);
    report('正在听。请让页面保持在前台。');
  } catch (error) {
    stream?.getTracks().forEach(track => track.stop());
    if (context) await context.close().catch(() => {});
    report(error.name === 'NotAllowedError' ? '请允许麦克风权限，或改用文字输入。' : error.message);
    button.disabled = false;
  }
});

$('stop').addEventListener('click', async () => {
  await stopSession();
  report('语音助手已结束。');
});
$('allow-location').addEventListener('change', () => {
  if ($('allow-location').checked) startLocation().catch(error => report(error.message));
  else stopLocation(session);
});
window.addEventListener('pagehide', () => { stopSession(); });

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
    } else if (key !== lastRouteKey && !busy && !playing && player.paused) {
      if (microphone?.capturing) return;
      const audio = await api('/api/navigation/speech', {method: 'POST', headers: navigationHeaders()});
      if (version !== sessionVersion || busy || microphone?.capturing) return;
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

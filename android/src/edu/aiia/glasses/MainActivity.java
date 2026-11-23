package edu.aiia.glasses;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaDataSource;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.text.InputFilter;
import android.text.InputType;
import android.text.method.PasswordTransformationMethod;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private static final int ASK_MIC = 1;
    private static final int SAMPLE_RATE = 16000;
    private static final int MAX_PCM = 320000;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private EditText camera, computer, password, question;
    private CheckBox cloud;
    private Button checkCamera, checkComputer, ask, voice, assistant;
    private TextView status, answer;
    private ImageView preview;
    private MediaPlayer player;
    private volatile boolean recording;
    private volatile int generation;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ScrollView screen = new ScrollView(this);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(20), dp(20), dp(20), dp(20));
        screen.addView(form);
        setContentView(screen);

        TextView title = new TextView(this);
        title.setText("3800 随身助手");
        title.setTextSize(26);
        title.setTypeface(null, Typeface.BOLD);
        title.setAccessibilityHeading(true);
        form.addView(title);

        camera = field(form, "眼镜 IP", "192.168.20.155", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        computer = field(form, "电脑 HTTPS 地址", "https://10.4.162.20:8443",
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        password = field(form, "电脑访问口令", "", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setTransformationMethod(PasswordTransformationMethod.getInstance());
        password.setSaveEnabled(false);
        camera.setText(getPreferences(MODE_PRIVATE).getString("camera", ""));
        computer.setText(getPreferences(MODE_PRIVATE).getString("computer", ""));
        checkCamera = button(form, "测试眼镜取图");
        checkComputer = button(form, "测试电脑连接");
        assistant = button(form, "打开完整语音助手");
        assistant.setOnClickListener(view -> {
            String host = computer.getText().toString().trim();
            try {
                Relay.hostUri(host);
                if (password.length() == 0) throw new IOException("请输入电脑访问口令。");
            } catch (IOException error) {
                status.setText(error.getMessage());
                return;
            }
            getPreferences(MODE_PRIVATE).edit().putString("camera", camera.getText().toString().trim())
                    .putString("computer", host).apply();
            startActivity(new Intent(this, AssistantActivity.class)
                    .putExtra("host", host).putExtra("camera", camera.getText().toString().trim())
                    .putExtra("password", password.getText().toString()));
        });

        question = field(form, "问题", "杯子在哪里？", InputType.TYPE_CLASS_TEXT);
        question.setFilters(new InputFilter[] {new InputFilter.LengthFilter(500)});
        cloud = new CheckBox(this);
        cloud.setText("允许电脑把本次图像和问题发送给配置的云视觉模型");
        form.addView(cloud);
        ask = button(form, "发送文字问题");
        voice = button(form, "开始录音");
        status = new TextView(this);
        status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        status.setText("先测试眼镜取图和电脑连接。");
        form.addView(status);
        answer = new TextView(this);
        answer.setTextSize(18);
        answer.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        form.addView(answer);
        preview = new ImageView(this);
        preview.setAdjustViewBounds(true);
        preview.setMaxHeight(dp(240));
        preview.setContentDescription("眼镜本次取得的画面");
        form.addView(preview);

        checkCamera.setOnClickListener(view -> start("正在从眼镜取图…", request -> {
            byte[] jpeg = Relay.snapshot(request.camera);
            return new Result("眼镜取图成功。画面保留在手机。", null, jpeg);
        }));
        checkComputer.setOnClickListener(view -> start("正在连接电脑…", request -> {
            JSONObject response = Relay.host(request.computer, request.password, null, null, false, false);
            return new Result(response.optBoolean("asr") ? "电脑已连接，可以语音提问。"
                    : "电脑已连接，可用文字提问。", null, null);
        }));
        ask.setOnClickListener(view -> {
            String text = question.getText().toString().trim();
            if (text.isEmpty()) {
                status.setText("请输入问题。");
                return;
            }
            start("正在取图并发送问题…",
                    request -> ask(request, text.getBytes(StandardCharsets.UTF_8), false));
        });
        voice.setOnClickListener(view -> {
            if (recording) {
                recording = false;
                voice.setEnabled(false);
                status.setText("正在取图并发送语音…");
            } else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, ASK_MIC);
            } else {
                startRecording();
            }
        });
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == ASK_MIC) {
            if (results.length == 1 && results[0] == PackageManager.PERMISSION_GRANTED) startRecording();
            else status.setText("请允许麦克风权限，或使用文字提问。");
        }
    }

    private void startRecording() {
        recording = true;
        start("正在录音，再点一次发送。", request -> {
            byte[] pcm = capture(request.generation);
            return ask(request, pcm, true);
        });
    }

    private byte[] capture(int requestGeneration) throws IOException {
        int minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minimum <= 0) throw new IOException("手机不支持当前录音格式。");
        AudioRecord mic = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                Math.max(minimum, 4096));
        try {
            if (mic.getState() != AudioRecord.STATE_INITIALIZED) throw new IOException("麦克风不可用。");
            mic.startRecording();
            ByteArrayOutputStream pcm = new ByteArrayOutputStream();
            byte[] chunk = new byte[2048];
            while (recording && generation == requestGeneration && pcm.size() < MAX_PCM) {
                int count = mic.read(chunk, 0, Math.min(chunk.length, MAX_PCM - pcm.size()));
                if (count <= 0) throw new IOException("读取麦克风失败。");
                pcm.write(chunk, 0, count);
            }
            recording = false;
            if (pcm.size() < 6400) throw new IOException("录音太短，请重新提问。");
            requireActive(requestGeneration);
            runOnUiThread(() -> {
                if (generation == requestGeneration) {
                    voice.setEnabled(false);
                    status.setText("正在取图并发送语音…");
                }
            });
            return pcm.toByteArray();
        } finally {
            mic.release();
        }
    }

    private Result ask(Request request, byte[] payload, boolean voiceInput) throws Exception {
        byte[] jpeg = Relay.snapshot(request.camera);
        requireActive(request.generation);
        JSONObject reply = Relay.host(request.computer, request.password, jpeg, payload, voiceInput, request.cloud);
        return new Result("电脑已返回答案。", reply, jpeg);
    }

    private void requireActive(int requestGeneration) throws IOException {
        if (generation != requestGeneration) throw new IOException("请求已结束。");
    }

    private void start(String message, Operation operation) {
        stopPlayback();
        int current = ++generation;
        Request request = new Request(camera.getText().toString().trim(), computer.getText().toString().trim(),
                password.getText().toString(), cloud.isChecked(), current);
        getPreferences(MODE_PRIVATE).edit().putString("camera", request.camera)
                .putString("computer", request.computer).apply();
        setBusy(true);
        status.setText(message);
        answer.setText("");
        preview.setImageDrawable(null);
        worker.execute(() -> {
            try {
                Result result = operation.run(request);
                runOnUiThread(() -> show(current, result));
            } catch (Exception error) {
                String detail = error instanceof SocketTimeoutException
                        ? "连接超时，请分别测试眼镜和电脑。"
                        : error instanceof IOException ? error.getMessage()
                        : "操作失败，请检查填写的地址、麦克风或电脑响应。";
                runOnUiThread(() -> {
                    if (generation != current) return;
                    recording = false;
                    setBusy(false);
                    status.setText(detail);
                });
            }
        });
    }

    private void show(int current, Result result) {
        if (generation != current) return;
        setBusy(false);
        status.setText(result.message);
        if (result.jpeg != null) {
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(result.jpeg, 0, result.jpeg.length, bounds);
            if (bounds.outWidth > 0 && bounds.outHeight > 0
                    && (long) bounds.outWidth * bounds.outHeight <= 12_000_000) {
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inSampleSize = Math.max(1, Math.max(bounds.outWidth, bounds.outHeight) / 640);
                Bitmap bitmap = BitmapFactory.decodeByteArray(result.jpeg, 0, result.jpeg.length, options);
                preview.setImageBitmap(bitmap);
            }
        }
        if (result.reply == null) return;
        answer.setText("识别问题：" + result.reply.optString("heard") + "\n" + result.reply.optString("reply"));
        String audio = result.reply.optString("audio_wav", "");
        if (!audio.isEmpty() && !audio.equals("null")) {
            play(Base64.getDecoder().decode(audio));
        }
    }

    private void play(byte[] wav) {
        try {
            player = new MediaPlayer();
            player.setDataSource(new MediaDataSource() {
                @Override public int readAt(long position, byte[] buffer, int offset, int size) {
                    if (position >= wav.length) return -1;
                    int count = (int) Math.min(size, wav.length - position);
                    System.arraycopy(wav, (int) position, buffer, offset, count);
                    return count;
                }
                @Override public long getSize() { return wav.length; }
                @Override public void close() {}
            });
            player.setOnPreparedListener(MediaPlayer::start);
            player.setOnCompletionListener(media -> stopPlayback());
            player.setOnErrorListener((media, what, extra) -> {
                stopPlayback();
                status.setText("答案已显示，语音播放失败。");
                return true;
            });
            player.prepareAsync();
        } catch (Exception error) {
            stopPlayback();
            status.setText("答案已显示，语音播放失败。");
        }
    }

    private void stopPlayback() {
        if (player != null) {
            player.release();
            player = null;
        }
    }

    private void setBusy(boolean busy) {
        checkCamera.setEnabled(!busy);
        checkComputer.setEnabled(!busy);
        assistant.setEnabled(!busy);
        ask.setEnabled(!busy);
        voice.setEnabled(!busy || recording);
        voice.setText(recording ? "结束录音并发送" : "开始录音");
    }

    @Override protected void onStop() {
        generation++;
        recording = false;
        stopPlayback();
        setBusy(false);
        super.onStop();
    }

    @Override protected void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    private EditText field(LinearLayout parent, String name, String hint, int type) {
        EditText input = new EditText(this);
        input.setId(View.generateViewId());
        input.setSingleLine(true);
        input.setInputType(type);
        input.setHint(hint);
        TextView label = new TextView(this);
        label.setText(name);
        label.setLabelFor(input.getId());
        parent.addView(label);
        parent.addView(input);
        return input;
    }

    private Button button(LinearLayout parent, String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setMinHeight(dp(48));
        parent.addView(button);
        return button;
    }

    private interface Operation { Result run(Request request) throws Exception; }

    private static final class Request {
        final String camera, computer, password;
        final boolean cloud;
        final int generation;

        Request(String camera, String computer, String password, boolean cloud, int generation) {
            this.camera = camera;
            this.computer = computer;
            this.password = password;
            this.cloud = cloud;
            this.generation = generation;
        }
    }

    private static final class Result {
        final String message;
        final JSONObject reply;
        final byte[] jpeg;

        Result(String message, JSONObject reply, byte[] jpeg) {
            this.message = message;
            this.reply = reply;
            this.jpeg = jpeg;
        }
    }
}

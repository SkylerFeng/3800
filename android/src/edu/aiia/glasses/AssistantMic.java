package edu.aiia.glasses;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

final class AssistantMic {
    static final int RATE = 16000;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile int generation;
    private boolean listening;

    void start(Consumer<String> deliver) {
        if (listening) return;
        listening = true;
        int ticket = ++generation;
        executor.execute(() -> record(ticket, deliver));
    }

    void stop() {
        generation++;
        listening = false;
    }

    void close() {
        stop();
        executor.shutdownNow();
    }

    private void record(int ticket, Consumer<String> deliver) {
        if (ticket != generation) return;
        AudioRecord input = null;
        try {
            int minimum = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            if (minimum <= 0) throw new IllegalStateException();
            input = new AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(minimum, 6400));
            if (input.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException();
            input.startRecording();
            if (input.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) throw new IllegalStateException();
            if (ticket == generation) deliver.accept("started");
            byte[] chunk = new byte[3200];
            while (ticket == generation) {
                int count = input.read(chunk, 0, chunk.length);
                if (count <= 0) throw new IllegalStateException();
                if (ticket == generation) deliver.accept(Base64.encodeToString(chunk, 0, count, Base64.NO_WRAP));
            }
        } catch (RuntimeException error) {
            if (ticket == generation) deliver.accept("error");
        } finally {
            if (input != null) input.release();
        }
    }
}

package com.heyme.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.ToneGenerator;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.view.KeyEvent;

import org.json.JSONArray;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;
import org.vosk.android.RecognitionListener;
import org.vosk.android.SpeechService;
import org.vosk.android.StorageService;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Runs in the background:
 *  - listens for "Hey Me" (offline, with Vosk)
 *  - records what you say and sends it to the Hey Me server
 *  - speaks the answer, plays media, rings timers and announcements
 */
public class HeyMeService extends Service implements RecognitionListener {
    public static final String ACTION_TALK = "com.heyme.app.TALK";
    static final String WAKE_WORD = "hey me";
    static final int RATE = 16000;
    static final String CHANNEL = "heyme";
    static final String ALERT_CHANNEL = "heyme_alerts";

    public interface StatusListener {
        void onStatus(String text);
    }

    static volatile StatusListener statusListener;
    static volatile String lastStatus = "Starting...";

    private Handler main, work;
    private HandlerThread workThread;
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;
    private Model model;
    private Recognizer recognizer;
    private SpeechService speechService;
    private volatile boolean busy = false;
    private volatile boolean stopping = false;
    private boolean listenEnabled;
    private String server, secret, name;
    private PowerManager.WakeLock wakeLock;

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("heyme", MODE_PRIVATE);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ------------------------------------------------------------------
    // Start / stop
    // ------------------------------------------------------------------
    @Override
    public void onCreate() {
        super.onCreate();
        SharedPreferences p = prefs(this);
        server = p.getString("server", "");
        secret = p.getString("secret", "");
        name = p.getString("name", "phone");
        boolean micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        listenEnabled = p.getBoolean("listen", true) && micOk;

        main = new Handler(Looper.getMainLooper());
        workThread = new HandlerThread("heyme-work");
        workThread.start();
        work = new Handler(workThread.getLooper());

        createChannels();
        if (!goForeground(micOk)) {
            stopSelf();
            return;
        }

        if (listenEnabled) {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "heyme:listen");
            wakeLock.acquire();
        }

        tts = new TextToSpeech(this, st -> {
            if (st == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.US);
                ttsReady = true;
            }
        });

        if (listenEnabled) {
            loadModel();
        } else {
            status(micOk ? "Ready. Tap Talk to speak." : "Ready for announcements. (No microphone permission.)");
        }
        new Thread(this::pollLoop, "heyme-poll").start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_TALK.equals(intent.getAction()) && !busy) {
            busy = true;
            main.post(() -> {
                stopListening();
                work.post(this::converse);
            });
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopping = true;
        stopListening();
        if (tts != null) tts.shutdown();
        if (workThread != null) workThread.quitSafely();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    private boolean goForeground(boolean micOk) {
        Notification n = buildNotification("Starting...");
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(1, n, micOk ? ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                        : ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(1, n);
            }
            return true;
        } catch (Exception e) {
            try {
                if (Build.VERSION.SDK_INT >= 29) {
                    startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
                    listenEnabled = false;
                    return true;
                }
            } catch (Exception ignored) {
            }
            return false;
        }
    }

    // ------------------------------------------------------------------
    // Notifications and status text
    // ------------------------------------------------------------------
    private void createChannels() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Hey Me", NotificationManager.IMPORTANCE_LOW));
        nm.createNotificationChannel(new NotificationChannel(ALERT_CHANNEL, "Hey Me media", NotificationManager.IMPORTANCE_HIGH));
    }

    private Notification buildNotification(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_mic)
                .setContentTitle("Hey Me")
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
    }

    private void status(String text) {
        lastStatus = text;
        StatusListener l = statusListener;
        if (l != null) l.onStatus(text);
        if (stopping) return;
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            nm.notify(1, buildNotification(text.split("\n")[0]));
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------------
    // Wake word ("Hey Me")
    // ------------------------------------------------------------------
    private void loadModel() {
        status("Loading the wake word...");
        StorageService.unpack(this, "model-en-us", "model",
                m -> {
                    model = m;
                    startListening();
                },
                e -> status("Couldn't load the wake word: " + e.getMessage()));
    }

    private synchronized void startListening() {
        if (!listenEnabled || model == null || stopping || busy || speechService != null) return;
        try {
            recognizer = new Recognizer(model, (float) RATE, "[\"" + WAKE_WORD + "\", \"[unk]\"]");
            speechService = new SpeechService(recognizer, (float) RATE);
            speechService.startListening(this);
            status("Listening for \"Hey Me\"");
        } catch (Exception e) {
            speechService = null;
            status("Microphone unavailable: " + e.getMessage());
        }
    }

    private synchronized void stopListening() {
        if (speechService != null) {
            speechService.stop();
            speechService.shutdown();
            speechService = null;
        }
        if (recognizer != null) {
            recognizer.close();
            recognizer = null;
        }
    }

    private void checkWake(String hypothesis) {
        if (hypothesis != null && hypothesis.contains(WAKE_WORD) && !busy) {
            busy = true;
            main.post(() -> {
                stopListening();
                work.post(this::converse);
            });
        }
    }

    @Override
    public void onPartialResult(String hypothesis) {
        checkWake(hypothesis);
    }

    @Override
    public void onResult(String hypothesis) {
        checkWake(hypothesis);
    }

    @Override
    public void onFinalResult(String hypothesis) {
    }

    @Override
    public void onError(Exception e) {
        status("Microphone error: " + e.getMessage());
        stopListening();
        main.postDelayed(this::startListening, 5000);
    }

    @Override
    public void onTimeout() {
    }

    // ------------------------------------------------------------------
    // A conversation: beep, record, ask the server, speak the answer
    // ------------------------------------------------------------------
    private void converse() {
        try {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                status("Hey Me needs microphone permission. Open the app to allow it.");
                return;
            }
            beep(ToneGenerator.TONE_PROP_BEEP);
            status("Listening...");
            byte[] wav = recordRequest();
            if (wav == null) {
                status("Didn't hear anything.");
                beep(ToneGenerator.TONE_PROP_NACK);
                return;
            }
            status("Thinking...");
            JSONObject r = postAudio(wav);
            String heard = r.optString("heard", "");
            String reply = r.optString("reply", "");
            status("You: " + heard + "\nHey Me: " + reply);
            speak(reply);
            if (r.optBoolean("media_stop", false)) pauseOtherMedia();
            String media = r.optString("media_url", "");
            if (!media.isEmpty()) openMedia(media, r.optString("media_type", ""));
        } catch (Exception e) {
            status("Couldn't reach the Hey Me server: " + e.getMessage());
            speak("Sorry, I couldn't reach the Hey Me server.");
        } finally {
            busy = false;
            main.post(this::startListening);
        }
    }

    private byte[] recordRequest() {
        int minBuf = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord rec;
        try {
            rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuf, RATE * 2));
        } catch (Exception e) {
            return null;
        }
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
            rec.release();
            return null;
        }
        ByteArrayOutputStream pcm = new ByteArrayOutputStream();
        short[] buf = new short[RATE / 10];          // 0.1 second
        byte[] bytes = new byte[buf.length * 2];
        double threshold = 500, ambient = 0, silent = 0, total = 0;
        boolean heard = false;
        int chunk = 0;
        rec.startRecording();
        try {
            while (total < 12) {
                int n = rec.read(buf, 0, buf.length);
                if (n <= 0) break;
                double sum = 0;
                for (int i = 0; i < n; i++) {
                    sum += (double) buf[i] * buf[i];
                    bytes[i * 2] = (byte) (buf[i] & 0xff);
                    bytes[i * 2 + 1] = (byte) ((buf[i] >> 8) & 0xff);
                }
                pcm.write(bytes, 0, n * 2);
                double level = Math.sqrt(sum / n);
                double secs = (double) n / RATE;
                total += secs;
                chunk++;
                if (chunk <= 2) {                     // measure the room for 0.2 s
                    ambient = Math.max(ambient, level);
                    threshold = Math.min(1500, Math.max(400, ambient * 2));
                    continue;
                }
                if (level > threshold) {
                    heard = true;
                    silent = 0;
                } else {
                    silent += secs;
                }
                if (heard && silent > 1.0) break;
                if (!heard && total > 5) break;
            }
        } finally {
            try {
                rec.stop();
            } catch (Exception ignored) {
            }
            rec.release();
        }
        return heard ? wav(pcm.toByteArray()) : null;
    }

    static byte[] wav(byte[] pcm) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        writeStr(o, "RIFF");
        writeInt(o, 36 + pcm.length);
        writeStr(o, "WAVE");
        writeStr(o, "fmt ");
        writeInt(o, 16);
        writeShort(o, 1);           // PCM
        writeShort(o, 1);           // mono
        writeInt(o, RATE);
        writeInt(o, RATE * 2);
        writeShort(o, 2);
        writeShort(o, 16);
        writeStr(o, "data");
        writeInt(o, pcm.length);
        o.write(pcm, 0, pcm.length);
        return o.toByteArray();
    }

    private static void writeStr(ByteArrayOutputStream o, String s) {
        byte[] b = s.getBytes(StandardCharsets.US_ASCII);
        o.write(b, 0, b.length);
    }

    private static void writeInt(ByteArrayOutputStream o, int v) {
        o.write(v & 0xff);
        o.write((v >> 8) & 0xff);
        o.write((v >> 16) & 0xff);
        o.write((v >> 24) & 0xff);
    }

    private static void writeShort(ByteArrayOutputStream o, int v) {
        o.write(v & 0xff);
        o.write((v >> 8) & 0xff);
    }

    // ------------------------------------------------------------------
    // Talking to the server
    // ------------------------------------------------------------------
    private String deviceQuery() throws IOException {
        return "device=" + URLEncoder.encode(name, "UTF-8") + "&kind=android";
    }

    private JSONObject postAudio(byte[] wav) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(server + "/api/ask?" + deviceQuery()).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(90000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "audio/wav");
        c.setRequestProperty("X-HeyMe-Secret", secret);
        c.setFixedLengthStreamingMode(wav.length);
        try (OutputStream o = c.getOutputStream()) {
            o.write(wav);
        }
        return readJson(c);
    }

    private static JSONObject readJson(HttpURLConnection c) throws Exception {
        int code = c.getResponseCode();
        if (code == 403) throw new IOException("the secret doesn't match the server");
        InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
        String body = readAll(in);
        c.disconnect();
        if (code >= 400) throw new IOException("server error " + code);
        return new JSONObject(body);
    }

    private static String readAll(InputStream in) throws IOException {
        if (in == null) return "";
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
        in.close();
        return new String(b.toByteArray(), StandardCharsets.UTF_8);
    }

    /** Every 4 seconds: anything for this device? (timers, announcements, music) */
    private void pollLoop() {
        while (!stopping) {
            try {
                if (!server.isEmpty()) {
                    HttpURLConnection c = (HttpURLConnection) new URL(server + "/api/events?" + deviceQuery()).openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(15000);
                    c.setRequestProperty("X-HeyMe-Secret", secret);
                    JSONArray events = readJson(c).optJSONArray("events");
                    if (events != null) {
                        for (int i = 0; i < events.length(); i++) {
                            final JSONObject ev = events.getJSONObject(i);
                            work.post(() -> handleEvent(ev));
                        }
                    }
                }
            } catch (Exception ignored) {
                // server not reachable right now; try again shortly
            }
            SystemClock.sleep(4000);
        }
    }

    private void handleEvent(JSONObject ev) {
        String type = ev.optString("type", "");
        busy = true;
        stopListening();
        try {
            if ("announce".equals(type)) {
                if (ev.optBoolean("alarm", false)) alarm();
                else beep(ToneGenerator.TONE_PROP_ACK);
                String text = ev.optString("text", "");
                status("Hey Me: " + text);
                speak(text);
            } else if ("media".equals(type)) {
                openMedia(ev.optString("url", ""), ev.optString("media_type", ""));
            } else if ("media_stop".equals(type)) {
                pauseOtherMedia();
            }
        } finally {
            busy = false;
            main.post(this::startListening);
        }
    }

    // ------------------------------------------------------------------
    // Sound and media
    // ------------------------------------------------------------------
    private void speak(String text) {
        if (text == null || text.isEmpty()) return;
        for (int i = 0; i < 30 && !ttsReady; i++) SystemClock.sleep(100);
        if (!ttsReady) return;
        final CountDownLatch done = new CountDownLatch(1);
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String id) {
            }

            @Override
            public void onDone(String id) {
                done.countDown();
            }

            @Override
            public void onError(String id) {
                done.countDown();
            }
        });
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "heyme" + SystemClock.uptimeMillis());
        try {
            done.await(60, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
    }

    private void beep(int tone) {
        try {
            ToneGenerator tg = new ToneGenerator(AudioManager.STREAM_MUSIC, 80);
            tg.startTone(tone, 200);
            SystemClock.sleep(250);
            tg.release();
        } catch (Exception ignored) {
        }
    }

    private void alarm() {
        try {
            ToneGenerator tg = new ToneGenerator(AudioManager.STREAM_MUSIC, 100);
            for (int i = 0; i < 3; i++) {
                tg.startTone(ToneGenerator.TONE_CDMA_ALERT_CALL_GUARD, 600);
                SystemClock.sleep(800);
            }
            tg.release();
        } catch (Exception ignored) {
        }
    }

    private void openMedia(String path, String type) {
        if (path == null || path.isEmpty()) return;
        String full = path.startsWith("http") ? path : server + path;
        Intent i = new Intent(Intent.ACTION_VIEW);
        if (type == null || type.isEmpty()) i.setData(Uri.parse(full));
        else i.setDataAndType(Uri.parse(full), type);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        if (!Settings.canDrawOverlays(this)) {
            // Android may block starting a player from the background,
            // so also show a "tap to play" notification.
            PendingIntent pi = PendingIntent.getActivity(this, 2, i,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification n = new Notification.Builder(this, ALERT_CHANNEL)
                    .setSmallIcon(R.drawable.ic_mic)
                    .setContentTitle("Hey Me")
                    .setContentText("Tap to play")
                    .setContentIntent(pi)
                    .setAutoCancel(true)
                    .build();
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(2, n);
        }
        try {
            startActivity(i);
        } catch (Exception e) {
            status("No app here can play that. Install VLC.");
        }
    }

    private void pauseOtherMedia() {
        AudioManager am = (AudioManager) getSystemService(AUDIO_SERVICE);
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PAUSE));
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_MEDIA_PAUSE));
    }
}

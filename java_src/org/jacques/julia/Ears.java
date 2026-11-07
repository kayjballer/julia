package org.jacques.julia;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/* Oreilles de Niko : le micro tourne en continu, un détecteur de voix (volume) découpe les phrases,
   chaque phrase est écrite dans un fichier .wav et son chemin est déposé dans une file que Python lit
   avec poll(). Aucun modèle ici : Whisper (côté Python) transcrit. */
public class Ears {
    private static final ConcurrentLinkedQueue<String> Q = new ConcurrentLinkedQueue<String>();
    private static Thread th = null;
    private static volatile boolean run = false;
    private static AudioRecord ar = null;

    private static final int RATE = 16000;
    private static final int FRAME = 512;                 /* 32 ms */
    private static final int PRE = 9600;                  /* 0,6 s gardées avant le début de la parole */
    private static final int MAXS = RATE * 12;            /* phrase de 12 s maximum */
    private static final int HANG = 22;                   /* ~0,7 s de silence = fin de phrase */
    private static final int START_FRAMES = 3;            /* ~0,1 s de voix pour déclencher */
    private static final int MIN_VOICED = 10;             /* moins de ~0,3 s de voix : on jette (claquement, bruit) */

    private static short[] seg = new short[MAXS + 4096];
    private static int segN = 0;
    private static int segId = 0;
    private static volatile boolean inSeg = false;
    private static volatile boolean muted = false;
    private static volatile long muteUntil = 0;
    private static volatile float lvl = 0f;
    private static volatile float peak = 0f;
    private static volatile float noiseEst = 0f;
    private static volatile int segs = 0;
    private static String dir = "";
    private static String infoTxt = "";

    private static void add(short[] b, int n) {
        if (!inSeg && segN + n > PRE) {
            int keep = Math.max(0, PRE - n);
            if (segN > keep) { System.arraycopy(seg, segN - keep, seg, 0, keep); segN = keep; }
        }
        if (segN + n > seg.length) n = seg.length - segN;
        if (n > 0) { System.arraycopy(b, 0, seg, segN, n); segN += n; }
    }

    private static String dump() {
        try {
            String path = dir + "/seg_" + (segId++ % 16) + ".wav";
            int bytes = segN * 2;
            ByteBuffer bb = ByteBuffer.allocate(44 + bytes).order(ByteOrder.LITTLE_ENDIAN);
            bb.put("RIFF".getBytes()); bb.putInt(36 + bytes); bb.put("WAVE".getBytes()); bb.put("fmt ".getBytes());
            bb.putInt(16); bb.putShort((short) 1); bb.putShort((short) 1); bb.putInt(RATE); bb.putInt(RATE * 2);
            bb.putShort((short) 2); bb.putShort((short) 16); bb.put("data".getBytes()); bb.putInt(bytes);
            for (int i = 0; i < segN; i++) bb.putShort(seg[i]);
            FileOutputStream f = new FileOutputStream(path);
            f.write(bb.array());
            f.close();
            return path;
        } catch (Throwable t) {
            return null;
        }
    }

    private static AudioRecord open(int source, int buf) {
        AudioRecord r = new AudioRecord(source, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf);
        if (r.getState() != AudioRecord.STATE_INITIALIZED) {
            r.release();
            return null;
        }
        return r;
    }

    public static synchronized String start(String d) {
        if (th != null) return "ok";
        try {
            dir = d == null ? "" : d;
            new File(dir).mkdirs();
            int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            int buf = Math.max(min * 2, RATE);
            String src = "micro reconnaissance";
            ar = open(MediaRecorder.AudioSource.VOICE_RECOGNITION, buf);
            if (ar == null) {
                src = "micro standard";
                ar = open(MediaRecorder.AudioSource.MIC, buf);
            }
            if (ar == null) return "micro occupe";
            ar.startRecording();
            if (ar.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                ar.release();
                ar = null;
                return "micro occupe";
            }
            infoTxt = src;
            run = true;
            final AudioRecord rr = ar;
            th = new Thread(new Runnable() {
                public void run() {
                    short[] b = new short[FRAME];
                    float noise = 0.004f;      /* bruit de fond estimé, s'adapte tout seul */
                    int loud = 0, quiet = 0, voiced = 0;
                    try {
                        while (run) {
                            int n = rr.read(b, 0, b.length);
                            if (n <= 0) {
                                if (n < 0) { if (run) push("E", "{\"err\":\"lecture micro " + n + "\"}"); break; }
                                continue;
                            }
                            double sum = 0;
                            for (int i = 0; i < n; i++) sum += (double) b[i] * b[i];
                            float rms = (float) (Math.sqrt(sum / n) / 32768.0);
                            lvl = rms;

                            /* pendant que Niko parle (et un instant après), on n'écoute pas : il ne s'entend pas lui-même */
                            if (muted || System.currentTimeMillis() < muteUntil) {
                                inSeg = false; segN = 0; loud = 0; quiet = 0; voiced = 0;
                                continue;
                            }

                            float thrStart = Math.max(0.007f, noise * 3.0f);
                            float thrStay = Math.max(0.005f, noise * 2.0f);
                            noiseEst = noise;
                            if (rms > peak) peak = rms;
                            if (!inSeg) {
                                add(b, n);
                                noise = Math.max(0.002f, noise * 0.99f + rms * 0.01f);
                                if (rms > thrStart) loud++; else loud = 0;
                                if (loud >= START_FRAMES) { inSeg = true; quiet = 0; voiced = loud; }
                            } else {
                                add(b, n);
                                if (rms > thrStay) { quiet = 0; voiced++; } else quiet++;
                                if (quiet >= HANG || segN >= MAXS) {
                                    boolean keep = voiced >= MIN_VOICED;
                                    String wp = keep ? dump() : null;
                                    inSeg = false; segN = 0; loud = 0; quiet = 0; voiced = 0;
                                    if (wp != null) { segs++; push("W", "{\"path\":\"" + wp + "\"}"); }
                                }
                            }
                        }
                    } catch (Throwable t) {
                        if (run) push("E", "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}");
                    }
                }
            }, "niko-ears");
            th.setDaemon(true);
            th.start();
            return "ok";
        } catch (Throwable t) {
            return "erreur " + t;
        }
    }

    public static synchronized void stop() {
        run = false;
        Thread old = th;
        try { if (ar != null) { ar.stop(); ar.release(); } } catch (Throwable t) { }
        try { if (old != null) old.join(2000); } catch (Throwable t) { }
        ar = null; th = null;
        inSeg = false; segN = 0;
    }

    /* coupe le micro pendant que Niko parle ; à la réouverture, 0,5 s de garde pour l'écho de la pièce */
    public static void setMuted(boolean m) {
        if (!m) muteUntil = System.currentTimeMillis() + 500;
        muted = m;
    }

    public static boolean voice() { return inSeg; }
    public static float level() { return lvl; }
    public static String poll() { return Q.poll(); }
    public static String info() { return infoTxt; }

    /* pour le diagnostic : volume max entendu, bruit de fond estimé, phrases découpées */
    public static String stats() {
        float p = peak; peak = 0f;
        return String.format("vol max %.3f | bruit %.3f | phrases %d", p, noiseEst, segs);
    }

    private static void push(String kind, String json) {
        if (Q.size() > 30) Q.poll();
        Q.add(kind + "|" + json);
    }
}

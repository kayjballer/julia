package org.jacques.julia;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/* Oreilles de Niko : le micro tourne en continu, un détecteur de voix (volume) découpe les phrases.
   Chaque phrase est nettoyée (filtre passe-haut + gain qui ramène la voix à un niveau normal), écrite dans
   un fichier .wav, puis son chemin et les niveaux mesurés sont déposés dans une file que Python lit avec poll().
   Aucun modèle ici : Vosk (classe Stt) transcrit. */
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
    private static final float TARGET = 0.10f;            /* niveau visé pour la voix (moyenne des trames les plus fortes) */
    private static final float GMAX = 12f;                /* gain maximum, environ +21 dB */

    private static short[] seg = new short[MAXS + 4096];
    private static int segN = 0;
    private static int segId = 0;
    private static volatile boolean inSeg = false;
    private static volatile boolean muted = false;
    private static volatile long muteUntil = 0;
    private static volatile float lvl = 0f;
    private static volatile float peak = 0f;
    private static volatile float noiseEst = 0f;
    private static volatile float lastLvl = 0f;
    private static volatile float lastGain = 1f;
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

    /* Nettoie une phrase sur place : filtre passe-haut (~100 Hz : enlève le grondement et le décalage continu),
       puis gain pour amener la voix au niveau TARGET (jamais d'atténuation).
       Renvoie {niveau de la voix avant gain, crête avant gain, gain appliqué}. */
    static float[] clean(short[] s, int n) {
        int nf = n / FRAME;
        if (nf < 2) return new float[] { 0f, 0f, 1f };
        double w0 = 2.0 * Math.PI * 100.0 / RATE;
        double cs = Math.cos(w0), al = Math.sin(w0) / (2.0 * 0.7071);
        double a0 = 1.0 + al;
        double b0 = (1.0 + cs) / 2.0 / a0, b1 = -(1.0 + cs) / a0, b2 = b0;
        double a1 = -2.0 * cs / a0, a2 = (1.0 - al) / a0;
        float[] y = new float[n];
        double x1 = 0, x2 = 0, y1 = 0, y2 = 0;
        for (int i = 0; i < n; i++) {
            double x0 = s[i];
            double v = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            x2 = x1; x1 = x0; y2 = y1; y1 = v;
            y[i] = (float) v;
        }
        float[] fr = new float[nf];
        float pk = 0f;
        for (int f = 0; f < nf; f++) {
            double sum = 0;
            for (int i = f * FRAME; i < (f + 1) * FRAME; i++) {
                float a = y[i];
                sum += (double) a * a;
                if (a < 0) a = -a;
                if (a > pk) pk = a;
            }
            fr[f] = (float) (Math.sqrt(sum / FRAME) / 32768.0);
        }
        float[] sorted = fr.clone();
        Arrays.sort(sorted);
        int top = Math.max(1, nf / 4);
        float level = 0f;
        for (int i = nf - top; i < nf; i++) level += sorted[i];
        level /= top;
        float g = level > 0.0005f ? TARGET / level : 1f;
        if (g > GMAX) g = GMAX;
        if (g < 1f) g = 1f;
        for (int i = 0; i < n; i++) {
            float v = y[i] * g;
            if (v > 32767f) v = 32767f; else if (v < -32768f) v = -32768f;
            s[i] = (short) Math.round(v);
        }
        return new float[] { level, pk / 32768f, g };
    }

    /* écrit la phrase en .wav et renvoie le JSON à déposer dans la file (chemin + niveaux), ou null */
    private static String dump() {
        try {
            float[] st = clean(seg, segN);
            lastLvl = st[0];
            lastGain = st[2];
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
            return String.format(Locale.US, "{\"path\":\"%s\",\"lvl\":%.4f,\"pk\":%.3f,\"g\":%.2f,\"nz\":%.4f}",
                    path, st[0], st[1], st[2], noiseEst);
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
                    float noise = 0.004f;      /* bruit de fond estimé pour les seuils (plancher 0,002), s'adapte tout seul */
                    float nraw = 0.004f;       /* même estimation sans plancher : c'est elle qu'on affiche */
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
                            noiseEst = nraw;
                            if (rms > peak) peak = rms;
                            if (!inSeg) {
                                add(b, n);
                                noise = Math.max(0.002f, noise * 0.99f + rms * 0.01f);
                                nraw = Math.max(0.0001f, nraw * 0.99f + rms * 0.01f);
                                if (rms > thrStart) loud++; else loud = 0;
                                if (loud >= START_FRAMES) { inSeg = true; quiet = 0; voiced = loud; }
                            } else {
                                add(b, n);
                                if (rms > thrStay) { quiet = 0; voiced++; } else quiet++;
                                if (quiet >= HANG || segN >= MAXS) {
                                    boolean keep = voiced >= MIN_VOICED;
                                    String wp = keep ? dump() : null;
                                    inSeg = false; segN = 0; loud = 0; quiet = 0; voiced = 0;
                                    if (wp != null) { segs++; push("W", wp); }
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

    /* pour le diagnostic : volume max entendu, bruit de fond estimé, phrases découpées, dernière phrase (niveau de la voix, gain) */
    public static String stats() {
        float p = peak; peak = 0f;
        return String.format("vol max %.3f | bruit %.4f | phrases %d | derniere : voix %.3f, gain x%.1f", p, noiseEst, segs, lastLvl, lastGain);
    }

    private static void push(String kind, String json) {
        if (Q.size() > 30) Q.poll();
        Q.add(kind + "|" + json);
    }
}

package org.jacques.julia;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import org.vosk.Model;
import org.vosk.Recognizer;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/* Oreilles de Julia : écoute le micro en continu avec Vosk et dépose les résultats dans une file
   que Python lit avec poll() (jamais de rappel vers Python : plus sûr).
   Le micro est ouvert en mode « communication » avec annulation d'écho (AEC) quand le téléphone
   la propose : Julia entend moins sa propre voix, ce qui permet de lui couper la parole. */
public class Ears {
    private static final ConcurrentLinkedQueue<String> Q = new ConcurrentLinkedQueue<String>();
    private static Thread th = null;
    private static volatile boolean run = false;
    private static Model model = null;
    private static AudioRecord ar = null;
    private static AcousticEchoCanceler aec = null;
    private static String infoTxt = "";

    /* audio de la phrase en cours, pour Whisper : 0,6 s avant la parole + la parole (25 s max) */
    private static final int PRE = 9600;
    private static final int MAXS = 16000 * 25;
    private static short[] seg = new short[MAXS + 4096];
    private static int segN = 0;
    private static int segId = 0;
    private static boolean inSeg = false;
    private static volatile boolean cap = false;
    private static String dir = "";

    public static void setDir(String d) {
        dir = d == null ? "" : d;
        try { new File(dir).mkdirs(); } catch (Throwable t) { }
    }

    public static void setCapture(boolean b) {
        cap = b;
    }

    private static void add(short[] b, int n) {
        if (!inSeg && segN + n > PRE) {
            int keep = Math.max(0, PRE - n);
            if (segN > keep) { System.arraycopy(seg, segN - keep, seg, 0, keep); segN = keep; }
        }
        if (segN + n > seg.length) n = seg.length - segN;
        if (n > 0) { System.arraycopy(b, 0, seg, segN, n); segN += n; }
    }

    private static boolean hasText(String r) {
        return r != null && !r.replaceAll("\\s", "").contains("\"text\":\"\"");
    }

    private static String dump() {
        try {
            String path = dir + "/seg_" + (segId++ % 16) + ".wav";
            int bytes = segN * 2;
            ByteBuffer bb = ByteBuffer.allocate(44 + bytes).order(ByteOrder.LITTLE_ENDIAN);
            bb.put("RIFF".getBytes()); bb.putInt(36 + bytes); bb.put("WAVE".getBytes()); bb.put("fmt ".getBytes());
            bb.putInt(16); bb.putShort((short) 1); bb.putShort((short) 1); bb.putInt(16000); bb.putInt(32000);
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
        AudioRecord r = new AudioRecord(source, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf);
        if (r.getState() != AudioRecord.STATE_INITIALIZED) {
            r.release();
            return null;
        }
        return r;
    }

    public static synchronized String start(String modelPath, String grammar) {
        return start(modelPath, grammar, "precis");
    }

    /* mode « precis » : micro de reconnaissance, aucun filtre (meilleure transcription)
       mode « antiecho » : micro de communication + annulation d'écho (permet de couper la parole) */
    public static synchronized String start(String modelPath, String grammar, String mode) {
        if (th != null) return "ok";
        try {
            model = new Model(modelPath);
            final Recognizer rec;
            if (grammar == null || grammar.length() == 0) {
                rec = new Recognizer(model, 16000.0f);
            } else {
                rec = new Recognizer(model, 16000.0f, grammar);
            }
            rec.setWords(true);
            boolean anti = "antiecho".equals(mode);
            int min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            int buf = Math.max(min * 2, 16000);
            String src = anti ? "anti-echo" : "precis";
            ar = open(anti ? MediaRecorder.AudioSource.VOICE_COMMUNICATION : MediaRecorder.AudioSource.VOICE_RECOGNITION, buf);
            if (ar == null) {
                src = "micro standard";
                ar = open(MediaRecorder.AudioSource.MIC, buf);
            }
            if (ar == null) return "micro occupe";
            int sid = ar.getAudioSessionId();
            String fx = "";
            try {
                if (anti && AcousticEchoCanceler.isAvailable()) {
                    aec = AcousticEchoCanceler.create(sid);
                    if (aec != null) { aec.setEnabled(true); fx += " echo"; }
                }
            } catch (Throwable t) { }
            ar.startRecording();
            if (ar.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                ar.release();
                ar = null;
                return "micro occupe";
            }
            infoTxt = src + (fx.length() == 0 ? " sans filtre" : fx);
            run = true;
            final AudioRecord rr = ar;
            th = new Thread(new Runnable() {
                public void run() {
                    short[] b = new short[2048];
                    String lastPartial = "";
                    try {
                        while (run) {
                            int n = rr.read(b, 0, b.length);
                            if (n <= 0) {
                                if (n < 0) { if (run) push("E", "{\"err\":\"lecture micro " + n + "\"}"); break; }
                                continue;
                            }
                            add(b, n);
                            if (rec.acceptWaveForm(b, n)) {
                                lastPartial = "";
                                String res = rec.getResult();
                                if (cap && dir.length() > 0 && segN > 3200 && hasText(res)) {
                                    String wp = dump();
                                    if (wp != null) push("W", "{\"path\":\"" + wp + "\"}");
                                }
                                inSeg = false;
                                segN = 0;
                                push("R", res);
                            } else {
                                String p = rec.getPartialResult();
                                if (p != null && !p.equals(lastPartial)) {
                                    lastPartial = p;
                                    if (!p.replaceAll("\\s", "").equals("{\"partial\":\"\"}")) inSeg = true;
                                    push("P", p);
                                }
                            }
                        }
                    } catch (Throwable t) {
                        if (run) push("E", "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}");
                    }
                    try { rec.close(); } catch (Throwable t) { }
                }
            }, "julia-ears");
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
        try { if (aec != null) aec.release(); } catch (Throwable t) { }
        try { if (model != null) model.close(); } catch (Throwable t) { }
        ar = null; aec = null; model = null; th = null;
        inSeg = false; segN = 0;
    }

    public static String poll() {
        return Q.poll();
    }

    public static String info() {
        return infoTxt;
    }

    private static void push(String kind, String json) {
        if (Q.size() > 60) Q.poll();
        Q.add(kind + "|" + json);
    }
}

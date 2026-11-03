package org.jacques.julia;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import org.vosk.Model;
import org.vosk.Recognizer;
import java.util.concurrent.ConcurrentLinkedQueue;

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
    private static NoiseSuppressor ns = null;
    private static AutomaticGainControl agc = null;
    private static String infoTxt = "";

    private static AudioRecord open(int source, int buf) {
        AudioRecord r = new AudioRecord(source, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf);
        if (r.getState() != AudioRecord.STATE_INITIALIZED) {
            r.release();
            return null;
        }
        return r;
    }

    public static synchronized String start(String modelPath, String grammar) {
        if (th != null) return "ok";
        try {
            model = new Model(modelPath);
            final Recognizer rec;
            if (grammar == null || grammar.length() == 0) {
                rec = new Recognizer(model, 16000.0f);
            } else {
                rec = new Recognizer(model, 16000.0f, grammar);
            }
            int min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            int buf = Math.max(min * 2, 16000);
            String src = "communication";
            ar = open(MediaRecorder.AudioSource.VOICE_COMMUNICATION, buf);
            if (ar == null) {
                src = "reconnaissance";
                ar = open(MediaRecorder.AudioSource.VOICE_RECOGNITION, buf);
            }
            if (ar == null) return "micro occupe";
            int sid = ar.getAudioSessionId();
            String fx = "";
            try {
                if (AcousticEchoCanceler.isAvailable()) {
                    aec = AcousticEchoCanceler.create(sid);
                    if (aec != null) { aec.setEnabled(true); fx += " echo"; }
                }
            } catch (Throwable t) { }
            try {
                if (NoiseSuppressor.isAvailable()) {
                    ns = NoiseSuppressor.create(sid);
                    if (ns != null) { ns.setEnabled(true); fx += " bruit"; }
                }
            } catch (Throwable t) { }
            try {
                if (AutomaticGainControl.isAvailable()) {
                    agc = AutomaticGainControl.create(sid);
                    if (agc != null) { agc.setEnabled(true); fx += " gain"; }
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
                                if (n < 0) { push("E", "{\"err\":\"lecture micro " + n + "\"}"); break; }
                                continue;
                            }
                            if (rec.acceptWaveForm(b, n)) {
                                lastPartial = "";
                                push("R", rec.getResult());
                            } else {
                                String p = rec.getPartialResult();
                                if (p != null && !p.equals(lastPartial)) {
                                    lastPartial = p;
                                    push("P", p);
                                }
                            }
                        }
                    } catch (Throwable t) {
                        push("E", "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}");
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
        try { if (ar != null) { ar.stop(); ar.release(); } } catch (Throwable t) { }
        try { if (aec != null) aec.release(); } catch (Throwable t) { }
        try { if (ns != null) ns.release(); } catch (Throwable t) { }
        try { if (agc != null) agc.release(); } catch (Throwable t) { }
        try { if (model != null) model.close(); } catch (Throwable t) { }
        ar = null; aec = null; ns = null; agc = null; model = null; th = null;
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

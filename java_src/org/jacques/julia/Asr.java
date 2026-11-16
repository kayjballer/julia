package org.jacques.julia;

import com.k2fsa.sherpa.onnx.*;
import org.json.JSONObject;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;

/* Transcription locale rapide (sherpa-onnx, modele francais en streaming).
   Meme forme que Stt : un .wav 16 kHz mono en entree, un JSON {text, ms, cpu} en sortie. */
public class Asr {
    private static OnlineRecognizer rec = null;

    private static String pick(File d, String prefix, boolean preferInt8) {
        File[] fs = d.listFiles();
        if (fs == null) return null;
        String best = null;
        int bs = -1;
        for (File f : fs) {
            String n = f.getName();
            if (!n.startsWith(prefix) || !n.endsWith(".onnx")) continue;
            boolean i8 = n.contains(".int8.");
            int sc = (i8 == preferInt8) ? 2 : 1;
            if (sc > bs) { bs = sc; best = f.getAbsolutePath(); }
        }
        return best;
    }

    public static synchronized String load(String dir) {
        if (rec != null) return "ok";
        try {
            LibraryLoader.setAutoLoadEnabled(false);
            try { System.loadLibrary("onnxruntime"); } catch (Throwable t) { }
            System.loadLibrary("sherpa-onnx-jni");
            File d = new File(dir);
            String enc = pick(d, "encoder", true);
            String dec = pick(d, "decoder", false);
            String joi = pick(d, "joiner", false);
            File tok = new File(d, "tokens.txt");
            if (enc == null || dec == null || joi == null || !tok.exists()) {
                return "erreur fichiers du modele manquants dans " + dir;
            }
            OnlineTransducerModelConfig transducer = OnlineTransducerModelConfig.builder()
                .setEncoder(enc).setDecoder(dec).setJoiner(joi).build();
            OnlineModelConfig modelConfig = OnlineModelConfig.builder()
                .setTransducer(transducer).setTokens(tok.getAbsolutePath())
                .setNumThreads(2).setDebug(false).build();
            OnlineRecognizerConfig config = OnlineRecognizerConfig.builder()
                .setOnlineModelConfig(modelConfig).setDecodingMethod("greedy_search").build();
            rec = new OnlineRecognizer(config);
            return "ok";
        } catch (Throwable t) {
            return "erreur " + t;
        }
    }

    /* Transcription a partir de fromMs (pour sauter le nom de Niko), jusqu'a la fin du fichier. */
    public static synchronized String transcribeFrom(String wavPath, int fromMs) {
        if (rec == null) return "{\"err\":\"asr non charge\"}";
        boolean prio = false;
        int oldPrio = 0;
        long w0 = System.nanoTime();
        long c0 = android.os.Debug.threadCpuTimeNanos();
        OnlineStream st = null;
        try {
            try {
                oldPrio = android.os.Process.getThreadPriority(android.os.Process.myTid());
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO);
                prio = true;
            } catch (Throwable t) { }

            File f = new File(wavPath);
            byte[] all = new byte[(int) f.length()];
            DataInputStream din = new DataInputStream(new FileInputStream(f));
            din.readFully(all);
            din.close();

            long from = 44L + 32L * Math.max(0, fromMs);
            if (from > all.length) from = all.length;
            int len = (int) (all.length - from);
            len -= len % 2;
            int n = len / 2;
            float[] samples = new float[n];
            for (int i = 0; i < n; i++) {
                int lo = all[(int) from + 2 * i] & 0xff;
                int hi = all[(int) from + 2 * i + 1];
                samples[i] = ((hi << 8) | lo) / 32768f;
            }

            st = rec.createStream();
            st.acceptWaveform(samples, 16000);
            st.acceptWaveform(new float[(int) (0.8 * 16000)], 16000);
            while (rec.isReady(st)) {
                rec.decode(st);
            }
            String text = rec.getResult(st).getText();
            JSONObject o = new JSONObject();
            o.put("text", text == null ? "" : text);
            o.put("ms", (System.nanoTime() - w0) / 1000000L);
            o.put("cpu", (android.os.Debug.threadCpuTimeNanos() - c0) / 1000000L);
            return o.toString();
        } catch (Throwable t) {
            return "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}";
        } finally {
            try { if (st != null) st.release(); } catch (Throwable t) { }
            if (prio) { try { android.os.Process.setThreadPriority(oldPrio); } catch (Throwable t) { } }
        }
    }
}

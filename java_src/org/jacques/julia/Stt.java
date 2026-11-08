package org.jacques.julia;

import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.util.Arrays;

/* Reconnaissance vocale locale (Vosk) : lit un .wav 16 kHz mono et renvoie le JSON de Vosk,
   complété par "ms" (durée réelle du calcul) et "cpu" (temps de processeur réellement utilisé) pour le diagnostic. */
public class Stt {
    private static Model model = null;

    public static synchronized String load(String modelPath) {
        if (model != null) return "ok";
        try {
            model = new Model(modelPath);
            return "ok";
        } catch (Throwable t) {
            return "erreur " + t;
        }
    }

    /* Toute la phrase. grammar vide = transcription libre. */
    public static String transcribe(String wavPath, String grammar) {
        return run(wavPath, grammar, 0, -1, false);
    }

    /* Passe rapide : seulement les toMs premières millisecondes, avec une liste de mots (grammaire) et les mots horodatés. */
    public static String wake(String wavPath, String grammar, int toMs) {
        return run(wavPath, grammar, 0, toMs, true);
    }

    /* Transcription libre à partir de fromMs (pour sauter le nom de Niko et ne transcrire que la commande). */
    public static String transcribeFrom(String wavPath, int fromMs) {
        return run(wavPath, "", fromMs, -1, false);
    }

    private static synchronized String run(String wavPath, String grammar, int fromMs, int toMs, boolean words) {
        if (model == null) return "{\"err\":\"modele non charge\"}";
        Recognizer rec = null;
        boolean prio = false;
        int oldPrio = 0;
        long w0 = System.nanoTime();
        long c0 = android.os.Debug.threadCpuTimeNanos();
        try {
            /* le calcul passe devant les autres tâches de l'appli pendant quelques secondes */
            try {
                oldPrio = android.os.Process.getThreadPriority(android.os.Process.myTid());
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO);
                prio = true;
            } catch (Throwable t) { }

            if (grammar == null || grammar.length() == 0) rec = new Recognizer(model, 16000.0f);
            else rec = new Recognizer(model, 16000.0f, grammar);
            if (words) {
                /* le nom de la méthode change selon la version de Vosk */
                try {
                    rec.getClass().getMethod("setWords", boolean.class).invoke(rec, true);
                } catch (Throwable t) {
                    try { rec.getClass().getMethod("setOutputWordTimes", boolean.class).invoke(rec, true); } catch (Throwable t2) { }
                }
            }

            File f = new File(wavPath);
            byte[] all = new byte[(int) f.length()];
            DataInputStream d = new DataInputStream(new FileInputStream(f));
            d.readFully(all);
            d.close();

            /* 16 échantillons par milliseconde, 2 octets chacun, après l'en-tête de 44 octets */
            long from = 44L + 32L * Math.max(0, fromMs);
            long end = toMs < 0 ? all.length : Math.min((long) all.length, 44L + 32L * toMs);
            if (from > all.length) from = all.length;
            int len = (int) Math.max(0L, end - from);
            len -= len % 2;
            byte[] pcm = Arrays.copyOfRange(all, (int) from, (int) from + len);
            rec.acceptWaveForm(pcm, pcm.length);

            JSONObject o = new JSONObject(rec.getFinalResult());
            o.put("ms", (System.nanoTime() - w0) / 1000000L);
            o.put("cpu", (android.os.Debug.threadCpuTimeNanos() - c0) / 1000000L);
            return o.toString();
        } catch (Throwable t) {
            return "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}";
        } finally {
            try { if (rec != null) rec.close(); } catch (Throwable t) { }
            if (prio) { try { android.os.Process.setThreadPriority(oldPrio); } catch (Throwable t) { } }
        }
    }
}

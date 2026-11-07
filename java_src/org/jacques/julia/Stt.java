package org.jacques.julia;

import org.vosk.Model;
import org.vosk.Recognizer;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.util.Arrays;

/* Reconnaissance vocale locale (Vosk) : lit un .wav 16 kHz mono et renvoie le JSON de Vosk. */
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

    public static synchronized String transcribe(String wavPath, String grammar) {
        if (model == null) return "{\"err\":\"modele non charge\"}";
        Recognizer rec = null;
        try {
            if (grammar == null || grammar.length() == 0) rec = new Recognizer(model, 16000.0f);
            else rec = new Recognizer(model, 16000.0f, grammar);
            File f = new File(wavPath);
            byte[] all = new byte[(int) f.length()];
            DataInputStream d = new DataInputStream(new FileInputStream(f));
            d.readFully(all);
            d.close();
            byte[] pcm = Arrays.copyOfRange(all, 44, all.length - (all.length % 2));
            rec.acceptWaveForm(pcm, pcm.length);
            return rec.getFinalResult();
        } catch (Throwable t) {
            return "{\"err\":\"" + String.valueOf(t).replace("\"", "'") + "\"}";
        } finally {
            try { if (rec != null) rec.close(); } catch (Throwable t) { }
        }
    }
}

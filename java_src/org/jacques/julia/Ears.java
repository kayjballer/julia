package org.jacques.julia;

import org.vosk.Model;
import org.vosk.Recognizer;
import org.vosk.android.RecognitionListener;
import org.vosk.android.SpeechService;
import java.util.concurrent.ConcurrentLinkedQueue;

/* Oreilles de Julia : écoute le micro en continu avec Vosk et dépose les résultats
   dans une file que Python lit avec poll() (jamais de rappel vers Python : plus sûr). */
public class Ears implements RecognitionListener {
    private static final ConcurrentLinkedQueue<String> Q = new ConcurrentLinkedQueue<String>();
    private static Ears inst = null;
    private Model model = null;
    private SpeechService svc = null;
    private String lastPartial = "";

    public static synchronized String start(String modelPath, String grammar) {
        if (inst != null) return "ok";
        try {
            Ears e = new Ears();
            e.model = new Model(modelPath);
            Recognizer rec;
            if (grammar == null || grammar.length() == 0) {
                rec = new Recognizer(e.model, 16000.0f);
            } else {
                rec = new Recognizer(e.model, 16000.0f, grammar);
            }
            e.svc = new SpeechService(rec, 16000.0f);
            if (!e.svc.startListening(e)) return "micro occupe";
            inst = e;
            return "ok";
        } catch (Throwable t) {
            return "erreur " + t;
        }
    }

    public static synchronized void stop() {
        if (inst == null) return;
        try {
            inst.svc.stop();
            inst.svc.shutdown();
            inst.model.close();
        } catch (Throwable t) {
            // rien
        }
        inst = null;
    }

    public static String poll() {
        return Q.poll();
    }

    private void push(String kind, String json) {
        if (Q.size() > 60) Q.poll();
        Q.add(kind + "|" + json);
    }

    @Override public void onPartialResult(String h) {
        if (h != null && !h.equals(lastPartial)) {
            lastPartial = h;
            push("P", h);
        }
    }
    @Override public void onResult(String h) {
        lastPartial = "";
        push("R", h);
    }
    @Override public void onFinalResult(String h) {
        push("F", h);
    }
    @Override public void onError(Exception e) {
        push("E", "{\"err\":\"" + String.valueOf(e).replace("\"", "'") + "\"}");
    }
    @Override public void onTimeout() {
        push("T", "{}");
    }
}

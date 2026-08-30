package playme.webview;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.Collections;
import java.util.List;

/**
 * PlayMe WebView: URL del servidor configurable.
 *
 * Orden de resolución de la URL:
 *   a) SharedPreferences "server_url" (guardada en uso anterior)
 *   b) 127.0.0.1:8090 (servidor local) -> si responde HTTP 200
 *   c) IP pública del servidor detectada vía api.ipify.org + :8090 -> si responde
 *   d) Diálogo manual pidiendo IP/host (guardado para el siguiente arranque)
 */
public class MainActivity extends Activity {
    private static final String PREFS = "playme_prefs";
    private static final String KEY_URL = "server_url";
    private static final int PORT = 8090;
    private static final int TIMEOUT_MS = 2000;

    private WebView webView;
    private android.content.SharedPreferences prefs;
    private final Handler main = new Handler(Looper.getMainLooper());
    private String currentUrl;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        webView = findViewById(R.id.webview);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                android.widget.Toast.makeText(MainActivity.this,
                        "Error de red. Falló: " + failingUrl + "\nUse el botón ⋮ para cambiar de servidor.",
                        android.widget.Toast.LENGTH_LONG).show();
            }
        });

        showConnecting("Conectando...");
        new Thread(resolveUrl, "resolve-url").start();
    }

    private void showConnecting(final String msg) {
        runOnUiThread(() -> android.widget.Toast.makeText(this, msg, android.widget.Toast.LENGTH_SHORT).show());
    }

    private final Runnable resolveUrl = new Runnable() {
        @Override public void run() {
            String saved = prefs.getString(KEY_URL, null);
            if (saved != null && responds(saved)) {
                load("http://" + stripScheme(saved));
                return;
            }
            // b) local
            String local = "http://127.0.0.1:" + PORT;
            if (responds(local + "/")) {
                saveAndLoad(local);
                return;
            }
            // c) IP pública autocstectada
            String publicIp = publicIp();
            if (publicIp != null) {
                String cand = "http://" + publicIp + ":" + PORT;
                if (responds(cand + "/")) {
                    saveAndLoad(cand);
                    return;
                }
            }
            // d) diálogo manual
            main.post(() -> askManual());
        }
    };

    private String stripScheme(String u) { return u.startsWith("http") ? u.substring(u.indexOf("//") + 2) : u; }

    /** Devuelve true si el endpoint /api/state responde HTTP 200. */
    private boolean responds(String base) {
        try {
            URL u = new URL(base + "/api/state");
            HttpURLConnection c = (HttpURLConnection) u.openConnection();
            c.setConnectTimeout(TIMEOUT_MS);
            c.setReadTimeout(1500);
            c.setRequestMethod("GET");
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (IOException e) {
            return false;
        }
    }

    /** IP pública vía api.ipify.org (timeout corto, no bloquea la UI). */
    private String publicIp() {
        try {
            URL u = new URL("https://api.ipify.org");
            HttpURLConnection c = (HttpURLConnection) u.openConnection();
            c.setConnectTimeout(3000);
            c.setReadTimeout(3000);
            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
            String ip = r.readLine().trim();
            r.close();
            c.disconnect();
            if (ip != null && ip.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return ip;
        } catch (IOException ignored) {}
        return null;
    }

    private void saveAndLoad(final String url) {
        prefs.edit().putString(KEY_URL, url).apply();
        load(url);
    }

    private void load(final String url) {
        currentUrl = url;
        runOnUiThread(() -> webView.loadUrl(url));
    }

    private void askManual() {
        final android.widget.EditText input = new android.widget.EditText(this);
        input.setHint("192.168.1.10");
        String prev = prefs.getString(KEY_URL, null);
        if (prev != null) input.setText(stripScheme(prev));
        new AlertDialog.Builder(this)
                .setTitle("Servidor PlayMe")
                .setMessage("IP o host del servidor (ej: 192.168.1.10):")
                .setView(input)
                .setPositiveButton("Conectar", (d, w) -> {
                    String host = input.getText().toString().trim();
                    if (!host.isEmpty()) {
                        String base = host.startsWith("http") ? host : "http://" + host;
                        base = base.endsWith(":" + PORT) ? base : base + ":" + PORT;
                        prefs.edit().putString(KEY_URL, base).apply();
                        load(base);
                    } else {
                        askManual();
                    }
                })
                .setNegativeButton("Cancelar", null)
                .show();
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}

package com.riveros.playme;

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;

/**
 * PlayMe local: corre el backend Python (yt-dlp embebido) en 127.0.0.1 y lo muestra en un WebView.
 *
 * Cookie de YouTube: se obtiene del propio login de YouTube hecho en el WebView
 * (CookieManager) y se guarda como cookies.txt Netscape en filesDir, que es lo que
 * usa yt-dlp en el dispositivo. No hay servidor externo ni PC.
 */
public class MainActivity extends Activity {

    private static final String TAG = "PlayMeLocal";
    private static final int PORT = 8191;
    private static final String LOCAL_URL = "http://127.0.0.1:" + PORT + "/";

    private WebView web;
    private TextView status;
    private Button btnCookie;
    private File dataDir;
    private File cookieFile;
    private int retries = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        status = findViewById(R.id.status);
        btnCookie = findViewById(R.id.btnCookie);

        dataDir = getFilesDir();
        cookieFile = new File(dataDir, "cookies.txt");

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        if (Build.VERSION.SDK_INT >= 26) s.setSafeBrowsingEnabled(false);

        CookieManager.getInstance().setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= 21) CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                // Mientras el backend Python arranca, el puerto local aun no escucha: reintentar.
                if (request.getUrl().toString().startsWith("http://127.0.0.1") && retries < 30) {
                    retries++;
                    final WebView v = view;
                    v.postDelayed(() -> v.loadUrl(LOCAL_URL), 1000);
                }
            }
        });

        btnCookie.setOnClickListener(v -> guardarCookieYArrancar());

        if (tieneCookie()) {
            arrancar();
        } else {
            modoLogin();
        }
    }

    private boolean tieneCookie() {
        if (!cookieFile.isFile() || cookieFile.length() == 0) return false;
        try (BufferedReader r = new BufferedReader(new FileReader(cookieFile))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (!line.startsWith("#") && line.contains("\tSID\t")) return true;
            }
        } catch (IOException e) {
            Log.w(TAG, "leyendo cookies.txt", e);
        }
        return false;
    }

    private void modoLogin() {
        status.setText("1) Inicia sesion en YouTube  2) Pulsa GUARDAR COOKIE");
        btnCookie.setText("Guardar cookie de YouTube");
        web.loadUrl("https://m.youtube.com/");
    }

    private void guardarCookieYArrancar() {
        CookieManager cm = CookieManager.getInstance();
        String ck = cm.getCookie("https://www.youtube.com");
        if (ck == null || !ck.contains("SID=")) {
            String m = cm.getCookie("https://m.youtube.com");
            if (m != null && m.contains("SID=")) ck = m;
        }
        if (ck == null || !ck.contains("SID=")) {
            Toast.makeText(this, "Sin sesion: inicia sesion en YouTube primero", Toast.LENGTH_LONG).show();
            return;
        }
        int n = escribirNetscape(ck);
        Toast.makeText(this, "Cookie guardada (" + n + " cookies)", Toast.LENGTH_SHORT).show();
        arrancar();
    }

    /** Convierte el header Cookie ("a=b; c=d") al formato Netscape que espera yt-dlp. */
    private int escribirNetscape(String header) {
        long exp = System.currentTimeMillis() / 1000 + 365L * 24 * 3600;
        int n = 0;
        StringBuilder sb = new StringBuilder("# Netscape HTTP Cookie File\n");
        sb.append("# PlayMe Android - cookies capturadas del login en el WebView\n");
        for (String part : header.split(";")) {
            String p = part.trim();
            int eq = p.indexOf('=');
            if (eq <= 0) continue;
            String name = p.substring(0, eq).trim();
            String value = p.substring(eq + 1).trim();
            if (name.isEmpty()) continue;
            sb.append(".youtube.com\tTRUE\t/\tTRUE\t").append(exp).append('\t')
              .append(name).append('\t').append(value).append('\n');
            n++;
        }
        try (FileWriter w = new FileWriter(cookieFile, false)) {
            w.write(sb.toString());
        } catch (IOException e) {
            Log.e(TAG, "escribiendo cookies.txt", e);
        }
        return n;
    }

    private void arrancar() {
        status.setText("Iniciando PlayMe local (yt-dlp embebido)...");
        btnCookie.setText("Actualizar cookie de YouTube");
        final Activity self = this;
        new Thread(() -> {
            String msg;
            boolean ok = false;
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(self));
                Object r = Python.getInstance().getModule("playme_boot")
                        .callAttr("start", dataDir.getAbsolutePath()).toJava(Object.class);
                ok = Boolean.TRUE.equals(r) || "True".equals(String.valueOf(r));
                msg = ok ? ("PlayMe local: " + LOCAL_URL) : "El servidor no arranco";
            } catch (Throwable e) {
                Log.e(TAG, "boot python", e);
                msg = "Error Python: " + e.getMessage();
            }
            final boolean fOk = ok;
            final String fMsg = msg;
            runOnUiThread(() -> {
                status.setText(fMsg);
                if (fOk) {
                    retries = 0;
                    web.loadUrl(LOCAL_URL);
                }
            });
        }).start();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}

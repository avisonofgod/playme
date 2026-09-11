package com.riveros.playme;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

public class MainActivity extends Activity {

    static final String TAG = "PlayMeLocal";
    static final int PORT = 8191;
    static final int REQ_COOKIES = 1001;

    WebView web;
    TextView status;
    Button btnLogin, btnImport, btnReload;
    File cookieFile;
    boolean loginMode = false;
    int retries = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        status = findViewById(R.id.status);
        btnLogin = findViewById(R.id.btnLogin);
        btnImport = findViewById(R.id.btnImport);
        btnReload = findViewById(R.id.btnReload);
        cookieFile = new File(getFilesDir(), "cookies.txt");

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return false;
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (!loginMode && url != null && url.contains("127.0.0.1:" + PORT)) {
                    status.setText("PlayMe local activo (" + (hasCookie() ? "con cookie" : "sin cookie") + ")");
                }
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, android.webkit.WebResourceError e) {
                if (!loginMode && retries < 20) {
                    retries++;
                    v.postDelayed(() -> v.loadUrl(localUrl()), 1000);
                } else if (!loginMode) {
                    status.setText("No responde el servidor local");
                }
            }
        });

        btnLogin.setOnClickListener(v -> {
            if (!loginMode) {
                loginMode = true;
                btnLogin.setText("GUARDAR COOKIE DE YOUTUBE");
                status.setText("1) Inicia sesion en YouTube  2) Pulsa GUARDAR COOKIE");
                web.loadUrl("https://m.youtube.com/");
            } else {
                guardarCookie();
            }
        });

        btnImport.setOnClickListener(v -> {
            Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            i.addCategory(Intent.CATEGORY_OPENABLE);
            i.setType("*/*");
            startActivityForResult(i, REQ_COOKIES);
        });

        btnReload.setOnClickListener(v -> {
            loginMode = false;
            btnLogin.setText("INICIAR SESION YOUTUBE");
            arrancar();
        });

        status.setText("Iniciando PlayMe local...");
        arrancar();
    }

    String localUrl() {
        return "http://127.0.0.1:" + PORT + "/";
    }

    boolean hasCookie() {
        if (!cookieFile.isFile() || cookieFile.length() == 0) return false;
        try {
            java.io.BufferedReader br = new java.io.BufferedReader(new java.io.FileReader(cookieFile));
            String l;
            while ((l = br.readLine()) != null) {
                if (l.contains("\tSID\t") || l.startsWith("SID\t") || l.contains("\tHSID\t")) {
                    br.close();
                    return true;
                }
            }
            br.close();
        } catch (Exception ignored) {
        }
        return false;
    }

    void arrancar() {
        status.setText("Iniciando PlayMe local...");
        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                Object r = Python.getInstance().getModule("playme_boot")
                        .callAttr("start", getFilesDir().getAbsolutePath()).toJava(Object.class);
                boolean ok = String.valueOf(r).equalsIgnoreCase("true");
                runOnUiThread(() -> {
                    if (ok) {
                        retries = 0;
                        status.setText(hasCookie() ? "PlayMe local activo (con cookie)" : "YouTube publico OK - importa cookies para mixes");
                        web.loadUrl(localUrl());
                    } else {
                        status.setText("Python arranco pero el puerto no respondio");
                    }
                });
            } catch (Throwable e) {
                Log.e(TAG, "boot", e);
                runOnUiThread(() -> status.setText("Error Python: " + e));
            }
        }).start();
    }

    void guardarCookie() {
        String ck = CookieManager.getInstance().getCookie("https://www.youtube.com");
        if (ck == null || !ck.contains("SID=")) {
            ck = CookieManager.getInstance().getCookie("https://m.youtube.com");
        }
        if (ck == null || !ck.contains("SID=")) {
            Toast.makeText(this, "Sin sesion en YouTube: inicia sesion primero", Toast.LENGTH_LONG).show();
            return;
        }
        int n = escribirNetscape(ck);
        Toast.makeText(this, "Cookie guardada (" + n + " cookies)", Toast.LENGTH_LONG).show();
        loginMode = false;
        btnLogin.setText("INICIAR SESION YOUTUBE");
        arrancar();
    }

    int escribirNetscape(String cookieHeader) {
        long exp = System.currentTimeMillis() / 1000 + 365L * 24 * 3600;
        int n = 0;
        try (FileOutputStream fo = new FileOutputStream(cookieFile)) {
            for (String pair : cookieHeader.split(";")) {
                String p = pair.trim();
                int eq = p.indexOf('=');
                if (eq <= 0) continue;
                String name = p.substring(0, eq);
                String value = p.substring(eq + 1);
                if (name.isEmpty() || value.isEmpty()) continue;
                String line = ".youtube.com\tTRUE\t/\tTRUE\t" + exp + "\t" + name + "\t" + value + "\n";
                fo.write(line.getBytes("UTF-8"));
                n++;
            }
        } catch (Exception e) {
            Log.e(TAG, "escribirNetscape", e);
        }
        return n;
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != REQ_COOKIES) return;
        if (res != RESULT_OK || data == null || data.getData() == null) {
            status.setText("Importacion cancelada");
            return;
        }
        Uri uri = data.getData();
        try (InputStream in = getContentResolver().openInputStream(uri);
             OutputStream out = new FileOutputStream(cookieFile)) {
            byte[] buf = new byte[8192];
            int r;
            long total = 0;
            while ((r = in.read(buf)) > 0) {
                out.write(buf, 0, r);
                total += r;
            }
            out.flush();
            boolean ok = hasCookie();
            status.setText(ok ? "Cookies importadas (" + total + " B)" : "Archivo sin SID/HSID");
            Toast.makeText(this, ok ? "Cookies OK" : "Ese archivo no tiene la cookie de YouTube", Toast.LENGTH_LONG).show();
            if (ok) arrancar();
        } catch (Exception e) {
            Log.e(TAG, "import", e);
            status.setText("Error importando: " + e.getMessage());
        }
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack() && !loginMode) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}

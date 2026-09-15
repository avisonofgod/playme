package com.riveros.playme;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.media.session.MediaSession;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/** Primer plano con notificacion: evita que el sistema corte la red de la app
 *  cuando la pantalla se apaga (MagicOS/Doze).
 *  En Android 14+ el tipo mediaPlayback exige una MediaSession ACTIVA: sin ella
 *  startForeground lanza SecurityException y el proceso muere. */
public class PlaymeService extends Service {
    private static final String CH = "playme";
    private static final String TAG = "PlayMeLocal";
    private MediaSession session;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm.getNotificationChannel(CH) == null) {
                nm.createNotificationChannel(new NotificationChannel(CH, "PlayMe", NotificationManager.IMPORTANCE_LOW));
            }
            n = new Notification.Builder(this, CH)
                    .setContentTitle("PlayMe").setContentText("Servidor local activo")
                    .setSmallIcon(android.R.drawable.ic_media_play).setOngoing(true).build();
        } else {
            n = new Notification.Builder(this)
                    .setContentTitle("PlayMe").setContentText("Servidor local activo")
                    .setSmallIcon(android.R.drawable.ic_media_play).setOngoing(true).build();
        }
        try {
            if (session == null) {
                session = new MediaSession(this, "PlaymeSession");
                session.setActive(true);
            }
            startForeground(1, n);
        } catch (Throwable e) {
            // nunca dejar que esto tumbe la app: sin primer plano se pierde la
            // red en background, pero la app sigue usable
            Log.w(TAG, "startForeground fallo: " + e);
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (session != null) {
            session.setActive(false);
            session.release();
            session = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}

package com.riveros.playme;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

/** Primer plano con notificacion: evita que el sistema corte la red de la app
 *  cuando la pantalla se apaga (MagicOS/Doze). */
public class PlaymeService extends Service {
    private static final String CH = "playme";

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
        startForeground(1, n);
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}

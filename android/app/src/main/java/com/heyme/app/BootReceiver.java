package com.heyme.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Starts Hey Me when the phone or Shield turns on. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (HeyMeService.prefs(context).getString("server", "").isEmpty()) return;
        try {
            context.startForegroundService(new Intent(context, HeyMeService.class));
        } catch (Exception ignored) {
            // Newer phones don't allow this; opening the app once starts it again.
        }
    }
}

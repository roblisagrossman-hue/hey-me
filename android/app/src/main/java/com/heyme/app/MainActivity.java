package com.heyme.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.EditText;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

/** The Hey Me screen: settings, a Talk button and what Hey Me is doing. */
public class MainActivity extends Activity {
    private EditText server, secret, name;
    private Switch listen;
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        server = findViewById(R.id.server);
        secret = findViewById(R.id.secret);
        name = findViewById(R.id.name);
        listen = findViewById(R.id.listen);
        status = findViewById(R.id.status);

        SharedPreferences p = HeyMeService.prefs(this);
        server.setText(p.getString("server", ""));
        secret.setText(p.getString("secret", ""));
        name.setText(p.getString("name", ""));
        listen.setChecked(p.getBoolean("listen", true));

        findViewById(R.id.save).setOnClickListener(v -> {
            save();
            restartService();
            toast("Saved");
        });
        findViewById(R.id.talk).setOnClickListener(v -> talk());
        listen.setOnCheckedChangeListener((v, on) -> {
            HeyMeService.prefs(this).edit().putBoolean("listen", on).apply();
            restartService();
        });
        findViewById(R.id.overlay).setOnClickListener(v -> openSettings(
                new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:" + getPackageName()))));
        findViewById(R.id.battery).setOnClickListener(v -> openSettings(
                new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)));

        askPermissions();
        if (!handleSetup(getIntent()) && configured()) {
            startHeyMe();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleSetup(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        status.setText(configured() ? HeyMeService.lastStatus
                : "Enter your Hey Me server address, secret and a name for this device, then tap Save.");
        HeyMeService.statusListener = text -> runOnUiThread(() -> status.setText(text));
    }

    @Override
    protected void onPause() {
        super.onPause();
        HeyMeService.statusListener = null;
    }

    /** Settings sent by the server (Shields) or by the "Set up this phone" link. */
    private boolean handleSetup(Intent i) {
        if (i == null) return false;
        String s = null, k = null, n = null;
        Uri u = i.getData();
        if (u != null && "heyme".equals(u.getScheme())) {
            s = u.getQueryParameter("server");
            k = u.getQueryParameter("secret");
            n = u.getQueryParameter("name");
        }
        if (i.hasExtra("server")) {
            s = i.getStringExtra("server");
            k = i.getStringExtra("secret");
            n = i.getStringExtra("name");
        }
        if (s == null || s.isEmpty()) return false;
        server.setText(s);
        if (k != null) secret.setText(k);
        if (n != null) name.setText(n);
        save();
        restartService();
        toast("Hey Me is set up!");
        return true;
    }

    private void save() {
        String s = server.getText().toString().trim();
        if (!s.isEmpty() && !s.startsWith("http")) s = "http://" + s;
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (!s.isEmpty() && Uri.parse(s).getPort() == -1) s = s + ":8765";
        server.setText(s);
        String n = name.getText().toString().trim();
        if (n.isEmpty()) n = Build.MODEL;
        name.setText(n);
        HeyMeService.prefs(this).edit()
                .putString("server", s)
                .putString("secret", secret.getText().toString().trim())
                .putString("name", n)
                .apply();
    }

    private boolean configured() {
        return !HeyMeService.prefs(this).getString("server", "").isEmpty();
    }

    private void startHeyMe() {
        startForegroundService(new Intent(this, HeyMeService.class));
    }

    private void restartService() {
        stopService(new Intent(this, HeyMeService.class));
        if (configured()) startHeyMe();
    }

    private void talk() {
        if (!configured()) {
            toast("Enter the server address and tap Save first.");
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            askPermissions();
            return;
        }
        startForegroundService(new Intent(this, HeyMeService.class).setAction(HeyMeService.ACTION_TALK));
    }

    private void askPermissions() {
        List<String> need = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            need.add(Manifest.permission.RECORD_AUDIO);
        }
        if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            need.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!need.isEmpty()) requestPermissions(need.toArray(new String[0]), 1);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (configured()) restartService();
    }

    private void openSettings(Intent i) {
        try {
            startActivity(i);
        } catch (Exception e) {
            toast("This device doesn't have that setting.");
        }
    }

    private void toast(String t) {
        Toast.makeText(this, t, Toast.LENGTH_SHORT).show();
    }
}

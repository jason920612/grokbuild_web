package com.grokweb.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * A thin WebView shell around the grokweb frontend. On first launch the user
 * pastes their grokweb URL (with the {@code ?key=} token); it is remembered and
 * loaded full-screen thereafter. Handles file uploads, downloads, the hardware
 * back button (in-page history, then a menu), and changing the URL.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "grokweb";
    private static final String KEY_URL = "url";
    private static final int FILE_CHOOSER_REQ = 1001;

    private static final int BG = 0xFF0A0A0A;
    private static final int FG = 0xFFF5F5F5;
    private static final int DIM = 0xFF9A9A9A;
    private static final int HINT = 0xFF6B6B6B;

    private WebView web;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String url = prefs().getString(KEY_URL, null);
        if (url == null || url.trim().isEmpty()) {
            showSetup(null);
        } else {
            showWeb(url);
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // ---------- setup screen ----------
    private void showSetup(String prefill) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(BG);
        int pad = dp(24);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("GrokWeb");
        title.setTextColor(FG);
        title.setTextSize(30);
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("貼上 grokweb 網址（含 ?key= token）");
        sub.setTextColor(DIM);
        sub.setTextSize(14);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, dp(8), 0, dp(28));
        root.addView(sub);

        final EditText input = new EditText(this);
        input.setHint("https://xxxx.trycloudflare.com/?key=...");
        input.setText(prefill != null ? prefill : "");
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextColor(FG);
        input.setHintTextColor(HINT);
        root.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Button go = new Button(this);
        go.setText("連線");
        go.setOnClickListener(v -> {
            String u = input.getText().toString().trim();
            if (u.isEmpty()) { toast("請輸入網址"); return; }
            if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://" + u;
            prefs().edit().putString(KEY_URL, u).apply();
            showWeb(u);
        });
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        glp.topMargin = dp(20);
        root.addView(go, glp);

        setContentView(root);
    }

    // ---------- web screen ----------
    @SuppressLint("SetJavaScriptEnabled")
    private void showWeb(String url) {
        web = new WebView(this);
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(BG);
        frame.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(frame);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return false; // keep navigation inside the app
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = cb;
                try {
                    startActivityForResult(params.createIntent(), FILE_CHOOSER_REQ);
                } catch (Exception e) {
                    filePathCallback = null;
                    toast("無法開啟檔案選擇器");
                    return false;
                }
                return true;
            }
        });

        web.setDownloadListener((durl, ua, cd, mime, len) -> {
            try {
                String name = URLUtil.guessFileName(durl, cd, mime);
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(durl));
                r.setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                toast("下載中：" + name);
            } catch (Exception e) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(durl)));
                } catch (Exception ignored) {
                    toast("無法下載");
                }
            }
        });

        web.loadUrl(url);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == FILE_CHOOSER_REQ) {
            if (filePathCallback == null) return;
            Uri[] results = null;
            if (res == Activity.RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int n = data.getClipData().getItemCount();
                    results = new Uri[n];
                    for (int i = 0; i < n; i++) {
                        results[i] = data.getClipData().getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        } else {
            super.onActivityResult(req, res, data);
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null) {
            if (web.canGoBack()) {
                web.goBack();
                return true;
            }
            new AlertDialog.Builder(this)
                    .setTitle("GrokWeb")
                    .setItems(new CharSequence[]{"重新載入", "更改網址", "離開"}, (d, which) -> {
                        if (which == 0) web.reload();
                        else if (which == 1) showSetup(prefs().getString(KEY_URL, ""));
                        else finish();
                    })
                    .show();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private void toast(String m) {
        Toast.makeText(this, m, Toast.LENGTH_SHORT).show();
    }
}

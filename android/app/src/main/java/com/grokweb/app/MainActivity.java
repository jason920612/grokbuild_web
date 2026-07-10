package com.grokweb.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
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
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;

/**
 * A thin WebView shell around the grokweb frontend. First launch takes the
 * grokweb URL — pasted or scanned from a QR code (with the {@code ?key=} token)
 * — remembers it, and loads full-screen thereafter. Handles file uploads,
 * downloads, the hardware back button, and prompts a re-scan when the saved URL
 * stops working (tunnel restarted -> the URL and token change).
 */
public class MainActivity extends ComponentActivity {

    private static final String PREFS = "grokweb";
    private static final String KEY_URL = "url";
    private static final int FILE_CHOOSER_REQ = 1001;

    private static final int BG = 0xFF0A0A0A;
    private static final int FG = 0xFFF5F5F5;
    private static final int DIM = 0xFF9A9A9A;
    private static final int HINT = 0xFF6B6B6B;

    private WebView web;
    private ValueCallback<Uri[]> filePathCallback;
    private boolean errorShown = false;

    private final ActivityResultLauncher<Intent> scanLauncher =
            registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), res -> {
                if (res.getResultCode() == Activity.RESULT_OK && res.getData() != null) {
                    String url = res.getData().getStringExtra("url");
                    if (url != null && !url.trim().isEmpty()) onScanned(url);
                }
            });

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

    private void launchScan() {
        scanLauncher.launch(new Intent(this, ScanActivity.class));
    }

    private void onScanned(String raw) {
        String u = raw.trim();
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://" + u;
        prefs().edit().putString(KEY_URL, u).apply();
        showWeb(u);
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
        sub.setText("掃描 QR，或貼上網址（含 ?key= token）");
        sub.setTextColor(DIM);
        sub.setTextSize(14);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, dp(8), 0, dp(28));
        root.addView(sub);

        Button scan = new Button(this);
        scan.setText("掃描 QR code");
        scan.setOnClickListener(v -> launchScan());
        root.addView(scan, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView or = new TextView(this);
        or.setText("— 或 —");
        or.setTextColor(HINT);
        or.setGravity(Gravity.CENTER);
        or.setPadding(0, dp(14), 0, dp(14));
        root.addView(or);

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
            if (u.isEmpty()) { toast("請輸入或掃描網址"); return; }
            onScanned(u);
        });
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        glp.topMargin = dp(16);
        root.addView(go, glp);

        setContentView(root);
    }

    // ---------- web screen ----------
    @SuppressLint("SetJavaScriptEnabled")
    private void showWeb(String url) {
        errorShown = false;
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

            @Override
            public void onPageStarted(WebView view, String u, android.graphics.Bitmap favicon) {
                errorShown = false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) {
                    showExpired("無法連線到伺服器（網址可能已失效）");
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest req,
                                            WebResourceResponse resp) {
                if (req.isForMainFrame() && resp.getStatusCode() == 403) {
                    showExpired("存取被拒（token 已失效）");
                }
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

    private void showExpired(String msg) {
        if (errorShown || isFinishing()) return;
        errorShown = true;
        new AlertDialog.Builder(this)
                .setTitle("連線問題")
                .setMessage(msg + "\n\n伺服器重開後網址與 token 會改變，請重新掃描 QR code。")
                .setCancelable(false)
                .setPositiveButton("重新掃描 QR", (d, w) -> launchScan())
                .setNeutralButton("重試", (d, w) -> { if (web != null) web.reload(); })
                .setNegativeButton("手動輸入", (d, w) -> showSetup(prefs().getString(KEY_URL, "")))
                .show();
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
                    .setItems(new CharSequence[]{"重新載入", "重新掃描 QR", "更改網址", "離開"},
                            (d, which) -> {
                                if (which == 0) web.reload();
                                else if (which == 1) launchScan();
                                else if (which == 2) showSetup(prefs().getString(KEY_URL, ""));
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

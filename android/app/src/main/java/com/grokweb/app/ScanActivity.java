package com.grokweb.app;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.Image;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.OptIn;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ExperimentalGetImage;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.mlkit.vision.barcode.BarcodeScanner;
import com.google.mlkit.vision.barcode.BarcodeScannerOptions;
import com.google.mlkit.vision.barcode.BarcodeScanning;
import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.common.InputImage;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Full-screen QR scanner. Returns the decoded string as the "url" result extra. */
public class ScanActivity extends ComponentActivity {

    private PreviewView previewView;
    private BarcodeScanner scanner;
    private ExecutorService analysisExecutor;
    private volatile boolean handled = false;

    private final ActivityResultLauncher<String> permLauncher =
            registerForActivityResult(new ActivityResultContracts.RequestPermission(), granted -> {
                if (granted) {
                    startCamera();
                } else {
                    Toast.makeText(this, "需要相機權限才能掃描 QR", Toast.LENGTH_LONG).show();
                    finish();
                }
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        previewView = new PreviewView(this);
        root.addView(previewView, new FrameLayout.LayoutParams(-1, -1));

        TextView hint = new TextView(this);
        hint.setText("將 QR code 對準框內");
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(15);
        hint.setPadding(0, 0, 0, dp(48));
        FrameLayout.LayoutParams hlp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hlp.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        root.addView(hint, hlp);
        setContentView(root);

        scanner = BarcodeScanning.getClient(new BarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .build());
        analysisExecutor = Executors.newSingleThreadExecutor();

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            permLauncher.launch(Manifest.permission.CAMERA);
        }
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                ProcessCameraProvider provider = future.get();
                Preview preview = new Preview.Builder().build();
                preview.setSurfaceProvider(previewView.getSurfaceProvider());

                ImageAnalysis analysis = new ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                analysis.setAnalyzer(analysisExecutor, this::analyze);

                provider.unbindAll();
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis);
            } catch (Exception e) {
                Toast.makeText(this, "無法啟動相機", Toast.LENGTH_LONG).show();
                finish();
            }
        }, ContextCompat.getMainExecutor(this));
    }

    @OptIn(markerClass = ExperimentalGetImage.class)
    private void analyze(ImageProxy proxy) {
        Image media = proxy.getImage();
        if (media == null || handled) {
            proxy.close();
            return;
        }
        InputImage input = InputImage.fromMediaImage(media, proxy.getImageInfo().getRotationDegrees());
        scanner.process(input)
                .addOnSuccessListener(barcodes -> {
                    for (Barcode b : barcodes) {
                        String raw = b.getRawValue();
                        if (raw != null && !raw.isEmpty() && !handled) {
                            handled = true;
                            Intent data = new Intent();
                            data.putExtra("url", raw);
                            setResult(RESULT_OK, data);
                            finish();
                            return;
                        }
                    }
                })
                .addOnCompleteListener(t -> proxy.close());
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (analysisExecutor != null) analysisExecutor.shutdown();
        if (scanner != null) scanner.close();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}

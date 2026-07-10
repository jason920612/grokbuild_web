# GrokWeb Android App

一個輕量的 WebView 外殼 App，把 grokweb 網頁介面包成原生 Android App。純 framework（無 AndroidX / 無第三方依賴），APK 僅約 20KB。

## 功能

- **掃描 QR code 自動填網址**（原生 CameraX + ML Kit 離線辨識），或手動貼上網址（含 `?key=` token）；之後記憶並全螢幕載入
- **網址/token 失效自動提示重掃**：主頁載入失敗或回 403 時彈出對話框（重新掃描 QR / 重試 / 手動輸入）——tunnel 重開後網址與 token 會變，這時直接重掃即可
- 檔案 / 圖片上傳（`<input type=file>` → 系統選擇器，支援多選）
- 檔案下載（`/api/.../file` 下載 → 系統 DownloadManager）
- 硬體返回鍵：先走網頁歷史，到頂後跳選單（重新載入 / 重新掃描 QR / 更改網址 / 離開）
- Cookie 持久化（token cookie），DOM storage、深色沉浸
- 支援 http（區網）與 https（Cloudflare tunnel）

## 安裝現成 APK

專案根目錄的 `GrokWeb.apk`（或 `android/app/build/outputs/apk/debug/app-debug.apk`）是簽章好的 debug APK，手機開啟即可安裝（需允許「安裝未知來源」）。

## 自行建置

需要 JDK 17 與 Android SDK（platform-34、build-tools 34.0.0）。設定 `local.properties` 的 `sdk.dir` 後：

```powershell
cd android
$env:JAVA_HOME="<jdk17 路徑>"
.\gradlew.bat assembleDebug
# 產物：app/build/outputs/apk/debug/app-debug.apk
```

或用 Android Studio 直接開啟 `android/` 資料夾建置。

## 規格

- package `com.grokweb.app`，minSdk 26（Android 8.0）、targetSdk 34，APK ~13MB
- AGP 8.6.1、Gradle 8.9、Java 17、AndroidX
- `MainActivity`（WebView 外殼）+ `ScanActivity`（CameraX + ML Kit QR），UI 以程式建立（無 layout XML）
- 依賴：androidx.camera:1.3.4、com.google.mlkit:barcode-scanning:17.3.0（離線 bundled）
- 只打包 arm64-v8a / armeabi-v7a（真實手機；不含 x86 模擬器）
